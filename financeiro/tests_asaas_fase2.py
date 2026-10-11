"""Fase 2 Asaas BaaS: provisionamento sandbox. Todos os HTTP são mocks."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import date
from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from financeiro.forms import OrganizationFinancialOnboardingForm
from financeiro.models import OrganizationAsaasSubaccount, OrganizationFinancialOnboarding
from financeiro.permissions import pode_provisionar_subconta_asaas
from financeiro.services import asaas_client as client_mod
from financeiro.services import asaas_provision as provision_mod
from financeiro.services.asaas_client import (
    LIST_ACCOUNT_FILTERS,
    USER_AGENT,
    AsaasClient,
    AsaasClientError,
    AsaasHttpError,
    AsaasHttpResponse,
    AsaasTimeoutError,
)
from financeiro.services.asaas_onboarding import (
    WEBHOOK_CONFIG_NAME,
    WEBHOOK_EVENTS,
    WEBHOOK_REQUIRED_KEYS,
    AsaasOnboardingError,
    build_create_account_payload,
    missing_onboarding_fields,
    normalize_optional_site,
    pending_fields_message,
)
from financeiro.services.asaas_provision import (
    AsaasProvisionError,
    lookup_existing_subaccount,
    provision_organization_subaccount,
)
from financeiro.services.asaas_subaccounts import start_provisioning, transition
from financeiro.tests_helpers import grant_all_finance_permissions, grant_caixa_permissions
from organizacoes.models import Membership, Organization
from organizacoes.provider_credentials import get as vault_get


PUBLIC_BASE_URL = "https://app.example.com"
WEBHOOK_URL = "https://app.example.com/financeiro/webhooks/asaas/00000000-0000-4000-8000-000000000001/"
WEBHOOK_OPS_EMAIL = "ops@advgrowth90.example"
PARENT_KEY = "parent-key-nao-logar"
API_KEY_A = "$aact_hmlg_org_a_secret"
API_KEY_B = "$aact_hmlg_org_b_secret"


def _settings():
    return {
        "INTEGRATION_CREDENTIALS_KEY": Fernet.generate_key().decode("ascii"),
        "DJANGO_PROCESS": "web",
        "ASAAS_ENVIRONMENT": "SANDBOX",
        "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
        "ASAAS_WEBHOOK_EMAIL": WEBHOOK_OPS_EMAIL,
    }


def _success_payload(*, account_id="acc-1", wallet_id="wal-1", api_key=API_KEY_A):
    return {
        "object": "account",
        "id": account_id,
        "walletId": wallet_id,
        "apiKey": api_key,
        "name": "Escritorio A",
    }


class RecordingTransport:
    def __init__(self, *, responses=None, error=None, on_post=None):
        self.calls = []
        self.responses = list(responses or [])
        self.error = error
        self.on_post = on_post

    def __call__(self, *, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
            }
        )
        if method == "POST" and self.on_post:
            self.on_post(self)
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return AsaasHttpResponse(status=200, payload=_success_payload())


@override_settings(**_settings())
class AsaasFase2Helpers(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Asaas Fase2 A")
        self.org_b = Organization.objects.create(name="Asaas Fase2 B")
        self.user_ok = User.objects.create_user("asaas_ok", password="senha123")
        self.user_no = User.objects.create_user("asaas_no", password="senha123")
        grant_caixa_permissions(self.user_ok, manage=True, view=True)
        Membership.objects.create(
            user=self.user_ok,
            organization=self.org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.user_no,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.parent_patch = patch.object(
            provision_mod, "get_asaas_parent_api_key", return_value=PARENT_KEY
        )
        self.parent_patch.start()
        self.addCleanup(self.parent_patch.stop)

    def _onboarding(self, organization, **overrides):
        data = {
            "organization": organization,
            "legal_name": "Escritorio A LTDA",
            "email": "org-a@example.com",
            "cpf_cnpj": "66625514000140",
            "person_type": OrganizationFinancialOnboarding.PersonType.JURIDICA,
            "company_type": OrganizationFinancialOnboarding.CompanyType.LIMITED,
            "tax_regime": OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
            "mobile_phone": "11988451155",
            "income_value": Decimal("25000.00"),
            "address": "Av. Rolf Wiest",
            "address_number": "277",
            "province": "Bom Retiro",
            "postal_code": "89223005",
        }
        data.update(overrides)
        return OrganizationFinancialOnboarding.objects.create(**data)


@override_settings(**_settings())
class AsaasProvisionFlowTests(AsaasFase2Helpers):
    def test_provisioning_antes_do_post(self):
        self._onboarding(self.org_a)
        seen = {}
        depth_before = len(connection.savepoint_ids)

        def on_post(_transport):
            row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
            seen["status"] = row.status
            seen["savepoint_depth"] = len(connection.savepoint_ids)
            seen["public_id"] = str(row.webhook_public_id)

        transport = RecordingTransport(on_post=on_post)
        provision_organization_subaccount(
            organization=self.org_a,
            user=self.user_ok,
            transport=transport,
        )
        self.assertEqual(seen["status"], OrganizationAsaasSubaccount.Status.PROVISIONING)
        self.assertEqual(seen["savepoint_depth"], depth_before)
        self.assertTrue(seen["public_id"])
        self.assertEqual(len([c for c in transport.calls if c["method"] == "POST"]), 1)

    def test_organization_ausente_fail_closed(self):
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=None,
                user=self.user_ok,
                transport=transport,
            )
        self.assertEqual(ctx.exception.reason, "missing_org")
        self.assertEqual(transport.calls, [])

    def test_usuario_sem_permissao_bloqueado(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport()
        self.assertFalse(pode_provisionar_subconta_asaas(self.user_no))
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a,
                user=self.user_no,
                transport=transport,
            )
        self.assertEqual(ctx.exception.reason, "forbidden")
        self.assertEqual(transport.calls, [])

    def test_org_a_nao_usa_dados_da_org_b(self):
        self._onboarding(self.org_a)
        self._onboarding(
            self.org_b,
            legal_name="Escritorio B LTDA",
            email="org-b@example.com",
            cpf_cnpj="39053344705",
            person_type=OrganizationFinancialOnboarding.PersonType.FISICA,
            birth_date=date(1990, 1, 15),
            company_type="",
            tax_regime="",
        )
        transport_a = RecordingTransport(
            responses=[AsaasHttpResponse(status=200, payload=_success_payload(api_key=API_KEY_A))]
        )
        provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport_a
        )
        user_b = User.objects.create_user("asaas_b", password="senha123")
        grant_caixa_permissions(user_b, manage=True, view=True)
        Membership.objects.create(
            user=user_b,
            organization=self.org_b,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        transport_b = RecordingTransport(
            responses=[
                AsaasHttpResponse(
                    status=200,
                    payload=_success_payload(
                        account_id="acc-b", wallet_id="wal-b", api_key=API_KEY_B
                    ),
                )
            ]
        )
        provision_organization_subaccount(
            organization=self.org_b, user=user_b, transport=transport_b
        )
        body_a = json.loads(transport_a.calls[0]["body"].decode("utf-8"))
        body_b = json.loads(transport_b.calls[0]["body"].decode("utf-8"))
        self.assertEqual(body_a["cpfCnpj"], "66625514000140")
        self.assertEqual(body_a["email"], "org-a@example.com")
        self.assertEqual(body_b["cpfCnpj"], "39053344705")
        self.assertEqual(body_b["email"], "org-b@example.com")
        self.assertNotEqual(
            vault_get(organization=self.org_a, provider="asaas")["api_key"],
            vault_get(organization=self.org_b, provider="asaas")["api_key"],
        )
        self.assertNotEqual(
            vault_get(organization=self.org_a, provider="asaas")["webhook_token"],
            vault_get(organization=self.org_b, provider="asaas")["webhook_token"],
        )

    def test_worker_nao_pode_provisionar(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport()
        with patch.object(
            provision_mod,
            "get_asaas_parent_api_key",
            side_effect=provision_mod.AsaasParentKeyError("worker"),
        ):
            with self.assertRaises(AsaasProvisionError) as ctx:
                provision_organization_subaccount(
                    organization=self.org_a,
                    user=self.user_ok,
                    transport=transport,
                )
        self.assertEqual(ctx.exception.reason, "worker")
        self.assertEqual(transport.calls, [])
        self.assertFalse(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).exists()
        )

    def test_configured_nao_cria_novamente(self):
        self._onboarding(self.org_a)
        transport1 = RecordingTransport()
        provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport1
        )
        transport2 = RecordingTransport()
        result = provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport2
        )
        self.assertEqual(result.reason, "already_configured")
        self.assertEqual(transport2.calls, [])
        self.assertEqual(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).count(),
            1,
        )

    def test_provisioning_nao_cria_novamente(self):
        self._onboarding(self.org_a)
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "already_provisioning")
        self.assertEqual(transport.calls, [])

    def test_recovery_required_nao_cria_novamente(self):
        self._onboarding(self.org_a)
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        transition(
            organization=self.org_a,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code="TIMEOUT",
        )
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "recovery_required")
        self.assertEqual(transport.calls, [])

    def test_campos_obrigatorios_ausentes_impedem_http(self):
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "missing_onboarding_fields")
        self.assertIn("nome", ctx.exception.missing_fields)
        self.assertIn("CPF/CNPJ", ctx.exception.missing_fields)
        self.assertEqual(transport.calls, [])
        self.assertFalse(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).exists()
        )

    def test_pessoa_juridica_sem_tipo_empresa_impede_http(self):
        self._onboarding(self.org_a, company_type="")
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "missing_onboarding_fields")
        self.assertIn("tipo de empresa", ctx.exception.missing_fields)
        self.assertEqual(transport.calls, [])

    def test_payload_valido_e_webhook_token_diferente_da_api_key(self):
        self._onboarding(self.org_a, complement="Sala 502", phone="1132300606")
        transport = RecordingTransport()
        provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport
        )
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/v3/accounts"))
        self.assertEqual(call["headers"]["User-Agent"], USER_AGENT)
        self.assertEqual(call["timeout"], 30)
        body = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(body["name"], "Escritorio A LTDA")
        self.assertEqual(body["email"], "org-a@example.com")
        self.assertEqual(body["cpfCnpj"], "66625514000140")
        self.assertEqual(body["companyType"], "LIMITED")
        self.assertEqual(body["taxRegime"], "NATIONAL_SIMPLE")
        self.assertEqual(body["mobilePhone"], "11988451155")
        self.assertEqual(body["incomeValue"], 25000)
        self.assertEqual(body["address"], "Av. Rolf Wiest")
        self.assertEqual(body["addressNumber"], "277")
        self.assertEqual(body["province"], "Bom Retiro")
        self.assertEqual(body["postalCode"], "89223005")
        self.assertNotIn("birthDate", body)
        self.assertNotIn("externalReference", body)
        hook = body["webhooks"][0]
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        expected_url = (
            f"{PUBLIC_BASE_URL}/financeiro/webhooks/asaas/{row.webhook_public_id}/"
        )
        self.assertTrue(WEBHOOK_REQUIRED_KEYS.issubset(hook.keys()))
        self.assertEqual(hook["name"], WEBHOOK_CONFIG_NAME)
        self.assertEqual(hook["url"], expected_url)
        self.assertTrue(hook["url"].startswith("https://"))
        self.assertEqual(hook["email"], WEBHOOK_OPS_EMAIL)
        self.assertNotEqual(hook["email"], body["email"])
        self.assertEqual(hook["sendType"], "SEQUENTIALLY")
        self.assertIs(hook["interrupted"], False)
        self.assertIs(hook["enabled"], True)
        self.assertEqual(hook["apiVersion"], 3)
        self.assertNotIn(f"/{self.org_a.pk}/", expected_url)
        self.assertIn(str(row.webhook_public_id), expected_url)
        self.assertGreaterEqual(len(hook["authToken"]), 32)
        self.assertNotIn(" ", hook["authToken"])
        vault = vault_get(organization=self.org_a, provider="asaas")
        self.assertEqual(hook["authToken"], vault["webhook_token"])
        self.assertNotEqual(hook["authToken"], vault["api_key"])
        self.assertNotEqual(hook["authToken"], PARENT_KEY)
        self.assertTrue(all(item.startswith("ACCOUNT_STATUS_") for item in hook["events"]))
        self.assertNotIn("PAYMENT_CREATED", hook["events"])
        self.assertNotIn("PAYMENT_RECEIVED", hook["events"])
        self.assertIn("ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED", hook["events"])
        self.assertIn("ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED", hook["events"])
        self.assertEqual(len(hook["events"]), len(WEBHOOK_EVENTS))
        self.assertEqual(set(hook["events"]), set(WEBHOOK_EVENTS))
        self.assertFalse(any("PAYMENT_" in item for item in hook["events"]))

    def test_resposta_valida_persiste_api_key_no_vault(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport()
        provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport
        )
        vault = vault_get(organization=self.org_a, provider="asaas")
        self.assertEqual(vault["api_key"], API_KEY_A)
        self.assertTrue(vault["webhook_token"])
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.CONFIGURED)
        self.assertEqual(row.account_id, "acc-1")
        self.assertEqual(row.wallet_id, "wal-1")
        self.assertNotIn("wallet_id", vault)
        self.assertNotIn("account_id", vault)
        self.assertNotIn("acc-1", vault.values())
        self.assertNotIn("wal-1", vault.values())

    def test_400_conclusivo_marca_error_sanitizado(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport(
            error=AsaasHttpError(
                reason="http_400",
                status=400,
                error_code="invalid_postalCode",
            )
        )
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "http_error")
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.ERROR)
        self.assertEqual(row.last_error_code, "INVALID_POSTALCODE")
        self.assertNotIn("{", row.last_error_code)
        vault = vault_get(organization=self.org_a, provider="asaas") or {}
        self.assertNotIn("api_key", vault)

    def test_invalid_object_loga_metadados_sem_corpo_nem_secret(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport(
            error=AsaasHttpError(
                reason="http_400",
                status=400,
                error_code="invalid_object",
            )
        )
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("financeiro.services.asaas_provision")
        handler = Handler()
        logger.addHandler(handler)
        try:
            with self.assertRaises(AsaasProvisionError):
                provision_organization_subaccount(
                    organization=self.org_a, user=self.user_ok, transport=transport
                )
        finally:
            logger.removeHandler(handler)
        blob = "\n".join(records)
        self.assertIn("status=400", blob)
        self.assertIn("code=INVALID_OBJECT", blob)
        self.assertIn("step=create_account", blob)
        self.assertNotIn("description", blob)
        self.assertNotIn("{", blob)
        vault = vault_get(organization=self.org_a, provider="asaas") or {}
        for secret in [API_KEY_A, PARENT_KEY, vault.get("webhook_token", "")]:
            if secret:
                self.assertNotIn(secret, blob)
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.last_error_code, "INVALID_OBJECT")
        self.assertNotIn("www.advgrowth90", row.last_error_code)

    def test_timeout_marca_recovery_e_nao_repete_post(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport(error=AsaasTimeoutError())
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "timeout")
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED)
        self.assertEqual(row.last_error_code, "TIMEOUT")
        transport2 = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx2:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport2
            )
        self.assertEqual(ctx2.exception.reason, "recovery_required")
        self.assertEqual(transport2.calls, [])

    def test_5xx_inconclusivo_marca_recovery(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport(
            error=AsaasHttpError(reason="http_500", status=500, error_code="")
        )
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "inconclusive")
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED)

    def test_consulta_recovery_usa_somente_filtros_suportados(self):
        self._onboarding(self.org_a)
        start_provisioning(organization=self.org_a, environment="SANDBOX")
        transition(
            organization=self.org_a,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code="TIMEOUT",
        )
        transport = RecordingTransport(
            responses=[
                AsaasHttpResponse(
                    status=200,
                    payload={
                        "data": [{"id": "acc-found", "walletId": "wal-found", "name": "Escritorio A LTDA"}]
                    },
                )
            ]
        )
        match = lookup_existing_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport
        )
        self.assertEqual(match["id"], "acc-found")
        call = transport.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertIn("cpfCnpj=66625514000140", call["url"])
        self.assertIn("email=org-a%40example.com", call["url"])
        self.assertNotIn("externalReference", call["url"])
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED)
        self.assertEqual(row.account_id, "acc-found")
        self.assertEqual(row.wallet_id, "wal-found")
        vault = vault_get(organization=self.org_a, provider="asaas")
        self.assertTrue(vault is None or "api_key" not in vault)

    def test_list_accounts_rejeita_filtro_nao_oficial(self):
        client = AsaasClient(
            environment="SANDBOX",
            api_key=PARENT_KEY,
            transport=RecordingTransport(),
        )
        with self.assertRaises(AsaasClientError) as ctx:
            client.list_accounts(externalReference="org-1")
        self.assertEqual(ctx.exception.reason, "unsupported_filter")
        self.assertEqual(LIST_ACCOUNT_FILTERS, {"offset", "limit", "cpfCnpj", "email", "name", "walletId"})

    def test_segredos_nao_aparecem_em_repr_log_exception(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport()
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("financeiro.services.asaas_provision")
        handler = Handler()
        logger.addHandler(handler)
        try:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        finally:
            logger.removeHandler(handler)
        vault = vault_get(organization=self.org_a, provider="asaas")
        secrets = [API_KEY_A, PARENT_KEY, vault["webhook_token"], vault["api_key"]]
        blob = "\n".join(records)
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        client = AsaasClient(environment="SANDBOX", api_key=API_KEY_A, transport=transport)
        texts = [
            blob,
            repr(row),
            str(row),
            repr(client),
            str(client),
            repr(AsaasProvisionError("http_error")),
            str(AsaasHttpError(reason="http_400", status=400, error_code="X")),
        ]
        for secret in secrets:
            for text in texts:
                self.assertNotIn(secret, text)

    def test_concorrencia_aninhada_nao_produz_dois_posts(self):
        self._onboarding(self.org_a)
        posts = []

        def on_post(transport):
            with self.assertRaises(AsaasProvisionError) as ctx:
                provision_organization_subaccount(
                    organization=self.org_a,
                    user=self.user_ok,
                    transport=transport,
                )
            self.assertEqual(ctx.exception.reason, "already_provisioning")
            posts.append("nested")

        transport = RecordingTransport(on_post=on_post)
        provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport
        )
        self.assertEqual(len([c for c in transport.calls if c["method"] == "POST"]), 1)
        self.assertEqual(posts, ["nested"])

    def test_locking_usa_atomic_e_select_for_update(self):
        src = inspect.getsource(provision_mod)
        self.assertIn("transaction.atomic", src)
        self.assertIn("select_for_update", src)
        self.assertIn("IntegrityError", src)
        claim_src = inspect.getsource(provision_mod._claim_provisioning)
        self.assertNotIn("create_account", claim_src)
        self.assertNotIn("/accounts", claim_src)
        finalize_src = inspect.getsource(provision_mod._finalize_provision)
        self.assertNotIn("create_account", finalize_src)

    def test_http_nao_ocorre_com_savepoint_de_lock_aberto(self):
        self._onboarding(self.org_a)
        seen = {}
        depth_before = len(connection.savepoint_ids)

        def on_post(_transport):
            seen["savepoint_depth"] = len(connection.savepoint_ids)

        transport = RecordingTransport(on_post=on_post)
        provision_organization_subaccount(
            organization=self.org_a, user=self.user_ok, transport=transport
        )
        self.assertEqual(seen["savepoint_depth"], depth_before)
        self.assertEqual(len([c for c in transport.calls if c["method"] == "POST"]), 1)

    def test_nenhum_http_real_no_modulo(self):
        with patch("urllib.request.urlopen") as urlopen:
            self._onboarding(self.org_a)
            provision_organization_subaccount(
                organization=self.org_a,
                user=self.user_ok,
                transport=RecordingTransport(),
            )
            urlopen.assert_not_called()
        src = inspect.getsource(client_mod)
        self.assertNotIn("/pix", src)
        self.assertNotIn("/payments", src)


@override_settings(**_settings())
class AsaasOnboardingPayloadTests(AsaasFase2Helpers):
    def test_nao_inventa_dados_ausentes(self):
        row = self._onboarding(self.org_a, complement="", phone="", site="")
        missing = missing_onboarding_fields(row)
        self.assertEqual(missing, [])
        payload = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertNotIn("complement", payload)
        self.assertNotIn("phone", payload)
        self.assertNotIn("site", payload)
        self.assertNotIn("birthDate", payload)
        hook = payload["webhooks"][0]
        self.assertTrue(WEBHOOK_REQUIRED_KEYS.issubset(hook.keys()))
        self.assertEqual(hook["email"], WEBHOOK_OPS_EMAIL)
        self.assertNotEqual(hook["email"], payload["email"])
        self.assertNotIn("authToken", repr(AsaasOnboardingError("invalid_site")))

    def test_pf_exige_nascimento_e_nao_company_type(self):
        self._onboarding(
            self.org_a,
            cpf_cnpj="39053344705",
            person_type=OrganizationFinancialOnboarding.PersonType.FISICA,
            birth_date=date(1988, 4, 12),
            company_type="",
            tax_regime="",
        )
        payload = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertEqual(payload["birthDate"], "1988-04-12")
        self.assertNotIn("companyType", payload)
        self.assertNotIn("taxRegime", payload)

    def test_onboarding_ausente_lista_campos(self):
        with self.assertRaises(AsaasOnboardingError) as ctx:
            build_create_account_payload(
                organization=self.org_a,
                webhook_token="t" * 40,
                webhook_url=WEBHOOK_URL,
            )
        self.assertEqual(ctx.exception.reason, "missing_onboarding_fields")
        self.assertTrue(ctx.exception.missing_fields)
        self.assertNotIn("data de nascimento", ctx.exception.missing_fields)
        self.assertNotIn("tipo de empresa", ctx.exception.missing_fields)
        self.assertNotIn("regime tributário", ctx.exception.missing_fields)

    def test_pj_nao_exige_birth_date_e_payload_nao_contem(self):
        row = self._onboarding(self.org_a, birth_date=date(1988, 4, 12))
        missing = missing_onboarding_fields(row)
        self.assertEqual(missing, [])
        self.assertNotIn("data de nascimento", missing)
        payload = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertNotIn("birthDate", payload)
        self.assertEqual(payload["companyType"], "LIMITED")
        self.assertEqual(payload["taxRegime"], "NATIONAL_SIMPLE")

    def test_pj_exige_company_type_e_tax_regime(self):
        row = self._onboarding(self.org_a, company_type="", tax_regime="")
        missing = missing_onboarding_fields(row)
        self.assertIn("tipo de empresa", missing)
        self.assertIn("regime tributário", missing)
        self.assertNotIn("data de nascimento", missing)
        with self.assertRaises(AsaasOnboardingError) as ctx:
            build_create_account_payload(
                organization=self.org_a,
                webhook_token="t" * 40,
                webhook_url=WEBHOOK_URL,
            )
        self.assertEqual(ctx.exception.reason, "missing_onboarding_fields")

    def test_pf_exige_birth_date_e_payload_nao_contem_empresa(self):
        row = self._onboarding(
            self.org_a,
            cpf_cnpj="39053344705",
            person_type=OrganizationFinancialOnboarding.PersonType.FISICA,
            birth_date=None,
            company_type=OrganizationFinancialOnboarding.CompanyType.LIMITED,
            tax_regime=OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
        )
        missing = missing_onboarding_fields(row)
        self.assertIn("data de nascimento", missing)
        self.assertNotIn("tipo de empresa", missing)
        self.assertNotIn("regime tributário", missing)
        msg = pending_fields_message(missing)
        self.assertIn("data de nascimento", msg)
        self.assertNotIn("tipo de empresa", msg)
        self.assertNotIn("regime tributário", msg)

    def test_pf_nao_exige_company_type_nem_tax_regime(self):
        row = self._onboarding(
            self.org_a,
            cpf_cnpj="39053344705",
            person_type=OrganizationFinancialOnboarding.PersonType.FISICA,
            birth_date=date(1988, 4, 12),
            company_type=OrganizationFinancialOnboarding.CompanyType.LIMITED,
            tax_regime=OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
        )
        self.assertEqual(missing_onboarding_fields(row), [])
        payload = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertEqual(payload["birthDate"], "1988-04-12")
        self.assertNotIn("companyType", payload)
        self.assertNotIn("taxRegime", payload)

    def test_form_pj_nao_exige_nascimento_e_limpa_birth_date(self):
        form = OrganizationFinancialOnboardingForm(
            {
                "legal_name": "Escritorio A LTDA",
                "email": "org-a@example.com",
                "cpf_cnpj": "66625514000140",
                "person_type": OrganizationFinancialOnboarding.PersonType.JURIDICA,
                "birth_date": "1988-04-12",
                "company_type": OrganizationFinancialOnboarding.CompanyType.LIMITED,
                "tax_regime": OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
                "mobile_phone": "11988451155",
                "income_value": "25000,00",
                "address": "Av. Rolf Wiest",
                "address_number": "277",
                "province": "Bom Retiro",
                "postal_code": "89223005",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["birth_date"])
        saved = form.save(commit=False)
        saved.organization = self.org_a
        saved.save()
        saved.refresh_from_db()
        self.assertIsNone(saved.birth_date)
        self.assertEqual(saved.company_type, "LIMITED")

    def test_form_pf_exige_nascimento_e_limpa_campos_de_empresa(self):
        invalid = OrganizationFinancialOnboardingForm(
            {
                "legal_name": "Maria Silva",
                "email": "maria@example.com",
                "cpf_cnpj": "39053344705",
                "person_type": OrganizationFinancialOnboarding.PersonType.FISICA,
                "company_type": OrganizationFinancialOnboarding.CompanyType.LIMITED,
                "tax_regime": OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
                "mobile_phone": "11988451155",
                "income_value": "8000,00",
                "address": "Rua A",
                "address_number": "10",
                "province": "Centro",
                "postal_code": "01310100",
            }
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("birth_date", invalid.errors)
        self.assertNotIn("company_type", invalid.errors)
        self.assertNotIn("tax_regime", invalid.errors)

        form = OrganizationFinancialOnboardingForm(
            {
                "legal_name": "Maria Silva",
                "email": "maria@example.com",
                "cpf_cnpj": "39053344705",
                "person_type": OrganizationFinancialOnboarding.PersonType.FISICA,
                "birth_date": "1988-04-12",
                "company_type": OrganizationFinancialOnboarding.CompanyType.LIMITED,
                "tax_regime": OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
                "mobile_phone": "11988451155",
                "income_value": "8000,00",
                "address": "Rua A",
                "address_number": "10",
                "province": "Centro",
                "postal_code": "01310100",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["company_type"], "")
        self.assertEqual(form.cleaned_data["tax_regime"], "")
        saved = form.save(commit=False)
        saved.organization = self.org_a
        saved.save()
        saved.refresh_from_db()
        self.assertEqual(saved.birth_date, date(1988, 4, 12))
        self.assertEqual(saved.company_type, "")
        self.assertEqual(saved.tax_regime, "")

    def test_form_pj_exige_company_type_e_tax_regime(self):
        form = OrganizationFinancialOnboardingForm(
            {
                "legal_name": "Escritorio A LTDA",
                "email": "org-a@example.com",
                "cpf_cnpj": "66625514000140",
                "person_type": OrganizationFinancialOnboarding.PersonType.JURIDICA,
                "birth_date": "",
                "company_type": "",
                "tax_regime": "",
                "mobile_phone": "11988451155",
                "income_value": "25000,00",
                "address": "Av. Rolf Wiest",
                "address_number": "277",
                "province": "Bom Retiro",
                "postal_code": "89223005",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("company_type", form.errors)
        self.assertIn("tax_regime", form.errors)
        self.assertNotIn("birth_date", form.errors)

    def test_site_sem_esquema_e_normalizado_https(self):
        self.assertEqual(
            normalize_optional_site("www.advgrowth90.com.br"),
            "https://www.advgrowth90.com.br",
        )
        self.assertEqual(
            normalize_optional_site("exemplo.com.br"),
            "https://exemplo.com.br",
        )
        self.assertEqual(
            normalize_optional_site("https://www.advgrowth90.com.br"),
            "https://www.advgrowth90.com.br",
        )
        self.assertEqual(
            normalize_optional_site("http://exemplo.test"),
            "http://exemplo.test",
        )
        self.assertEqual(normalize_optional_site(""), "")
        with self.assertRaises(ValueError):
            normalize_optional_site("javascript:alert(1)")
        self._onboarding(self.org_a, site="www.advgrowth90.com.br")
        payload = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertEqual(payload["site"], "https://www.advgrowth90.com.br")
        hook = payload["webhooks"][0]
        self.assertTrue(WEBHOOK_REQUIRED_KEYS.issubset(hook.keys()))
        self.assertEqual(hook["sendType"], "SEQUENTIALLY")
        self.assertIs(hook["interrupted"], False)
        self.assertIs(hook["enabled"], True)
        self.assertEqual(hook["apiVersion"], 3)
        self.assertTrue(hook["url"].startswith("https://"))
        self.assertTrue(all(item.startswith("ACCOUNT_STATUS_") for item in hook["events"]))
        self.assertFalse(any(item.startswith("PAYMENT_") for item in hook["events"]))

    def test_site_https_vai_ao_payload_e_vazio_nao_e_enviado(self):
        self._onboarding(self.org_a, site="https://www.advgrowth90.com.br")
        payload = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertEqual(payload["site"], "https://www.advgrowth90.com.br")
        OrganizationFinancialOnboarding.objects.filter(organization=self.org_a).update(site="")
        payload_empty = build_create_account_payload(
            organization=self.org_a,
            webhook_token="t" * 40,
            webhook_url=WEBHOOK_URL,
        )
        self.assertNotIn("site", payload_empty)

    def test_site_invalido_impede_http(self):
        self._onboarding(self.org_a, site="javascript:alert(1)")
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a, user=self.user_ok, transport=transport
            )
        self.assertEqual(ctx.exception.reason, "invalid_site")
        self.assertEqual(transport.calls, [])
        self.assertFalse(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).exists()
        )

    def test_form_normaliza_site_sem_esquema(self):
        form = OrganizationFinancialOnboardingForm(
            {
                "legal_name": "Escritorio A LTDA",
                "email": "org-a@example.com",
                "cpf_cnpj": "66625514000140",
                "person_type": OrganizationFinancialOnboarding.PersonType.JURIDICA,
                "company_type": OrganizationFinancialOnboarding.CompanyType.LIMITED,
                "tax_regime": OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
                "mobile_phone": "11988451155",
                "income_value": "25000,00",
                "address": "Av. Rolf Wiest",
                "address_number": "277",
                "province": "Bom Retiro",
                "postal_code": "89223005",
                "site": "www.advgrowth90.com.br",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["site"], "https://www.advgrowth90.com.br")

    def test_webhook_email_ausente_impede_http(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport()
        with override_settings(ASAAS_WEBHOOK_EMAIL=""):
            with self.assertRaises(AsaasProvisionError) as ctx:
                provision_organization_subaccount(
                    organization=self.org_a, user=self.user_ok, transport=transport
                )
        self.assertEqual(ctx.exception.reason, "missing_webhook_email")
        self.assertEqual(transport.calls, [])
        self.assertFalse(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).exists()
        )

    def test_mensagem_pendente_respeita_tipo_de_pessoa(self):
        pf = pending_fields_message(["data de nascimento"])
        self.assertIn("Preencha os dados obrigatórios para ativar sua conta financeira.", pf)
        self.assertIn("data de nascimento", pf)
        self.assertNotIn("tipo de empresa", pf)
        self.assertNotIn("regime tributário", pf)
        pj = pending_fields_message(["tipo de empresa", "regime tributário"])
        self.assertIn("tipo de empresa", pj)
        self.assertIn("regime tributário", pj)
        self.assertNotIn("data de nascimento", pj)


@override_settings(**_settings())
class AsaasViewRbacTests(AsaasFase2Helpers):
    def test_view_sem_permissao_bloqueada(self):
        self.client.force_login(self.user_no)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        resp = self.client.post(reverse("financeiro_asaas_provisionar"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_rota_neutra_funciona_e_white_label(self):
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Ativar conta financeira", html)
        self.assertIn("Conta Financeira", html)
        self.assertIn("Salvar cadastro", html)
        self.assertIn("data-person-only=\"FISICA\"", html)
        self.assertIn("data-person-only=\"JURIDICA\"", html)
        self.assertIn("Ambiente de testes", html)
        self.assertNotIn("Asaas", html)
        self.assertNotIn("Onboarding financeiro Asaas", html)
        self.assertNotIn("Provisionar Asaas", html)
        self.assertNotIn("Criar subconta Asaas", html)
        self.assertNotIn("Conectar Asaas", html)
        self.assertNotIn("tipo de empresa, regime tributário", html)
        self.assertNotIn("data de nascimento, tipo de empresa", html)
        self.assertNotIn("max-w-xl", html)
        self.assertIn("Dados da organização", html)
        self.assertIn("Informações fiscais", html)
        self.assertIn("Contato", html)
        self.assertIn("Endereço", html)
        self.assertIn("Ativação da conta financeira", html)
        self.assertIn("Cadastro não concluído", html)
        self.assertIn("Complete os dados obrigatórios para ativar sua conta financeira.", html)
        self.assertEqual(resp.context["activation_status"], "Cadastro não concluído")
        self.assertIn("lg:grid-cols-[minmax(0,2fr)_minmax(17rem,1fr)]", html)

    def test_rota_antiga_redireciona(self):
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_asaas_onboarding"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("financeiro_conta_onboarding"))

    def test_onboarding_isola_por_organization(self):
        self._onboarding(self.org_a, legal_name="Escritorio Tenant A")
        self._onboarding(self.org_b, legal_name="Escritorio Tenant B")
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Escritorio Tenant A", html)
        self.assertNotIn("Escritorio Tenant B", html)

    def test_pj_nao_lista_nascimento_como_pendente_na_ui(self):
        self._onboarding(self.org_a, company_type="", tax_regime="")
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        missing = list(resp.context["missing_fields"])
        self.assertIn("tipo de empresa", missing)
        self.assertIn("regime tributário", missing)
        self.assertNotIn("data de nascimento", missing)
        self.assertIn("tipo de empresa", resp.context["pending_fields_message"])
        self.assertNotIn("data de nascimento", resp.context["pending_fields_message"])

    def test_pf_nao_lista_empresa_como_pendente_na_ui(self):
        self._onboarding(
            self.org_a,
            cpf_cnpj="39053344705",
            person_type=OrganizationFinancialOnboarding.PersonType.FISICA,
            birth_date=None,
            company_type="",
            tax_regime="",
        )
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        missing = list(resp.context["missing_fields"])
        self.assertIn("data de nascimento", missing)
        self.assertNotIn("tipo de empresa", missing)
        self.assertNotIn("regime tributário", missing)
        self.assertIn("data de nascimento", resp.context["pending_fields_message"])
        self.assertNotIn("tipo de empresa", resp.context["pending_fields_message"])
        self.assertNotIn("regime tributário", resp.context["pending_fields_message"])

    def test_layout_status_pronto_para_ativacao(self):
        self._onboarding(self.org_a)
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["activation_status"], "Pronto para ativação")
        self.assertTrue(all(item["done"] for item in resp.context["onboarding_checklist"]))
        html = resp.content.decode()
        self.assertIn("Salvar cadastro", html)
        self.assertIn("Ativar conta financeira", html)
        self.assertNotIn("Asaas", html)
        self.assertNotIn("Consultar situação", html)

    def test_layout_status_requer_atencao_mostra_consultar(self):
        self._onboarding(self.org_a)
        OrganizationAsaasSubaccount.objects.create(
            organization=self.org_a,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
        )
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["activation_status"], "Requer atenção")
        self.assertIn("Consultar situação", resp.content.decode())
        self.assertNotIn("Asaas", resp.content.decode())

    def test_view_provisiona_com_transport_mockado(self):
        self._onboarding(self.org_a)
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        transport = RecordingTransport()
        with patch("financeiro.views_asaas.urllib_transport", transport):
            resp = self.client.post(reverse("financeiro_asaas_provisionar"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(transport.calls)
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.CONFIGURED)

    def test_cadastro_de_usuario_nao_cria_subconta(self):
        from usuarios.views import cadastro

        self.assertNotIn("provision_organization_subaccount", inspect.getsource(cadastro))
        self.assertFalse(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).exists()
        )


@override_settings(**_settings())
class AsaasConcurrencyThreadTests(TransactionTestCase):
    def setUp(self):
        self._cred = override_settings(**_settings())
        self._cred.enable()
        self.addCleanup(self._cred.disable)
        self.org = Organization.objects.create(name="Asaas Race Org")
        self.user = User.objects.create_user("asaas_race", password="senha123")
        grant_caixa_permissions(self.user, manage=True, view=True)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        OrganizationFinancialOnboarding.objects.create(
            organization=self.org,
            legal_name="Race LTDA",
            email="race@example.com",
            cpf_cnpj="66625514000140",
            person_type=OrganizationFinancialOnboarding.PersonType.JURIDICA,
            company_type=OrganizationFinancialOnboarding.CompanyType.LIMITED,
            tax_regime=OrganizationFinancialOnboarding.TaxRegime.NATIONAL_SIMPLE,
            mobile_phone="11988451155",
            income_value=Decimal("25000.00"),
            address="Rua A",
            address_number="1",
            province="Centro",
            postal_code="01001000",
        )
        self.parent_patch = patch.object(
            provision_mod, "get_asaas_parent_api_key", return_value=PARENT_KEY
        )
        self.parent_patch.start()
        self.addCleanup(self.parent_patch.stop)

    def test_http_fora_de_transaction_atomic(self):
        seen = {}

        def transport(*, method, url, headers, body, timeout):
            if method == "POST":
                seen["in_atomic"] = connection.in_atomic_block
                seen["savepoints"] = list(connection.savepoint_ids)
            return AsaasHttpResponse(status=200, payload=_success_payload())

        provision_organization_subaccount(
            organization=self.org,
            user=self.user,
            transport=transport,
        )
        self.assertFalse(seen["in_atomic"])
        self.assertEqual(seen["savepoints"], [])

    def test_dois_cliques_no_maximo_um_post(self):
        barrier = Barrier(2)
        posts = []
        errors = []

        def transport(*, method, url, headers, body, timeout):
            if method == "POST":
                posts.append(1)
            return AsaasHttpResponse(
                status=200,
                payload=_success_payload(account_id=f"acc-{len(posts)}", wallet_id=f"wal-{len(posts)}"),
            )

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                provision_organization_subaccount(
                    organization=self.org,
                    user=self.user,
                    transport=transport,
                )
            except AsaasProvisionError as exc:
                errors.append(exc.reason)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                close_old_connections()

        t1 = Thread(target=worker)
        t2 = Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertLessEqual(len(posts), 1)
        self.assertEqual(OrganizationAsaasSubaccount.objects.filter(organization=self.org).count(), 1)
        self.assertTrue(all(reason != "missing_org" for reason in errors))
