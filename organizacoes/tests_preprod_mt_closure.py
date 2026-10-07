"""PREPROD-MT-CLOSURE-01 — testes adversariais A1/A2/B1."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.goals import meta_do_ano, salvar_meta
from comercial.tests_helpers import grant_comercial_permissions
from marketing.models import ContentIdea, ContentItem
from marketing.tests_helpers import grant_marketing_permissions
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente, Compromisso
from usuarios.permissions import pode_ver_agenda
from usuarios.tests_helpers import grant_agenda_permissions


MARKER_A = "PREPROD_ORG_A_7F3K"
MARKER_B = "PREPROD_ORG_B_9Q2M"


class PreprodMtClosureTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Preprod Org A")
        self.org_b = Organization.objects.create(name="Preprod Org B")
        self.a1 = User.objects.create_user("preprod_a1", password="senha123")
        self.a2 = User.objects.create_user("preprod_a2", password="senha123")
        self.b1 = User.objects.create_user("preprod_b1", password="senha123")
        self.sem_perm = User.objects.create_user("preprod_sem", password="senha123")
        self.zero_m = User.objects.create_user("preprod_zero", password="senha123")
        self.amb = User.objects.create_user("preprod_amb", password="senha123")
        self.hoje = timezone.localdate()
        self.http = Client()

        for user, org in (
            (self.a1, self.org_a),
            (self.a2, self.org_a),
            (self.sem_perm, self.org_a),
            (self.b1, self.org_b),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=Membership.Role.MEMBER,
                status=Membership.Status.ACTIVE,
            )
        Membership.objects.create(
            user=self.amb,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.amb,
            organization=self.org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )

        grant_agenda_permissions(self.a1)
        grant_agenda_permissions(self.a2)
        grant_agenda_permissions(self.b1)
        grant_comercial_permissions(self.a1)
        grant_comercial_permissions(self.a2)
        grant_comercial_permissions(self.b1)
        grant_marketing_permissions(self.a1)
        grant_marketing_permissions(self.a2)
        grant_marketing_permissions(self.b1)

        grupo = Group.objects.create(name="Preprod RBAC")
        for user in (self.a1, self.a2, self.b1, self.sem_perm):
            user.groups.add(grupo)

        self.cli_a = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome=f"Cliente {MARKER_A}",
            email="a1@preprod.test",
            status="em_prospeccao",
            relatorio_prospeccao="META_SECRET_A",
        )
        self.cli_b = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome=f"Cliente {MARKER_B}",
            email="b1@preprod.test",
        )
        self.comp_a = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo=f"Compromisso {MARKER_A}",
            data_hora=timezone.now() + timedelta(hours=2),
            cliente=self.cli_a,
        )
        salvar_meta(
            self.a1,
            ator=self.a1,
            ano=self.hoje.year,
            meta_anual=Decimal("120000"),
            meta_mensal=Decimal("10000"),
            ticket_medio=Decimal("5000"),
            ticket_medio_manual=True,
            vigencia_inicio=self.hoje.replace(month=1, day=1),
            vigencia_fim=self.hoje.replace(month=12, day=31),
            organization=self.org_a,
        )
        self.ideia_a = ContentIdea.objects.create(
            usuario=self.a1,
            organization=self.org_a,
            titulo=f"Ideia {MARKER_A}",
        )
        self.item_a = ContentItem.objects.create(
            usuario=self.a1,
            organization=self.org_a,
            titulo=f"Conteudo {MARKER_A}",
            tema="tema",
            canal="blog",
        )

    def test_a1_a2_mesma_meta_organizacional(self):
        meta_a1 = meta_do_ano(self.a1, self.hoje.year, organization=self.org_a)
        meta_a2 = meta_do_ano(self.a2, self.hoje.year, organization=self.org_a)
        self.assertIsNotNone(meta_a1)
        self.assertEqual(meta_a1.pk, meta_a2.pk)
        self.assertEqual(meta_a1.meta_anual, Decimal("120000.00"))

    def test_b1_nao_ve_meta_a(self):
        self.assertIsNone(meta_do_ano(self.b1, self.hoje.year, organization=self.org_b))
        self.assertEqual(
            MetaComercial.objects.filter(organization=self.org_b).count(), 0
        )

    def test_a2_opera_cliente_360(self):
        self.http.force_login(self.a2)
        resp = self.http.get(reverse("cliente", kwargs={"id": self.cli_a.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MARKER_A)
        self.assertContains(resp, "META_SECRET_A")
        post = self.http.post(
            reverse("cliente", kwargs={"id": self.cli_a.pk}),
            {
                "action": "atualizar_prospeccao",
                "fase_funil": "proposta_enviada",
                "relatorio_prospeccao": "A2_EDIT",
            },
        )
        self.assertEqual(post.status_code, 302)
        self.cli_a.refresh_from_db()
        self.assertEqual(self.cli_a.relatorio_prospeccao, "A2_EDIT")

    def test_b1_cliente_a_404(self):
        self.http.force_login(self.b1)
        resp = self.http.get(reverse("cliente", kwargs={"id": self.cli_a.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_a2_ve_compromisso_organizacional(self):
        self.http.force_login(self.a2)
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MARKER_A)
        self.assertNotContains(resp, MARKER_B)

    def test_b1_nao_ve_compromisso_a(self):
        self.http.force_login(self.b1)
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, MARKER_A)

    def test_a2_acessa_conteudo_a1(self):
        self.http.force_login(self.a2)
        resp = self.http.get(
            reverse("marketing_conteudo_editar", kwargs={"pk": self.item_a.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MARKER_A)

    def test_b1_conteudo_a_404(self):
        self.http.force_login(self.b1)
        resp = self.http.get(
            reverse("marketing_conteudo_editar", kwargs={"pk": self.item_a.pk})
        )
        self.assertEqual(resp.status_code, 404)

    def test_same_org_sem_capability_agenda(self):
        self.assertFalse(pode_ver_agenda(self.sem_perm))
        self.http.force_login(self.sem_perm)
        resp = self.http.get(reverse("agenda"))
        self.assertEqual(resp.status_code, 302)

    def test_invalid_tenant_zero_membership(self):
        self.http.force_login(self.zero_m)
        resp = self.http.get(reverse("cliente", kwargs={"id": self.cli_a.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_invalid_tenant_ambiguous(self):
        self.http.force_login(self.amb)
        resp = self.http.get(reverse("cliente", kwargs={"id": self.cli_a.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_null_ownership_invisivel(self):
        nulo = Cliente.objects.create(
            user=self.a1,
            organization=None,
            nome="Cliente NULL",
            email="nulo@preprod.test",
        )
        self.http.force_login(self.a1)
        resp = self.http.get(reverse("cliente", kwargs={"id": nulo.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_home_a1_a2_sem_marker_b(self):
        self.http.force_login(self.a1)
        r1 = self.http.get(reverse("home"))
        self.http.force_login(self.a2)
        r2 = self.http.get(reverse("home"))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertNotContains(r1, MARKER_B)
        self.assertNotContains(r2, MARKER_B)
        self.assertNotContains(r1, "ADV Growth Score")
        self.assertNotContains(r2, "ADV Growth Score")
