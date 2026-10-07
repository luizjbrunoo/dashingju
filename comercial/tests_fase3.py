"""Testes Fase 3 — score, ROI, insights, carteira, isolamento."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import AdvGrowthScoreSnapshot, InvestimentoMidia
from comercial.services.periodo import PeriodoComercial
from comercial.services.portfolio import oportunidades_na_base
from comercial.services.roi import calcular_metricas_midia
from comercial.services.score import calcular_adv_growth_score
from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead
from usuarios.models import Cliente

User = get_user_model()


class ComercialFase3Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="com3_a", password="senha123")
        self.user_b = User.objects.create_user(username="com3_b", password="senha123")
        self.org_a = Organization.objects.create(name="Com3 Org A")
        self.org_b = Organization.objects.create(name="Com3 Org B")
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

    def test_score_persistido_e_isolado(self):
        score_a = calcular_adv_growth_score(self.user_a, persistir=True)
        self.assertTrue(0 <= score_a.score <= 100)
        self.assertEqual(len(score_a.dimensoes), 6)
        self.assertEqual(
            AdvGrowthScoreSnapshot.objects.filter(usuario=self.user_a).count(), 1
        )
        self.assertEqual(
            AdvGrowthScoreSnapshot.objects.filter(usuario=self.user_b).count(), 0
        )
        score_b = calcular_adv_growth_score(self.user_b, persistir=True)
        self.assertEqual(
            AdvGrowthScoreSnapshot.objects.filter(usuario=self.user_b).count(), 1
        )
        # regrava mesmo dia
        calcular_adv_growth_score(self.user_a, persistir=True)
        self.assertEqual(
            AdvGrowthScoreSnapshot.objects.filter(usuario=self.user_a).count(), 1
        )

    def test_roi_indisponivel_sem_investimento(self):
        midia = calcular_metricas_midia(self.user_a, self.periodo)
        self.assertFalse(midia.disponivel)
        self.assertIn("ROI indisponível", midia.mensagem)

    def test_roi_com_investimento_real(self):
        org_a = self.org_a
        lead = Cliente.objects.create(
            user=self.user_a,
            organization=org_a,
            nome="Ads",
            email="ads3@t.com",
            origem=OrigemLead.GOOGLE_ADS,
        )
        Contrato.objects.create(
            usuario=self.user_a,
            organization=org_a,
            cliente=lead,
            referencia="ROI-1",
            descricao="C",
            valor_total=Decimal("10000"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        InvestimentoMidia.objects.create(
            usuario=self.user_a,
            organization=org_a,
            data_inicio=self.hoje - timedelta(days=20),
            data_fim=self.hoje,
            valor=Decimal("2000"),
            canal="google_ads",
            criado_por=self.user_a,
        )
        # investimento do outro tenant não conta
        InvestimentoMidia.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            data_inicio=self.hoje - timedelta(days=20),
            data_fim=self.hoje,
            valor=Decimal("99999"),
            criado_por=self.user_b,
        )
        midia = calcular_metricas_midia(self.user_a, self.periodo, organization=org_a)
        self.assertTrue(midia.disponivel)
        self.assertEqual(midia.investimento, Decimal("2000.00"))
        self.assertEqual(midia.receita_atribuida, Decimal("10000.00"))
        self.assertEqual(midia.roi_pct, Decimal("400.00"))
        self.assertEqual(midia.cac, Decimal("2000.00"))

    def test_carteira_sem_inventar(self):
        painel = oportunidades_na_base(self.user_a, organization=self.org_a)
        self.assertIsInstance(painel.itens, tuple)

    def test_dashboard_fase3_render(self):
        self.http.login(username="com3_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"), {"aba": "score"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ADV Growth Score")
        self.assertContains(resp, "Insights comerciais")
        self.assertContains(resp, "ROI")
        op = self.http.get(reverse("comercial_dashboard"), {"aba": "oportunidades"})
        self.assertContains(op, "Oportunidades na base")

    def test_investimento_form_tenant(self):
        self.http.login(username="com3_a", password="senha123")
        resp = self.http.post(
            reverse("comercial_investimento"),
            {
                "data_inicio": self.hoje.replace(day=1).isoformat(),
                "data_fim": self.hoje.isoformat(),
                "valor": "1500.00",
                "canal": "google_ads",
                "observacoes": "teste",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            InvestimentoMidia.objects.filter(usuario=self.user_a).count(), 1
        )
        self.assertEqual(
            InvestimentoMidia.objects.filter(usuario=self.user_b).count(), 0
        )
