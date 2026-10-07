"""FINANCEIRO-PRO-01 — inteligência determinística + isolamento."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from financeiro.choices import CategoriaCobranca, FormaPagamento, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas
from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization
from financeiro.services.cobranca_previsao import calcular_previsao
from financeiro.services.financeiro_pro import (
    CONCENTRACAO_LIMITE_PCT,
    HEALTH_ATENCAO,
    HEALTH_CRITICO,
    HEALTH_INSUFICIENTE,
    HEALTH_SAUDAVEL,
    TREND_INSUFICIENTE,
    TREND_MELHORANDO,
    TREND_PIORANDO,
    montar_financeiro_pro,
)
from financeiro.tests_helpers import grant_all_finance_permissions, grant_finance_permissions
from financeiro.views import dashboard
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE
from usuarios.models import Cliente

HOJE = date(2026, 6, 15)


class FinanceiroProBase(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="PRO Org A")
        self.org_b = Organization.objects.create(name="PRO Org B")
        self.a1 = User.objects.create_user("pro_a1", password="senha123")
        self.a2 = User.objects.create_user("pro_a2", password="senha123")
        self.b1 = User.objects.create_user("pro_b1", password="senha123")
        for u in (self.a1, self.a2, self.b1):
            grant_all_finance_permissions(u)
        for user, org, role in (
            (self.a1, self.org_a, Membership.Role.OWNER),
            (self.a2, self.org_a, Membership.Role.MEMBER),
            (self.b1, self.org_b, Membership.Role.OWNER),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=role,
                status=Membership.Status.ACTIVE,
            )
        self.ca = Cliente.objects.create(
            user=self.a1, organization=self.org_a, nome="PRO-CA", email="ca@pro.test"
        )
        self.ca2 = Cliente.objects.create(
            user=self.a2, organization=self.org_a, nome="PRO-CA2", email="ca2@pro.test"
        )
        self.cb = Cliente.objects.create(
            user=self.b1, organization=self.org_b, nome="PRO-CB", email="cb@pro.test"
        )

    def _cob(self, user, org, cliente, descricao, valor, vencimento, status=StatusCobranca.PENDING):
        return Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            descricao=descricao,
            valor_original=valor,
            data_vencimento=vencimento,
            categoria=CategoriaCobranca.HONORARIOS,
            status=status,
            criado_por=user,
            responsavel=user,
        )

    def _rec(self, cobranca, valor, dia, user=None):
        user = user or cobranca.usuario
        return CobrancaRecebimento.objects.create(
            cobranca=cobranca,
            usuario=user,
            organization=cobranca.organization,
            valor=valor,
            data_recebimento=dia,
            forma_pagamento=FormaPagamento.PIX,
            registrado_por=user,
        )

    def _ctr(self, user, org, cliente, ref, valor):
        return Contrato.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            referencia=ref,
            descricao=ref,
            valor_total=valor,
            status=StatusContrato.ACTIVE,
            criado_por=user,
            responsavel=user,
        )


class FinanceiroProCalculoTests(FinanceiroProBase):
    def test_recebido_nao_usa_contratado_nem_cobranca_criada(self):
        self._ctr(self.a1, self.org_a, self.ca, "CTR-A", Decimal("9000.00"))
        cob = self._cob(
            self.a1, self.org_a, self.ca, "COB-ABERTA", Decimal("4000.00"), HOJE + timedelta(days=10)
        )
        self._rec(cob, Decimal("500.00"), HOJE)
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        self.assertEqual(kpis.recebido_mes, Decimal("500.00"))
        self.assertNotEqual(kpis.recebido_mes, Decimal("9000.00"))
        self.assertNotEqual(kpis.recebido_mes, Decimal("4000.00"))

    def test_a_receber_exclui_cancelado_e_quitado(self):
        self._cob(
            self.a1,
            self.org_a,
            self.ca,
            "CANC",
            Decimal("1000.00"),
            HOJE + timedelta(days=5),
            status=StatusCobranca.CANCELED,
        )
        pago = self._cob(
            self.a1, self.org_a, self.ca, "PAGO", Decimal("800.00"), HOJE + timedelta(days=5)
        )
        self._rec(pago, Decimal("800.00"), HOJE)
        aberto = self._cob(
            self.a1, self.org_a, self.ca, "ABERTO", Decimal("300.00"), HOJE + timedelta(days=8)
        )
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        self.assertEqual(kpis.a_receber, Decimal("300.00"))
        self.assertGreater(aberto.valor_original, 0)

    def test_parcial_e_vencido_e_futuro(self):
        parcial = self._cob(
            self.a1, self.org_a, self.ca, "PARCIAL", Decimal("1000.00"), HOJE - timedelta(days=3)
        )
        self._rec(parcial, Decimal("400.00"), HOJE)
        self._cob(
            self.a1, self.org_a, self.ca, "FUTURA", Decimal("200.00"), HOJE + timedelta(days=10)
        )
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        prev = calcular_previsao(self.org_a, hoje=HOJE)
        self.assertEqual(kpis.vencido, Decimal("600.00"))
        self.assertEqual(kpis.a_receber, Decimal("800.00"))
        self.assertEqual(prev.cumulativo_30, Decimal("200.00"))

    def test_zero_real_vs_indisponivel(self):
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=HOJE)
        self.assertEqual(kpis.recebido_mes, Decimal("0"))
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertEqual(painel.health, HEALTH_INSUFICIENTE)
        vazio = montar_financeiro_pro(None, hoje=HOJE)
        self.assertFalse(vazio.disponivel)
        self.assertIsNone(vazio.prioridade)

    def test_health_saudavel_atencao_critico(self):
        self._cob(
            self.a1, self.org_a, self.ca, "OK", Decimal("100.00"), HOJE + timedelta(days=20)
        )
        self.assertEqual(montar_financeiro_pro(self.org_a, hoje=HOJE).health, HEALTH_SAUDAVEL)
        self._cob(
            self.a1,
            self.org_a,
            self.ca,
            "VENC-PEQ",
            Decimal("20.00"),
            HOJE - timedelta(days=2),
            status=StatusCobranca.OVERDUE,
        )
        self.assertEqual(montar_financeiro_pro(self.org_a, hoje=HOJE).health, HEALTH_ATENCAO)
        self._cob(
            self.a1,
            self.org_a,
            self.ca,
            "VENC-GRANDE",
            Decimal("500.00"),
            HOJE - timedelta(days=4),
            status=StatusCobranca.OVERDUE,
        )
        self.assertEqual(montar_financeiro_pro(self.org_a, hoje=HOJE).health, HEALTH_CRITICO)

    def test_health_atraso_90_dias_critico(self):
        self._cob(
            self.a1,
            self.org_a,
            self.ca,
            "VELHA",
            Decimal("10.00"),
            HOJE - timedelta(days=95),
            status=StatusCobranca.OVERDUE,
        )
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertEqual(painel.health, HEALTH_CRITICO)
        self.assertEqual(painel.dias_atraso_max, 95)

    def test_tendencia_melhorando_e_piorando(self):
        base = self._cob(
            self.a1, self.org_a, self.ca, "BASE-REC", Decimal("1000.00"), date(2026, 5, 5)
        )
        self._rec(base, Decimal("200.00"), date(2026, 5, 20))
        self._rec(base, Decimal("400.00"), HOJE)
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertEqual(painel.tendencia, TREND_MELHORANDO)
        self._rec(base, Decimal("10.00"), date(2026, 5, 22))
        # mês anterior 210, atual 400 → ainda melhorando; estorna lógica com novo mês anterior maior
        extra = self._cob(
            self.a1, self.org_a, self.ca2, "MAIO-ALTO", Decimal("800.00"), date(2026, 5, 8)
        )
        self._rec(extra, Decimal("800.00"), date(2026, 5, 18), user=self.a2)
        painel2 = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertEqual(painel2.tendencia, TREND_PIORANDO)

    def test_tendencia_insuficiente_sem_historico(self):
        self._cob(
            self.a1, self.org_a, self.ca, "SO-FUTURO", Decimal("80.00"), HOJE + timedelta(days=12)
        )
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertEqual(painel.tendencia, TREND_INSUFICIENTE)

    def test_prioridade_vencido_com_evidence_e_cta(self):
        self._cob(
            self.a1,
            self.org_a,
            self.ca,
            "VENC-A",
            Decimal("1200.00"),
            HOJE - timedelta(days=17),
            status=StatusCobranca.OVERDUE,
        )
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE)
        p = painel.prioridade
        self.assertIsNotNone(p)
        self.assertEqual(p.type, "cobrancas_vencidas")
        self.assertEqual(p.quantidade, 1)
        self.assertEqual(p.valor, Decimal("1200.00"))
        self.assertTrue(any("17 dias" in e for e in p.evidence))
        self.assertTrue(any("1200" in e for e in p.evidence))
        self.assertIn("status=overdue", p.cta_url)
        self.assertEqual(p.cta, "Ver cobranças")
        self.assertEqual(p.priority, 91)

    def test_attention_limitada_e_nba(self):
        for i in range(7):
            self._cob(
                self.a1,
                self.org_a,
                self.ca,
                f"VENC-{i}",
                Decimal("10.00") + i,
                HOJE - timedelta(days=i + 1),
                status=StatusCobranca.OVERDUE,
            )
        self._cob(
            self.a1, self.org_a, self.ca, "BREVE", Decimal("30.00"), HOJE + timedelta(days=2)
        )
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertLessEqual(len(painel.atencao), 5)
        self.assertTrue(all(item.recommended_action for item in painel.atencao))
        self.assertTrue(all("/financeiro/cobrancas/" in item.url for item in painel.atencao))

    def test_concentracao_threshold_documentado(self):
        self.assertEqual(CONCENTRACAO_LIMITE_PCT, Decimal("50"))
        c1 = self._cob(
            self.a1, self.org_a, self.ca, "REC-CA", Decimal("900.00"), HOJE - timedelta(days=1)
        )
        c2 = self._cob(
            self.a2, self.org_a, self.ca2, "REC-CA2", Decimal("100.00"), HOJE - timedelta(days=1)
        )
        self._rec(c1, Decimal("900.00"), HOJE)
        self._rec(c2, Decimal("100.00"), HOJE, user=self.a2)
        painel = montar_financeiro_pro(self.org_a, hoje=HOJE, ver_recebimentos=True)
        self.assertIsNotNone(painel.concentracao)
        self.assertEqual(painel.concentracao.percentual, Decimal("90.0"))
        sem_perm = montar_financeiro_pro(self.org_a, hoje=HOJE, ver_recebimentos=False)
        self.assertIsNone(sem_perm.concentracao)


class FinanceiroProTenantTests(FinanceiroProBase):
    def setUp(self):
        super().setUp()
        self._cob(
            self.a1,
            self.org_a,
            self.ca,
            "SEGREDO-A1",
            Decimal("2000.00"),
            HOJE - timedelta(days=5),
            status=StatusCobranca.OVERDUE,
        )
        self._cob(
            self.a2,
            self.org_a,
            self.ca2,
            "SEGREDO-A2",
            Decimal("500.00"),
            HOJE - timedelta(days=2),
            status=StatusCobranca.OVERDUE,
        )
        self._cob(
            self.b1,
            self.org_b,
            self.cb,
            "SEGREDO-B1",
            Decimal("9999.00"),
            HOJE - timedelta(days=3),
            status=StatusCobranca.OVERDUE,
        )
        cob_b = Cobranca.objects.get(descricao="SEGREDO-B1")
        self._rec(cob_b, Decimal("100.00"), HOJE)

    def test_same_org_a1_a2_agregam(self):
        p1 = montar_financeiro_pro(self.org_a, hoje=HOJE)
        p2 = montar_financeiro_pro(self.org_a, hoje=HOJE)
        self.assertEqual(p1.qtd_vencidas, 2)
        self.assertEqual(p2.qtd_vencidas, 2)
        self.assertEqual(p1.prioridade.valor, Decimal("2500.00"))
        desc = {i.descricao for i in p1.atencao}
        self.assertIn("SEGREDO-A1", desc)
        self.assertIn("SEGREDO-A2", desc)
        self.assertNotIn("SEGREDO-B1", desc)

    def test_cross_org_b_bloqueado(self):
        pa = montar_financeiro_pro(self.org_a, hoje=HOJE)
        pb = montar_financeiro_pro(self.org_b, hoje=HOJE)
        self.assertNotEqual(pa.prioridade.valor, pb.prioridade.valor)
        self.assertEqual(pb.prioridade.valor, Decimal("9899.00"))
        self.assertFalse(any(i.descricao == "SEGREDO-B1" for i in pa.atencao))
        dash_a = calcular_dashboard_cobrancas(self.org_a, hoje=HOJE)
        self.assertEqual(dash_a.vencido, Decimal("2500.00"))

    def test_invalid_context_fail_closed(self):
        painel = montar_financeiro_pro(None, hoje=HOJE)
        self.assertFalse(painel.disponivel)
        self.assertIsNone(painel.prioridade)
        self.assertEqual(painel.atencao, ())
        factory = RequestFactory()
        request = factory.get(reverse("financeiro_dashboard"))
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        resp = dashboard(request)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8", errors="replace")
        self.assertNotIn("SEGREDO-A1", body)
        self.assertNotIn("SEGREDO-B1", body)
        request.organization = self.org_a
        request.organization_context = CONTEXT_AMBIGUOUS
        resp2 = dashboard(request)
        body2 = resp2.content.decode("utf-8", errors="replace")
        self.assertNotIn("SEGREDO-A1", body2)

    def test_capability_sem_cobrancas_nao_vaza(self):
        painel = montar_financeiro_pro(
            self.org_a, hoje=HOJE, ver_cobrancas=False, ver_recebimentos=False
        )
        self.assertFalse(painel.disponivel)
        self.assertIsNone(painel.prioridade)
        self.assertEqual(painel.atencao, ())

    def test_capability_sem_recebimentos_oculta_valores_de_recebimento(self):
        painel = montar_financeiro_pro(
            self.org_a, hoje=HOJE, ver_cobrancas=True, ver_recebimentos=False
        )
        self.assertTrue(painel.disponivel)
        self.assertIsNone(painel.prioridade.valor)
        self.assertFalse(any(e.startswith("R$") for e in painel.prioridade.evidence))
        self.assertIsNone(painel.concentracao)

    def test_owner_sem_perm_nao_acessa_dashboard(self):
        owner = User.objects.create_user("pro_owner_noperm", password="senha123")
        Membership.objects.create(
            user=owner,
            organization=self.org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        self.client.force_login(owner)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_dashboard_http_same_org_e_nao_vaza_b(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Previsão 30 dias")
        self.assertContains(resp, "Growth Advisor Financeiro")
        self.assertContains(resp, "SEGREDO-A1")
        self.assertContains(resp, "SEGREDO-A2")
        self.assertNotContains(resp, "SEGREDO-B1")
        self.assertContains(resp, "Cobranças vencidas")
        self.assertContains(resp, "Por quê")
        self.assertContains(resp, "Próxima ação")
        self.assertContains(resp, "Exigem atenção")
        self.assertContains(resp, "Próximos vencimentos")
        ctx = resp.context["fin_pro"]
        self.assertEqual(ctx.prioridade.type, "cobrancas_vencidas")
        self.assertIn(ctx.health, ("ATENCAO", "CRITICO"))
        self.assertContains(resp, ctx.health_label)

    def test_dashboard_empty_states_nao_parecem_saudavel(self):
        self.client.force_login(self.a1)
        Cobranca.objects.filter(organization=self.org_a).delete()
        CobrancaRecebimento.objects.filter(organization=self.org_a).delete()
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dados insuficientes")
        self.assertNotContains(resp, "Saudável")
        self.assertContains(resp, "Nenhuma cobrança exige atenção agora.")
        self.assertContains(resp, "Nenhum vencimento nos próximos 90 dias.")
        self.assertEqual(resp.context["fin_pro"].health, "DADOS_INSUFICIENTES")

    def test_caixa_sem_view_cobrancas_nao_traz_advisor(self):
        so_caixa = User.objects.create_user("pro_caixa", password="senha123")
        Membership.objects.create(
            user=so_caixa,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        grant_finance_permissions(so_caixa, "view_caixa")
        self.client.force_login(so_caixa)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["cobrancas"])
        self.assertIsNone(resp.context["fin_pro"])
        self.assertNotContains(resp, "SEGREDO-A1")
        self.assertNotContains(resp, "9999")
