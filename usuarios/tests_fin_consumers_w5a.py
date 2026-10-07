"""FIN-CONSUMERS-W5a — Cliente 360 Financeiro Organization-scoped."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.http import Http404, HttpResponseNotFound
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, Contrato
from financeiro.services.cliente_financeiro import (
    cobrancas_cliente_organization,
    contratos_cliente_organization,
    resumo_financeiro_cliente_organization,
)
from financeiro.tests_helpers import grant_finance_permissions
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente
from usuarios.views import cliente as cliente_view


class FinConsumersW5aCliente360Tests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="W5a Org A")
        self.org_b = Organization.objects.create(name="W5a Org B")
        self.a1 = User.objects.create_user("w5a_a1", password="senha123")
        self.a2 = User.objects.create_user("w5a_a2", password="senha123")
        self.b1 = User.objects.create_user("w5a_b1", password="senha123")
        self.owner_a = User.objects.create_user("w5a_owner_a", password="senha123")
        grant_finance_permissions(self.a1, "view_cobrancas")
        grant_finance_permissions(self.a2, "view_cobrancas")
        grant_finance_permissions(self.b1, "view_cobrancas")
        for user, org, role in (
            (self.a1, self.org_a, Membership.Role.MEMBER),
            (self.a2, self.org_a, Membership.Role.MEMBER),
            (self.owner_a, self.org_a, Membership.Role.OWNER),
            (self.b1, self.org_b, Membership.Role.MEMBER),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=role,
                status=Membership.Status.ACTIVE,
            )
        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="W5A-CLI-CA",
            email="ca@w5a.test",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="W5A-CLI-CB",
            email="cb@w5a.test",
        )
        self.cli_null = Cliente.objects.create(
            user=self.a1,
            organization=None,
            nome="W5A-CLI-NULL",
            email="null@w5a.test",
        )
        self.ctr_a2 = Contrato.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.ca,
            referencia="W5A-CTR-A2",
            descricao="Contrato CA por A2",
            valor_total=Decimal("5000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a2,
            responsavel=self.a2,
        )
        self.cob_a2 = Cobranca.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.ca,
            contrato=self.ctr_a2,
            descricao="W5A-COB-A2-2000",
            valor_original=Decimal("2000.00"),
            data_vencimento=timezone.localdate(),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=self.a2,
            responsavel=self.a2,
        )
        self.ctr_b1 = Contrato.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cb,
            referencia="W5A-CTR-B1",
            descricao="Contrato CB",
            valor_total=Decimal("90000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.b1,
            responsavel=self.b1,
        )
        self.cob_b1 = Cobranca.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cb,
            contrato=self.ctr_b1,
            descricao="W5A-COB-B1-90000",
            valor_original=Decimal("90000.00"),
            data_vencimento=timezone.localdate(),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=self.b1,
            responsavel=self.b1,
        )
        Cobranca.objects.create(
            usuario=self.a1,
            organization=None,
            cliente=self.ca,
            descricao="W5A-COB-NULL",
            valor_original=Decimal("99999.00"),
            data_vencimento=timezone.localdate(),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=self.a1,
        )

    def _login(self, user):
        self.client.force_login(user)

    def _detail(self, user, cliente):
        self._login(user)
        return self.client.get(reverse("cliente", kwargs={"id": cliente.pk}))

    def _get_view(self, user, cliente, *, organization=None, context=None, omit_tenant=False):
        request = RequestFactory().get(reverse("cliente", kwargs={"id": cliente.pk}))
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        try:
            return cliente_view(request, cliente.pk)
        except Http404:
            return HttpResponseNotFound()

    def test_same_org_a1_ve_financeiro_criado_por_a2(self):
        expected = Decimal("2000.00")
        resumo = resumo_financeiro_cliente_organization(self.org_a, self.ca)
        self.assertEqual(resumo.a_receber, expected)
        self.assertEqual(resumo.vencido, expected)
        self.assertEqual(resumo.contratos_ativos, 1)
        self.assertEqual(resumo.cobrancas_abertas, 1)
        desc = {c.descricao for c in cobrancas_cliente_organization(self.org_a, self.ca)}
        refs = {c.referencia for c in contratos_cliente_organization(self.org_a, self.ca)}
        self.assertIn("W5A-COB-A2-2000", desc)
        self.assertIn("W5A-CTR-A2", refs)
        self.assertNotIn("W5A-COB-B1-90000", desc)
        self.assertNotIn("W5A-COB-NULL", desc)

        resp = self._detail(self.a1, self.ca)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["financeiro_oculto"])
        self.assertEqual(resp.context["financeiro_resumo"].a_receber, expected)
        body = resp.content.decode("utf-8", errors="replace")
        self.assertIn("W5A-COB-A2-2000", body)
        self.assertIn("W5A-CTR-A2", body)
        self.assertNotIn("W5A-COB-B1-90000", body)
        self.assertNotIn("90000", body)

    def test_cross_org_a1_nao_ve_cb(self):
        resp = self._detail(self.a1, self.cb)
        self.assertEqual(resp.status_code, 404)
        resumo_b_via_a = resumo_financeiro_cliente_organization(self.org_a, self.cb)
        self.assertEqual(resumo_b_via_a.a_receber, Decimal("0"))
        self.assertEqual(resumo_b_via_a.contratos_ativos, 0)

    def test_capability_obrigatoria(self):
        a3 = User.objects.create_user("w5a_a3", password="senha123")
        Membership.objects.create(
            user=a3,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        ca3 = Cliente.objects.create(
            user=a3, organization=self.org_a, nome="W5A-CLI-A3", email="a3@w5a.test"
        )
        Cobranca.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=ca3,
            descricao="W5A-COB-A3-HIDDEN",
            valor_original=Decimal("2000.00"),
            data_vencimento=timezone.localdate(),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=self.a2,
        )
        resp = self._detail(a3, ca3)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["financeiro_oculto"])
        self.assertNotIn("financeiro_resumo", resp.context)
        self.assertNotIn("W5A-COB-A3-HIDDEN", resp.content.decode("utf-8", errors="replace"))

    def test_owner_sem_permission_nao_concede(self):
        ca_owner = Cliente.objects.create(
            user=self.owner_a,
            organization=self.org_a,
            nome="W5A-CLI-OWNER",
            email="owner@w5a.test",
        )
        Cobranca.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=ca_owner,
            descricao="W5A-COB-OWNER-HIDDEN",
            valor_original=Decimal("2000.00"),
            data_vencimento=timezone.localdate(),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=self.a2,
        )
        resp = self._detail(self.owner_a, ca_owner)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["financeiro_oculto"])
        self.assertNotIn("W5A-COB-OWNER-HIDDEN", resp.content.decode("utf-8", errors="replace"))

    def test_null_organization_nao_fallback_user(self):
        resumo = resumo_financeiro_cliente_organization(self.org_a, self.cli_null)
        self.assertEqual(resumo.a_receber, Decimal("0"))
        self.assertFalse(list(cobrancas_cliente_organization(self.org_a, self.cli_null)))
        resp = self._detail(self.a1, self.cli_null)
        self.assertEqual(resp.status_code, 404)

    def test_tenantcontext_invalido_fail_closed(self):
        for kwargs in (
            {"organization": None, "context": CONTEXT_NONE},
            {"organization": self.org_a, "context": CONTEXT_AMBIGUOUS},
            {"omit_tenant": True},
            {"organization": None, "context": CONTEXT_RESOLVED},
            {"organization": self.org_a, "context": "inconsistent"},
        ):
            resp = self._get_view(self.a1, self.ca, **kwargs)
            self.assertEqual(resp.status_code, 404, msg=kwargs)

        vazio = resumo_financeiro_cliente_organization(None, self.ca)
        self.assertEqual(vazio.a_receber, Decimal("0"))
        self.assertEqual(vazio.vencido, Decimal("0"))
