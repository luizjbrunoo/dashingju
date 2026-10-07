"""FIN-CONSUMERS-W5b — Comercial + Growth Advisor Organization-scoped."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.advisor import montar_advisor
from comercial.services.dashboard import contexto_dashboard
from comercial.services.metrics import calcular_kpis, periodo_mes, receita_no_periodo
from comercial.services.opportunities import listar_oportunidades
from comercial.services.revenue_risk import calcular_receita_em_risco
from comercial.services.score import calcular_adv_growth_score
from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization
from financeiro.tenancy_write import organization_for_finance_write
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente


class FinConsumersW5bComercialAdvisorTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="W5b Org A")
        self.org_b = Organization.objects.create(name="W5b Org B")
        self.a1 = User.objects.create_user("w5b_a1", password="senha123")
        self.a2 = User.objects.create_user("w5b_a2", password="senha123")
        self.b1 = User.objects.create_user("w5b_b1", password="senha123")
        self.owner_a = User.objects.create_user("w5b_owner_a", password="senha123")
        self.member_sem_cap = User.objects.create_user("w5b_a3", password="senha123")
        hoje = timezone.localdate()
        self.hoje = hoje

        for user, org, role in (
            (self.a1, self.org_a, Membership.Role.MEMBER),
            (self.a2, self.org_a, Membership.Role.MEMBER),
            (self.owner_a, self.org_a, Membership.Role.OWNER),
            (self.member_sem_cap, self.org_a, Membership.Role.MEMBER),
            (self.b1, self.org_b, Membership.Role.MEMBER),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=role,
                status=Membership.Status.ACTIVE,
            )

        ct = ContentType.objects.get_for_model(MetaComercial)
        view = Permission.objects.get(content_type=ct, codename="view_dashboard")
        grupo_sem_receita = Group.objects.create(name="W5b Comercial sem receita")
        grupo_sem_receita.permissions.add(view)
        self.owner_a.groups.add(grupo_sem_receita)
        self.member_sem_cap.groups.add(grupo_sem_receita)
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.a1)
        grant_comercial_permissions(self.a2)
        grant_comercial_permissions(self.b1)

        self.cli_a1 = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="W5B-CLI-A1",
            email="a1@w5b.test",
        )
        self.cli_a2 = Cliente.objects.create(
            user=self.a2,
            organization=self.org_a,
            nome="W5B-CLI-A2",
            email="a2@w5b.test",
        )
        self.cli_b1 = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="W5B-CLI-B1",
            email="b1@w5b.test",
        )

        self.ctr_a1 = self._contrato(self.a1, self.org_a, self.cli_a1, "W5B-CTR-A1", Decimal("2000.00"))
        self.ctr_a2 = self._contrato(self.a2, self.org_a, self.cli_a2, "W5B-CTR-A2", Decimal("8000.00"))
        self.ctr_b1 = self._contrato(self.b1, self.org_b, self.cli_b1, "W5B-CTR-B1", Decimal("100000.00"))
        self._contrato(self.a1, None, self.cli_a1, "W5B-CTR-NULL", Decimal("99999.00"))

        self._recebimento(self.a1, self.org_a, self.cli_a1, Decimal("1000.00"), "W5B-REC-A1")
        self._recebimento(self.a2, self.org_a, self.cli_a2, Decimal("4000.00"), "W5B-REC-A2")
        self._recebimento(self.b1, self.org_b, self.cli_b1, Decimal("90000.00"), "W5B-REC-B1")
        self._recebimento(self.a1, None, self.cli_a1, Decimal("77777.00"), "W5B-REC-NULL")

        self.cob_venc_a1 = self._cobranca_vencida(
            self.a1, self.org_a, self.cli_a1, Decimal("500.00"), "W5B-COB-VENC-A1"
        )
        self.cob_venc_a2 = self._cobranca_vencida(
            self.a2, self.org_a, self.cli_a2, Decimal("1500.00"), "W5B-COB-VENC-A2"
        )
        self.cob_venc_b1 = self._cobranca_vencida(
            self.b1, self.org_b, self.cli_b1, Decimal("50000.00"), "W5B-COB-VENC-B1"
        )
        self._cobranca_vencida(
            self.a1, None, self.cli_a1, Decimal("88888.00"), "W5B-COB-VENC-NULL"
        )

    def _contrato(self, user, org, cliente, referencia, valor):
        return Contrato.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            referencia=referencia,
            descricao=referencia,
            valor_total=valor,
            status=StatusContrato.ACTIVE,
            criado_por=user,
            responsavel=user,
        )

    def _recebimento(self, user, org, cliente, valor, tag):
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            descricao=tag,
            valor_original=valor,
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=user,
            responsavel=user,
        )
        return CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=user,
            organization=org,
            valor=valor,
            data_recebimento=self.hoje,
            registrado_por=user,
        )

    def _cobranca_vencida(self, user, org, cliente, valor, descricao):
        return Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            descricao=descricao,
            valor_original=valor,
            data_vencimento=self.hoje - timedelta(days=5),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=user,
            responsavel=user,
        )

    def _tenant_request(self, user, *, organization=None, context=None, omit_tenant=False):
        request = RequestFactory().get(reverse("comercial_dashboard"))
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        return request

    def test_same_org_aggregation(self):
        expected_receita = Decimal("5000.00")
        expected_contratos = Decimal("10000.00")
        rec_a1 = receita_no_periodo(self.org_a, periodo_mes())
        rec_a2 = receita_no_periodo(self.org_a, periodo_mes())
        self.assertEqual(rec_a1.recebido, expected_receita)
        self.assertEqual(rec_a2.recebido, expected_receita)
        self.assertEqual(rec_a1.contratado, expected_contratos)
        self.assertEqual(rec_a2.contratado, expected_contratos)
        self.assertEqual(rec_a1.contratos, 2)
        self.assertEqual(rec_a2.contratos, 2)

        kpis_a1 = calcular_kpis_cobrancas_organization(self.org_a)
        kpis_a2 = calcular_kpis_cobrancas_organization(self.org_a)
        self.assertEqual(kpis_a1.vencido, Decimal("2000.00"))
        self.assertEqual(kpis_a2.vencido, Decimal("2000.00"))
        self.assertEqual(kpis_a1.recebido_mes, expected_receita)
        self.assertEqual(kpis_a2.recebido_mes, expected_receita)

        risco_a1 = calcular_receita_em_risco(self.a1, organization=self.org_a)
        risco_a2 = calcular_receita_em_risco(self.a2, organization=self.org_a)
        self.assertEqual(risco_a1.total_confirmado_risco, Decimal("2000.00"))
        self.assertEqual(risco_a2.total_confirmado_risco, Decimal("2000.00"))

        op_a1 = listar_oportunidades(self.a1, organization=self.org_a)
        op_a2 = listar_oportunidades(self.a2, organization=self.org_a)
        venc_a1 = next(i for i in op_a1.itens if i.key == "cobrancas_vencidas")
        venc_a2 = next(i for i in op_a2.itens if i.key == "cobrancas_vencidas")
        self.assertEqual(venc_a1.quantidade, 2)
        self.assertEqual(venc_a2.quantidade, 2)

    def test_cross_org_b1_excluido(self):
        rec_a = receita_no_periodo(self.org_a, periodo_mes())
        rec_b = receita_no_periodo(self.org_b, periodo_mes())
        self.assertEqual(rec_a.recebido, Decimal("5000.00"))
        self.assertEqual(rec_a.contratado, Decimal("10000.00"))
        self.assertEqual(rec_b.recebido, Decimal("90000.00"))
        self.assertEqual(rec_b.contratado, Decimal("100000.00"))
        self.assertNotEqual(rec_a.recebido, rec_b.recebido)

        kpis_a = calcular_kpis_cobrancas_organization(self.org_a)
        self.assertEqual(kpis_a.vencido, Decimal("2000.00"))
        self.assertNotEqual(kpis_a.vencido, Decimal("50000.00"))
        self.assertNotEqual(kpis_a.recebido_mes, Decimal("90000.00"))

        risco_a = calcular_receita_em_risco(self.a1, organization=self.org_a)
        self.assertEqual(risco_a.total_confirmado_risco, Decimal("2000.00"))
        self.assertNotEqual(risco_a.total_confirmado_risco, Decimal("50000.00"))

        op_a = listar_oportunidades(self.a1, organization=self.org_a)
        venc = next(i for i in op_a.itens if i.key == "cobrancas_vencidas")
        self.assertEqual(venc.quantidade, 2)

    def test_null_organization_nao_entra(self):
        rec_a = receita_no_periodo(self.org_a, periodo_mes())
        self.assertEqual(rec_a.recebido, Decimal("5000.00"))
        self.assertEqual(rec_a.contratado, Decimal("10000.00"))
        vazio = receita_no_periodo(None, periodo_mes())
        self.assertEqual(vazio.recebido, Decimal("0.00"))
        self.assertEqual(vazio.contratado, Decimal("0.00"))
        kpis_none = calcular_kpis_cobrancas_organization(None)
        self.assertEqual(kpis_none.vencido, Decimal("0"))
        self.assertEqual(kpis_none.recebido_mes, Decimal("0"))
        risco_none = calcular_receita_em_risco(self.a1, organization=None)
        self.assertEqual(risco_none.total_confirmado_risco, Decimal("0.00"))

    def test_capability_membership_e_owner_nao_concedem_receita(self):
        ctx_member = contexto_dashboard(
            self.member_sem_cap,
            organization=self.org_a,
            get_params={"aba": "visao"},
        )
        self.assertTrue(ctx_member.ocultar_financeiro)
        self.assertEqual(ctx_member.kpis.realizado_mes, Decimal("0.00"))
        self.assertEqual(ctx_member.kpis.contratado_mes, Decimal("0.00"))
        self.assertIsNone(ctx_member.advisor.principal.valor_potencial)

        ctx_owner = contexto_dashboard(
            self.owner_a,
            organization=self.org_a,
            get_params={"aba": "visao"},
        )
        self.assertTrue(ctx_owner.ocultar_financeiro)
        self.assertEqual(ctx_owner.kpis.realizado_mes, Decimal("0.00"))
        self.assertIsNone(ctx_owner.advisor.principal.valor_potencial)

        self.client.force_login(self.member_sem_cap)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8", errors="replace")
        self.assertNotIn("R$ 5.000", html)
        self.assertNotIn("R$ 10.000", html)

    def test_tenantcontext_invalido_fail_closed_sem_fallback_user(self):
        for kwargs in (
            {"organization": None, "context": CONTEXT_NONE},
            {"organization": self.org_a, "context": CONTEXT_AMBIGUOUS},
            {"omit_tenant": True},
            {"organization": None, "context": CONTEXT_RESOLVED},
            {"organization": self.org_a, "context": "inconsistent"},
            {"organization": self.org_a, "context": "missing"},
        ):
            request = self._tenant_request(self.a1, **kwargs)
            org = organization_for_finance_write(request)
            self.assertIsNone(org, msg=kwargs)
            ctx = contexto_dashboard(
                self.a1, organization=org, get_params={"aba": "visao"}
            )
            self.assertEqual(ctx.kpis.realizado_mes, Decimal("0.00"), msg=kwargs)
            self.assertEqual(ctx.kpis.contratado_mes, Decimal("0.00"), msg=kwargs)
            self.assertEqual(ctx.risco.total_confirmado_risco, Decimal("0.00"), msg=kwargs)
            cob_keys = [i.key for i in ctx.advisor.secundarias] + (
                [ctx.advisor.principal.key] if ctx.advisor.principal else []
            )
            self.assertNotIn("cobrancas_vencidas", cob_keys, msg=kwargs)

        ctx_ok = contexto_dashboard(
            self.a1, organization=self.org_a, get_params={"aba": "visao"}
        )
        self.assertEqual(ctx_ok.kpis.realizado_mes, Decimal("10000.00"))
        self.assertEqual(ctx_ok.risco.total_confirmado_risco, Decimal("2000.00"))

    def test_growth_advisor_cadeia_organization(self):
        risco_a = calcular_receita_em_risco(self.a1, organization=self.org_a)
        risco_b = calcular_receita_em_risco(self.b1, organization=self.org_b)
        advisor_a1 = montar_advisor(self.a1, organization=self.org_a, risco=risco_a)
        advisor_a2 = montar_advisor(self.a2, organization=self.org_a, risco=risco_a)
        advisor_b1 = montar_advisor(self.b1, organization=self.org_b, risco=risco_b)

        venc_a1 = next(p for p in (advisor_a1.principal,) + advisor_a1.secundarias if p.key == "cobrancas_vencidas")
        venc_a2 = next(p for p in (advisor_a2.principal,) + advisor_a2.secundarias if p.key == "cobrancas_vencidas")
        venc_b1 = next(p for p in (advisor_b1.principal,) + advisor_b1.secundarias if p.key == "cobrancas_vencidas")
        self.assertEqual(venc_a1.quantidade, 2)
        self.assertEqual(venc_a2.quantidade, 2)
        self.assertEqual(venc_b1.quantidade, 1)
        self.assertEqual(venc_a1.valor_potencial, Decimal("2000.00"))
        self.assertEqual(venc_b1.valor_potencial, Decimal("50000.00"))

        ctx_a1 = contexto_dashboard(
            self.a1, organization=self.org_a, get_params={"aba": "visao"}
        )
        self.assertEqual(ctx_a1.kpis.realizado_mes, Decimal("10000.00"))
        self.assertEqual(ctx_a1.risco.total_confirmado_risco, Decimal("2000.00"))
        self.assertEqual(ctx_a1.advisor.principal.key, "cobrancas_vencidas")
        self.assertEqual(ctx_a1.advisor.principal.quantidade, 2)
        self.assertEqual(ctx_a1.advisor.principal.valor_potencial, Decimal("2000.00"))

        score_a1 = calcular_adv_growth_score(
            self.a1, organization=self.org_a, persistir=False
        )
        score_b1 = calcular_adv_growth_score(
            self.b1, organization=self.org_b, persistir=False
        )
        fi_a1 = next(d for d in score_a1.dimensoes if d.codigo == "financeiro")
        fi_b1 = next(d for d in score_b1.dimensoes if d.codigo == "financeiro")
        self.assertEqual(fi_a1.pontos, 55)
        self.assertEqual(fi_b1.pontos, 55)
        self.assertIn("recebimentos", fi_a1.criterio.lower())

        kpis_com = calcular_kpis(
            self.org_a,
            meta=None,
            receita_risco=Decimal("2000.00"),
            ocultar_financeiro=False,
            projecao_anual=None,
        )
        self.assertEqual(kpis_com.realizado_mes, Decimal("10000.00"))
        self.assertEqual(kpis_com.contratado_mes, Decimal("10000.00"))
        self.assertEqual(kpis_com.receita_em_risco, Decimal("2000.00"))

    def test_http_a1_agrega_same_org_e_exclui_b1(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context["ctx"]
        self.assertEqual(ctx.kpis.realizado_mes, Decimal("10000.00"))
        self.assertEqual(ctx.kpis.contratado_mes, Decimal("10000.00"))
        self.assertEqual(ctx.risco.total_confirmado_risco, Decimal("2000.00"))
        self.assertNotEqual(ctx.kpis.realizado_mes, Decimal("90000.00"))

        self.client.force_login(self.a2)
        resp_a2 = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp_a2.context["ctx"].kpis.realizado_mes, Decimal("10000.00"))
        self.assertEqual(
            resp_a2.context["ctx"].risco.total_confirmado_risco, Decimal("2000.00")
        )
