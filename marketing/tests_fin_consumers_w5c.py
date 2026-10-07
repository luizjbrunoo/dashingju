"""FIN-CONSUMERS-W5c — atribuição financeira Marketing Organization-scoped."""

from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.tenancy_write import organization_for_finance_write
from financeiro.tests_helpers import grant_finance_permissions
from marketing.models import MarketingIntegracao
from marketing.services.google_ads_resultados import (
    _contratos_qs,
    calcular_resultados_negocio,
    contexto_dashboard_resultados,
    get_receita_contratada,
    get_receita_recebida,
)
from marketing.services.periodo import PeriodoMarketing
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.choices import OrigemLead
from usuarios.models import Cliente


class FinConsumersW5cMarketingAttributionTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="W5c Org A")
        self.org_b = Organization.objects.create(name="W5c Org B")
        self.a1 = User.objects.create_user("w5c_a1", password="senha123")
        self.a2 = User.objects.create_user("w5c_a2", password="senha123")
        self.b1 = User.objects.create_user("w5c_b1", password="senha123")
        self.owner_a = User.objects.create_user("w5c_owner_a", password="senha123")
        self.member_sem_cap = User.objects.create_user("w5c_a3", password="senha123")
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(30, referencia=self.hoje)

        grant_finance_permissions(self.a1, "view_cobrancas", "view_recebimentos")
        grant_finance_permissions(self.a2, "view_cobrancas", "view_recebimentos")
        grant_finance_permissions(self.b1, "view_cobrancas", "view_recebimentos")

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

        ct = ContentType.objects.get_for_model(MarketingIntegracao)
        view_mkt = Permission.objects.get(content_type=ct, codename="view_marketing")
        view_res = Permission.objects.get(
            content_type=ct, codename="view_resultados_marketing"
        )
        grupo_sem_fin = Group.objects.create(name="W5c Marketing sem financeiro")
        grupo_sem_fin.permissions.add(view_mkt, view_res)
        self.owner_a.groups.add(grupo_sem_fin)
        self.member_sem_cap.groups.add(grupo_sem_fin)
        from marketing.tests_helpers import grant_marketing_permissions

        grant_marketing_permissions(self.a1)
        grant_marketing_permissions(self.a2)
        grant_marketing_permissions(self.b1)

        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="W5C-CLI-CA",
            email="ca@w5c.test",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            utm_campaign="campanha-a",
            gclid="W5C-GCLID-A",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="W5C-CLI-CB",
            email="cb@w5c.test",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            utm_campaign="campanha-b",
            gclid="W5C-GCLID-B",
        )

        self.ctr_a2 = Contrato.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.ca,
            referencia="W5C-CTR-A2",
            descricao="Contrato CA por A2",
            valor_total=Decimal("10000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a2,
            responsavel=self.a2,
        )
        cob_a2 = Cobranca.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.ca,
            contrato=self.ctr_a2,
            descricao="W5C-COB-A2",
            valor_original=Decimal("4000.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.a2,
            responsavel=self.a2,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_a2,
            usuario=self.a2,
            organization=self.org_a,
            valor=Decimal("4000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.a2,
        )

        self.ctr_b1 = Contrato.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cb,
            referencia="W5C-CTR-B1",
            descricao="Contrato CB",
            valor_total=Decimal("100000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.b1,
            responsavel=self.b1,
        )
        cob_b1 = Cobranca.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cb,
            contrato=self.ctr_b1,
            descricao="W5C-COB-B1",
            valor_original=Decimal("90000.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.b1,
            responsavel=self.b1,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_b1,
            usuario=self.b1,
            organization=self.org_b,
            valor=Decimal("90000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.b1,
        )

        Contrato.objects.create(
            usuario=self.a1,
            organization=None,
            cliente=self.ca,
            referencia="W5C-CTR-NULL",
            descricao="NULL",
            valor_total=Decimal("99999.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a1,
        )
        cob_null = Cobranca.objects.create(
            usuario=self.a1,
            organization=None,
            cliente=self.ca,
            descricao="W5C-COB-NULL",
            valor_original=Decimal("77777.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.a1,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_null,
            usuario=self.a1,
            organization=None,
            valor=Decimal("77777.00"),
            data_recebimento=self.hoje,
            registrado_por=self.a1,
        )

        for user in (self.member_sem_cap, self.owner_a):
            lead_cap = Cliente.objects.create(
                user=user,
                organization=self.org_a,
                nome=f"W5C-CLI-{user.username}",
                email=f"{user.username}@w5c.test",
                origem=OrigemLead.GOOGLE_ADS,
                atribuicao_confiavel=True,
                gclid=f"W5C-{user.username}",
            )
            Contrato.objects.create(
                usuario=self.a2,
                organization=self.org_a,
                cliente=lead_cap,
                referencia=f"W5C-CTR-{user.username}",
                descricao="Cap",
                valor_total=Decimal("10000.00"),
                status=StatusContrato.ACTIVE,
                criado_por=self.a2,
            )

    def _tenant_request(self, user, *, organization=None, context=None, omit_tenant=False):
        request = RequestFactory().get(reverse("marketing_dashboard"))
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        return request

    def test_same_org_a1_atribui_financeiro_criado_por_a2(self):
        expected_ctr = Decimal("30000.00")
        expected_rec = Decimal("4000.00")
        contratada = get_receita_contratada(
            self.a1, self.periodo, organization=self.org_a
        )
        recebida = get_receita_recebida(self.a1, self.periodo, organization=self.org_a)
        self.assertEqual(contratada, expected_ctr)
        self.assertEqual(recebida, expected_rec)

        funil = calcular_resultados_negocio(
            self.a1, self.periodo, organization=self.org_a
        ).funil
        self.assertEqual(funil.contratos, 3)
        self.assertEqual(funil.receita_contratada, expected_ctr)
        self.assertEqual(funil.receita_recebida, expected_rec)
        self.assertNotEqual(funil.receita_contratada, funil.receita_recebida)

        ctx = contexto_dashboard_resultados(
            self.a1,
            self.periodo,
            organization=self.org_a,
            modo_demo=False,
        )
        self.assertFalse(ctx.ocultar_financeiro)
        self.assertEqual(ctx.resultados.funil.receita_contratada, expected_ctr)
        self.assertEqual(ctx.resultados.funil.receita_recebida, expected_rec)

    def test_cross_org_b1_excluido(self):
        funil_a = calcular_resultados_negocio(
            self.a1, self.periodo, organization=self.org_a
        ).funil
        funil_b = calcular_resultados_negocio(
            self.b1, self.periodo, organization=self.org_b
        ).funil
        self.assertEqual(funil_a.receita_contratada, Decimal("30000.00"))
        self.assertEqual(funil_a.receita_recebida, Decimal("4000.00"))
        self.assertEqual(funil_b.receita_contratada, Decimal("100000.00"))
        self.assertEqual(funil_b.receita_recebida, Decimal("90000.00"))

        ids_b = [self.cb.pk]
        qs_cross = _contratos_qs(self.org_a, ids_b, self.periodo)
        self.assertFalse(qs_cross.exists())
        rec_cross = get_receita_recebida(
            self.a1, self.periodo, organization=self.org_a
        )
        self.assertNotEqual(rec_cross, Decimal("90000.00"))

    def test_null_organization_nao_entra(self):
        funil = calcular_resultados_negocio(
            self.a1, self.periodo, organization=self.org_a
        ).funil
        self.assertEqual(funil.receita_contratada, Decimal("30000.00"))
        self.assertEqual(funil.receita_recebida, Decimal("4000.00"))
        vazio = calcular_resultados_negocio(
            self.a1, self.periodo, organization=None
        ).funil
        self.assertEqual(vazio.receita_contratada, Decimal("0.00"))
        self.assertEqual(vazio.receita_recebida, Decimal("0.00"))
        self.assertEqual(vazio.contratos, 0)

    def test_capability_membership_e_owner_nao_concedem_receita(self):
        ctx_member = contexto_dashboard_resultados(
            self.member_sem_cap,
            self.periodo,
            organization=self.org_a,
            modo_demo=False,
        )
        self.assertTrue(ctx_member.ocultar_financeiro)
        self.assertEqual(ctx_member.resultados.funil.receita_contratada, Decimal("0.00"))
        self.assertEqual(ctx_member.resultados.funil.receita_recebida, Decimal("0.00"))

        ctx_owner = contexto_dashboard_resultados(
            self.owner_a,
            self.periodo,
            organization=self.org_a,
            modo_demo=False,
        )
        self.assertTrue(ctx_owner.ocultar_financeiro)
        self.assertEqual(ctx_owner.resultados.funil.receita_contratada, Decimal("0.00"))
        self.assertEqual(ctx_owner.resultados.funil.receita_recebida, Decimal("0.00"))

        self.client.force_login(self.member_sem_cap)
        resp = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ocultos conforme suas permiss")
        self.assertNotContains(resp, "R$ 4.000")
        self.assertNotContains(resp, "R$ 90.000")

    def test_tenantcontext_invalido_fail_closed(self):
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
            funil = calcular_resultados_negocio(
                self.a1, self.periodo, organization=org
            ).funil
            self.assertEqual(funil.receita_contratada, Decimal("0.00"), msg=kwargs)
            self.assertEqual(funil.receita_recebida, Decimal("0.00"), msg=kwargs)
            self.assertEqual(funil.contratos, 0, msg=kwargs)

        funil_ok = calcular_resultados_negocio(
            self.a1, self.periodo, organization=self.org_a
        ).funil
        self.assertEqual(funil_ok.receita_contratada, Decimal("30000.00"))
        self.assertEqual(funil_ok.receita_recebida, Decimal("4000.00"))

    def test_http_a1_agrega_same_org_e_exclui_b1(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context["ctx_resultados"]
        self.assertEqual(ctx.resultados.funil.receita_contratada, Decimal("30000.00"))
        self.assertEqual(ctx.resultados.funil.receita_recebida, Decimal("4000.00"))
        self.assertNotEqual(ctx.resultados.funil.receita_contratada, Decimal("100000.00"))
        self.assertNotEqual(ctx.resultados.funil.receita_recebida, Decimal("90000.00"))
