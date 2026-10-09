"""OAuth Google Ads por Organization. Sem rede Google, sem GAQL."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.runtime_env import resolve_google_ads_oauth_redirect_uri
from django.core.exceptions import ImproperlyConfigured
from marketing.context_processors import marketing_permissoes
from marketing.models import OrganizationGoogleAdsConnection
from marketing.permissions import (
    pode_gerenciar_integracoes_marketing,
    pode_ver_marketing,
)
from marketing.services.google_ads_oauth import (
    MSG_OAUTH_FALHOU,
    SESSION_KEY,
)
from marketing.tests_rbac import _grupo_restrito
from organizacoes.models import Membership, Organization
from organizacoes.provider_credentials import get, has, put
from organizacoes.role_capabilities import create_organization_with_owner

User = get_user_model()

_OAUTH = {
    "GOOGLE_ADS_CLIENT_ID": "cid.apps.googleusercontent.com",
    "GOOGLE_ADS_CLIENT_SECRET": "oauth-client-secret-test",
    "GOOGLE_ADS_OAUTH_REDIRECT_URI": (
        "https://staging.example.com/marketing/google-ads/callback/"
    ),
}


def _fernet_settings():
    cfg = dict(_OAUTH)
    cfg["INTEGRATION_CREDENTIALS_KEY"] = Fernet.generate_key().decode("ascii")
    return cfg


class _FakeCreds:
    def __init__(self, refresh_token="rt-oauth-org-a"):
        self.refresh_token = refresh_token
        self.token = "ya29.access-should-not-persist"


def _mock_flow(refresh_token="rt-oauth-org-a"):
    flow = MagicMock()
    flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?client_id=cid",
        "ignored",
    )
    flow.credentials = _FakeCreds(refresh_token)
    flow.fetch_token.return_value = {}
    return flow


@override_settings(**_fernet_settings())
class GoogleAdsOAuthTests(TestCase):
    def setUp(self):
        self.http_a = Client()
        self.http_b = Client()
        self.user_a = User.objects.create_user("oauth_a", password="senha123")
        self.user_b = User.objects.create_user("oauth_b", password="senha123")
        self.org_a, _, _ = create_organization_with_owner(
            name="OAuth Org A", user=self.user_a
        )
        self.org_b, _, _ = create_organization_with_owner(
            name="OAuth Org B", user=self.user_b
        )
        self.http_a.login(username="oauth_a", password="senha123")
        self.http_b.login(username="oauth_b", password="senha123")

    def _start(self, client):
        flow = _mock_flow()
        with patch(
            "marketing.services.google_ads_oauth.Flow.from_client_config",
            return_value=flow,
        ):
            resp = client.post(reverse("marketing_google_ads_connect"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("https://accounts.google.com/"))
        pending = client.session[SESSION_KEY]
        return pending, flow

    def _callback(self, client, pending, flow, **params):
        query = {"state": pending["state"], **params}
        with patch(
            "marketing.services.google_ads_oauth.Flow.from_client_config",
            return_value=flow,
        ):
            return client.get(reverse("marketing_google_ads_callback"), query)

    def test_connect_callback_autoriza_somente_org_a(self):
        pending, flow = self._start(self.http_a)
        resp = self._callback(self.http_a, pending, flow, code="4/fake-code")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("marketing_dashboard"))
        self.assertTrue(has(organization=self.org_a, provider="google_ads"))
        self.assertFalse(has(organization=self.org_b, provider="google_ads"))
        payload = get(organization=self.org_a, provider="google_ads")
        self.assertEqual(payload, {"refresh_token": "rt-oauth-org-a"})
        self.assertNotIn("access_token", payload)
        conn_a = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn_a.status, OrganizationGoogleAdsConnection.Status.AUTHORIZED)
        self.assertIsNotNone(conn_a.connected_at)
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(organization=self.org_b).exists()
        )

    def test_state_org_a_nao_funciona_na_org_b(self):
        pending, flow = self._start(self.http_a)
        sess = self.http_b.session
        sess[SESSION_KEY] = dict(pending)
        sess.save()
        resp = self._callback(self.http_b, pending, flow, code="4/stolen")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))
        self.assertFalse(has(organization=self.org_b, provider="google_ads"))
        self.assertNotIn("4/stolen", resp.content.decode(errors="ignore"))

    def test_outro_user_nao_reaproveita_state(self):
        extra = User.objects.create_user("oauth_c", password="senha123")
        Membership.objects.create(
            user=extra,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        grupo = Group.objects.get(name="Marketing — acesso completo")
        extra.groups.add(grupo)
        http_c = Client()
        http_c.login(username="oauth_c", password="senha123")
        pending, flow = self._start(self.http_a)
        sess = http_c.session
        sess[SESSION_KEY] = dict(pending)
        sess.save()
        self._callback(http_c, pending, flow, code="4/other-user")
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_state_reutilizado_falha(self):
        pending, flow = self._start(self.http_a)
        self._callback(self.http_a, pending, flow, code="4/once")
        self.assertTrue(has(organization=self.org_a, provider="google_ads"))
        resp = self._callback(self.http_a, pending, flow, code="4/twice")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            OrganizationGoogleAdsConnection.objects.filter(organization=self.org_a).count(),
            1,
        )

    def test_state_expirado_falha(self):
        pending, flow = self._start(self.http_a)
        sess = self.http_a.session
        data = dict(sess[SESSION_KEY])
        data["issued_at"] = int(time.time()) - 601
        sess[SESSION_KEY] = data
        sess.save()
        self._callback(self.http_a, pending, flow, code="4/expired")
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_issued_at_invalido_e_futuro_falham(self):
        pending, flow = self._start(self.http_a)
        sess = self.http_a.session
        data = dict(sess[SESSION_KEY])
        data["issued_at"] = "ontem"
        sess[SESSION_KEY] = data
        sess.save()
        self._callback(self.http_a, pending, flow, code="4/bad-ts")
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

        pending2, flow2 = self._start(self.http_a)
        sess = self.http_a.session
        data = dict(sess[SESSION_KEY])
        data["issued_at"] = int(time.time()) + 3600
        sess[SESSION_KEY] = data
        sess.save()
        self._callback(self.http_a, pending2, flow2, code="4/future")
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_cancelamento_google_nao_altera_cofre_nem_status(self):
        pending, flow = self._start(self.http_a)
        resp = self._callback(
            self.http_a, pending, flow, error="access_denied"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(organization=self.org_a).exists()
        )
        self.assertIsNone(self.http_a.session.get(SESSION_KEY))
        follow = self.http_a.get(reverse("marketing_dashboard"))
        self.assertContains(follow, MSG_OAUTH_FALHOU)
        self.assertNotContains(follow, "access_denied")
        body = follow.content.decode()
        self.assertNotIn(pending["state"], body)

    def test_callback_sem_code_nao_autoriza(self):
        pending, flow = self._start(self.http_a)
        self._callback(self.http_a, pending, flow)
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(
                organization=self.org_a,
                status=OrganizationGoogleAdsConnection.Status.AUTHORIZED,
            ).exists()
        )

    def test_sem_refresh_token_nao_autoriza(self):
        pending, flow = self._start(self.http_a)
        flow.credentials = _FakeCreds(refresh_token="")
        self._callback(self.http_a, pending, flow, code="4/no-refresh")
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_token_exchange_falha_nao_autoriza(self):
        pending, flow = self._start(self.http_a)
        flow.fetch_token.side_effect = RuntimeError("token endpoint 400 code=secret")
        resp = self._callback(self.http_a, pending, flow, code="4/boom")
        follow = self.http_a.get(resp.url)
        self.assertContains(follow, MSG_OAUTH_FALHOU)
        self.assertNotContains(follow, "4/boom")
        self.assertNotContains(follow, "oauth-client-secret-test")
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_vault_falha_nao_marca_authorized(self):
        pending, flow = self._start(self.http_a)
        with patch(
            "marketing.services.google_ads_oauth.put",
            side_effect=__import__(
                "organizacoes.provider_credentials", fromlist=["CredentialAccessError"]
            ).CredentialAccessError("Cofre indisponível."),
        ):
            with patch(
                "marketing.services.google_ads_oauth.Flow.from_client_config",
                return_value=flow,
            ):
                self.http_a.get(
                    reverse("marketing_google_ads_callback"),
                    {"state": pending["state"], "code": "4/vault"},
                )
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(
                organization=self.org_a,
                status=OrganizationGoogleAdsConnection.Status.AUTHORIZED,
            ).exists()
        )

    def test_disconnect_apaga_vault_e_volta_disconnected(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets={"refresh_token": "rt-oauth-org-a"},
        )
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            status=OrganizationGoogleAdsConnection.Status.AUTHORIZED,
            customer_id="1234567890",
            login_customer_id="1234567890",
            descriptive_name="Conta A",
            currency_code="BRL",
        )
        resp = self.http_a.post(reverse("marketing_google_ads_disconnect"))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))
        conn = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.DISCONNECTED)
        self.assertEqual(conn.customer_id, "")
        self.assertEqual(conn.login_customer_id, "")
        self.assertEqual(conn.descriptive_name, "")
        self.assertEqual(conn.currency_code, "")
        self.assertIsNone(conn.connected_at)
        self.assertFalse(has(organization=self.org_b, provider="google_ads"))
        resp2 = self.http_a.post(reverse("marketing_google_ads_disconnect"))
        self.assertEqual(resp2.status_code, 302)

    def test_sem_capability_nao_conecta_nem_desconecta(self):
        user = User.objects.create_user("oauth_view", password="senha123")
        Membership.objects.create(
            user=user,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        grupo = _grupo_restrito("OAuth só view", "view_marketing")
        user.groups.add(grupo)
        http = Client()
        http.login(username="oauth_view", password="senha123")
        self.assertTrue(pode_ver_marketing(user))
        self.assertFalse(pode_gerenciar_integracoes_marketing(user))
        resp = http.post(reverse("marketing_google_ads_connect"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("clientes"))
        resp = http.post(reverse("marketing_google_ads_disconnect"))
        self.assertEqual(resp.url, reverse("clientes"))
        dash = http.get(reverse("marketing_dashboard"))
        self.assertEqual(dash.status_code, 200)
        self.assertNotContains(dash, "Conectar Google Ads")
        self.assertNotContains(dash, "Desconectar Google Ads")

    def test_com_manage_mostra_botao_conectar(self):
        dash = self.http_a.get(reverse("marketing_dashboard"))
        self.assertEqual(dash.status_code, 200)
        self.assertContains(dash, "Conectar Google Ads")
        perms = marketing_permissoes(dash.wsgi_request)["mkt_perms"]
        self.assertTrue(perms.gerenciar_integracoes)

    def test_tenant_none_nao_inicia_oauth(self):
        solo = User.objects.create_user("oauth_solo", password="senha123")
        grupo = Group.objects.get(name="Marketing — acesso completo")
        solo.groups.add(grupo)
        http = Client()
        http.login(username="oauth_solo", password="senha123")
        resp = http.post(reverse("marketing_google_ads_connect"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("marketing_dashboard"))
        self.assertIsNone(http.session.get(SESSION_KEY))

    def test_redirect_http_rejeitado_debug_false(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_google_ads_oauth_redirect_uri(
                debug=False,
                raw="http://staging.example.com/marketing/google-ads/callback/",
            )


class GoogleAdsOAuthCsrfTests(TestCase):
    def test_disconnect_sem_csrf_bloqueado(self):
        user = User.objects.create_user("oauth_csrf", password="senha123")
        create_organization_with_owner(name="OAuth CSRF Org", user=user)
        http = Client(enforce_csrf_checks=True)
        http.login(username="oauth_csrf", password="senha123")
        resp = http.post(reverse("marketing_google_ads_disconnect"))
        self.assertEqual(resp.status_code, 403)
