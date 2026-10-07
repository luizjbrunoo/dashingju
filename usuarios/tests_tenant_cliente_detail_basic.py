from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponseNotFound
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente, Compromisso, Documentos
from usuarios.tests_helpers import grant_agenda_permissions
from usuarios.views import (
    DETAIL_ACCESS_BASIC,
    DETAIL_ACCESS_FULL,
    MSG_TENANT_INDETERMINADO,
    cliente as cliente_view,
)


class ClienteDetailBasicTests(TestCase):
    """6E2A: GET BASIC organizacional; FULL 360 e POST permanecem por User+tenant."""

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
        for u in (self.owner_a, self.member_a, self.member_a2, self.owner_b):
            grant_agenda_permissions(u)
        self.cli_a1 = self._cliente(
            self.owner_a,
            nome="Cliente A1",
            email="a1@ex.test",
            organization=self.org_a,
            status="em_prospeccao",
            relatorio_prospeccao="SEGREDO_TESTE",
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

    def _cliente(self, user, *, nome, email, organization=None, **kwargs):
        return Cliente.objects.create(
            user=user, nome=nome, email=email, organization=organization, **kwargs
        )

    def _detail(self, user, cliente, params=None):
        self.client.force_login(user)
        return self.client.get(
            reverse("cliente", kwargs={"id": cliente.pk}), params or {}
        )

    def _post_detail(self, user, cliente, data=None):
        self.client.force_login(user)
        return self.client.post(
            reverse("cliente", kwargs={"id": cliente.pk}), data or {}
        )

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

    def test_01_owner_proprio_full(self):
        resp = self._detail(self.owner_a, self.cli_a1)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertContains(resp, "Cliente A1")
        self.assertContains(resp, "Chat")

    def test_02_member_proprio_full(self):
        resp = self._detail(self.member_a, self.cli_a2)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)

    def test_03_owner_same_org_full(self):
        resp = self._detail(self.owner_a, self.cli_a2)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertContains(resp, "Cliente A2")

    def test_04_member_same_org_full(self):
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertContains(resp, "Cliente A1")

    def test_05_member_sem_cliente_proprio_full(self):
        self.assertFalse(Cliente.objects.filter(user=self.member_a2).exists())
        r1 = self._detail(self.member_a2, self.cli_a1)
        r2 = self._detail(self.member_a2, self.cli_a2)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.context["detail_access_mode"], DETAIL_ACCESS_FULL)

    def test_06_cross_org_404(self):
        self.assertEqual(self._detail(self.owner_a, self.cli_b1).status_code, 404)
        self.assertEqual(self._detail(self.owner_b, self.cli_a1).status_code, 404)

    def test_07_none_nao_abre_n1(self):
        self.assertEqual(self._detail(self.user_none, self.cli_n1).status_code, 404)

    def test_08_ambiguous_fail_closed(self):
        self.assertEqual(self._detail(self.user_ambiguous, self.cli_a1).status_code, 404)
        self.assertEqual(self._detail(self.user_ambiguous, self.cli_b1).status_code, 404)

    def test_09_resolved_inconsistente_fail_closed(self):
        resp = self._get_view(
            self.owner_a, self.cli_a1, organization=None, context=CONTEXT_RESOLVED
        )
        self.assertEqual(resp.status_code, 404)

    def test_10_none_inconsistente_fail_closed(self):
        resp = self._get_view(
            self.user_none, self.cli_a1, organization=self.org_a, context=CONTEXT_NONE
        )
        self.assertEqual(resp.status_code, 404)

    def test_11_context_desconhecido_fail_closed(self):
        resp = self._get_view(
            self.owner_a, self.cli_a1, organization=self.org_a, context="qualquer_coisa"
        )
        self.assertEqual(resp.status_code, 404)

    def test_12_context_ausente_fail_closed(self):
        resp = self._get_view(self.owner_a, self.cli_a1, omit_tenant=True)
        self.assertEqual(resp.status_code, 404)

    def test_13_frontend_nao_escolhe_org(self):
        resp = self._detail(
            self.owner_a,
            self.cli_a1,
            {
                "organization_id": str(self.org_b.pk),
                "organization": str(self.org_b.pk),
                "tenant_id": str(self.org_b.pk),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertContains(resp, "Cliente A1")
        resp_b = self._detail(
            self.owner_a,
            self.cli_b1,
            {"organization_id": str(self.org_b.pk), "tenant_id": str(self.org_b.pk)},
        )
        self.assertEqual(resp_b.status_code, 404)

    def test_14_same_org_ve_documentos(self):
        Documentos.objects.create(
            cliente=self.cli_a1,
            tipo="P",
            arquivo=SimpleUploadedFile("peca.txt", b"conteudo-peca"),
            data_upload=timezone.now(),
            content="conteudo-peca",
        )
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertIn("documentos", resp.context)

    def test_15_same_org_tem_upload(self):
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertContains(resp, "Adicionar Documento")

    def test_16_same_org_ve_agenda_quando_autorizado(self):
        from datetime import timedelta

        Compromisso.objects.create(
            user=self.owner_a,
            organization=self.org_a,
            titulo="Reuniao Org A1",
            cliente=self.cli_a1,
            data_hora=timezone.now() + timedelta(hours=2),
        )
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertIn("agenda_resumo", resp.context)
        self.assertContains(resp, "Reuniao Org A1")

    def test_17_same_org_financeiro_respeita_capability(self):
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertTrue(resp.context.get("financeiro_oculto", True))

    def test_18_same_org_hook_financeiro_respeita_capability(self):
        from decimal import Decimal

        from financeiro.choices import StatusCobranca
        from financeiro.models import Cobranca

        Cobranca.objects.create(
            usuario=self.owner_a,
            organization=self.org_a,
            cliente=self.cli_a1,
            descricao="Honorarios Owner",
            valor_original=Decimal("9999.00"),
            data_vencimento=timezone.localdate(),
            status=StatusCobranca.OVERDUE,
        )
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertNotContains(resp, "Honorarios Owner")

    def test_19_same_org_tem_ia(self):
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertContains(resp, "Chat")

    def test_20_same_org_ve_relatorio_prospeccao(self):
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertContains(resp, "SEGREDO_TESTE")

    def test_21_same_org_tem_sugestoes_crm(self):
        resp = self._detail(self.member_a, self.cli_a1)
        self.assertIn("sugestoes_crm", resp.context)

    def test_22_post_funil_same_org_permitido(self):
        resp = self._post_detail(
            self.member_a,
            self.cli_a1,
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "ATUALIZADO_A2",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.cli_a1.refresh_from_db()
        self.assertEqual(self.cli_a1.relatorio_prospeccao, "ATUALIZADO_A2")
        self.assertEqual(self.cli_a1.fase_funil, "proposta_enviada")

    def test_23_post_upload_same_org_permitido(self):
        antes = Documentos.objects.filter(cliente=self.cli_a1).count()
        self.client.force_login(self.member_a)
        resp = self.client.post(
            reverse("cliente", kwargs={"id": self.cli_a1.pk}),
            {
                "tipo": "O",
                "data": timezone.localdate().isoformat(),
                "documento": SimpleUploadedFile("x.txt", b"abc"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Documentos.objects.filter(cliente=self.cli_a1).count(), antes + 1)

    def test_24_post_proprio_preservado(self):
        resp = self._post_detail(
            self.owner_a,
            self.cli_a1,
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "Atualizado owner",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.cli_a1.refresh_from_db()
        self.assertEqual(self.cli_a1.fase_funil, "proposta_enviada")
        self.assertEqual(self.cli_a1.relatorio_prospeccao, "Atualizado owner")

    def test_25_upload_proprio_preservado(self):
        self.client.force_login(self.owner_a)
        resp = self.client.post(
            reverse("cliente", kwargs={"id": self.cli_a1.pk}),
            {
                "tipo": "P",
                "data": timezone.localdate().isoformat(),
                "documento": SimpleUploadedFile("peca-owner.txt", b"texto-owner"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Documentos.objects.filter(cliente=self.cli_a1, tipo="P").exists()
        )

    def test_26_post_cross_org(self):
        resp = self._post_detail(
            self.owner_a,
            self.cli_b1,
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "cross",
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.cli_b1.refresh_from_db()
        self.assertNotEqual(self.cli_b1.relatorio_prospeccao, "cross")

    def test_27_post_none(self):
        resp = self._post_detail(
            self.user_none,
            self.cli_n1,
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "none-write",
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.cli_n1.refresh_from_db()
        self.assertNotEqual(self.cli_n1.relatorio_prospeccao, "none-write")

    def test_28_post_ambiguous(self):
        resp = self._post_detail(
            self.user_ambiguous,
            self.cli_a1,
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "amb-write",
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.cli_a1.refresh_from_db()
        self.assertEqual(self.cli_a1.relatorio_prospeccao, "SEGREDO_TESTE")

    def test_29_full_continua_documentos(self):
        Documentos.objects.create(
            cliente=self.cli_a1,
            tipo="C",
            arquivo=SimpleUploadedFile("contrato.txt", b"contrato"),
            data_upload=timezone.now(),
            content="contrato",
        )
        resp = self._detail(self.owner_a, self.cli_a1)
        self.assertEqual(resp.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        self.assertIn("documentos", resp.context)
        self.assertContains(resp, "Documentos Cadastrados")
        self.assertContains(resp, "Contrato")

    def test_30_full_continua_agenda(self):
        from datetime import timedelta

        Compromisso.objects.create(
            user=self.owner_a,
            organization=self.org_a,
            titulo="Reuniao Full A1",
            cliente=self.cli_a1,
            data_hora=timezone.now() + timedelta(hours=3),
        )
        resp = self._detail(self.owner_a, self.cli_a1)
        self.assertContains(resp, "Agenda")
        self.assertContains(resp, "Reuniao Full A1")
        self.assertContains(resp, "Ver na agenda")

    def test_31_full_continua_financeiro(self):
        from decimal import Decimal

        from financeiro.models import Cobranca

        Cobranca.objects.create(
            usuario=self.owner_a,
            organization=self.org_a,
            cliente=self.cli_a1,
            descricao="Honorarios Full",
            valor_original=Decimal("1500.00"),
            data_vencimento=timezone.localdate(),
        )
        from financeiro.tests_helpers import grant_finance_permissions

        grant_finance_permissions(self.owner_a, "view_cobrancas")
        resp = self._detail(self.owner_a, self.cli_a1)
        self.assertContains(resp, "Financeiro")
        self.assertContains(resp, "Honorarios Full")
        self.assertContains(resp, "A receber")

    def test_32_full_continua_chat(self):
        resp = self._detail(self.owner_a, self.cli_a1)
        self.assertContains(resp, "Chat")
        self.assertContains(resp, reverse("chat", kwargs={"id": self.cli_a1.pk}))

    def test_33_organization_governa_get(self):
        x = self._cliente(
            self.owner_b,
            nome="Cliente X Inconsistente",
            email="x.inc@ex.test",
            organization=self.org_a,
        )
        resp_a = self._detail(self.owner_a, x)
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_a.context["detail_access_mode"], DETAIL_ACCESS_FULL)
        resp_b = self._detail(self.owner_b, x)
        self.assertEqual(resp_b.status_code, 404)

    def test_34_full_exige_user_e_org(self):
        x = self._cliente(
            self.owner_b,
            nome="Cliente X Full Negado",
            email="x.full@ex.test",
            organization=self.org_a,
        )
        resp_b = self._detail(self.owner_b, x)
        self.assertEqual(resp_b.status_code, 404)

    def test_35_post_exige_user_e_tenant(self):
        x = self._cliente(
            self.owner_b,
            nome="Cliente X Post Negado",
            email="x.post@ex.test",
            organization=self.org_a,
            status="em_prospeccao",
            relatorio_prospeccao="orig",
        )
        resp = self._post_detail(
            self.owner_b,
            x,
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "nao",
            },
        )
        self.assertEqual(resp.status_code, 404)
        x.refresh_from_db()
        self.assertEqual(x.relatorio_prospeccao, "orig")

    def test_36_same_org_permite_escrita(self):
        resp = self._detail(self.member_a, self.cli_a1)
        html = resp.content.decode("utf-8")
        self.assertIn("atualizar_prospeccao", html)
        self.assertIn("Adicionar Documento", html)
        self.assertIn("Salvar prospecção", html)

    def test_37_sem_or_user_none_nao_abre(self):
        self.assertEqual(self._detail(self.user_none, self.cli_n1).status_code, 404)
        self.assertEqual(self._detail(self.user_none, self.cli_a1).status_code, 404)

    def test_38_listagem_6e1_intacta(self):
        self.client.force_login(self.owner_a)
        lista = self.client.get(reverse("clientes"))
        self.assertContains(lista, "Cliente A1")
        self.assertContains(lista, "Cliente A2")
        self.assertNotContains(lista, "Cliente B1 UnicoXYZ")
        self.assertNotContains(lista, "Cliente N1")
        self.client.force_login(self.owner_b)
        lista_b = self.client.get(reverse("clientes"))
        self.assertContains(lista_b, "Cliente B1 UnicoXYZ")
        self.assertNotContains(lista_b, "Cliente A1")

    def test_get_nao_chama_resolver_paralelo(self):
        with patch(
            "organizacoes.services.resolver_organization",
            side_effect=AssertionError("view nao deve resolver tenant"),
        ):
            resp = self._get_view(
                self.owner_a,
                self.cli_a1,
                organization=self.org_a,
                context=CONTEXT_RESOLVED,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A1")
        self.assertContains(resp, "Chat")

    def test_ambiguous_constante_nao_lista_detail(self):
        resp = self._get_view(
            self.user_ambiguous,
            self.cli_a1,
            organization=None,
            context=CONTEXT_AMBIGUOUS,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertNotContains(resp, MSG_TENANT_INDETERMINADO, status_code=404)
