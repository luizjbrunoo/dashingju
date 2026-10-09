"""Cofre Organization-owned: isolamento A/B, fail-closed, unique, redaction."""

import inspect

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from core.runtime_env import (
    DEV_SECRET_KEY,
    KEY_SOURCE_DEV_DERIVED,
    KEY_SOURCE_ENV,
    IntegrationCredentialsKey,
    derive_dev_fernet_key,
    resolve_integration_credentials_key,
)
from organizacoes.models import Organization, OrganizationProviderCredential
from organizacoes.provider_credentials import (
    CredentialAccessError,
    InvalidPayload,
    InvalidProvider,
    OrganizationRequired,
    delete,
    get,
    has,
    put,
)


class IntegrationCredentialsKeyRuntimeTests(SimpleTestCase):
    def test_debug_false_ausente_fail_fast(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_integration_credentials_key(
                debug=False, raw="", secret_key=DEV_SECRET_KEY
            )

    def test_debug_false_invalida_fail_fast(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_integration_credentials_key(
                debug=False, raw="nao-e-fernet", secret_key="prod-secret"
            )

    def test_debug_false_rejeita_derivada_de_dev(self):
        derived = derive_dev_fernet_key(DEV_SECRET_KEY)
        with self.assertRaises(ImproperlyConfigured):
            resolve_integration_credentials_key(
                debug=False, raw=derived, secret_key="qualquer"
            )

    def test_debug_true_fallback_marcado_dev(self):
        resolved = resolve_integration_credentials_key(
            debug=True, raw="", secret_key=DEV_SECRET_KEY
        )
        self.assertEqual(resolved.source, KEY_SOURCE_DEV_DERIVED)
        self.assertTrue(Fernet(resolved.value.encode("utf-8")))

    def test_debug_true_env_fernet_source_env(self):
        key = Fernet.generate_key().decode("ascii")
        resolved = resolve_integration_credentials_key(
            debug=True, raw=key, secret_key=DEV_SECRET_KEY
        )
        self.assertEqual(resolved.source, KEY_SOURCE_ENV)
        self.assertEqual(resolved.value, key)

    def test_chave_repr_nao_expoe_valor(self):
        key = Fernet.generate_key().decode("ascii")
        resolved = IntegrationCredentialsKey(value=key, source=KEY_SOURCE_ENV)
        self.assertNotIn(key, repr(resolved))
        self.assertNotIn(key, str(resolved))


class ProviderCredentialServiceTests(TestCase):
    def setUp(self):
        # DiscoverRunner força DEBUG=False; put/get exigem Fernet de teste.
        self._cred_key = override_settings(
            INTEGRATION_CREDENTIALS_KEY=Fernet.generate_key().decode("ascii"),
        )
        self._cred_key.enable()
        self.addCleanup(self._cred_key.disable)
        self.org_a = Organization.objects.create(name="Cred Org A")
        self.org_b = Organization.objects.create(name="Cred Org B")
        self.ads_a = {
            "refresh_token": "rt-org-a-secret",
        }
        self.asaas_a = {
            "api_key": "asaas_a_live_secret",
            "environment": "sandbox",
        }

    def test_api_somente_organization_keyword(self):
        for fn in (put, get, delete, has):
            params = inspect.signature(fn).parameters
            self.assertNotIn("user", params)
            self.assertNotIn("request", params)
            self.assertNotIn("organization_id", params)
            self.assertEqual(params["organization"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_ab_org_a_nao_le_org_b(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets=self.ads_a,
        )
        put(
            organization=self.org_a,
            provider="asaas",
            secrets=self.asaas_a,
        )
        self.assertEqual(
            get(organization=self.org_a, provider="google_ads")["refresh_token"],
            "rt-org-a-secret",
        )
        self.assertEqual(
            get(organization=self.org_a, provider="asaas")["api_key"],
            "asaas_a_live_secret",
        )
        self.assertIsNone(get(organization=self.org_b, provider="google_ads"))
        self.assertIsNone(get(organization=self.org_b, provider="asaas"))
        self.assertTrue(has(organization=self.org_a, provider="google_ads"))
        self.assertTrue(has(organization=self.org_a, provider="asaas"))
        self.assertFalse(has(organization=self.org_b, provider="google_ads"))

    def test_mesma_org_google_ads_e_asaas_isolados(self):
        put(organization=self.org_a, provider="google_ads", secrets=self.ads_a)
        put(organization=self.org_a, provider="asaas", secrets=self.asaas_a)
        ads = get(organization=self.org_a, provider="google_ads")
        asaas = get(organization=self.org_a, provider="asaas")
        self.assertNotIn("api_key", ads)
        self.assertNotIn("refresh_token", asaas)
        self.assertEqual(ads["refresh_token"], "rt-org-a-secret")
        self.assertEqual(asaas["api_key"], "asaas_a_live_secret")

    def test_duas_orgs_mesmo_provider_sem_colisao(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets={"refresh_token": "rt-a"},
        )
        put(
            organization=self.org_b,
            provider="google_ads",
            secrets={"refresh_token": "rt-b"},
        )
        self.assertEqual(
            get(organization=self.org_a, provider="google_ads")["refresh_token"],
            "rt-a",
        )
        self.assertEqual(
            get(organization=self.org_b, provider="google_ads")["refresh_token"],
            "rt-b",
        )

    def test_organization_none_fail_closed(self):
        for fn in (
            lambda: put(
                organization=None, provider="google_ads", secrets=self.ads_a
            ),
            lambda: get(organization=None, provider="google_ads"),
            lambda: delete(organization=None, provider="google_ads"),
            lambda: has(organization=None, provider="google_ads"),
        ):
            with self.assertRaises(OrganizationRequired):
                fn()
        self.assertEqual(OrganizationProviderCredential.objects.count(), 0)

    def test_organization_id_cru_fail_closed(self):
        with self.assertRaises(OrganizationRequired):
            get(organization=self.org_a.pk, provider="google_ads")

    def test_get_sem_credencial_retorna_none(self):
        self.assertIsNone(get(organization=self.org_a, provider="google_ads"))
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_get_ausente_producao_sem_chave_nao_exige_fernet(self):
        with override_settings(DEBUG=False, INTEGRATION_CREDENTIALS_KEY=""):
            self.assertIsNone(get(organization=self.org_a, provider="asaas"))
            self.assertFalse(has(organization=self.org_a, provider="asaas"))

    def test_inactive_nao_e_utilizavel(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets=self.ads_a,
        )
        OrganizationProviderCredential.objects.filter(
            organization=self.org_a, provider="google_ads"
        ).update(status=OrganizationProviderCredential.Status.INACTIVE)
        self.assertIsNone(get(organization=self.org_a, provider="google_ads"))
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))

    def test_delete_remove_segredo(self):
        put(
            organization=self.org_a,
            provider="asaas",
            secrets=self.asaas_a,
        )
        delete(organization=self.org_a, provider="asaas")
        self.assertIsNone(get(organization=self.org_a, provider="asaas"))
        self.assertEqual(
            OrganizationProviderCredential.objects.filter(
                organization=self.org_a, provider="asaas"
            ).count(),
            0,
        )

    def test_payload_google_ads_rejeita_campo_asaas(self):
        with self.assertRaises(InvalidPayload):
            put(
                organization=self.org_a,
                provider="google_ads",
                secrets={"api_key": "nao"},
            )

    def test_vault_google_ads_aceita_somente_refresh_token(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets={"refresh_token": "rt-org-a-secret"},
        )
        payload = get(organization=self.org_a, provider="google_ads")
        self.assertEqual(payload, {"refresh_token": "rt-org-a-secret"})
        self.assertNotIn("developer_token", payload)
        self.assertNotIn("client_id", payload)
        self.assertNotIn("client_secret", payload)
        self.assertNotIn("access_token", payload)
        self.assertNotIn("customer_id", payload)
        self.assertNotIn("login_customer_id", payload)

    def test_vault_google_ads_rejeita_campos_proibidos(self):
        proibidos = (
            {"developer_token": "devtok-a"},
            {"client_id": "cid.apps.googleusercontent.com"},
            {"client_secret": "gsecret"},
            {"access_token": "ya29.fake"},
            {"customer_id": "1234567890"},
            {"login_customer_id": "1234567890"},
            {"refresh_token": "rt-org-a-secret", "developer_token": "devtok-a"},
            {"refresh_token": "rt-org-a-secret", "customer_id": "1234567890"},
        )
        for secrets in proibidos:
            with self.subTest(secrets=tuple(secrets)):
                with self.assertRaises(InvalidPayload):
                    put(
                        organization=self.org_a,
                        provider="google_ads",
                        secrets=secrets,
                    )
        self.assertEqual(
            OrganizationProviderCredential.objects.filter(
                organization=self.org_a, provider="google_ads"
            ).count(),
            0,
        )

    def test_payload_asaas_rejeita_refresh_token(self):
        with self.assertRaises(InvalidPayload):
            put(
                organization=self.org_a,
                provider="asaas",
                secrets={"refresh_token": "nao"},
            )

    def test_provider_invalido(self):
        with self.assertRaises(InvalidProvider):
            put(
                organization=self.org_a,
                provider="stripe",
                secrets={"api_key": "x"},
            )

    def test_unique_organization_provider(self):
        blob = b"opaque"
        OrganizationProviderCredential.objects.create(
            organization=self.org_a,
            provider="google_ads",
            status=OrganizationProviderCredential.Status.CONFIGURED,
            ciphertext=blob,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationProviderCredential.objects.create(
                    organization=self.org_a,
                    provider="google_ads",
                    status=OrganizationProviderCredential.Status.CONFIGURED,
                    ciphertext=b"other",
                )

    def test_redaction_str_repr_sem_segredo(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets=self.ads_a,
        )
        row = OrganizationProviderCredential.objects.get(
            organization=self.org_a, provider="google_ads"
        )
        self.assertNotIn("rt-org-a-secret", str(row))
        self.assertNotIn("rt-org-a-secret", repr(row))
        ciphertext = bytes(row.ciphertext)
        self.assertNotEqual(ciphertext, b"rt-org-a-secret")
        self.assertNotIn(b"rt-org-a-secret", ciphertext)

    def test_put_substitui_mesmo_par_org_provider(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets={"refresh_token": "primeiro"},
        )
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets={"refresh_token": "segundo"},
        )
        self.assertEqual(
            get(organization=self.org_a, provider="google_ads")["refresh_token"],
            "segundo",
        )
        self.assertEqual(
            OrganizationProviderCredential.objects.filter(
                organization=self.org_a, provider="google_ads"
            ).count(),
            1,
        )

    def test_admin_nao_registra_cofre(self):
        from django.contrib import admin

        self.assertFalse(
            admin.site.is_registered(OrganizationProviderCredential)
        )

    def test_producao_sem_chave_put_get_falham_delete_has_nao(self):
        put(
            organization=self.org_a,
            provider="asaas",
            secrets=self.asaas_a,
        )
        with override_settings(DEBUG=False, INTEGRATION_CREDENTIALS_KEY=""):
            with self.assertRaises(CredentialAccessError) as ctx_put:
                put(
                    organization=self.org_a,
                    provider="google_ads",
                    secrets={"refresh_token": "rt-org-a-secret"},
                )
            with self.assertRaises(CredentialAccessError) as ctx_get:
                get(organization=self.org_a, provider="asaas")
            self.assertNotIn("asaas_a_live_secret", str(ctx_put.exception))
            self.assertNotIn("rt-org-a-secret", str(ctx_put.exception))
            self.assertNotIn("asaas_a_live_secret", str(ctx_get.exception))
            self.assertNotIn("api_key", str(ctx_put.exception))
            self.assertNotIn("api_key", str(ctx_get.exception))
            self.assertTrue(has(organization=self.org_a, provider="asaas"))
            delete(organization=self.org_a, provider="asaas")
        self.assertFalse(has(organization=self.org_a, provider="asaas"))

    def test_fernet_invalida_falha(self):
        with override_settings(DEBUG=False, INTEGRATION_CREDENTIALS_KEY="nao-e-fernet"):
            with self.assertRaises(CredentialAccessError) as ctx:
                put(
                    organization=self.org_a,
                    provider="asaas",
                    secrets=self.asaas_a,
                )
            self.assertNotIn("asaas_a_live_secret", str(ctx.exception))

    def test_decrypt_chave_errada_credential_access_error(self):
        key_a = Fernet.generate_key().decode("ascii")
        key_b = Fernet.generate_key().decode("ascii")
        with override_settings(INTEGRATION_CREDENTIALS_KEY=key_a):
            put(
                organization=self.org_a,
                provider="asaas",
                secrets=self.asaas_a,
            )
        with override_settings(INTEGRATION_CREDENTIALS_KEY=key_b):
            with self.assertRaises(CredentialAccessError) as ctx:
                get(organization=self.org_a, provider="asaas")
            self.assertNotIn("asaas_a_live_secret", str(ctx.exception))
            self.assertNotIn(key_a, str(ctx.exception))
            self.assertNotIn(key_b, str(ctx.exception))

    def test_ciphertext_adulterado_credential_access_error(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets=self.ads_a,
        )
        row = OrganizationProviderCredential.objects.get(
            organization=self.org_a, provider="google_ads"
        )
        blob = bytearray(bytes(row.ciphertext))
        blob[-1] = blob[-1] ^ 0xFF
        row.ciphertext = bytes(blob)
        row.save(update_fields=["ciphertext"])
        with self.assertRaises(CredentialAccessError) as ctx:
            get(organization=self.org_a, provider="google_ads")
        self.assertNotIn("rt-org-a-secret", str(ctx.exception))
        self.assertNotIn("api_key", str(ctx.exception))
        self.assertNotIn("token", str(ctx.exception).lower())


class ProviderCredentialKeyOverrideTests(TestCase):
    def test_override_settings_usa_fernet_de_teste(self):
        org = Organization.objects.create(name="Cred Key Org")
        key = Fernet.generate_key().decode("ascii")
        with override_settings(INTEGRATION_CREDENTIALS_KEY=key):
            put(
                organization=org,
                provider="asaas",
                secrets={"api_key": "k-teste"},
            )
            self.assertEqual(
                get(organization=org, provider="asaas")["api_key"], "k-teste"
            )
