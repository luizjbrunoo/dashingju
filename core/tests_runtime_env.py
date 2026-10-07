"""RAILWAY-STAGING-PREP-01 — parsers de env e health/readiness."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.runtime_env import (
    DEV_SECRET_KEY,
    env_bool,
    parse_allowed_hosts,
    parse_csrf_trusted_origins,
    resolve_allowed_hosts,
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
