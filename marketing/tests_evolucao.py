"""Testes Fase 11 — evolução temporal e comparação de períodos."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from marketing.services.periodo import PeriodoMarketing
from marketing.services.resultados_evolucao import (
    GRAN_DIA,
    GRAN_SEMANA,
    calcular_evolucao_resultados,
    iter_buckets,
    linhas_comparacao_periodo,
    resolver_granularidade,
    tendencias_completas,
)
from marketing.services.google_ads_resultados import calcular_resultados_negocio
from marketing.tests_helpers import grant_marketing_permissions
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

User = get_user_model()


def _dt_no_dia(d, hora=10):
    from datetime import datetime, time

    naive = datetime.combine(d, time(hour=hora))
    return timezone.make_aware(naive)


class ResultadosEvolucaoFase11Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mkt_f11", password="senha123")
        self.org = Organization.objects.create(name="Mkt F11 Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        grant_marketing_permissions(self.user)
        self.http = Client()
        self.http.login(username="mkt_f11", password="senha123")
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(14, referencia=self.hoje)

    def _lead(self, nome, email, dia=None):
        c = Cliente(
            user=self.user,
            organization=self.org,
            nome=nome,
            email=email,
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        if dia is not None:
            c.save()
            Cliente.objects.filter(pk=c.pk).update(criado_em=_dt_no_dia(dia))
            c.refresh_from_db()
            return c
        c.save()
        return c

    def test_resolver_granularidade_auto(self):
        self.assertEqual(resolver_granularidade(PeriodoMarketing.ultimos_dias(7), "auto"), GRAN_DIA)
        self.assertEqual(
            resolver_granularidade(PeriodoMarketing.ultimos_dias(45), "auto"), GRAN_SEMANA
        )

    def test_iter_buckets_dia(self):
        p = PeriodoMarketing.ultimos_dias(3, referencia=self.hoje)
        buckets = iter_buckets(p, GRAN_DIA)
        self.assertEqual(len(buckets), 3)

    def test_evolucao_serie_leads_por_dia(self):
        d1 = self.periodo.data_inicio
        d2 = d1 + timedelta(days=1)
        self._lead("L1", "l1@test.com", d1)
        self._lead("L2", "l2@test.com", d2)
        evo = calcular_evolucao_resultados(
            self.user, self.periodo, organization=self.org, granularidade=GRAN_DIA
        )
        self.assertEqual(len(evo.labels), self.periodo.dias)
        self.assertEqual(sum(evo.leads), 2)
        self.assertTrue(evo.tem_dados())

    def test_evolucao_consultas_e_contratos(self):
        lead = self._lead("Full", "full@test.com", self.periodo.data_inicio)
        Compromisso.objects.create(
            user=self.user,
            organization=self.org,
            cliente=lead,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.periodo.data_inicio),
        )
        Contrato.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=lead,
            referencia="CTR-001",
            descricao="Contrato teste",
            valor_total=Decimal("5000.00"),
            status=StatusContrato.ACTIVE,
        )
        Contrato.objects.filter(cliente=lead).update(criado_em=_dt_no_dia(self.periodo.data_inicio))
        evo = calcular_evolucao_resultados(
            self.user, self.periodo, organization=self.org, granularidade=GRAN_DIA
        )
        self.assertEqual(sum(evo.consultas), 1)
        self.assertEqual(sum(evo.contratos), 1)
        self.assertAlmostEqual(sum(evo.receita), 5000.0)

    def test_tendencias_completas_inclui_propostas(self):
        atual = calcular_resultados_negocio(
            self.user, self.periodo, organization=self.org, modo_demo=True
        )
        anterior = calcular_resultados_negocio(
            self.user,
            self.periodo.periodo_anterior(),
            organization=self.org,
            modo_demo=True,
        )
        t = tendencias_completas(atual, anterior)
        self.assertIn("propostas", t)
        self.assertIn("consultas_agendadas", t)

    def test_linhas_comparacao(self):
        atual = calcular_resultados_negocio(
            self.user, self.periodo, organization=self.org, modo_demo=True
        )
        anterior = calcular_resultados_negocio(
            self.user,
            self.periodo.periodo_anterior(),
            organization=self.org,
            modo_demo=True,
        )
        t = tendencias_completas(atual, anterior)
        linhas = linhas_comparacao_periodo(atual, anterior, t, ocultar_financeiro=False)
        self.assertGreaterEqual(len(linhas), 5)
        self.assertEqual(linhas[0].metrica, "Leads")

    def test_dashboard_exibe_comparacao_e_evolucao(self):
        self._lead("Dash", "dash@test.com", self.hoje)
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "Comparação com período anterior")
        self.assertContains(resp, "Evolução de resultados")
        self.assertContains(resp, "chartResultadosEvolucao")

    def test_dashboard_granularidade_semana(self):
        self._lead("Sem", "sem@test.com", self.hoje)
        resp = self.http.get(
            reverse("marketing_dashboard"), {"dias": "30", "granularidade": "semana"}
        )
        self.assertContains(resp, "agrupamento: semana")

    def test_chart_payload_evolucao(self):
        self._lead("Chart", "chart@test.com", self.hoje)
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, '"evolucao"')
        self.assertContains(resp, '"leads"')
