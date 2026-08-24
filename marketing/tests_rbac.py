"""Testes Fase 13 — RBAC Marketing."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse

from usuarios.choices import OrigemLead
from usuarios.models import Cliente

User = get_user_model()


def _perm_marketing(codename: str) -> Permission:
    ct = ContentType.objects.get(app_label="marketing", model="marketingintegracao")
    return Permission.objects.get(content_type=ct, codename=codename)


def _grupo_restrito(nome: str, *codenames: str) -> Group:
    grupo, _ = Group.objects.get_or_create(name=nome)
    grupo.permissions.clear()
    if codenames:
        grupo.permissions.set([_perm_marketing(c).pk for c in codenames])
    return grupo


class MarketingRbacTests(TestCase):
    def setUp(self):
        self.http = Client()
        self.legado = User.objects.create_user(username="mkt_legado", password="senha123")
        self.restrito = User.objects.create_user(username="mkt_restrito", password="senha123")
        self.grupo_vazio = _grupo_restrito("Mkt RBAC — vazio")
        self.restrito.groups.add(self.grupo_vazio)

    def test_legado_acessa_dashboard_google_ads(self):
        self.http.login(username="mkt_legado", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_grupo_sem_perm_redireciona_clientes(self):
        self.http.login(username="mkt_restrito", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("clientes"))

    def test_grupo_com_view_marketing_acessa_painel(self):
        user = User.objects.create_user(username="mkt_ads", password="senha123")
        grupo = _grupo_restrito("Mkt RBAC — ads", "view_marketing")
        user.groups.add(grupo)
        self.http.login(username="mkt_ads", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_sem_view_resultados_oculta_secao_negocio(self):
        user = User.objects.create_user(username="mkt_sem_analytics", password="senha123")
        grupo = _grupo_restrito(
            "Mkt RBAC — sem analytics",
            "view_marketing",
        )
        user.groups.add(grupo)
        Cliente.objects.create(
            user=user,
            nome="Lead",
            email="lead@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        self.http.login(username="mkt_sem_analytics", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="resultados-negocio-titulo"')
        self.assertNotContains(resp, "Leads Google Ads por fase do funil")

    def test_com_view_resultados_exibe_secao_negocio(self):
        user = User.objects.create_user(username="mkt_com_analytics", password="senha123")
        grupo = _grupo_restrito(
            "Mkt RBAC — com analytics",
            "view_marketing",
            "view_resultados_marketing",
        )
        user.groups.add(grupo)
        self.http.login(username="mkt_com_analytics", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="resultados-negocio-titulo"')

    def test_conteudo_leitura_sem_edit_bloqueia_criar(self):
        user = User.objects.create_user(username="mkt_leitura", password="senha123")
        grupo = _grupo_restrito("Mkt RBAC — leitura conteudo", "view_conteudo_marketing")
        user.groups.add(grupo)
        self.http.login(username="mkt_leitura", password="senha123")
        self.assertEqual(self.http.get(reverse("marketing_conteudo_biblioteca")).status_code, 200)
        resp = self.http.get(reverse("marketing_conteudo_criar"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("clientes"))

    def test_conteudo_edit_acessa_criar(self):
        user = User.objects.create_user(username="mkt_editor", password="senha123")
        grupo = _grupo_restrito(
            "Mkt RBAC — editor",
            "view_conteudo_marketing",
            "edit_conteudo_marketing",
        )
        user.groups.add(grupo)
        self.http.login(username="mkt_editor", password="senha123")
        self.assertEqual(self.http.get(reverse("marketing_conteudo_criar")).status_code, 200)

    def test_apenas_conteudo_nao_acessa_google_ads(self):
        user = User.objects.create_user(username="mkt_so_conteudo", password="senha123")
        grupo = _grupo_restrito(
            "Mkt RBAC — so conteudo",
            "view_conteudo_marketing",
            "edit_conteudo_marketing",
        )
        user.groups.add(grupo)
        self.http.login(username="mkt_so_conteudo", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("clientes"))
