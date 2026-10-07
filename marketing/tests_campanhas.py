"""Testes Fase 12 — ranking por campanha (utm_campaign confiável)."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from marketing.services.periodo import PeriodoMarketing
from marketing.services.resultados_campanhas import (
    ORDEN_CONTRATOS,
    ORDEN_LEADS,
    calcular_ranking_campanhas,
)
from marketing.tests_helpers import grant_marketing_permissions
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead
from usuarios.models import Cliente

User = get_user_model()


class ResultadosCampanhasFase12Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mkt_f12", password="senha123")
        self.org = Organization.objects.create(name="Mkt F12 Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        grant_marketing_permissions(self.user)
        self.http = Client()
        self.http.login(username="mkt_f12", password="senha123")
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(30, referencia=self.hoje)

    def _lead(self, nome, email, campanha, *, confiavel=True):
        return Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome=nome,
            email=email,
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=confiavel,
            utm_campaign=campanha,
            gclid="CjwK123" if confiavel else "",
        )

    def test_sem_utm_nao_exibe_ranking(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Sem camp",
            email="sem@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            gclid="CjwK",
        )
        rank = calcular_ranking_campanhas(
            self.user, self.periodo, organization=self.org, modo_demo=True
        )
        self.assertFalse(rank.exibir)

    def test_manual_sem_prova_nao_entra(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Manual",
            email="man@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=False,
            utm_campaign="fake",
        )
        rank = calcular_ranking_campanhas(
            self.user, self.periodo, organization=self.org, modo_demo=True
        )
        self.assertFalse(rank.exibir)

    def test_duas_campanhas_metricas(self):
        self._lead("A", "a@test.com", "trabalhista")
        lead_b = self._lead("B", "b@test.com", "previdenciario")
        Contrato.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=lead_b,
            referencia="C1",
            descricao="Contrato B",
            valor_total=Decimal("8000.00"),
            status=StatusContrato.ACTIVE,
        )
        rank = calcular_ranking_campanhas(
            self.user,
            self.periodo,
            organization=self.org,
            ordenacao=ORDEN_CONTRATOS,
            modo_demo=True,
        )
        self.assertTrue(rank.exibir)
        self.assertEqual(len(rank.campanhas), 2)
        self.assertEqual(rank.campanhas[0].utm_campaign, "previdenciario")
        self.assertEqual(rank.campanhas[0].contratos, 1)
        self.assertEqual(rank.campanhas[0].leads, 1)

    def test_ordenacao_por_leads(self):
        self._lead("A1", "a1@test.com", "alpha")
        self._lead("A2", "a2@test.com", "alpha")
        self._lead("B1", "b1@test.com", "beta")
        rank = calcular_ranking_campanhas(
            self.user,
            self.periodo,
            organization=self.org,
            ordenacao=ORDEN_LEADS,
            modo_demo=True,
        )
        self.assertEqual(rank.campanhas[0].utm_campaign, "alpha")
        self.assertEqual(rank.campanhas[0].leads, 2)

    def test_dashboard_exibe_tabela_campanhas(self):
        self._lead("Dash", "dash@test.com", "google_search")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "Campanhas (utm_campaign)")
        self.assertContains(resp, "google_search")

    def test_dashboard_sem_campanha_nao_exibe_tabela(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="X",
            email="x@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            gclid="G1",
        )
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertNotContains(resp, "Campanhas (utm_campaign)")

    def test_tenant_isolado(self):
        outro = User.objects.create_user(username="outro_f12", password="senha123")
        self._lead("Meu", "meu@test.com", "minha")
        Cliente.objects.create(
            user=outro,
            nome="Outro",
            email="outro@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            utm_campaign="outra",
            gclid="G2",
        )
        rank = calcular_ranking_campanhas(
            self.user, self.periodo, organization=self.org, modo_demo=True
        )
        self.assertEqual(len(rank.campanhas), 1)
        self.assertEqual(rank.campanhas[0].utm_campaign, "minha")
