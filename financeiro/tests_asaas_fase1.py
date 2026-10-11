"""Fase 1 Asaas BaaS: estado, vault, runtime. Sem HTTP."""

from __future__ import annotations

import inspect
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from core.runtime_env import (
    ASAAS_BASE_URLS,
    AsaasParentKeyError,
    asaas_base_url,
    get_asaas_parent_api_key,
    resolve_asaas_environment,
)
from financeiro.models import OrganizationAsaasSubaccount
from financeiro.services import asaas_client as client_mod
from financeiro.services.asaas_client import (
    API_TOKEN_HEADER,
    WEBHOOK_TOKEN_HEADER,
    AsaasClient,
    AsaasClientError,
)
from financeiro.services.asaas_subaccounts import (
    AsaasSubaccountError,
    get_subaccount,
    persist_public_ids,
    start_provisioning,
    transition,
)
from organizacoes.models import Organization
from organizacoes.provider_credentials import InvalidPayload, get, put


def _fernet_settings():
    return {
        "INTEGRATION_CREDENTIALS_KEY": Fernet.generate_key().decode("ascii"),
        "DJANGO_PROCESS": "web",
        "ASAAS_ENVIRONMENT": "SANDBOX",
    }


@override_settings(**_fernet_settings())
class AsaasSubaccountModelTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Asaas Org A")
        self.org_b = Organization.objects.create(name="Asaas Org B")

    def test_organization_subaccount_um_para_um(self):
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationAsaasSubaccount.objects.create(
                    organization=self.org_a,
                    environment="SANDBOX",
                    status=OrganizationAsaasSubaccount.Status.PROVISIONING,
                )
        self.assertEqual(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).count(),
            1,
        )

    def test_org_a_nao_le_vault_da_org_b(self):
        put(
            organization=self.org_a,
            provider="asaas",
            secrets={"api_key": "ak-org-a", "webhook_token": "whk-org-a"},
        )
        put(
            organization=self.org_b,
            provider="asaas",
            secrets={"api_key": "ak-org-b", "webhook_token": "whk-org-b"},
        )
        self.assertEqual(get(organization=self.org_a, provider="asaas")["api_key"], "ak-org-a")
        self.assertEqual(get(organization=self.org_b, provider="asaas")["api_key"], "ak-org-b")
        self.assertNotEqual(
            get(organization=self.org_a, provider="asaas")["webhook_token"],
            get(organization=self.org_b, provider="asaas")["webhook_token"],
        )

    def test_organization_none_fail_closed(self):
        with self.assertRaises(AsaasSubaccountError) as ctx:
            get_subaccount(organization=None)
        self.assertEqual(ctx.exception.reason, "missing_org")
        with self.assertRaises(AsaasSubaccountError):
            start_provisioning(organization=None, environment="SANDBOX")

    def test_account_id_duplicado_mesmo_ambiente_rejeitado(self):
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        start_provisioning(organization=self.org_b, environment="SANDBOX")
        persist_public_ids(organization=self.org_a, account_id="acc_shared")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                persist_public_ids(organization=self.org_b, account_id="acc_shared")

    def test_wallet_id_duplicado_mesmo_ambiente_rejeitado(self):
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        start_provisioning(organization=self.org_b, environment="SANDBOX")
        persist_public_ids(organization=self.org_a, wallet_id="wal_shared")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                persist_public_ids(organization=self.org_b, wallet_id="wal_shared")

    def test_mesmo_account_id_ambientes_diferentes_permitido(self):
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        row_b = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_b,
            environment="PRODUCTION",
            status=OrganizationAsaasSubaccount.Status.PROVISIONING,
        )
        persist_public_ids(organization=self.org_a, account_id="acc_same")
        row_b.account_id = "acc_same"
        row_b.save()
        self.assertEqual(
            OrganizationAsaasSubaccount.objects.filter(account_id="acc_same").count(),
            2,
        )

    def test_repr_sem_segredo(self):
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        put(
            organization=self.org_a,
            provider="asaas",
            secrets={"api_key": "ak-secret-valor", "webhook_token": "whk-secret-valor"},
        )
        row = get_subaccount(organization=self.org_a)
        self.assertNotIn("ak-secret-valor", repr(row))
        self.assertNotIn("whk-secret-valor", str(row))
        self.assertNotIn("ak-secret-valor", str(row))

    def test_transicao_invalida_falha(self):
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        with self.assertRaises(AsaasSubaccountError) as ctx:
            transition(
                organization=self.org_a,
                to_status=OrganizationAsaasSubaccount.Status.PROVISIONING,
            )
        self.assertEqual(ctx.exception.reason, "invalid_transition")


