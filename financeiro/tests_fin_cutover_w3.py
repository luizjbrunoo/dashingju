"""FIN-CUTOVER-W3 — Billing analytics Organization-scoped, fail-closed, valores reais."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from financeiro.choices import CategoriaCobranca, FormaPagamento, StatusCobranca
from financeiro.models import Cobranca, CobrancaRecebimento
from financeiro.services.cobranca_auditoria import resumo_auditoria_organization
from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas
from financeiro.services.cobranca_inadimplencia import calcular_inadimplencia
from financeiro.services.cobranca_listagem import (
    calcular_kpis_cobrancas,
    calcular_kpis_cobrancas_organization,
    queryset_anotado_organization,
)
from financeiro.services.cobranca_previsao import calcular_previsao
from financeiro.tests_fin_isolation import FinIsolationAdversarialBase
from financeiro.tests_helpers import grant_all_finance_permissions, grant_finance_permissions
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE
from usuarios.models import Cliente

HOJE = date(2026, 6, 15)


class FinCutoverW3ValueDatasetTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="W3 Org A")
        self.org_b = Organization.objects.create(name="W3 Org B")
        self.a1 = User.objects.create_user("w3_a1", password="senha123")
        self.a2 = User.objects.create_user("w3_a2", password="senha123")
        self.b1 = User.objects.create_user("w3_b1", password="senha123")
        for u in (self.a1, self.a2, self.b1):
            grant_all_finance_permissions(u)
        for user, org in ((self.a1, self.org_a), (self.a2, self.org_a), (self.b1, self.org_b)):
            Membership.objects.create(
                user=user,
                organization=org,
                role=Membership.Role.MEMBER,
                status=Membership.Status.ACTIVE,
            )
        self.cli_a1 = Cliente.objects.create(
            user=self.a1, organization=self.org_a, nome="W3-CLI-A1", email="a1@w3.test"
        )
        self.cli_a2 = Cliente.objects.create(
            user=self.a2, organization=self.org_a, nome="W3-CLI-A2", email="a2@w3.test"
        )
        self.cli_b1 = Cliente.objects.create(
            user=self.b1, organization=self.org_b, nome="W3-CLI-B1", email="b1@w3.test"
        )
        self._cob(self.a1, self.org_a, self.cli_a1, "W3-A1-PAID", Decimal("1000.00"), HOJE - timedelta(days=20), rec=Decimal("1000.00"))
        self._cob(self.a1, self.org_a, self.cli_a1, "W3-A1-OPEN", Decimal("2000.00"), HOJE + timedelta(days=10))
        self._cob(self.a1, self.org_a, self.cli_a1, "W3-A1-OVERDUE", Decimal("3000.00"), HOJE - timedelta(days=10))
        self._cob(self.a2, self.org_a, self.cli_a2, "W3-A2-PAID", Decimal("4000.00"), HOJE - timedelta(days=8), rec=Decimal("4000.00"))
        self._cob(self.a2, self.org_a, self.cli_a2, "W3-A2-OPEN-M1", Decimal("5000.00"), HOJE + timedelta(days=20))
        self._cob(self.a2, self.org_a, self.cli_a2, "W3-A2-OVERDUE", Decimal("6000.00"), HOJE - timedelta(days=40))
        self._cob(self.a2, self.org_a, self.cli_a2, "W3-A2-OPEN-M2", Decimal("1500.00"), HOJE + timedelta(days=45))
        self._cob(self.b1, self.org_b, self.cli_b1, "W3-B1-PAID", Decimal("70000.00"), HOJE - timedelta(days=5), rec=Decimal("70000.00"))
        self._cob(self.b1, self.org_b, self.cli_b1, "W3-B1-OPEN", Decimal("80000.00"), HOJE + timedelta(days=15))
        self._cob(self.b1, self.org_b, self.cli_b1, "W3-B1-OVERDUE", Decimal("90000.00"), HOJE - timedelta(days=12))
        Cobranca.objects.create(
            usuario=self.a1,
            organization=None,
            cliente=self.cli_a1,
            descricao="W3-NULL-COB",
            valor_original=Decimal("99999.00"),
            data_vencimento=HOJE - timedelta(days=3),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
        )
        cob_dup = self._cob(
            self.a1, self.org_a, self.cli_a1, "W3-A1-MULTI-REC", Decimal("800.00"), HOJE + timedelta(days=5)
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_dup, usuario=self.a1, organization=self.org_a,
            valor=Decimal("100.00"), data_recebimento=HOJE,
            forma_pagamento=FormaPagamento.PIX, registrado_por=self.a1,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_dup, usuario=self.a1, organization=self.org_a,
            valor=Decimal("200.00"), data_recebimento=HOJE,
            forma_pagamento=FormaPagamento.PIX, registrado_por=self.a2,
        )

    def _cob(self, user, org, cliente, desc, valor, venc, rec=None):
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            descricao=desc,
            valor_original=valor,
            data_vencimento=venc,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PENDING,
            criado_por=user,
            responsavel=user,
        )
        if rec is not None:
            CobrancaRecebimento.objects.create(
                cobranca=cob,
                usuario=user,
                organization=org,
                valor=rec,
                data_recebimento=HOJE,
                forma_pagamento=FormaPagamento.PIX,
                registrado_por=user,
            )
        return cob

    def test_kpis_org_a_somam_a1_a2_excluem_b1_e_null(self):
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        kpis_peer = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        self.assertEqual(kpis.recebido_mes, Decimal("5300.00"))
        self.assertEqual(kpis.a_receber, Decimal("18000.00"))
        self.assertEqual(kpis.vencido, Decimal("9000.00"))
        self.assertEqual(kpis.vence_mes, Decimal("5500.00"))
        self.assertEqual(kpis.taxa_recebimento, Decimal("37.1"))
        self.assertEqual(kpis.recebido_mes, kpis_peer.recebido_mes)
        self.assertEqual(kpis.a_receber, kpis_peer.a_receber)
        desc = set(queryset_anotado_organization(self.org_a).values_list("descricao", flat=True))
        self.assertNotIn("W3-NULL-COB", desc)
        self.assertNotIn("W3-B1-OPEN", desc)

    def test_legacy_kpis_usuario_ainda_w5(self):
        kpis = calcular_kpis_cobrancas(self.a1, hoje=HOJE)
        self.assertEqual(kpis.recebido_mes, Decimal("1300.00"))

    def test_dashboard_cards_org_a(self):
        dash_a1 = calcular_dashboard_cobrancas(self.org_a, hoje=HOJE)
        dash_a2 = calcular_dashboard_cobrancas(self.org_a, hoje=HOJE)
        self.assertEqual(dash_a1.a_receber, Decimal("18000.00"))
        self.assertEqual(dash_a1.recebido_mes, Decimal("5300.00"))
        self.assertEqual(dash_a1.vencido, Decimal("9000.00"))
        self.assertEqual(dash_a1.taxa_inadimplencia, Decimal("50.0"))
        self.assertEqual(dash_a1.previsao_30, Decimal("7500.00"))
        self.assertEqual(dash_a1.ticket_medio, Decimal("3000.00"))
        self.assertEqual(dash_a1.a_receber, dash_a2.a_receber)
        nomes = {c.cliente_nome for c in dash_a1.clientes_inadimplentes}
        self.assertEqual(nomes, {"W3-CLI-A1", "W3-CLI-A2"})
        desc = {c.descricao for c in dash_a1.proximos_vencimentos}
        self.assertIn("W3-A1-OPEN", desc)
        self.assertNotIn("W3-B1-OPEN", desc)

    def test_inadimplencia_faixas(self):
        resumo = calcular_inadimplencia(self.org_a, hoje=HOJE)
        self.assertEqual(resumo.total_vencido, Decimal("9000.00"))
        self.assertEqual(resumo.quantidade, 2)
        self.assertEqual(resumo.faixas[0].total, Decimal("3000.00"))
        self.assertEqual(resumo.faixas[1].total, Decimal("6000.00"))
        self.assertEqual(sum((f.total for f in resumo.faixas), Decimal("0")), resumo.total_vencido)
        self.assertEqual({c.cliente_nome for c in resumo.clientes}, {"W3-CLI-A1", "W3-CLI-A2"})
        self.assertNotIn("W3-B1-OVERDUE", {c.descricao for c in resumo.cobrancas})

    def test_previsao_por_janela(self):
        resumo = calcular_previsao(self.org_a, hoje=HOJE)
        self.assertEqual(resumo.janelas[0].total, Decimal("7500.00"))
        self.assertEqual(resumo.janelas[1].total, Decimal("1500.00"))
        self.assertEqual(resumo.janelas[2].total, Decimal("0"))
        self.assertEqual(resumo.cumulativo_30, Decimal("7500.00"))
        self.assertEqual(resumo.cumulativo_60, Decimal("9000.00"))
        self.assertEqual(resumo.cumulativo_90, Decimal("9000.00"))
        self.assertNotIn("W3-B1-OPEN", {c.descricao for c in resumo.cobrancas})

    def test_double_count_recebimentos(self):
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        self.assertEqual(kpis.recebido_mes, Decimal("5300.00"))
        cob = Cobranca.objects.get(descricao="W3-A1-MULTI-REC")
        self.assertEqual(cob.valor_original - Decimal("300.00"), Decimal("500.00"))

    def test_fail_closed_sem_organization(self):
        dash = calcular_dashboard_cobrancas(None, hoje=HOJE)
        self.assertEqual(dash.recebido_mes, Decimal("0"))
        self.assertEqual(calcular_kpis_cobrancas_organization(None, hoje=HOJE).a_receber, Decimal("0"))
        self.assertEqual(calcular_inadimplencia(None, hoje=HOJE).total_vencido, Decimal("0"))
        self.assertEqual(calcular_previsao(None, hoje=HOJE).total_previsto, Decimal("0"))
        self.assertEqual(resumo_auditoria_organization(None)["total_registrado"], 0)


class FinCutoverW3HttpIsolationTests(FinIsolationAdversarialBase):
    def test_a1_a2_mesmos_numeros_http(self):
        r1 = self._get("financeiro_cobranca_listar", self.a1)
        r2 = self._get("financeiro_cobranca_listar", self.a2)
        self.assertEqual(r1.context["kpis"].recebido_mes, r2.context["kpis"].recebido_mes)
        self.assertEqual(r1.context["kpis"].a_receber, r2.context["kpis"].a_receber)
        d1 = self._get("financeiro_dashboard", self.a1)
        d2 = self._get("financeiro_dashboard", self.a2)
        self.assertEqual(d1.context["cobrancas"].recebido_mes, d2.context["cobrancas"].recebido_mes)
        i1 = self._get("financeiro_cobranca_inadimplencia", self.a1)
        i2 = self._get("financeiro_cobranca_inadimplencia", self.a2)
        self.assertEqual(i1.context["resumo"].total_vencido, i2.context["resumo"].total_vencido)

    def test_listagem_filtro_cliente_b1_nao_atravessa_tenant(self):
        url = reverse("financeiro_cobranca_listar")
        self._login(self.a1)
        resp = self.client.get(url, {"cliente": self.pack_b1.cliente.pk})
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertNotIn("ISO-COB-B1", body)
        self.assertNotIn("ISO-CLI-B1", body)
        self.assertEqual(resp.context["kpis"].recebido_mes, Decimal("300.00"))
        self.assertEqual(len(resp.context["cobrancas"]), 0)

    def test_responsavel_filtra_dentro_da_org_nao_troca_tenant(self):
        url = reverse("financeiro_cobranca_listar")
        self._login(self.a1)
        resp = self.client.get(url, {"responsavel": self.a2.pk})
        self.assertEqual(resp.status_code, 200)
        desc = {c.descricao for c in resp.context["cobrancas"]}
        self.assertIn("ISO-COB-A2", desc)
        self.assertNotIn("ISO-COB-A1", desc)
        self.assertNotIn("ISO-COB-B1", desc)
        self.assertEqual(resp.context["kpis"].recebido_mes, Decimal("300.00"))

    def test_capability_relatorios_deny(self):
        a3 = User.objects.create_user("w3_a3", password="senha123")
        self._membership(a3, self.org_a)
        grant_finance_permissions(a3, "view_cobrancas")
        self._login(a3)
        self.assertNotEqual(
            self.client.get(reverse("financeiro_cobranca_inadimplencia")).status_code,
            200,
        )

    def test_analytics_tenantcontext_fail_closed(self):
        from financeiro.views_cobrancas import cobranca_inadimplencia, cobranca_listar, cobranca_previsao

        request = RequestFactory().get(reverse("financeiro_cobranca_listar"))
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-COB-A1", resp.content.decode("utf-8", errors="replace"))

        req = RequestFactory().get(reverse("financeiro_cobranca_inadimplencia"))
        req.user = self.a1
        req.organization = self.org_a
        req.organization_context = CONTEXT_AMBIGUOUS
        out = cobranca_inadimplencia(req)
        self.assertEqual(out.status_code, 200)
        self.assertNotIn("ISO-CLI-A1", out.content.decode("utf-8", errors="replace"))

        req2 = RequestFactory().get(reverse("financeiro_cobranca_previsao"))
        req2.user = self.a1
        out2 = cobranca_previsao(req2)
        self.assertEqual(out2.status_code, 200)
        self.assertNotIn("ISO-COB-A1", out2.content.decode("utf-8", errors="replace"))

        req3 = RequestFactory().get(reverse("financeiro_cobranca_listar"))
        req3.user = self.a1
        req3.organization = self.org_a
        req3.organization_context = "inconsistent"
        out3 = cobranca_listar(req3)
        self.assertEqual(out3.status_code, 200)
        body3 = out3.content.decode("utf-8", errors="replace")
        self.assertNotIn("ISO-COB-A1", body3)
        self.assertNotIn("ISO-COB-A2", body3)
