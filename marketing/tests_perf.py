"""Testes Fase 14 — orçamento de queries (performance)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from marketing.services.google_ads_resultados import calcular_funil, calcular_resultados_negocio
from marketing.services.periodo import PeriodoMarketing
from marketing.services.resultados_campanhas import calcular_ranking_campanhas
from usuarios.choices import OrigemLead
from usuarios.models import Cliente

User = get_user_model()


class MarketingPerfTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mkt_perf", password="senha123")
        self.periodo = PeriodoMarketing.ultimos_dias(30)

    def _lead(self, nome, email, campanha):
        return Cliente.objects.create(
            user=self.user,
            nome=nome,
            email=email,
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            utm_campaign=campanha,
            gclid="CjwK123",
        )

    def test_calcular_funil_query_budget(self):
        self._lead("L1", "l1@test.com", "alpha")
        Contrato.objects.create(
            usuario=self.user,
            cliente=Cliente.objects.get(email="l1@test.com"),
            referencia="P1",
            descricao="Contrato",
            valor_total=Decimal("5000.00"),
            status=StatusContrato.ACTIVE,
        )
        with CaptureQueriesContext(connection) as ctx:
            funil = calcular_funil(self.user, self.periodo, modo_demo=True)
        self.assertEqual(funil.leads, 1)
        self.assertLessEqual(len(ctx.captured_queries), 6)

    def test_ranking_nao_escala_com_numero_de_campanhas(self):
        for i in range(3):
            self._lead(f"A{i}", f"a{i}@test.com", f"camp_{i}")
        with CaptureQueriesContext(connection) as ctx_tres:
            calcular_ranking_campanhas(self.user, self.periodo, modo_demo=True)
        qtd_tres = len(ctx_tres.captured_queries)

        for i in range(3, 8):
            self._lead(f"B{i}", f"b{i}@test.com", f"camp_{i}")

        with CaptureQueriesContext(connection) as ctx_oito:
            rank = calcular_ranking_campanhas(self.user, self.periodo, modo_demo=True)
        qtd_oito = len(ctx_oito.captured_queries)

        self.assertEqual(len(rank.campanhas), 8)
        self.assertEqual(qtd_tres, qtd_oito)

    def test_resultados_negocio_query_budget(self):
        self._lead("R1", "r1@test.com", "x")
        with CaptureQueriesContext(connection) as ctx:
            calcular_resultados_negocio(self.user, self.periodo, modo_demo=True)
        self.assertLessEqual(len(ctx.captured_queries), 12)
