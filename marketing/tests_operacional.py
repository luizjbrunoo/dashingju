"""Testes Fase 10 — links operacionais e filtros em clientes."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from marketing.services.periodo import PeriodoMarketing
from marketing.services.resultados_operacional import (
    MKT_FOLLOWUP_ATRASADO,
    MKT_SEM_ACAO,
    aplicar_filtro_mkt_clientes,
    links_operacionais,
    queryset_followups_atrasados,
    queryset_leads_sem_proxima_acao,
    url_lista_clientes_mkt,
)
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

User = get_user_model()


def _dt_no_dia(d, hora=10):
    from datetime import datetime, time

    naive = datetime.combine(d, time(hour=hora))
    return timezone.make_aware(naive)


class ResultadosOperacionalFase10Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mkt_f10", password="senha123")
        self.http = Client()
        self.http.login(username="mkt_f10", password="senha123")
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(30, referencia=self.hoje)

    def _lead(self, nome, email, **kwargs):
        return Cliente.objects.create(
            user=self.user,
            nome=nome,
            email=email,
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            **kwargs,
        )

    def test_lead_sem_acao_sem_compromisso_nem_tarefa(self):
        lead = self._lead("Sem Acao", "sem@test.com")
        self.assertEqual(queryset_leads_sem_proxima_acao(self.user, self.periodo).get(), lead)

    def test_lead_com_compromisso_futuro_nao_conta_sem_acao(self):
        lead = self._lead("Com Agenda", "agenda@test.com")
        Compromisso.objects.create(
            user=self.user,
            cliente=lead,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje + timedelta(days=2)),
        )
        self.assertEqual(queryset_leads_sem_proxima_acao(self.user, self.periodo).count(), 0)

    def test_followup_vencido_aparece_na_lista(self):
        lead = self._lead("Follow Atraso", "fu@test.com")
        Compromisso.objects.create(
            user=self.user,
            cliente=lead,
            titulo="Follow-up",
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora=_dt_no_dia(self.hoje - timedelta(days=2)),
            status=StatusCompromisso.AGENDADO,
        )
        qs = queryset_followups_atrasados(self.user, self.periodo)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.get(), lead)

    def test_proposta_sem_followup_futuro(self):
        lead = self._lead(
            "Proposta",
            "prop@test.com",
            fase_funil="proposta_enviada",
            status="em_prospeccao",
        )
        self.assertIn(lead, queryset_followups_atrasados(self.user, self.periodo))

    def test_links_somente_com_itens(self):
        sem_links = links_operacionais(
            self.user, self.periodo, leads_sem_acao=0, followups_atrasados=0
        )
        self.assertIsNone(sem_links.leads_sem_acao)
        self.assertIsNone(sem_links.followups_atrasados)

        self._lead("X", "x@test.com")
        com_link = links_operacionais(
            self.user, self.periodo, leads_sem_acao=1, followups_atrasados=0
        )
        self.assertIn("mkt=sem_acao", com_link.leads_sem_acao)
        self.assertIn("dias=30", com_link.leads_sem_acao)

    def test_url_lista_clientes_mkt(self):
        url = url_lista_clientes_mkt(self.periodo, MKT_FOLLOWUP_ATRASADO)
        self.assertTrue(url.startswith("/usuarios/clientes/"))
        self.assertIn("mkt=followup_atrasado", url)

    def test_clientes_filtro_sem_acao(self):
        incluido = self._lead("Lista OK", "ok@test.com")
        outro = self._lead("Com Futuro", "fut@test.com")
        Compromisso.objects.create(
            user=self.user,
            cliente=outro,
            titulo="Call",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
        )
        resp = self.http.get(reverse("clientes"), {"mkt": MKT_SEM_ACAO, "dias": "30"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Lista OK")
        self.assertNotContains(resp, "Com Futuro")
        self.assertContains(resp, "Filtro Marketing")
        self.assertContains(resp, incluido.nome)

    def test_dashboard_link_sem_acao(self):
        self._lead("Dash Link", "dashlink@test.com")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "mkt=sem_acao")
        self.assertContains(resp, "Ver na lista de clientes")

    def test_aplicar_filtro_mkt_tenant(self):
        user_b = User.objects.create_user(username="outro_t", password="senha123")
        self._lead("Meu", "meu@test.com")
        Cliente.objects.create(
            user=user_b,
            nome="Outro tenant",
            email="outro@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        base = Cliente.objects.filter(user=self.user)
        filtrado = aplicar_filtro_mkt_clientes(
            self.user, base, MKT_SEM_ACAO, self.periodo
        )
        self.assertEqual(filtrado.count(), 1)
        self.assertEqual(filtrado.get().nome, "Meu")
