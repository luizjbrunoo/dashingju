"""PREPROD-RBAC-PROVISIONING-01 — provisioning determinístico A1/A2/B1."""

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models.signals import post_save
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from io import StringIO

from comercial.permissions import pode_ver_dashboard
from comercial.tests_helpers import grant_comercial_permissions
from core.services.executive_home import montar_home_executiva, resolver_caps_executivas
from financeiro.permissions import pode_ver_cobrancas
from marketing.permissions import pode_ver_marketing
from organizacoes.management.commands.provision_demo_tenant import (
    ORGANIZATION_NAME,
    RESULT_ALREADY,
    RESULT_PROVISIONED,
    TARGET_USER_ID,
)
from organizacoes.models import Membership, Organization
from organizacoes.role_capabilities import (
    GRUPO_COMERCIAL,
    GRUPO_DOCUMENTOS,
    GRUPO_FINANCEIRO,
    GRUPO_MARKETING,
    MANAGED_GROUPS,
    OWNER_MODULE_GROUPS,
    ProvisioningError,
    UnmappedRoleError,
    create_organization_with_owner,
    ensure_module_rbac_groups,
    grant_capabilities_for_membership,
    grant_owner_module_capabilities,
    groups_for_role,
    permission_for,
)
from usuarios.models import Cliente, Documentos
from usuarios.permissions import pode_baixar_documento, pode_ver_agenda
from usuarios.signals import post_save_documentos


