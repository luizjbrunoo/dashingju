from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse

from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_NONE, CONTEXT_RESOLVED, resolver_organization
from usuarios.models import Cliente
from usuarios.views import MSG_TENANT_INDETERMINADO, clientes as clientes_view


class ClienteDualWriteTests(TestCase):
    def _user(self, username, **kwargs):
        return User.objects.create_user(username=username, password="senha123", **kwargs)

    def _org(self, name, *, created_by=None, status=Organization.Status.ACTIVE):
        return Organization.objects.create(
            name=name, created_by=created_by, status=status
        )

    def _membership(
        self,
        user,
        org,
        *,
        role=Membership.Role.OWNER,
        status=Membership.Status.ACTIVE,
    ):
        return Membership.objects.create(
            user=user, organization=org, role=role, status=status
        )

    def _post_data(self, *, nome="Novo Cliente", email="novo@ex.test"):
        return {
            "nome": nome,
            "email": email,
            "tipo": "PF",
            "status": "em_prospeccao",
        }

    def _post_view(self, user, data, *, organization=None, context=None, omit_tenant=False):
        request = RequestFactory().post(reverse("clientes"), data)
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        return clientes_view(request)

    def test_resolved_grava_user_e_organization(self):
        user = self._user("dw_resolved")
        org = self._org("Org Dual A")
        self._membership(user, org)
        self.assertEqual(resolver_organization(user)[1], CONTEXT_RESOLVED)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("clientes"),
            self._post_data(nome="Cliente Resolved", email="resolved@ex.test"),
        )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(email="resolved@ex.test")
        self.assertEqual(cli.user_id, user.pk)
        self.assertEqual(cli.organization_id, org.pk)

    def test_none_grava_user_e_organization_null(self):
        user = self._user("dw_none")
        self.assertEqual(resolver_organization(user)[1], CONTEXT_NONE)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("clientes"),
            self._post_data(nome="Cliente None", email="none@ex.test"),
        )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(email="none@ex.test")
        self.assertEqual(cli.user_id, user.pk)
        self.assertIsNone(cli.organization_id)

    def test_ambiguous_nao_cria(self):
        user = self._user("dw_amb")
        org_a = self._org("Org Amb A")
        org_b = self._org("Org Amb B")
        self._membership(user, org_a)
        self._membership(user, org_b)
        self.client.force_login(user)
        antes = Cliente.objects.count()
        resp = self.client.post(
            reverse("clientes"),
            self._post_data(nome="Nao Deve", email="amb@ex.test"),
            follow=True,
        )
        self.assertEqual(Cliente.objects.count(), antes)
        self.assertFalse(Cliente.objects.filter(email="amb@ex.test").exists())
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "organization_context")
        self.assertNotContains(resp, "Membership")

    def test_resolved_ignora_post_malicioso(self):
        user = self._user("dw_mal_res")
        org_a = self._org("Org Mal A")
        org_b = self._org("Org Mal B")
        self._membership(user, org_a)
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"),
            {
                **self._post_data(email="mal.res@ex.test"),
                "organization": str(org_b.pk),
                "organization_id": str(org_b.pk),
            },
        )
        cli = Cliente.objects.get(email="mal.res@ex.test")
        self.assertEqual(cli.organization_id, org_a.pk)
        self.assertNotEqual(cli.organization_id, org_b.pk)

    def test_none_ignora_post_malicioso(self):
        user = self._user("dw_mal_none")
        org = self._org("Org Mal None")
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"),
            {
                **self._post_data(email="mal.none@ex.test"),
                "organization": str(org.pk),
                "organization_id": str(org.pk),
            },
        )
        cli = Cliente.objects.get(email="mal.none@ex.test")
        self.assertIsNone(cli.organization_id)

    def test_ambiguous_ignora_post_malicioso(self):
        user = self._user("dw_mal_amb")
        org_a = self._org("Org Mal Amb A")
        org_b = self._org("Org Mal Amb B")
        self._membership(user, org_a)
        self._membership(user, org_b)
        self.client.force_login(user)
        antes = Cliente.objects.count()
        self.client.post(
            reverse("clientes"),
            {
                **self._post_data(email="mal.amb@ex.test"),
                "organization": str(org_a.pk),
                "organization_id": str(org_a.pk),
            },
        )
        self.assertEqual(Cliente.objects.count(), antes)
        self.assertFalse(Cliente.objects.filter(email="mal.amb@ex.test").exists())

    def test_resolved_sem_organization_fail_closed(self):
        user = self._user("dw_inc1")
        antes = Cliente.objects.count()
        resp = self._post_view(
            user,
            self._post_data(email="inc1@ex.test"),
            organization=None,
            context=CONTEXT_RESOLVED,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Cliente.objects.count(), antes)

    def test_none_com_organization_fail_closed(self):
        user = self._user("dw_inc2")
        org = self._org("Org Inc2")
        antes = Cliente.objects.count()
        resp = self._post_view(
            user,
            self._post_data(email="inc2@ex.test"),
            organization=org,
            context=CONTEXT_NONE,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Cliente.objects.count(), antes)

    def test_context_desconhecido_fail_closed(self):
        user = self._user("dw_unk")
        org = self._org("Org Unk")
        antes = Cliente.objects.count()
        resp = self._post_view(
            user,
            self._post_data(email="unk@ex.test"),
            organization=org,
            context="qualquer_coisa",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Cliente.objects.count(), antes)

    def test_context_ausente_fail_closed(self):
        user = self._user("dw_ausente")
        antes = Cliente.objects.count()
        resp = self._post_view(
            user,
            self._post_data(email="ausente@ex.test"),
            omit_tenant=True,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Cliente.objects.count(), antes)
        self.assertFalse(Cliente.objects.filter(email="ausente@ex.test").exists())

    def test_member_nao_le_cliente_do_owner_apos_dual_write(self):
        org = self._org("Org Shared DW")
        owner = self._user("dw_owner")
        member = self._user("dw_member")
        self._membership(owner, org, role=Membership.Role.OWNER)
        self._membership(member, org, role=Membership.Role.MEMBER)
        self.client.force_login(owner)
        self.client.post(
            reverse("clientes"),
            self._post_data(nome="Do Owner DW", email="owner.dw@ex.test"),
        )
        cli = Cliente.objects.get(email="owner.dw@ex.test")
        self.assertEqual(cli.user_id, owner.pk)
        self.assertEqual(cli.organization_id, org.pk)
        self.client.force_login(member)
        lista = self.client.get(reverse("clientes"))
        self.assertContains(lista, "Do Owner DW")
        det = self.client.get(reverse("cliente", kwargs={"id": cli.pk}))
        self.assertEqual(det.status_code, 200)
        self.assertEqual(det.context["detail_access_mode"], "full")

    def test_owner_nao_le_cliente_do_member(self):
        org = self._org("Org Shared DW2")
        owner = self._user("dw_owner2")
        member = self._user("dw_member2")
        self._membership(owner, org, role=Membership.Role.OWNER)
        self._membership(member, org, role=Membership.Role.MEMBER)
        self.client.force_login(member)
        self.client.post(
            reverse("clientes"),
            self._post_data(nome="Do Member DW", email="member.dw@ex.test"),
        )
        cli = Cliente.objects.get(email="member.dw@ex.test")
        self.assertEqual(cli.user_id, member.pk)
        self.assertEqual(cli.organization_id, org.pk)
        self.client.force_login(owner)
        lista = self.client.get(reverse("clientes"))
        self.assertContains(lista, "Do Member DW")
        det = self.client.get(reverse("cliente", kwargs={"id": cli.pk}))
        self.assertEqual(det.status_code, 200)
        self.assertEqual(det.context["detail_access_mode"], "full")

    def test_nao_altera_cliente_legado_null(self):
        user = self._user("dw_legado")
        legado = Cliente.objects.create(
            user=user, nome="Legado Null", email="legado.null@ex.test"
        )
        self.assertIsNone(legado.organization_id)
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"),
            self._post_data(nome="Outro", email="outro.legado@ex.test"),
        )
        legado.refresh_from_db()
        self.assertIsNone(legado.organization_id)
        novo = Cliente.objects.get(email="outro.legado@ex.test")
        self.assertIsNone(novo.organization_id)

    def test_nao_altera_clientes_existentes(self):
        user = self._user("dw_exist")
        org = self._org("Org Exist")
        self._membership(user, org)
        existente = Cliente.objects.create(
            user=user,
            nome="Ja Existia",
            email="existia@ex.test",
            organization=org,
        )
        org_id = existente.organization_id
        user_id = existente.user_id
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"),
            self._post_data(nome="Mais Um", email="mais.um@ex.test"),
        )
        existente.refresh_from_db()
        self.assertEqual(existente.organization_id, org_id)
        self.assertEqual(existente.user_id, user_id)
        self.assertEqual(existente.nome, "Ja Existia")
        novo = Cliente.objects.get(email="mais.um@ex.test")
        self.assertEqual(novo.organization_id, org.pk)
        self.assertNotEqual(novo.pk, existente.pk)

    def test_user_continua_request_user(self):
        user = self._user("dw_user_obrig")
        org = self._org("Org User Obrig")
        self._membership(user, org)
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"), self._post_data(email="user.obrig@ex.test")
        )
        cli = Cliente.objects.get(email="user.obrig@ex.test")
        self.assertEqual(cli.user_id, user.pk)
        self.assertIsNotNone(cli.organization_id)

    def test_organization_nao_define_user(self):
        org = self._org("Org Dois")
        user_a = self._user("dw_ua")
        user_b = self._user("dw_ub")
        self._membership(user_a, org)
        self._membership(user_b, org)
        self.client.force_login(user_b)
        self.client.post(
            reverse("clientes"), self._post_data(email="user.b.org@ex.test")
        )
        cli = Cliente.objects.get(email="user.b.org@ex.test")
        self.assertEqual(cli.user_id, user_b.pk)
        self.assertNotEqual(cli.user_id, user_a.pk)
        self.assertEqual(cli.organization_id, org.pk)

    def test_superuser_sem_membership_none(self):
        user = self._user("dw_super", is_superuser=True, is_staff=True)
        self._org("Org Super Ignorada")
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"), self._post_data(email="super.none@ex.test")
        )
        cli = Cliente.objects.get(email="super.none@ex.test")
        self.assertEqual(cli.user_id, user.pk)
        self.assertIsNone(cli.organization_id)

    def test_group_nao_e_tenant(self):
        user = self._user("dw_group")
        grupo, _ = Group.objects.get_or_create(name="Agenda — acesso completo")
        user.groups.add(grupo)
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"), self._post_data(email="group.none@ex.test")
        )
        cli = Cliente.objects.get(email="group.none@ex.test")
        self.assertIsNone(cli.organization_id)

    def test_created_by_nao_e_tenant(self):
        user = self._user("dw_created")
        org = self._org("Org Created By DW", created_by=user)
        self.assertEqual(resolver_organization(user)[1], CONTEXT_NONE)
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"), self._post_data(email="created.by@ex.test")
        )
        cli = Cliente.objects.get(email="created.by@ex.test")
        self.assertIsNone(cli.organization_id)
        self.assertEqual(org.created_by_id, user.pk)

    def test_usa_request_organization_sem_resolver_paralelo(self):
        user = self._user("dw_no_resolver")
        org_a = self._org("Org Request A")
        with patch(
            "organizacoes.services.resolver_organization",
            side_effect=AssertionError("view nao deve resolver tenant"),
        ):
            resp = self._post_view(
                user,
                self._post_data(email="no.resolver@ex.test"),
                organization=org_a,
                context=CONTEXT_RESOLVED,
            )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(email="no.resolver@ex.test")
        self.assertEqual(cli.user_id, user.pk)
        self.assertEqual(cli.organization_id, org_a.pk)

    def test_listagem_e_busca_continuam_por_user(self):
        user_a = self._user("dw_list_a")
        user_b = self._user("dw_list_b")
        org = self._org("Org List")
        self._membership(user_a, org)
        self._membership(user_b, org)
        self.client.force_login(user_a)
        self.client.post(
            reverse("clientes"),
            self._post_data(nome="Alpha Busca", email="alpha.busca@ex.test"),
        )
        self.client.force_login(user_b)
        self.client.post(
            reverse("clientes"),
            self._post_data(nome="Beta Busca", email="beta.busca@ex.test"),
        )
        self.client.force_login(user_a)
        lista = self.client.get(reverse("clientes"))
        self.assertContains(lista, "Alpha Busca")
        self.assertContains(lista, "Beta Busca")
        busca = self.client.get(reverse("clientes"), {"q": "Beta"})
        self.assertContains(busca, "Beta Busca")
        cli_b = Cliente.objects.get(email="beta.busca@ex.test")
        det = self.client.get(reverse("cliente", kwargs={"id": cli_b.pk}))
        self.assertEqual(det.status_code, 200)
        self.assertEqual(det.context["detail_access_mode"], "full")
