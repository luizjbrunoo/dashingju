"""Feature flag ASAAS_BAAS_ENABLED. Fail-closed. Sem HTTP real."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from financeiro.models import OrganizationAsaasSubaccount, OrganizationFinancialOnboarding
from financeiro.services import asaas_provision as provision_mod
from financeiro.services.asaas_client import WEBHOOK_TOKEN_HEADER
from financeiro.services.asaas_provision import (
    AsaasProvisionError,
    lookup_existing_subaccount,
    provision_organization_subaccount,
)
from financeiro.tests_asaas_fase2 import PARENT_KEY, RecordingTransport, _settings
from financeiro.tests_asaas_webhooks import STATUS_EVENT
from financeiro.tests_helpers import grant_all_finance_permissions, grant_caixa_permissions
from organizacoes.models import Membership, Organization
from organizacoes.provider_credentials import put as vault_put


_OFF_SETTINGS = {**_settings(), "ASAAS_BAAS_ENABLED": False}
_ON_SETTINGS = _settings()


class _BaasFlagHelpers(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Asaas Flag A")
        self.org_b = Organization.objects.create(name="Asaas Flag B")
        self.user_ok = User.objects.create_user("asaas_flag_ok", password="senha123")
        grant_caixa_permissions(self.user_ok, manage=True, view=True)
        Membership.objects.create(
            user=self.user_ok,
            organization=self.org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        self.parent_patch = patch.object(
            provision_mod, "get_asaas_parent_api_key", return_value=PARENT_KEY
        )
        self.parent_key = self.parent_patch.start()
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


@override_settings(**_OFF_SETTINGS)
class AsaasBaasFlagOffTests(_BaasFlagHelpers):
    def test_menu_nao_contem_conta_financeira(self):
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn("Conta Financeira", html)
        self.assertNotIn(reverse("financeiro_conta_onboarding"), html)
        self.assertNotIn("Ativar conta financeira", html)
        self.assertIn("Painel", html)
        self.assertIn("Bancos", html)
        self.assertIn("Extrato", html)
        self.assertIn("Novo lançamento", html)

    def test_get_onboarding_e_legado_404(self):
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 404)
        resp_old = self.client.get(reverse("financeiro_asaas_onboarding"))
        self.assertEqual(resp_old.status_code, 404)

    def test_post_provisionamento_nao_altera_banco_nem_http(self):
        self._onboarding(self.org_a)
        row = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_a,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.CONFIGURED,
            account_id="acc-keep",
            wallet_id="wal-keep",
        )
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        transport = RecordingTransport()
        with patch("financeiro.views_asaas.urllib_transport", transport):
            resp = self.client.post(reverse("financeiro_asaas_provisionar"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.parent_key.call_count, 0)
        row.refresh_from_db()
        self.assertEqual(row.account_id, "acc-keep")
        self.assertEqual(row.wallet_id, "wal-keep")
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.CONFIGURED)
        self.assertEqual(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).count(),
            1,
        )

    def test_servico_nao_trava_nem_chama_http(self):
        self._onboarding(self.org_a)
        transport = RecordingTransport()
        with self.assertRaises(AsaasProvisionError) as ctx:
            provision_organization_subaccount(
                organization=self.org_a,
                user=self.user_ok,
                transport=transport,
            )
        self.assertEqual(ctx.exception.reason, "baas_disabled")
        self.assertEqual(transport.calls, [])
        self.assertFalse(
            OrganizationAsaasSubaccount.objects.filter(organization=self.org_a).exists()
        )
        with self.assertRaises(AsaasProvisionError) as lctx:
            lookup_existing_subaccount(
                organization=self.org_a,
                user=self.user_ok,
                transport=transport,
            )
        self.assertEqual(lctx.exception.reason, "baas_disabled")
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.parent_key.call_count, 0)

    def test_recuperacao_404_sem_http(self):
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        transport = RecordingTransport()
        with patch("financeiro.views_asaas.urllib_transport", transport):
            resp = self.client.post(reverse("financeiro_asaas_recuperar"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(transport.calls, [])

    def test_demais_funcoes_financeiro_acessiveis(self):
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        for name in (
            "financeiro_dashboard",
            "financeiro_cobranca_listar",
            "financeiro_contrato_listar",
            "financeiro_banco_listar",
            "financeiro_extrato",
        ):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)

    def test_isolamento_por_organization_preservado(self):
        keep_a = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_a,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.CONFIGURED,
            account_id="acc-a",
            wallet_id="wal-a",
        )
        keep_b = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_b,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.ERROR,
            account_id="acc-b",
            wallet_id="wal-b",
        )
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        self.client.post(reverse("financeiro_asaas_provisionar"))
        keep_a.refresh_from_db()
        keep_b.refresh_from_db()
        self.assertEqual(keep_a.account_id, "acc-a")
        self.assertEqual(keep_b.account_id, "acc-b")
        self.assertEqual(keep_a.organization_id, self.org_a.pk)
        self.assertEqual(keep_b.organization_id, self.org_b.pk)
        self.assertNotEqual(keep_a.organization_id, keep_b.organization_id)

    def test_webhook_inbound_continua(self):
        token = "webhook-token-flag-off-com-mais-de-32ch"
        row = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_a,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.PROVISIONING,
        )
        vault_put(
            organization=self.org_a,
            provider="asaas",
            secrets={"webhook_token": token},
        )
        url = reverse("financeiro_asaas_webhook", args=[row.webhook_public_id])
        with patch("urllib.request.urlopen") as urlopen:
            resp = self.client.post(
                url,
                data=json.dumps(STATUS_EVENT),
                content_type="application/json",
                headers={WEBHOOK_TOKEN_HEADER: token},
            )
            urlopen.assert_not_called()
        self.assertEqual(resp.status_code, 200)

    def test_google_ads_intacto(self):
        from marketing.services import google_ads_accounts
        from marketing import views_google_ads_accounts
        from marketing import views_google_ads_oauth

        for module in (google_ads_accounts, views_google_ads_accounts, views_google_ads_oauth):
            src = inspect.getsource(module)
            self.assertNotIn("ASAAS_BAAS_ENABLED", src)
            self.assertNotIn("asaas_baas_enabled", src)


@override_settings(**_ON_SETTINGS)
class AsaasBaasFlagOnTests(_BaasFlagHelpers):
    def test_true_preserva_fluxo_atual(self):
        self._onboarding(self.org_a)
        grant_all_finance_permissions(self.user_ok)
        self.client.force_login(self.user_ok)
        resp = self.client.get(reverse("financeiro_conta_onboarding"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Conta Financeira", html)
        self.assertIn("Ativar conta financeira", html)
        transport = RecordingTransport()
        with patch("financeiro.views_asaas.urllib_transport", transport):
            provision = self.client.post(reverse("financeiro_asaas_provisionar"))
        self.assertEqual(provision.status_code, 302)
        self.assertTrue(transport.calls)
        row = OrganizationAsaasSubaccount.objects.get(organization=self.org_a)
        self.assertEqual(row.status, OrganizationAsaasSubaccount.Status.CONFIGURED)
        self.assertTrue(any(call["method"] == "POST" for call in transport.calls))
        dash = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(dash.status_code, 200)
        self.assertIn("Conta Financeira", dash.content.decode())
