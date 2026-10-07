import json
import os
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, User
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from organizacoes.middleware import TenantContextMiddleware
from organizacoes.models import Membership, Organization
from organizacoes.services import (
    CONTEXT_AMBIGUOUS,
    CONTEXT_NONE,
    CONTEXT_RESOLVED,
    resolver_organization,
)
from usuarios.models import Cliente


class OrganizationMembershipTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="org_a", password="senha123")
        self.user_b = User.objects.create_user(username="org_b", password="senha123")

    def test_cria_organization_com_slug(self):
        org = Organization.objects.create(name="Escritório Alfa", created_by=self.user_a)
        self.assertEqual(org.status, Organization.Status.ACTIVE)
        self.assertTrue(org.slug)
        self.assertEqual(org.created_by_id, self.user_a.pk)

    def test_created_by_opcional(self):
        org = Organization.objects.create(name="Sem criador")
        self.assertIsNone(org.created_by)

    def test_slug_unico_para_nomes_iguais(self):
        a = Organization.objects.create(name="Mesmo Nome")
        b = Organization.objects.create(name="Mesmo Nome")
        self.assertNotEqual(a.slug, b.slug)

    def test_status_inactive(self):
        org = Organization.objects.create(
            name="Inativa", status=Organization.Status.INACTIVE
        )
        self.assertEqual(org.status, Organization.Status.INACTIVE)

    def test_membership_owner(self):
        org = Organization.objects.create(name="Org A")
        m = Membership.objects.create(
            user=self.user_a,
            organization=org,
            role=Membership.Role.OWNER,
        )
        self.assertEqual(m.status, Membership.Status.ACTIVE)
        self.assertEqual(org.memberships.count(), 1)

    def test_membership_unica_por_user_e_org(self):
        org = Organization.objects.create(name="Org única")
        Membership.objects.create(user=self.user_a, organization=org)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Membership.objects.create(user=self.user_a, organization=org)

    def test_mesmo_user_em_duas_orgs_permitido_no_schema(self):
        org1 = Organization.objects.create(name="Org 1")
        org2 = Organization.objects.create(name="Org 2")
        Membership.objects.create(user=self.user_a, organization=org1)
        Membership.objects.create(user=self.user_a, organization=org2)
        self.assertEqual(self.user_a.organization_memberships.count(), 2)

    def test_duas_orgs_isoladas_por_membership(self):
        org_a = Organization.objects.create(name="Tenant A")
        org_b = Organization.objects.create(name="Tenant B")
        Membership.objects.create(
            user=self.user_a, organization=org_a, role=Membership.Role.OWNER
        )
        Membership.objects.create(
            user=self.user_b, organization=org_b, role=Membership.Role.OWNER
        )
        self.assertFalse(
            Membership.objects.filter(user=self.user_a, organization=org_b).exists()
        )
        self.assertFalse(
            Membership.objects.filter(user=self.user_b, organization=org_a).exists()
        )

    def test_user_sem_membership(self):
        Organization.objects.create(name="Órfã de membros")
        self.assertEqual(self.user_a.organization_memberships.count(), 0)

    def test_group_nao_cria_organization(self):
        Group.objects.get_or_create(name="Agenda — acesso completo")
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_nao_conecta_cliente(self):
        org = Organization.objects.create(name="Não ligada")
        cliente = Cliente.objects.create(
            nome="Cliente A", email="a@example.com", user=self.user_a
        )
        self.assertTrue(hasattr(cliente, "organization_id"))
        self.assertIsNone(cliente.organization_id)
        self.assertEqual(org.clientes.count(), 0)


class ResolverOrganizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ctx_user", password="senha123")

    def _org(self, name, status=Organization.Status.ACTIVE):
        return Organization.objects.create(name=name, status=status)

    def _membership(self, user, org, *, role=Membership.Role.MEMBER, status=Membership.Status.ACTIVE):
        return Membership.objects.create(
            user=user, organization=org, role=role, status=status
        )

    def test_anonymous_none(self):
        with self.assertNumQueries(0):
            org, status = resolver_organization(AnonymousUser())
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_NONE)

    def test_user_sem_membership_none(self):
        org, status = resolver_organization(self.user)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_NONE)

    def test_uma_membership_valida_resolved(self):
        escritorio = self._org("Única")
        self._membership(self.user, escritorio)
        org, status = resolver_organization(self.user)
        self.assertEqual(org.pk, escritorio.pk)
        self.assertEqual(status, CONTEXT_RESOLVED)

    def test_membership_inativa_none(self):
        escritorio = self._org("Ativa")
        self._membership(self.user, escritorio, status=Membership.Status.INACTIVE)
        org, status = resolver_organization(self.user)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_NONE)

    def test_organization_inativa_none(self):
        escritorio = self._org("Inativa", status=Organization.Status.INACTIVE)
        self._membership(self.user, escritorio)
        org, status = resolver_organization(self.user)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_NONE)

    def test_duas_memberships_ativas_ambiguous(self):
        a = self._org("Org A")
        b = self._org("Org B")
        self._membership(self.user, a)
        self._membership(self.user, b)
        org, status = resolver_organization(self.user)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_AMBIGUOUS)

    def test_owner_nao_desempata_duas_orgs(self):
        a = self._org("Owner Org")
        b = self._org("Member Org")
        self._membership(self.user, a, role=Membership.Role.OWNER)
        self._membership(self.user, b, role=Membership.Role.MEMBER)
        org, status = resolver_organization(self.user)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_AMBIGUOUS)

    def test_superuser_sem_membership_none(self):
        admin = User.objects.create_superuser(
            username="super_ctx", email="super@example.com", password="senha123"
        )
        org, status = resolver_organization(admin)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_NONE)

    def test_staff_sem_membership_none(self):
        staff = User.objects.create_user(
            username="staff_ctx", password="senha123", is_staff=True
        )
        org, status = resolver_organization(staff)
        self.assertIsNone(org)
        self.assertEqual(status, CONTEXT_NONE)


class TenantContextMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="mw_user", password="senha123")
        self.captured = {}

        def get_response(request):
            self.captured["organization"] = getattr(request, "organization", "MISSING")
            self.captured["context"] = getattr(request, "organization_context", "MISSING")
            return HttpResponse("ok")

        self.middleware = TenantContextMiddleware(get_response)

    def test_anonymous_atribui_none_none(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.captured["organization"])
        self.assertEqual(self.captured["context"], CONTEXT_NONE)

    def test_query_e_header_nao_escolhem_tenant(self):
        org_propria = Organization.objects.create(name="Própria")
        org_outra = Organization.objects.create(name="Outra")
        Membership.objects.create(
            user=self.user,
            organization=org_propria,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        request = self.factory.get(
            "/",
            {"organization": str(org_outra.pk), "organization_id": str(org_outra.pk)},
        )
        request.user = self.user
        request.META["HTTP_X_ORGANIZATION_ID"] = str(org_outra.pk)
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.captured["organization"].pk, org_propria.pk)
        self.assertEqual(self.captured["context"], CONTEXT_RESOLVED)
        self.assertNotEqual(self.captured["organization"].pk, org_outra.pk)

    def test_ambiguous_nao_escolhe_silenciosamente(self):
        a = Organization.objects.create(name="A")
        b = Organization.objects.create(name="B")
        Membership.objects.create(user=self.user, organization=a)
        Membership.objects.create(user=self.user, organization=b)
        request = self.factory.get("/")
        request.user = self.user
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.captured["organization"])
        self.assertEqual(self.captured["context"], CONTEXT_AMBIGUOUS)


class TenantContextRegressionTests(TestCase):
    def setUp(self):
        self.password = "senha123"
        self.user = User.objects.create_user(
            username="reg_user", password=self.password
        )

    def test_login_continua_funcionando_sem_membership(self):
        resp = self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        follow = self.client.get(resp.url)
        self.assertEqual(follow.status_code, 200)
        self.assertContains(follow, "Home Executiva")
        self.assertContains(follow, "Não foi possível determinar o escritório ativo")

    def test_logout_continua_funcionando(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse("logout"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("login"))

    def test_signup_nao_cria_membership(self):
        resp = self.client.post(
            reverse("cadastro"),
            {
                "username": "novo_ctx",
                "senha": "senha123",
                "confirmar_senha": "senha123",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("login"))
        criado = User.objects.get(username="novo_ctx")
        self.assertEqual(criado.organization_memberships.count(), 0)

    def test_user_sem_membership_acessa_clientes(self):
        Cliente.objects.create(
            nome="Cliente legado", email="legado@example.com", user=self.user
        )
        self.client.force_login(self.user)
        resp = self.client.get(reverse("clientes"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Cliente legado")

    def test_admin_nao_e_bloqueado_pelo_middleware(self):
        staff = User.objects.create_user(
            username="admin_ctx", password=self.password, is_staff=True
        )
        self.client.force_login(staff)
        resp = self.client.get("/admin/")
        self.assertNotIn(resp.status_code, (401, 403, 404))
        self.assertEqual(resp.status_code, 200)

    def test_webhook_nao_e_bloqueado_pelo_middleware(self):
        env = os.environ.copy()
        env.pop("IA_WEBHOOK_SECRET", None)
        env.pop("IA_WHATSAPP_USER_ID", None)
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post(
                reverse("webhook_whatsapp"),
                data=json.dumps({"phone": "5511999999999"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "forbidden")

    def test_ia_privada_mantem_ownership_legado(self):
        outro = User.objects.create_user(username="outro_ctx", password=self.password)
        cliente_b = Cliente.objects.create(
            nome="Cliente B", email="b@example.com", user=outro
        )
        self.client.force_login(self.user)
        resp = self.client.get(reverse("chat", args=[cliente_b.id]))
        self.assertEqual(resp.status_code, 404)
