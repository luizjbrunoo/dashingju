"""Ingress de webhook Asaas. Sem HTTP real ao Asaas. Sem regras de pagamento."""

from __future__ import annotations

import inspect
import json
import uuid

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from financeiro.models import AsaasWebhookEvent, OrganizationAsaasSubaccount
from financeiro.services import asaas_webhooks as webhook_mod
from financeiro.services.asaas_client import WEBHOOK_TOKEN_HEADER
from financeiro.services.asaas_onboarding import WEBHOOK_EVENTS
from financeiro.services.asaas_webhooks import ingest_asaas_webhook
from organizacoes.models import Organization
from organizacoes.provider_credentials import put as vault_put


def _settings():
    return {
        "INTEGRATION_CREDENTIALS_KEY": Fernet.generate_key().decode("ascii"),
        "DJANGO_PROCESS": "web",
        "ASAAS_ENVIRONMENT": "SANDBOX",
        "PUBLIC_BASE_URL": "https://app.example.com",
    }


STATUS_EVENT = {
    "id": "evt_test_001",
    "event": "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED",
    "account": {"id": "acc-1", "ownerId": None},
    "accountStatus": {"general": "APPROVED"},
    "unknownFutureField": {"nested": True},
}


@override_settings(**_settings())
class AsaasWebhookIngressTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Webhook Org A")
        self.org_b = Organization.objects.create(name="Webhook Org B")
        self.token_a = "webhook-token-org-a-com-mais-de-32ch"
        self.token_b = "webhook-token-org-b-com-mais-de-32ch"
        self.row_a = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_a,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.PROVISIONING,
        )
        self.row_b = OrganizationAsaasSubaccount.objects.create(
            organization=self.org_b,
            environment="SANDBOX",
            status=OrganizationAsaasSubaccount.Status.CONFIGURED,
            account_id="acc-b",
            wallet_id="wal-b",
        )
        vault_put(
            organization=self.org_a,
            provider="asaas",
            secrets={"webhook_token": self.token_a},
        )
        vault_put(
            organization=self.org_b,
            provider="asaas",
            secrets={"api_key": "ak-org-b", "webhook_token": self.token_b},
        )

    def _url(self, row):
        return reverse("financeiro_asaas_webhook", args=[row.webhook_public_id])

    def _post(self, row, token, payload, **extra):
        return self.client.post(
            self._url(row),
            data=json.dumps(payload),
            content_type="application/json",
            headers={WEBHOOK_TOKEN_HEADER: token, **extra},
        )

    def test_webhook_public_id_unico_e_nao_previsivel(self):
        self.assertIsInstance(self.row_a.webhook_public_id, uuid.UUID)
        self.assertEqual(self.row_a.webhook_public_id.version, 4)
        self.assertNotEqual(self.row_a.webhook_public_id, self.row_b.webhook_public_id)
        self.assertNotEqual(str(self.row_a.webhook_public_id), str(self.org_a.pk))
        self.assertFalse(str(self.row_a.webhook_public_id).startswith(f"{self.org_a.pk}-"))

    def test_token_ausente_rejeitado(self):
        resp = self.client.post(
            self._url(self.row_a),
            data=json.dumps(STATUS_EVENT),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AsaasWebhookEvent.objects.exists())

    def test_token_incorreto_rejeitado(self):
        resp = self._post(self.row_a, "token-errado-com-tamanho-diferente-xx", STATUS_EVENT)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AsaasWebhookEvent.objects.exists())

    def test_org_a_nao_valida_token_da_org_b(self):
        resp = self._post(self.row_a, self.token_b, STATUS_EVENT)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AsaasWebhookEvent.objects.exists())

    def test_comparacao_constant_time(self):
        src = inspect.getsource(webhook_mod)
        self.assertIn("compare_digest", src)
        self.assertIn("hmac.compare_digest", inspect.getsource(webhook_mod._token_matches))

    def test_evento_valido_persiste(self):
        resp = self._post(self.row_a, self.token_a, STATUS_EVENT)
        self.assertEqual(resp.status_code, 200)
        event = AsaasWebhookEvent.objects.get(organization=self.org_a)
        self.assertEqual(event.event_id, "evt_test_001")
        self.assertEqual(event.event_type, "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED")
        self.assertEqual(event.account_id, "acc-1")
        self.assertTrue(event.payload.get("unknownFutureField"))
        self.row_a.refresh_from_db()
        self.assertIsNone(self.row_a.account_id)

    def test_mesmo_event_id_nao_duplica_e_retorna_200(self):
        first = self._post(self.row_a, self.token_a, STATUS_EVENT)
        second = self._post(self.row_a, self.token_a, STATUS_EVENT)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(AsaasWebhookEvent.objects.filter(organization=self.org_a).count(), 1)

    def test_campos_desconhecidos_nao_quebram(self):
        payload = dict(STATUS_EVENT)
        payload["brandNewAsaasField"] = "ok"
        resp = self._post(self.row_a, self.token_a, payload)
        self.assertEqual(resp.status_code, 200)
        event = AsaasWebhookEvent.objects.get()
        self.assertEqual(event.payload.get("brandNewAsaasField"), "ok")

    def test_segredo_nao_aparece_em_payload_repr_log(self):
        payload = dict(STATUS_EVENT)
        payload["apiKey"] = "$aact_nao_persistir"
        payload["authToken"] = "auth-token-secreto"
        resp = self._post(self.row_a, self.token_a, payload)
        self.assertEqual(resp.status_code, 200)
        event = AsaasWebhookEvent.objects.get()
        blob = json.dumps(event.payload) + repr(event) + str(event)
        self.assertNotIn("$aact_nao_persistir", blob)
        self.assertNotIn("auth-token-secreto", blob)
        self.assertNotIn(self.token_a, blob)
        self.assertNotIn("apiKey", event.payload)
        self.assertNotIn("authToken", event.payload)

    def test_account_id_divergente_apos_configured_nao_e_aplicado(self):
        payload = dict(STATUS_EVENT)
        payload["id"] = "evt_mismatch"
        payload["account"] = {"id": "acc-outro"}
        resp = self._post(self.row_b, self.token_b, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        self.assertNotIn("acc-b", resp.content.decode("utf-8"))
        self.assertNotIn("acc-outro", resp.content.decode("utf-8"))
        self.row_b.refresh_from_db()
        self.assertEqual(self.row_b.account_id, "acc-b")
        self.assertEqual(self.row_b.organization_id, self.org_b.pk)
        event = AsaasWebhookEvent.objects.get(event_id="evt_mismatch")
        self.assertEqual(event.organization_id, self.org_b.pk)
        self.assertEqual(event.account_id, "acc-outro")
        self.assertEqual(event.inconsistent_reason, "ACCOUNT_ID_MISMATCH")
        self.assertFalse(event.processable)
        self.assertIsNone(event.processed_at)

    def test_evento_em_provisioning_nao_inventa_account_id(self):
        self.assertEqual(self.row_a.status, OrganizationAsaasSubaccount.Status.PROVISIONING)
        self.assertIsNone(self.row_a.account_id)
        resp = self._post(self.row_a, self.token_a, STATUS_EVENT)
        self.assertEqual(resp.status_code, 200)
        self.row_a.refresh_from_db()
        self.assertIsNone(self.row_a.account_id)
        self.assertEqual(self.row_a.status, OrganizationAsaasSubaccount.Status.PROVISIONING)
        self.assertTrue(AsaasWebhookEvent.objects.filter(organization=self.org_a).exists())

    def test_somente_eventos_account_status_configurados(self):
        expected = (
            "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_APPROVED",
            "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_AWAITING_APPROVAL",
            "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_PENDING",
            "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_REJECTED",
            "ACCOUNT_STATUS_COMMERCIAL_INFO_APPROVED",
            "ACCOUNT_STATUS_COMMERCIAL_INFO_AWAITING_APPROVAL",
            "ACCOUNT_STATUS_COMMERCIAL_INFO_PENDING",
            "ACCOUNT_STATUS_COMMERCIAL_INFO_REJECTED",
            "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON",
            "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED",
            "ACCOUNT_STATUS_DOCUMENT_APPROVED",
            "ACCOUNT_STATUS_DOCUMENT_AWAITING_APPROVAL",
            "ACCOUNT_STATUS_DOCUMENT_PENDING",
            "ACCOUNT_STATUS_DOCUMENT_REJECTED",
            "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED",
            "ACCOUNT_STATUS_GENERAL_APPROVAL_AWAITING_APPROVAL",
            "ACCOUNT_STATUS_GENERAL_APPROVAL_PENDING",
            "ACCOUNT_STATUS_GENERAL_APPROVAL_REJECTED",
        )
        self.assertEqual(WEBHOOK_EVENTS, expected)
        self.assertTrue(all(item.startswith("ACCOUNT_STATUS_") for item in WEBHOOK_EVENTS))
        self.assertFalse(any(item.startswith("PAYMENT_") for item in WEBHOOK_EVENTS))

    def test_json_invalido_apos_auth_nao_persiste(self):
        resp = self.client.post(
            self._url(self.row_a),
            data="nao-e-json",
            content_type="application/json",
            headers={WEBHOOK_TOKEN_HEADER: self.token_a},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(AsaasWebhookEvent.objects.exists())

    def test_uuid_desconhecido_404(self):
        missing = uuid.uuid4()
        resp = self.client.post(
            reverse("financeiro_asaas_webhook", args=[missing]),
            data=json.dumps(STATUS_EVENT),
            content_type="application/json",
            headers={WEBHOOK_TOKEN_HEADER: self.token_a},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(AsaasWebhookEvent.objects.exists())

    def test_ingest_nao_usa_user_nem_membership(self):
        src = inspect.getsource(webhook_mod.ingest_asaas_webhook)
        self.assertNotIn("request.user", src)
        self.assertNotIn("Membership", src)
        self.assertNotIn("request.GET", src)
        self.assertNotIn('payload.get("organization', src)
        self.assertIn("webhook_public_id", src)

    def test_nenhum_http_real_ao_asaas(self):
        from unittest.mock import patch

        with patch("urllib.request.urlopen") as urlopen:
            ingest_asaas_webhook(
                public_id=self.row_a.webhook_public_id,
                header_token=self.token_a,
                payload=STATUS_EVENT,
            )
            urlopen.assert_not_called()
