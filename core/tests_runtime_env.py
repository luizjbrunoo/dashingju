"""RAILWAY-STAGING-PREP-01 — parsers de env e health/readiness."""

import os
import subprocess
import sys

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from cryptography.fernet import Fernet

from core.runtime_env import (
    DEV_SECRET_KEY,
    KEY_SOURCE_DEV_DERIVED,
    KEY_SOURCE_ENV,
    AsaasParentKeyError,
    asaas_base_url,
    env_bool,
    asaas_baas_enabled,
    get_asaas_parent_api_key,
    parse_allowed_hosts,
    parse_csrf_trusted_origins,
    resolve_allowed_hosts,
    resolve_asaas_baas_enabled,
    resolve_asaas_environment,
    build_asaas_webhook_ingress_url,
    resolve_asaas_webhook_email,
    resolve_asaas_webhook_url,
    resolve_public_base_url,
    resolve_google_ads_oauth_redirect_uri,
    resolve_integration_credentials_key,
    resolve_secret_key,
)


class RuntimeEnvParserTests(SimpleTestCase):
    def test_debug_malformado_nao_cai_em_true(self):
        with self.assertRaises(ImproperlyConfigured):
            env_bool("talvez", default=True, name="DJANGO_DEBUG")

    def test_debug_vazio_usa_default(self):
        self.assertTrue(env_bool("", default=True, name="DJANGO_DEBUG"))
        self.assertFalse(env_bool("0", default=True, name="DJANGO_DEBUG"))

    def test_hosts_rejeita_wildcard_e_protocolo(self):
        with self.assertRaises(ImproperlyConfigured):
            parse_allowed_hosts("*")
        with self.assertRaises(ImproperlyConfigured):
            parse_allowed_hosts("https://app.example.com")

    def test_hosts_trim_e_vazios(self):
        self.assertEqual(
            parse_allowed_hosts(" a.example.com, ,b.example.com "),
            ["a.example.com", "b.example.com"],
        )

    def test_csrf_exige_esquema(self):
        with self.assertRaises(ImproperlyConfigured):
            parse_csrf_trusted_origins("app.example.com")
        self.assertEqual(
            parse_csrf_trusted_origins("https://app.example.com/"),
            ["https://app.example.com"],
        )

    def test_secret_debug_false_fail_fast(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key(debug=False, raw="")
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key(debug=False, raw=DEV_SECRET_KEY)

    def test_secret_debug_true_aceita_dev(self):
        self.assertEqual(resolve_secret_key(debug=True, raw=""), DEV_SECRET_KEY)

    def test_hosts_debug_false_obrigatorio(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_allowed_hosts(debug=False, raw="")
        self.assertEqual(
            resolve_allowed_hosts(debug=False, raw="staging.example.com"),
            ["staging.example.com"],
        )

    def test_integration_key_debug_false_obrigatoria(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_integration_credentials_key(
                debug=False, raw="", secret_key="prod"
            )

    def test_integration_key_debug_false_invalida(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_integration_credentials_key(
                debug=False, raw="abc", secret_key="prod"
            )

    def test_integration_key_debug_true_deriva_marcada(self):
        resolved = resolve_integration_credentials_key(
            debug=True, raw="", secret_key=DEV_SECRET_KEY
        )
        self.assertEqual(resolved.source, KEY_SOURCE_DEV_DERIVED)
        self.assertNotIn(resolved.value, repr(resolved))

    def test_oauth_redirect_https_obrigatorio_debug_false(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_google_ads_oauth_redirect_uri(
                debug=False,
                raw="http://staging.example.com/marketing/google-ads/callback/",
            )
        with self.assertRaises(ImproperlyConfigured):
            resolve_google_ads_oauth_redirect_uri(
                debug=False,
                raw="https://*.example.com/callback",
            )
        self.assertEqual(
            resolve_google_ads_oauth_redirect_uri(
                debug=False,
                raw="https://staging.example.com/marketing/google-ads/callback/",
            ),
            "https://staging.example.com/marketing/google-ads/callback/",
        )

    def test_asaas_webhook_email_fail_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_email("")
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_email("sem-arroba")
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_email("ops@advgrowth90.example extra")
        self.assertEqual(
            resolve_asaas_webhook_email(" ops@advgrowth90.example "),
            "ops@advgrowth90.example",
        )
        self.assertNotIn("ops@", str(ImproperlyConfigured("ASAAS_WEBHOOK_EMAIL inválido.")))

    def test_asaas_webhook_url_exige_https_com_path(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_url("")
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_url("http://app.example.com/financeiro/asaas/webhook/")
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_url("https://app.example.com")
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_webhook_url("https://*.example.com/webhook/")
        self.assertEqual(
            resolve_asaas_webhook_url(
                "https://app.example.com/financeiro/webhooks/asaas/11111111-1111-4111-8111-111111111111/"
            ),
            "https://app.example.com/financeiro/webhooks/asaas/11111111-1111-4111-8111-111111111111/",
        )

    def test_public_base_url_https_em_producao_e_constroi_webhook(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_public_base_url("")
        with self.assertRaises(ImproperlyConfigured):
            resolve_public_base_url("http://app.example.com", debug=False)
        with self.assertRaises(ImproperlyConfigured):
            resolve_public_base_url("https://app.example.com/path")
        self.assertEqual(
            resolve_public_base_url("https://app.example.com/", debug=False),
            "https://app.example.com",
        )
        uid = "11111111-1111-4111-8111-111111111111"
        url = build_asaas_webhook_ingress_url(
            public_id=uid,
            base_url="https://app.example.com",
            debug=False,
        )
        self.assertEqual(
            url,
            f"https://app.example.com/financeiro/webhooks/asaas/{uid}/",
        )
        self.assertNotIn("token", url)
        self.assertNotIn("secret", url)

    def test_asaas_baas_flag_fail_closed(self):
        self.assertFalse(resolve_asaas_baas_enabled(None))
        self.assertFalse(resolve_asaas_baas_enabled(""))
        self.assertFalse(resolve_asaas_baas_enabled("   "))
        self.assertFalse(resolve_asaas_baas_enabled("false"))
        self.assertFalse(resolve_asaas_baas_enabled("0"))
        self.assertFalse(resolve_asaas_baas_enabled("off"))
        self.assertFalse(resolve_asaas_baas_enabled("talvez"))
        self.assertTrue(resolve_asaas_baas_enabled("true"))
        self.assertTrue(resolve_asaas_baas_enabled("TRUE"))
        self.assertTrue(resolve_asaas_baas_enabled(" TRUE "))
        self.assertTrue(resolve_asaas_baas_enabled("1"))
        self.assertTrue(resolve_asaas_baas_enabled("yes"))
        self.assertTrue(resolve_asaas_baas_enabled("on"))
        with override_settings(ASAAS_BAAS_ENABLED=False):
            self.assertFalse(asaas_baas_enabled())
        with override_settings(ASAAS_BAAS_ENABLED=True):
            self.assertTrue(asaas_baas_enabled())

    def test_asaas_urls_somente_sandbox_e_production(self):
        self.assertEqual(
            asaas_base_url("SANDBOX"), "https://api-sandbox.asaas.com/v3"
        )
        self.assertEqual(
            asaas_base_url("production"), "https://api.asaas.com/v3"
        )
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_environment("")
        with self.assertRaises(ImproperlyConfigured):
            asaas_base_url("https://api-sandbox.asaas.com/v3")

    def test_chave_pai_web_ok_worker_fail_closed(self):
        self.assertEqual(
            get_asaas_parent_api_key(process="web", raw="parent-key-test"),
            "parent-key-test",
        )
        with self.assertRaises(AsaasParentKeyError) as ctx:
            get_asaas_parent_api_key(process="worker", raw="parent-key-test")
        self.assertEqual(ctx.exception.reason, "worker")
        self.assertNotIn("parent-key-test", str(ctx.exception))

    def test_integration_key_debug_true_env_valida(self):
        key = Fernet.generate_key().decode("ascii")
        resolved = resolve_integration_credentials_key(
            debug=True, raw=key, secret_key=DEV_SECRET_KEY
        )
        self.assertEqual(resolved.source, KEY_SOURCE_ENV)
        self.assertEqual(resolved.value, key)


class HealthEndpointsTests(TestCase):
    def test_health_liveness_sem_auth(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_select_one(self):
        response = self.client.get(reverse("ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_media_documentos_continua_404(self):
        response = self.client.get("/media/documentos/nao-publico.txt")
        self.assertEqual(response.status_code, 404)


class SettingsLazyCredentialsKeyTests(SimpleTestCase):
    def test_settings_carrega_debug_false_sem_chave(self):
        with override_settings(DEBUG=False, INTEGRATION_CREDENTIALS_KEY=""):
            self.assertFalse(settings.DEBUG)
            self.assertEqual(settings.INTEGRATION_CREDENTIALS_KEY, "")
            self.assertTrue(settings.INSTALLED_APPS)

    def _prod_env_sem_cofre(self):
        env = os.environ.copy()
        env["DJANGO_DEBUG"] = "0"
        env["DJANGO_SECRET_KEY"] = "staging-runtime-not-dev-secret"
        env["DJANGO_ALLOWED_HOSTS"] = "localhost,testserver"
        env["INTEGRATION_CREDENTIALS_KEY"] = ""
        env["GOOGLE_ADS_CLIENT_ID"] = ""
        env["GOOGLE_ADS_CLIENT_SECRET"] = ""
        env["GOOGLE_ADS_OAUTH_REDIRECT_URI"] = ""
        env["DJANGO_PROCESS"] = "worker"
        env["ASAAS_ENVIRONMENT"] = "SANDBOX"
        env.pop("ASAAS_PARENT_API_KEY", None)
        env.pop("DATABASE_URL", None)
        return env

    def test_check_nao_bloqueia_sem_chave(self):
        proc = subprocess.run(
            [sys.executable, "manage.py", "check"],
            env=self._prod_env_sem_cofre(),
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("INTEGRATION_CREDENTIALS_KEY", proc.stderr)
        self.assertNotIn("GOOGLE_ADS_CLIENT", proc.stderr)
        self.assertNotIn("GOOGLE_ADS_OAUTH", proc.stderr)
        self.assertNotIn("ASAAS_PARENT_API_KEY", proc.stderr)
        self.assertNotIn("ASAAS_PARENT", proc.stderr)

    def test_collectstatic_nao_bloqueia_sem_chave(self):
        proc = subprocess.run(
            [sys.executable, "manage.py", "collectstatic", "--noinput", "--dry-run"],
            env=self._prod_env_sem_cofre(),
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("INTEGRATION_CREDENTIALS_KEY", proc.stderr)
        self.assertNotIn("GOOGLE_ADS_CLIENT", proc.stderr)
        self.assertNotIn("GOOGLE_ADS_OAUTH", proc.stderr)

    def test_migrate_plan_nao_bloqueia_sem_oauth(self):
        proc = subprocess.run(
            [sys.executable, "manage.py", "migrate", "--plan"],
            env=self._prod_env_sem_cofre(),
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("GOOGLE_ADS_CLIENT", proc.stderr)
        self.assertNotIn("GOOGLE_ADS_OAUTH", proc.stderr)
