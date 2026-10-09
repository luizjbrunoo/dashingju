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
    env_bool,
    parse_allowed_hosts,
    parse_csrf_trusted_origins,
    resolve_allowed_hosts,
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
