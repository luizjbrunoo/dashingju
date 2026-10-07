"""Testes Fase 2 — oportunidades, recomendações, simulador, origem, área."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import AcaoComercialResolvida
from comercial.services.opportunities import listar_oportunidades
from comercial.services.origem import origem_para_receita
from comercial.services.periodo import PeriodoComercial
from comercial.services.recommendations import (
    criar_tarefa_da_recomendacao,
    listar_recomendacoes,
    marcar_resolvida,
)
from comercial.services.simulator import simular_crescimento
from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa

User = get_user_model()


def _dt(d, hora=10):
    from datetime import datetime, time

    return timezone.make_aware(datetime.combine(d, time(hour=hora)))


class ComercialFase2Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="com2_a", password="senha123")
        self.user_b = User.objects.create_user(username="com2_b", password="senha123")
        self.org_a = Organization.objects.create(name="Com2 Org A")
        self.org_b = Organization.objects.create(name="Com2 Org B")
        Membership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.user_a)
        grant_comercial_permissions(self.user_b)
        self.http = Client()
        self.hoje = timezone.localdate()
        self.periodo = PeriodoComercial.ultimos_dias(30, referencia=self.hoje)

    def test_simulador_diferenca(self):
        res = simular_crescimento(
            leads_atual=100,
            taxa_atual_pct=Decimal("20"),
            ticket_atual=Decimal("5000"),
            leads_sim=100,
            taxa_sim_pct=Decimal("25"),
            ticket_sim=Decimal("5000"),
        )
        self.assertEqual(res.atual.contratos, Decimal("20.00"))
        self.assertEqual(res.simulado.contratos, Decimal("25.00"))
        self.assertEqual(res.diff_contratos, Decimal("5.00"))
        self.assertEqual(res.diff_receita, Decimal("25000.00"))
        self.assertIn("Não representa garantia", res.aviso)

    def test_oportunidades_proposta_sem_followup(self):
        Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="Prop",
            email="prop@t.com",
            fase_funil="proposta_enviada",
        )
        painel = listar_oportunidades(self.user_a, organization=self.org_a)
        self.assertTrue(any(i.key == "propostas_aguardando" for i in painel.itens))

    def test_recomendacao_e_resolver_isolamento(self):
        Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="Novo",
            email="novo@t.com",
            status="em_prospeccao",
        )
        # força criado_em recente
        Cliente.objects.filter(user=self.user_a, email="novo@t.com").update(
            criado_em=timezone.now() - timedelta(hours=2)
        )
        rec_a = listar_recomendacoes(self.user_a, organization=self.org_a)
        rec_b = listar_recomendacoes(self.user_b, organization=self.org_b)
        self.assertTrue(any("lead_24h" in i.chave for i in rec_a.itens))
        self.assertFalse(any("lead_24h" in i.chave for i in rec_b.itens))

        chave = next(i.chave for i in rec_a.itens if "lead_24h" in i.chave)
        cli = Cliente.objects.get(user=self.user_a, email="novo@t.com")
        ok = marcar_resolvida(
            self.user_a,
            ator=self.user_a,
            chave=chave,
            cliente_id=cli.pk,
            organization=self.org_a,
        )
        self.assertTrue(ok)
        self.assertEqual(
            AcaoComercialResolvida.objects.filter(usuario=self.user_a).count(), 1
        )
        self.assertEqual(
            AcaoComercialResolvida.objects.filter(usuario=self.user_b).count(), 0
        )
        rec_a2 = listar_recomendacoes(self.user_a, organization=self.org_a)
        self.assertFalse(any(i.chave == chave for i in rec_a2.itens))

    def test_criar_tarefa_tenant(self):
        cli = Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="T",
            email="t2@t.com",
        )
        # tentativa cross-tenant
        tarefa = criar_tarefa_da_recomendacao(
            self.user_b,
            ator=self.user_b,
            chave="x",
            cliente_id=cli.pk,
            titulo="Hack",
            motivo="não deve",
            organization=self.org_b,
        )
        self.assertIsNone(tarefa)
        tarefa_ok = criar_tarefa_da_recomendacao(
            self.user_a,
            ator=self.user_a,
            chave="ok",
            cliente_id=cli.pk,
            titulo="Follow-up",
            motivo="teste",
            organization=self.org_a,
        )
        self.assertIsNotNone(tarefa_ok)
        self.assertEqual(tarefa_ok.user_id, self.user_a.pk)
        self.assertEqual(Tarefa.objects.filter(user=self.user_a).count(), 1)

    def test_origem_para_receita(self):
        org_a = self.org_a
        lead = Cliente.objects.create(
            user=self.user_a,
            organization=org_a,
            nome="Ads",
            email="ads@t.com",
            origem=OrigemLead.GOOGLE_ADS,
        )
        Compromisso.objects.create(
            user=self.user_a,
            organization=org_a,
            cliente=lead,
            titulo="C",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt(self.hoje),
            area_juridica="Trabalhista",
        )
        Contrato.objects.create(
            usuario=self.user_a,
            organization=org_a,
            cliente=lead,
            referencia="O-1",
            descricao="C",
            valor_total=Decimal("7000"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = origem_para_receita(self.user_a, self.periodo, organization=org_a)
        self.assertTrue(res.disponivel)
        linha = next(l for l in res.linhas if l.origem == OrigemLead.GOOGLE_ADS)
        self.assertEqual(linha.leads, 1)
        self.assertEqual(linha.contratos, 1)
        self.assertEqual(linha.receita, Decimal("7000.00"))

    def test_dashboard_fase2_render(self):
        self.http.login(username="com2_a", password="senha123")
        visao = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(visao.status_code, 200)
        self.assertContains(visao, "Growth Advisor")
        op = self.http.get(reverse("comercial_dashboard"), {"aba": "oportunidades"})
        self.assertContains(op, "Oportunidades")
        sim = self.http.get(reverse("comercial_dashboard"), {"aba": "simulador"})
        self.assertContains(sim, "Simulador de crescimento")
        orig = self.http.get(reverse("comercial_dashboard"), {"aba": "origens"})
        self.assertContains(orig, "Origem")

    def test_acao_resolver_post(self):
        self.http.login(username="com2_a", password="senha123")
        resp = self.http.post(
            reverse("comercial_acao_resolver"),
            {"chave": "teste:1", "cliente_id": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            AcaoComercialResolvida.objects.filter(
                usuario=self.user_a, chave="teste:1"
            ).exists()
        )
