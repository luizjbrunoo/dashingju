"""Testes do Growth Advisor, projeção mensal e home enxuta."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.advisor import montar_advisor
from comercial.services.goals import salvar_meta
from comercial.services.projections import projetar_faturamento_mensal
from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente

User = get_user_model()


class ProjecaoMensalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="proj_a", password="senha123")
        self.org = Organization.objects.create(name="Proj Org A")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.hoje = timezone.localdate()

    def _meta(self, mensal: Decimal):
        return salvar_meta(
            self.user,
            ator=self.user,
            ano=self.hoje.year,
            meta_anual=mensal * 12,
            meta_mensal=mensal,
            ticket_medio=Decimal("7000"),
            ticket_medio_manual=True,
            vigencia_inicio=date(self.hoje.year, 1, 1),
            vigencia_fim=date(self.hoje.year, 12, 31),
            organization=self.org,
        )

    def _contrato(self, valor: Decimal):
        lead = Cliente.objects.create(
            user=self.user, nome="Cli", email="cli@t.com", organization=self.org
        )
        return Contrato.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=lead,
            referencia=f"P-{valor}",
            descricao="Contrato",
            valor_total=valor,
            status=StatusContrato.ACTIVE,
            criado_por=self.user,
        )

    def test_sem_contrato_projecao_indisponivel(self):
        self._meta(Decimal("100000"))
        proj = projetar_faturamento_mensal(self.org, Decimal("100000"))
        self.assertIsNone(proj.projecao)
        self.assertIsNone(proj.gap)
        self.assertIn("insuficientes", proj.mensagem.lower())

    def test_contrato_entra_no_realizado_sem_fabricar_projecao(self):
        self._meta(Decimal("100000"))
        self._contrato(Decimal("10000"))
        proj = projetar_faturamento_mensal(self.org, Decimal("100000"))
        self.assertEqual(proj.realizado, Decimal("10000.00"))
        self.assertIsNone(proj.projecao)
        self.assertIn("insuficientes", proj.mensagem.lower())


class GrowthAdvisorTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.http = Client()
        self.hoje = timezone.localdate()
        self.org_a = Organization.objects.create(name="Adv Org A")
        self.org_b = Organization.objects.create(name="Adv Org B")
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

    def test_propostas_viram_prioridade(self):
        Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="João",
            email="j@t.com",
            fase_funil="proposta_enviada",
        )
        painel = montar_advisor(self.user_a, organization=self.org_a)
        self.assertIsNotNone(painel.principal)
        self.assertEqual(painel.principal.key, "propostas_aguardando")
        self.assertEqual(painel.principal.quantidade, 1)
        self.assertGreaterEqual(painel.principal.score, 85)

    def test_tenant_nao_ve_prioridade_do_outro(self):
        Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="João",
            email="j@t.com",
            fase_funil="proposta_enviada",
        )
        painel_b = montar_advisor(self.user_b, organization=self.org_b)
        self.assertIsNone(painel_b.principal)

    def test_home_enxuta(self):
        self.http.login(username="adv_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Growth Advisor")
        self.assertContains(resp, "Funil comercial")
        self.assertContains(resp, "Ações de hoje")
        self.assertNotContains(resp, "Simulador de crescimento")
        self.assertNotContains(resp, "ADV Growth Score")
        self.assertNotContains(resp, "Cálculo reverso")

    def test_sem_meta_empty_state(self):
        self.http.login(username="adv_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertContains(resp, "Defina uma meta")

    def test_sem_perm_financeira_oculta_reais(self):
        self.user_a.user_permissions.clear()
        ct = ContentType.objects.get_for_model(MetaComercial)
        view = Permission.objects.get(content_type=ct, codename="view_dashboard")
        grupo = Group.objects.create(name="Comercial view sem receita")
        grupo.permissions.add(view)
        self.user_a.groups.add(grupo)
        salvar_meta(
            self.user_a,
            ator=self.user_a,
            ano=self.hoje.year,
            meta_anual=Decimal("120000"),
            meta_mensal=Decimal("10000"),
            ticket_medio=Decimal("5000"),
            ticket_medio_manual=True,
            vigencia_inicio=date(self.hoje.year, 1, 1),
            vigencia_fim=date(self.hoje.year, 12, 31),
            organization=self.org_a,
        )
        self.http.login(username="adv_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Growth Advisor")
        html = resp.content.decode()
        self.assertNotIn("R$ 10.000", html)
