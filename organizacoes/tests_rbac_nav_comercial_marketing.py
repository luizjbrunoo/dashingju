"""Regressão: Comercial/Marketing visíveis só com capability explícita."""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from comercial.permissions import pode_ver_dashboard
from marketing.permissions import pode_ver_conteudo_marketing, pode_ver_marketing
from organizacoes.models import Membership, Organization
from organizacoes.role_capabilities import (
    GRUPO_COMERCIAL,
    GRUPO_MARKETING,
    grant_owner_module_capabilities,
)
from usuarios.models import Cliente


class ComercialMarketingNavRbacTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Nav Org A")
        self.org_b = Organization.objects.create(name="Nav Org B")
        self.a1 = User.objects.create_user("nav_a1", password="senha123")
        self.a2 = User.objects.create_user("nav_a2", password="senha123")
        self.b1 = User.objects.create_user("nav_b1", password="senha123")
        self.zero = User.objects.create_user("nav_zero", password="senha123")
        self.amb = User.objects.create_user("nav_amb", password="senha123")
        Membership.objects.create(
            user=self.a1,
            organization=self.org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.a2,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.b1,
            organization=self.org_b,
            role=Membership.Role.OWNER,
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
        grant_owner_module_capabilities(self.a1)
        grant_owner_module_capabilities(self.b1)
        Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente A MARKER_COM_A",
            email="nav.a@ex.test",
        )
        Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente B MARKER_COM_B",
            email="nav.b@ex.test",
        )

    def _nav(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("clientes"))

    def test_a1_autorizado_menu_e_rotas(self):
        self.assertTrue(pode_ver_dashboard(self.a1))
        self.assertTrue(pode_ver_marketing(self.a1))
        self.assertTrue(pode_ver_conteudo_marketing(self.a1))
        resp = self._nav(self.a1)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("comercial_dashboard"))
        self.assertContains(resp, reverse("marketing_dashboard"))
        self.assertContains(resp, ">Comercial<")
        self.assertContains(resp, ">Marketing<")
        dash_c = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(dash_c.status_code, 200)
        dash_m = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(dash_m.status_code, 200)
        home = self.client.get(reverse("home"))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, reverse("comercial_dashboard"))
        self.assertContains(home, reverse("marketing_dashboard"))

    def test_a2_same_org_sem_capability(self):
        self.assertFalse(pode_ver_dashboard(self.a2))
        self.assertFalse(pode_ver_marketing(self.a2))
        resp = self._nav(self.a2)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("comercial_dashboard"))
        self.assertNotContains(resp, reverse("marketing_dashboard"))
        self.assertNotContains(resp, ">Comercial<")
        self.assertNotContains(resp, ">Marketing<")
        com = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(com.status_code, 302)
        self.assertEqual(com.url, reverse("clientes"))
        mkt = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(mkt.status_code, 302)
        self.assertEqual(mkt.url, reverse("clientes"))
        home = self.client.get(reverse("home"))
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, reverse("comercial_dashboard"))
        self.assertNotContains(home, reverse("marketing_dashboard"))

    def test_membership_nao_e_capability(self):
        grupo_vazio, _ = Group.objects.get_or_create(name="Nav Group vazio")
        self.a2.groups.add(grupo_vazio)
        self.a2.refresh_from_db()
        self.assertFalse(pode_ver_dashboard(self.a2))
        self.assertFalse(pode_ver_marketing(self.a2))

    def test_b1_cross_org_isolado(self):
        self.client.force_login(self.b1)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode(errors="ignore")
        self.assertNotIn("MARKER_COM_A", body)
        self.client.force_login(self.a1)
        resp_a = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp_a.status_code, 200)
        self.assertNotIn("MARKER_COM_B", resp_a.content.decode(errors="ignore"))

    def test_sem_tenant_context_fail_closed_dados(self):
        grant_owner_module_capabilities(self.zero)
        self.client.force_login(self.zero)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("MARKER_COM_A", resp.content.decode(errors="ignore"))
        self.assertNotIn("MARKER_COM_B", resp.content.decode(errors="ignore"))

    def test_tenant_ambiguo_fail_closed_dados(self):
        grant_owner_module_capabilities(self.amb)
        self.client.force_login(self.amb)
        resp = self.client.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode(errors="ignore")
        self.assertNotIn("MARKER_COM_A", body)
        self.assertNotIn("MARKER_COM_B", body)

    def test_owner_groups_existem_com_permissions(self):
        grant_owner_module_capabilities(self.a1)
        comercial = Group.objects.get(name=GRUPO_COMERCIAL)
        marketing = Group.objects.get(name=GRUPO_MARKETING)
        self.assertTrue(comercial.permissions.filter(codename="view_dashboard").exists())
        self.assertTrue(marketing.permissions.filter(codename="view_marketing").exists())
        self.assertTrue(self.a1.groups.filter(name=GRUPO_COMERCIAL).exists())
        self.assertTrue(self.a1.groups.filter(name=GRUPO_MARKETING).exists())
        self.assertFalse(self.a2.groups.filter(name=GRUPO_COMERCIAL).exists())