class AsaasVaultPayloadTests(TestCase):
    def setUp(self):
        self._cred = override_settings(
            INTEGRATION_CREDENTIALS_KEY=Fernet.generate_key().decode("ascii")
        )
        self._cred.enable()
        self.addCleanup(self._cred.disable)
        self.org = Organization.objects.create(name="Asaas Vault Org")

    def test_payload_aceita_api_key(self):
        put(organization=self.org, provider="asaas", secrets={"api_key": "ak-only"})
        self.assertEqual(get(organization=self.org, provider="asaas")["api_key"], "ak-only")

    def test_payload_aceita_webhook_token(self):
        put(
            organization=self.org,
            provider="asaas",
            secrets={"webhook_token": "whk-only"},
        )
        payload = get(organization=self.org, provider="asaas")
        self.assertEqual(payload["webhook_token"], "whk-only")
        self.assertNotIn("api_key", payload)

    def test_payload_rejeita_wallet_id(self):
        with self.assertRaises(InvalidPayload):
            put(
                organization=self.org,
                provider="asaas",
                secrets={"api_key": "ak", "wallet_id": "wal"},
            )

    def test_payload_rejeita_environment(self):
        with self.assertRaises(InvalidPayload):
            put(
                organization=self.org,
                provider="asaas",
                secrets={"api_key": "ak", "environment": "SANDBOX"},
            )


class AsaasClientFoundationTests(SimpleTestCase):
    def test_sandbox_e_production_urls_fixas(self):
        self.assertEqual(
            asaas_base_url("SANDBOX"),
            "https://api-sandbox.asaas.com/v3",
        )
        self.assertEqual(
            asaas_base_url("PRODUCTION"),
            "https://api.asaas.com/v3",
        )
        self.assertEqual(
            set(ASAAS_BASE_URLS.values()),
            {
                "https://api-sandbox.asaas.com/v3",
                "https://api.asaas.com/v3",
            },
        )

    def test_environment_invalido_fail_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_asaas_environment("https://evil.example")
        with self.assertRaises(ImproperlyConfigured):
            asaas_base_url("staging")
        with self.assertRaises(ImproperlyConfigured):
            AsaasClient(environment="DEV", api_key="ak")

    def test_webhook_token_nao_e_api_key(self):
        params = inspect.signature(AsaasClient.__init__).parameters
        self.assertIn("api_key", params)
        self.assertNotIn("webhook_token", params)
        client = AsaasClient(environment="SANDBOX", api_key="ak-live")
        headers = client.api_headers()
        self.assertIn(API_TOKEN_HEADER, headers)
        self.assertNotIn(WEBHOOK_TOKEN_HEADER, headers)
        self.assertEqual(headers[API_TOKEN_HEADER], "ak-live")

    def test_repr_sem_api_key(self):
        client = AsaasClient(environment="SANDBOX", api_key="ak-nao-logar")
        self.assertNotIn("ak-nao-logar", repr(client))
        self.assertNotIn("ak-nao-logar", str(client))
        with self.assertRaises(AsaasClientError) as ctx:
            AsaasClient(environment="SANDBOX", api_key="")
        self.assertNotIn("ak-", str(ctx.exception))

    def test_request_nao_faz_http(self):
        client = AsaasClient(environment="SANDBOX", api_key="ak")
        with patch("urllib.request.urlopen") as urlopen, patch(
            "httpx.Client.request"
        ) as httpx_req, patch("requests.request") as req:
            with self.assertRaises(AsaasClientError) as ctx:
                client.request("GET", "/accounts")
            self.assertEqual(ctx.exception.reason, "http_disabled")
            urlopen.assert_not_called()
            httpx_req.assert_not_called()
            req.assert_not_called()
        src = inspect.getsource(client_mod)
        self.assertNotIn("urlopen", src)
        self.assertNotIn("requests.", src)
        self.assertNotIn("httpx.", src)
        self.assertNotIn("/accounts", src)
        self.assertNotIn("/payments", src)

    def test_chave_pai_web_autorizado(self):
        with override_settings(DJANGO_PROCESS="web"):
            key = get_asaas_parent_api_key(raw="parent-secret-web")
        self.assertEqual(key, "parent-secret-web")

    def test_chave_pai_worker_fail_closed(self):
        with override_settings(DJANGO_PROCESS="worker"):
            with self.assertRaises(AsaasParentKeyError) as ctx:
                get_asaas_parent_api_key(raw="parent-secret-web")
        self.assertEqual(ctx.exception.reason, "worker")
        self.assertNotIn("parent-secret-web", str(ctx.exception))
        self.assertNotIn("parent-secret-web", repr(ctx.exception))

    def test_chave_pai_processo_vazio_fail_closed(self):
        with override_settings(DJANGO_PROCESS=""):
            with self.assertRaises(AsaasParentKeyError) as ctx:
                get_asaas_parent_api_key(raw="parent-secret-web")
        self.assertEqual(ctx.exception.reason, "not_web")