class PreprodRbacProvisioningTests(TestCase):
    def setUp(self):
        post_save.disconnect(post_save_documentos, sender=Documentos)
        self.a1 = User.objects.create_user("rbac_a1", password="senha123")
        self.a2 = User.objects.create_user("rbac_a2", password="senha123")
        self.b1 = User.objects.create_user("rbac_b1", password="senha123")
        self.zero = User.objects.create_user("rbac_zero", password="senha123")
        self.amb = User.objects.create_user("rbac_amb", password="senha123")
        self.org_a, self.mem_a1, self.granted_a = create_organization_with_owner(
            name="RBAC Org A", user=self.a1
        )
        self.org_b, self.mem_b1, _ = create_organization_with_owner(
            name="RBAC Org B", user=self.b1
        )
        self.mem_a2 = Membership.objects.create(
            user=self.a2,
            organization=self.org_a,
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
        grant_capabilities_for_membership(self.mem_a2)
        self.cli_a = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente A MARKER_RBAC_A",
            email="rbac.a@ex.test",
        )
        self.cli_b = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente B MARKER_RBAC_B",
            email="rbac.b@ex.test",
        )
        self.doc_a = Documentos.objects.create(
            cliente=self.cli_a,
            tipo="P",
            arquivo=SimpleUploadedFile("rbac-a.txt", b"segredo-a"),
            data_upload=timezone.now(),
            content="segredo-a",
        )

    def tearDown(self):
        post_save.connect(post_save_documentos, sender=Documentos)

    def _nav(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("clientes"))

    def test_01_group_inexistente_criado(self):
        Group.objects.filter(name=GRUPO_COMERCIAL).delete()
        groups = ensure_module_rbac_groups()
        self.assertIn(GRUPO_COMERCIAL, groups)
        self.assertTrue(
            groups[GRUPO_COMERCIAL].permissions.filter(codename="view_dashboard").exists()
        )

    def test_02_group_vazio_reparado(self):
        grupo, _ = Group.objects.get_or_create(name=GRUPO_FINANCEIRO)
        grupo.permissions.clear()
        self.assertFalse(grupo.permissions.exists())
        ensure_module_rbac_groups()
        grupo.refresh_from_db()
        self.assertTrue(grupo.permissions.filter(codename="view_cobrancas").exists())
        self.assertTrue(grupo.permissions.filter(codename="view_caixa").exists())

    def test_03_group_parcial_adiciona_required(self):
        ensure_module_rbac_groups()
        grupo = Group.objects.get(name=GRUPO_MARKETING)
        extra_ct = ContentType.objects.get(app_label="auth", model="user")
        extra, _ = Permission.objects.get_or_create(
            content_type=extra_ct,
            codename="view_user",
            defaults={"name": "Can view user"},
        )
        grupo.permissions.add(extra)
        view_mkt = grupo.permissions.get(codename="view_marketing")
        grupo.permissions.remove(view_mkt)
        ensure_module_rbac_groups()
        grupo.refresh_from_db()
        self.assertTrue(grupo.permissions.filter(codename="view_marketing").exists())
        self.assertTrue(grupo.permissions.filter(pk=extra.pk).exists())

    def test_04_idempotente(self):
        first = ensure_module_rbac_groups()
        n1 = first[GRUPO_COMERCIAL].permissions.count()
        ids1 = set(self.a1.groups.values_list("pk", flat=True))
        grant_owner_module_capabilities(self.a1)
        second = ensure_module_rbac_groups()
        n2 = second[GRUPO_COMERCIAL].permissions.count()
        ids2 = set(self.a1.groups.values_list("pk", flat=True))
        self.assertEqual(n1, n2)
        self.assertEqual(ids1, ids2)
        self.assertEqual(Group.objects.filter(name=GRUPO_COMERCIAL).count(), 1)

    def test_05_nova_organization_owner(self):
        novo = User.objects.create_user("rbac_new_owner", password="senha123")
        org, mem, granted = create_organization_with_owner(
            name="Org NEW Independente", user=novo
        )
        self.assertNotEqual(org.name, ORGANIZATION_NAME)
        self.assertEqual(mem.role, Membership.Role.OWNER)
        self.assertFalse(novo.is_superuser)
        self.assertFalse(novo.is_staff)
        self.assertIn(GRUPO_COMERCIAL, granted)
        self.assertIn(GRUPO_DOCUMENTOS, granted)
        self.assertTrue(pode_ver_dashboard(novo))
        self.assertTrue(pode_ver_marketing(novo))
        self.assertTrue(pode_ver_cobrancas(novo))
        self.assertTrue(pode_ver_agenda(novo))
        self.assertTrue(pode_baixar_documento(novo))

    def test_06_owner_nao_e_superuser(self):
        self.assertFalse(self.a1.is_superuser)
        self.assertFalse(self.a1.is_staff)
        self.assertTrue(pode_ver_dashboard(self.a1))

    def test_07_owner_nao_depende_fail_open(self):
        self.assertTrue(self.a1.groups.filter(name=GRUPO_COMERCIAL).exists())
        self.assertEqual(groups_for_role(Membership.Role.MEMBER), ())
        self.assertEqual(groups_for_role(Membership.Role.OWNER), OWNER_MODULE_GROUPS)
        self.assertFalse(self.a2.groups.filter(name=GRUPO_COMERCIAL).exists())

    def test_08_novo_membro_sem_capabilities(self):
        granted = grant_capabilities_for_membership(self.mem_a2)
        self.assertEqual(granted, [])
        self.assertFalse(pode_ver_dashboard(self.a2))
        self.assertFalse(pode_ver_marketing(self.a2))
        self.assertFalse(pode_ver_cobrancas(self.a2))
        self.assertFalse(pode_ver_agenda(self.a2))
        self.assertFalse(pode_baixar_documento(self.a2))

    def test_09_membership_nao_e_autorizacao(self):
        self.assertEqual(self.mem_a2.status, Membership.Status.ACTIVE)
        self.assertEqual(self.mem_a2.organization_id, self.org_a.pk)
        self.assertFalse(pode_ver_dashboard(self.a2))

    def test_10_capability_sem_membership_nao_acessa_tenant(self):
        grant_owner_module_capabilities(self.zero)
        self.assertTrue(pode_ver_dashboard(self.zero))
        self.client.force_login(self.zero)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("MARKER_RBAC_A", resp.content.decode(errors="ignore"))
        self.assertNotIn("MARKER_RBAC_B", resp.content.decode(errors="ignore"))
        clientes = self.client.get(reverse("clientes"))
        self.assertEqual(clientes.status_code, 200)
        self.assertNotContains(clientes, "MARKER_RBAC_A")

    def test_11_a1_modulos_visiveis(self):
        resp = self._nav(self.a1)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("clientes"))
        self.assertContains(resp, reverse("financeiro_dashboard"))
        self.assertContains(resp, reverse("agenda"))
        self.assertContains(resp, reverse("comercial_dashboard"))
        self.assertContains(resp, reverse("marketing_dashboard"))

    def test_12_a1_urls_permitidas(self):
        self.client.force_login(self.a1)
        self.assertEqual(self.client.get(reverse("clientes")).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("agenda")).status_code, 200)
        self.assertEqual(self.client.get(reverse("comercial_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("marketing_dashboard")).status_code, 200)

    def test_13_a2_menus_protegidos_ocultos(self):
        resp = self._nav(self.a2)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("clientes"))
        self.assertNotContains(resp, reverse("financeiro_dashboard"))
        self.assertNotContains(resp, reverse("comercial_dashboard"))
        self.assertNotContains(resp, reverse("marketing_dashboard"))

    def test_14_a2_urls_protegidas_negadas(self):
        self.client.force_login(self.a2)
        fin = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(fin.status_code, 302)
        self.assertEqual(fin.url, reverse("home"))
        agenda = self.client.get(reverse("agenda"))
        self.assertEqual(agenda.status_code, 302)
        self.assertEqual(agenda.url, reverse("clientes"))
        com = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(com.status_code, 302)
        self.assertEqual(com.url, reverse("clientes"))
        mkt = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(mkt.status_code, 302)
        self.assertEqual(mkt.url, reverse("clientes"))

    def test_15_a2_capability_especifica_libera_comercial_na_org_a(self):
        grant_comercial_permissions(self.a2, "view_dashboard")
        self.a2 = User.objects.get(pk=self.a2.pk)
        self.assertTrue(pode_ver_dashboard(self.a2))
        self.assertFalse(pode_ver_marketing(self.a2))
        self.client.force_login(self.a2)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("MARKER_RBAC_B", resp.content.decode(errors="ignore"))
        mkt = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(mkt.status_code, 302)

    def test_16_b1_owner_modulos_na_org_b(self):
        self.client.force_login(self.b1)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("MARKER_RBAC_A", resp.content.decode(errors="ignore"))

    def test_17_b1_nao_acessa_dados_a(self):
        self.client.force_login(self.b1)
        resp = self.client.get(reverse("cliente", kwargs={"id": self.cli_a.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_18_a1_nao_acessa_dados_b(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("cliente", kwargs={"id": self.cli_b.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_19_documento_a1_autorizado(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("documento_download", args=[self.doc_a.pk]))
        self.assertEqual(resp.status_code, 200)
        payload = b"".join(resp.streaming_content) if resp.streaming else resp.content
        self.assertEqual(payload, b"segredo-a")

    def test_20_documento_a2_sem_capability(self):
        self.client.force_login(self.a2)
        resp = self.client.get(reverse("documento_download", args=[self.doc_a.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_21_documento_b1_404(self):
        self.client.force_login(self.b1)
        resp = self.client.get(reverse("documento_download", args=[self.doc_a.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("segredo-a", resp.content.decode(errors="ignore"))

    def test_22_home_capability_first(self):
        caps_a1 = resolver_caps_executivas(self.a1)
        caps_a2 = resolver_caps_executivas(self.a2)
        self.assertTrue(caps_a1.comercial)
        self.assertTrue(caps_a1.marketing)
        self.assertTrue(caps_a1.financeiro)
        self.assertTrue(caps_a1.agenda)
        self.assertFalse(caps_a2.comercial)
        self.assertFalse(caps_a2.marketing)
        self.assertFalse(caps_a2.financeiro)
        self.assertFalse(caps_a2.agenda)
        home_a1 = montar_home_executiva(self.org_a, user=self.a1)
        home_a2 = montar_home_executiva(self.org_a, user=self.a2)
        from core.services.executive_home import DOMAIN_COMERCIAL, DOMAIN_MARKETING

        domains_a1 = {c.domain for c in home_a1.snapshot}
        domains_a2 = {c.domain for c in home_a2.snapshot}
        self.assertIn(DOMAIN_COMERCIAL, domains_a1)
        self.assertNotIn(DOMAIN_COMERCIAL, domains_a2)
        self.assertNotIn(DOMAIN_MARKETING, domains_a2)
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, reverse("comercial_dashboard"))
        self.client.force_login(self.a2)
        resp2 = self.client.get(reverse("home"))
        self.assertNotContains(resp2, reverse("comercial_dashboard"))

    def test_23_advisor_capability_first(self):
        self.client.force_login(self.a1)
        resp_a1 = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp_a1.status_code, 200)
        self.client.force_login(self.a2)
        resp_a2 = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp_a2.status_code, 302)
        self.assertNotIn("MARKER_RBAC_A", resp_a2.content.decode(errors="ignore"))
        self.client.force_login(self.b1)
        resp_b = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp_b.status_code, 200)
        self.assertNotIn("MARKER_RBAC_A", resp_b.content.decode(errors="ignore"))

    def test_24_catalogo_canonico_cobre_managed_groups(self):
        for name, perms in MANAGED_GROUPS.items():
            self.assertTrue(perms, name)
            for perm_string in perms:
                permission_for(perm_string)

    def test_25_permission_ausente_nao_silenciosa(self):
        with self.assertRaises(ProvisioningError) as ctx:
            permission_for("marketing.perm_que_nao_existe")
        self.assertIn("PERMISSION_NOT_FOUND", str(ctx.exception))

    def test_26_role_nao_mapeada_fail_closed(self):
        with self.assertRaises(UnmappedRoleError) as ctx:
            groups_for_role("admin")
        self.assertIn("ROLE_UNMAPPED", str(ctx.exception))

    def test_27_tenant_ambiguo(self):
        grant_owner_module_capabilities(self.amb)
        self.client.force_login(self.amb)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode(errors="ignore")
        self.assertNotIn("MARKER_RBAC_A", body)
        self.assertNotIn("MARKER_RBAC_B", body)


class DemoTenantReusableProvisioningTests(TestCase):
    def test_demo_provisioning_idempotente(self):
        demo = User.objects.create_user(
            username="demo_user_1",
            password="senha123",
            id=TARGET_USER_ID,
            is_active=True,
        )
        out1 = StringIO()
        call_command("provision_demo_tenant", "--apply", stdout=out1)
        self.assertIn(RESULT_PROVISIONED, out1.getvalue())
        out2 = StringIO()
        call_command("provision_demo_tenant", "--apply", stdout=out2)
        self.assertIn(RESULT_ALREADY, out2.getvalue())
        demo.refresh_from_db()
        self.assertIn("comercial.view_dashboard", demo.get_all_permissions())
        self.assertIn("usuarios.view_documentos", demo.get_all_permissions())
        self.assertEqual(Organization.objects.filter(name=ORGANIZATION_NAME).count(), 1)
        self.assertFalse(demo.is_superuser)
        self.assertFalse(demo.is_staff)
