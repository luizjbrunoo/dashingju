import inspect
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse

from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.choices import OrigemLead
from usuarios.models import Cliente
from usuarios.views import (
    MSG_TENANT_INDETERMINADO,
    _queryset_base_listagem_clientes,
    clientes as clientes_view,
)


class ClienteListagemOrganizacionalTests(TestCase):
    """6E1: GET da listagem/busca/filtros por Organization. Detail permanece por User."""

    def setUp(self):
        self.org_a = Organization.objects.create(name="Organization A")
        self.org_b = Organization.objects.create(name="Organization B")
        self.owner_a = self._user("owner_a")
        self.member_a = self._user("member_a")
        self.member_a2 = self._user("member_a2")
        self.owner_b = self._user("owner_b")
        self.user_none = self._user("user_none")
        self.user_ambiguous = self._user("user_ambiguous")
        self._membership(self.owner_a, self.org_a, role=Membership.Role.OWNER)
        self._membership(self.member_a, self.org_a, role=Membership.Role.MEMBER)
        self._membership(self.member_a2, self.org_a, role=Membership.Role.MEMBER)
        self._membership(self.owner_b, self.org_b, role=Membership.Role.OWNER)
        self._membership(self.user_ambiguous, self.org_a, role=Membership.Role.MEMBER)
        self._membership(self.user_ambiguous, self.org_b, role=Membership.Role.MEMBER)
        self.cli_a1 = self._cliente(
            self.owner_a,
            nome="Cliente A1",
            email="a1@ex.test",
            organization=self.org_a,
            status="ativo",
        )
        self.cli_a2 = self._cliente(
            self.member_a,
            nome="Cliente A2",
            email="a2@ex.test",
            organization=self.org_a,
            status="em_prospeccao",
        )
        self.cli_b1 = self._cliente(
            self.owner_b,
            nome="Cliente B1 UnicoXYZ",
            email="b1@ex.test",
            organization=self.org_b,
            status="ativo",
        )
        self.cli_n1 = self._cliente(
            self.user_none,
            nome="Cliente N1",
            email="n1@ex.test",
            organization=None,
        )

    def _user(self, username):
        return User.objects.create_user(username=username, password="senha123")

    def _membership(self, user, org, *, role=Membership.Role.OWNER):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=role,
            status=Membership.Status.ACTIVE,
        )

    def _cliente(self, user, *, nome, email, organization=None, status="em_prospeccao", **kwargs):
        return Cliente.objects.create(
            user=user,
            nome=nome,
            email=email,
            organization=organization,
            status=status,
            **kwargs,
        )

    def _lista(self, user, params=None):
        self.client.force_login(user)
        return self.client.get(reverse("clientes"), params or {})

    def _get_view(self, user, params=None, *, organization=None, context=None, omit_tenant=False):
        request = RequestFactory().get(reverse("clientes"), params or {})
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        return clientes_view(request)

    def _nomes(self, resp):
        return [c.nome for c in resp.context["clientes"]]

    def test_01_owner_a_lista_org_a(self):
        resp = self._lista(self.owner_a)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")
        self.assertContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
        self.assertNotContains(resp, "Cliente N1")
        self.assertEqual(resp.context["total"], 2)

    def test_02_member_a_lista_org_a(self):
        resp = self._lista(self.member_a)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")
        self.assertContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
        self.assertNotContains(resp, "Cliente N1")

    def test_03_member_a2_sem_cliente_proprio_lista_org_a(self):
        self.assertFalse(Cliente.objects.filter(user=self.member_a2).exists())
        resp = self._lista(self.member_a2)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")
        self.assertContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
        self.assertNotContains(resp, "Cliente N1")

    def test_04_owner_b_lista_org_b(self):
        resp = self._lista(self.owner_b)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente B1 UnicoXYZ")
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente N1")

    def test_05_busca_cross_tenant_nao_vaza(self):
        resp = self._lista(self.owner_a, {"q": "Cliente B1 UnicoXYZ"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._nomes(resp), [])
        self.assertContains(resp, "Nenhum cliente encontrado")
        self.assertNotContains(resp, 'href="/usuarios/cliente/%s"' % self.cli_b1.pk)

    def test_06_busca_same_org_encontra_cliente_de_outro_user(self):
        resp = self._lista(self.owner_a, {"q": "Cliente A2"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")

    def test_07_filtro_status_fica_na_org_a(self):
        resp = self._lista(self.owner_a, {"status": "ativo"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
        self.assertEqual(resp.context["total"], 2)
        self.assertEqual(resp.context["ativos"], 1)
        self.assertEqual(resp.context["prospects"], 1)

    def test_08_filtro_mkt_nao_amplia_tenant(self):
        self.cli_a1.origem = OrigemLead.GOOGLE_ADS
        self.cli_a1.atribuicao_confiavel = True
        self.cli_a1.save(update_fields=["origem", "atribuicao_confiavel"])
        self.cli_b1.origem = OrigemLead.GOOGLE_ADS
        self.cli_b1.atribuicao_confiavel = True
        self.cli_b1.save(update_fields=["origem", "atribuicao_confiavel"])
        self.cli_n1.origem = OrigemLead.GOOGLE_ADS
        self.cli_n1.atribuicao_confiavel = True
        self.cli_n1.save(update_fields=["origem", "atribuicao_confiavel"])
        resp = self._lista(self.owner_a, {"mkt": "sem_acao", "dias": "30"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
        self.assertNotContains(resp, "Cliente N1")
        nomes = self._nomes(resp)
        for nome in nomes:
            self.assertIn(nome, {"Cliente A1", "Cliente A2"})

    def test_09_none_lista_vazia_sem_fallback_user(self):
        resp = self._lista(self.user_none)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Cliente N1")
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertEqual(resp.context["total"], 0)
        self.assertContains(resp, "Nenhum cliente encontrado")

    def test_10_ambiguous_fail_closed(self):
        resp = self._lista(self.user_ambiguous)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
        self.assertEqual(resp.context["total"], 0)

    def test_11_resolved_inconsistente_fail_closed(self):
        resp = self._get_view(
            self.owner_a, organization=None, context=CONTEXT_RESOLVED
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente A2")

    def test_12_none_inconsistente_fail_closed(self):
        resp = self._get_view(
            self.user_none, organization=self.org_a, context=CONTEXT_NONE
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente N1")

    def test_13_context_desconhecido_fail_closed(self):
        resp = self._get_view(
            self.owner_a, organization=self.org_a, context="qualquer_coisa"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "Cliente A1")

    def test_14_context_ausente_fail_closed(self):
        resp = self._get_view(self.owner_a, omit_tenant=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente A2")

    def test_15_frontend_nao_escolhe_org(self):
        resp = self._lista(
            self.owner_a,
            {
                "organization_id": str(self.org_b.pk),
                "organization": str(self.org_b.pk),
                "tenant_id": str(self.org_b.pk),
                "tenant": str(self.org_b.pk),
                "office_id": str(self.org_b.pk),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")
        self.assertContains(resp, "Cliente A2")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")

    def test_16_owner_a_detail_a2_e_full(self):
        lista = self._lista(self.owner_a)
        self.assertContains(lista, "Cliente A2")
        detail = self.client.get(reverse("cliente", kwargs={"id": self.cli_a2.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["detail_access_mode"], "full")

    def test_17_member_a_detail_a1_e_full(self):
        lista = self._lista(self.member_a)
        self.assertContains(lista, "Cliente A1")
        detail = self.client.get(reverse("cliente", kwargs={"id": self.cli_a1.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["detail_access_mode"], "full")

    def test_18_proprio_detail_continua_funcionando(self):
        self.client.force_login(self.owner_a)
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": self.cli_a1.pk})).status_code,
            200,
        )
        self.client.force_login(self.member_a)
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": self.cli_a2.pk})).status_code,
            200,
        )

    def test_19_dual_write_resolved_continua(self):
        self.client.force_login(self.owner_a)
        resp = self.client.post(
            reverse("clientes"),
            {
                "nome": "Cliente Novo A",
                "email": "novo.a@ex.test",
                "tipo": "PF",
                "status": "em_prospeccao",
            },
        )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(email="novo.a@ex.test")
        self.assertEqual(cli.user_id, self.owner_a.pk)
        self.assertEqual(cli.organization_id, self.org_a.pk)

    def test_20_none_create_continua_e_nao_aparece_na_lista(self):
        self.client.force_login(self.user_none)
        resp = self.client.post(
            reverse("clientes"),
            {
                "nome": "Cliente None Novo",
                "email": "none.novo@ex.test",
                "tipo": "PF",
                "status": "em_prospeccao",
            },
        )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(email="none.novo@ex.test")
        self.assertEqual(cli.user_id, self.user_none.pk)
        self.assertIsNone(cli.organization_id)
        lista = self.client.get(reverse("clientes"))
        self.assertEqual(lista.status_code, 200)
        self.assertNotContains(lista, "Cliente None Novo")
        self.assertNotContains(lista, "Cliente N1")

    def test_21_ambiguous_create_continua_bloqueado(self):
        self.client.force_login(self.user_ambiguous)
        antes = Cliente.objects.count()
        resp = self.client.post(
            reverse("clientes"),
            {
                "nome": "Nao Deve 6E1",
                "email": "amb.6e1@ex.test",
                "tipo": "PF",
                "status": "em_prospeccao",
            },
            follow=True,
        )
        self.assertEqual(Cliente.objects.count(), antes)
        self.assertFalse(Cliente.objects.filter(email="amb.6e1@ex.test").exists())
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)

    def test_22_cliente_null_nao_entra_em_nenhuma_org(self):
        for user in (self.owner_a, self.member_a, self.owner_b):
            resp = self._lista(user)
            self.assertNotContains(resp, "Cliente N1")

    def test_23_organization_governa_listagem(self):
        inconsistente = self._cliente(
            self.owner_b,
            nome="Cliente Inconsistente OrgA",
            email="inc.orga@ex.test",
            organization=self.org_a,
        )
        lista_a = self._lista(self.owner_a)
        self.assertContains(lista_a, "Cliente Inconsistente OrgA")
        lista_m = self._lista(self.member_a)
        self.assertContains(lista_m, "Cliente Inconsistente OrgA")
        lista_b = self._lista(self.owner_b)
        self.assertNotContains(lista_b, "Cliente Inconsistente OrgA")
        self.client.force_login(self.owner_b)
        self.assertEqual(
            self.client.get(reverse("cliente", kwargs={"id": inconsistente.pk})).status_code,
            404,
        )
        self.client.force_login(self.owner_a)
        det_a = self.client.get(reverse("cliente", kwargs={"id": inconsistente.pk}))
        self.assertEqual(det_a.status_code, 200)
        self.assertEqual(det_a.context["detail_access_mode"], "full")

    def test_24_sem_or_de_compatibilidade_user(self):
        resp = self._lista(self.user_none)
        self.assertNotContains(resp, "Cliente N1")
        self.assertEqual(resp.context["total"], 0)

    def test_25_queryset_nao_e_global(self):
        helper_src = inspect.getsource(_queryset_base_listagem_clientes)
        view_src = inspect.getsource(clientes_view)
        self.assertNotIn("Cliente.objects.all()", helper_src)
        self.assertNotIn("Cliente.objects.all()", view_src)
        self.assertNotIn("Q(user=", helper_src)
        self.assertNotIn("Q(organization=", helper_src)
        self.assertIn("Cliente.objects.filter(organization=organization)", helper_src)
        self.assertIn("_queryset_base_listagem_clientes", view_src)
        self.assertNotIn("base = Cliente.objects.filter(user=request.user)", view_src)
        resp = self._lista(self.owner_a, {"q": "Cliente"})
        nomes = self._nomes(resp)
        self.assertIn("Cliente A1", nomes)
        self.assertIn("Cliente A2", nomes)
        self.assertNotIn("Cliente B1 UnicoXYZ", nomes)
        self.assertNotIn("Cliente N1", nomes)

    def test_get_nao_chama_resolver_paralelo(self):
        with patch(
            "organizacoes.services.resolver_organization",
            side_effect=AssertionError("view nao deve resolver tenant"),
        ):
            resp = self._get_view(
                self.owner_a,
                organization=self.org_a,
                context=CONTEXT_RESOLVED,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")

    def test_ambiguous_constante_nao_lista(self):
        resp = self._get_view(
            self.user_ambiguous,
            organization=None,
            context=CONTEXT_AMBIGUOUS,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)
        self.assertNotContains(resp, "Cliente A1")
        self.assertNotContains(resp, "Cliente B1 UnicoXYZ")
