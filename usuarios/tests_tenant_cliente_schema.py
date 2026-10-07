from django.contrib.auth.models import User
from django.db.models import CASCADE, SET_NULL
from django.test import TestCase
from django.urls import reverse

from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_NONE, CONTEXT_RESOLVED, resolver_organization
from usuarios.models import Cliente


class ClienteOrganizationSchemaTests(TestCase):
    def _field(self):
        return Cliente._meta.get_field("organization")

    def _user(self, username):
        return User.objects.create_user(username=username, password="senha123")

    def _org(self, name, created_by=None):
        return Organization.objects.create(name=name, created_by=created_by)

    def _membership(self, user, org, *, role=Membership.Role.OWNER):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=role,
            status=Membership.Status.ACTIVE,
        )

    def _cliente(self, user, *, nome="Cliente schema", email="schema@example.com", organization=None):
        return Cliente.objects.create(
            user=user, nome=nome, email=email, organization=organization
        )

    def test_campo_existe(self):
        self.assertIsNotNone(self._field())

    def test_tipo_fk_organization(self):
        field = self._field()
        self.assertEqual(field.related_model, Organization)

    def test_nullable(self):
        field = self._field()
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_on_delete_set_null(self):
        self.assertIs(self._field().remote_field.on_delete, SET_NULL)

    def test_related_name_clientes(self):
        user = self._user("rel_user")
        org = self._org("Org Rel")
        cli = self._cliente(user, organization=org)
        self.assertEqual(list(org.clientes.all()), [cli])

    def test_legado_sem_organization(self):
        user = self._user("legado_null")
        cli = Cliente.objects.create(
            user=user, nome="Legado", email="legado@example.com"
        )
        self.assertIsNone(cli.organization)
        self.assertIsNone(cli.organization_id)

    def test_tenant_resolvido_nao_preenche(self):
        user = self._user("resolved_null")
        org = self._org("Org Resolved")
        self._membership(user, org)
        org_res, ctx = resolver_organization(user)
        self.assertEqual(ctx, CONTEXT_RESOLVED)
        self.assertEqual(org_res.pk, org.pk)
        cli = Cliente.objects.create(
            user=user, nome="Nao dual", email="nodual@example.com"
        )
        self.assertIsNone(cli.organization_id)

    def test_listagem_legado_por_user(self):
        user_a = self._user("list_a")
        user_b = self._user("list_b")
        self._cliente(user_a, nome="Cliente List A", email="lista@example.com")
        self._cliente(user_b, nome="Cliente List B", email="listb@example.com")
        self.client.force_login(user_a)
        resp = self.client.get(reverse("clientes"))
        self.assertEqual(resp.status_code, 200)
        # 6E1: none válido → lista vazia (sem fallback por Cliente.user).
        self.assertNotContains(resp, "Cliente List A")
        self.assertNotContains(resp, "Cliente List B")

    def test_detail_legado(self):
        user_a = self._user("det_a")
        user_b = self._user("det_b")
        cli_a = self._cliente(user_a, nome="Det A", email="deta@example.com")
        cli_b = self._cliente(user_b, nome="Det B", email="detb@example.com")
        self.client.force_login(user_a)
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": cli_a.pk})).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": cli_b.pk})).status_code,
            404,
        )

    def test_mesma_org_member_nao_acessa_por_user(self):
        org = self._org("Org Shared")
        owner = self._user("own_shared")
        member = self._user("mem_shared")
        self._membership(owner, org, role=Membership.Role.OWNER)
        self._membership(member, org, role=Membership.Role.MEMBER)
        cli = self._cliente(
            owner, nome="Do Owner", email="ownercli@example.com", organization=org
        )
        self.client.force_login(member)
        resp = self.client.get(reverse("cliente", kwargs={"id": cli.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["detail_access_mode"], "full")
        lista = self.client.get(reverse("clientes"))
        self.assertContains(lista, "Do Owner")

    def test_outra_organization_legado(self):
        org_a = self._org("Org A iso")
        org_b = self._org("Org B iso")
        user_a = self._user("iso_a")
        user_b = self._user("iso_b")
        self._membership(user_a, org_a)
        self._membership(user_b, org_b)
        cli_a = self._cliente(
            user_a, nome="Iso A", email="isoa@example.com", organization=org_a
        )
        cli_b = self._cliente(
            user_b, nome="Iso B", email="isob@example.com", organization=org_b
        )
        self.client.force_login(user_a)
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": cli_a.pk})).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": cli_b.pk})).status_code,
            404,
        )

    def test_organization_none_salva(self):
        user = self._user("save_none")
        cli = Cliente(
            user=user,
            nome="None Org",
            email="noneorg@example.com",
            organization=None,
        )
        cli.save()
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)

    def test_set_null_ao_apagar_organization(self):
        user = self._user("setnull")
        org = self._org("Org Apagar")
        cli = self._cliente(
            user, nome="Fica", email="fica@example.com", organization=org
        )
        org_id = org.pk
        org.delete()
        cli.refresh_from_db()
        self.assertTrue(Cliente.objects.filter(pk=cli.pk).exists())
        self.assertIsNone(cli.organization_id)
        self.assertFalse(Organization.objects.filter(pk=org_id).exists())

    def test_user_continua_obrigatorio(self):
        user_field = Cliente._meta.get_field("user")
        self.assertFalse(user_field.null)
        self.assertFalse(user_field.blank)
        self.assertIs(user_field.remote_field.on_delete, CASCADE)

    def test_http_create_ignora_request_organization(self):
        user = self._user("http_create")
        org = self._org("Org Http")
        self._membership(user, org)
        self.assertEqual(resolver_organization(user)[1], CONTEXT_RESOLVED)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("clientes"),
            {
                "nome": "Criado HTTP",
                "email": "http@example.com",
                "tipo": "PF",
                "status": "em_prospeccao",
            },
        )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(email="http@example.com")
        self.assertEqual(cli.user_id, user.pk)
        self.assertEqual(cli.organization_id, org.pk)

    def test_listagem_mesma_org_ainda_por_user(self):
        org = self._org("Org Dois Users")
        user_a = self._user("same_a")
        user_b = self._user("same_b")
        self._membership(user_a, org)
        self._membership(user_b, org)
        self._cliente(
            user_b, nome="Cliente Do B", email="dob@example.com", organization=org
        )
        self.client.force_login(user_a)
        resp = self.client.get(reverse("clientes"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente Do B")

    def test_user_sem_organization_legado(self):
        user = self._user("sem_mem")
        org_res, ctx = resolver_organization(user)
        self.assertIsNone(org_res)
        self.assertEqual(ctx, CONTEXT_NONE)
        cli = self._cliente(user, nome="Sem Org", email="semorg@example.com")
        self.assertIsNone(cli.organization_id)
        self.client.force_login(user)
        lista = self.client.get(reverse("clientes"))
        self.assertEqual(lista.status_code, 200)
        self.assertNotContains(lista, "Sem Org")
        detail = self.client.get(reverse("cliente", kwargs={"id": cli.pk}))
        self.assertEqual(detail.status_code, 404)

    def test_organization_nao_vem_do_post(self):
        user = self._user("post_org")
        org = self._org("Org Maliciosa")
        outra = self._org("Org Alheia")
        self._membership(user, org)
        self.client.force_login(user)
        self.client.post(
            reverse("clientes"),
            {
                "nome": "Post Org",
                "email": "postorg@example.com",
                "tipo": "PF",
                "status": "em_prospeccao",
                "organization": str(outra.pk),
                "organization_id": str(outra.pk),
            },
        )
        cli = Cliente.objects.get(email="postorg@example.com")
        self.assertEqual(cli.organization_id, org.pk)
        self.assertNotEqual(cli.organization_id, outra.pk)
