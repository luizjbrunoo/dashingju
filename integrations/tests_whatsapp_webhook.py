"""Webhook oficial Meta Cloud API. Sem HTTP real. Sem persistência."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.runtime_env import get_meta_whatsapp_app_secret, get_whatsapp_webhook_verify_token
from integrations.services.whatsapp_webhook import ingest_whatsapp_event


VERIFY_TOKEN = "verify-token-test-nao-logar"
APP_SECRET = "app-secret-test-nao-logar"
CHALLENGE = "1758228034"

VALID_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA123",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "5511999999999",
                            "phone_number_id": "PNID1",
                        },
                        "contacts": [
                            {"profile": {"name": "Nome Sensivel"}, "wa_id": "5511988887777"}
                        ],
                        "messages": [
                            {
                                "from": "5511988887777",
                                "id": "wamid.secret",
                                "timestamp": "123",
                                "type": "text",
                                "text": {"body": "conteudo-secreto-da-conversa"},
                            }
                        ],
                    },
                },
                {
                    "field": "statuses",
                    "value": {
                        "statuses": [
                            {
                                "id": "wamid.secret",
                                "status": "delivered",
                                "recipient_id": "5511988887777",
                            }
                        ]
                    },
                },
            ],
        }
    ],
}


def _settings():
    return {
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN": VERIFY_TOKEN,
        "META_WHATSAPP_APP_SECRET": APP_SECRET,
    }


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _url():
    return reverse("integrations_whatsapp_webhook")


class WhatsAppWebhookResolverTests(SimpleTestCase):
    def test_tokens_ausentes_sao_vazios(self):
        with override_settings(WHATSAPP_WEBHOOK_VERIFY_TOKEN="", META_WHATSAPP_APP_SECRET=""):
            self.assertEqual(get_whatsapp_webhook_verify_token(), "")
            self.assertEqual(get_meta_whatsapp_app_secret(), "")
        self.assertEqual(get_whatsapp_webhook_verify_token(raw="  abc  "), "abc")
        self.assertEqual(get_meta_whatsapp_app_secret(raw=None), get_meta_whatsapp_app_secret())


@override_settings(**_settings())
class WhatsAppWebhookGetTests(TestCase):
    def test_get_valido_devolve_challenge_200(self):
        resp = self.client.get(
            _url(),
            {
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": CHALLENGE,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode("utf-8"), CHALLENGE)
        self.assertTrue(resp["Content-Type"].startswith("text/plain"))

    def test_verify_token_errado_403(self):
        resp = self.client.get(
            _url(),
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "token-errado",
                "hub.challenge": CHALLENGE,
            },
        )
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(CHALLENGE.encode(), resp.content)

    def test_parametros_ausentes_403(self):
        self.assertEqual(self.client.get(_url()).status_code, 403)
        self.assertEqual(
            self.client.get(_url(), {"hub.mode": "subscribe"}).status_code, 403
        )
        self.assertEqual(
            self.client.get(
                _url(),
                {"hub.verify_token": VERIFY_TOKEN, "hub.challenge": CHALLENGE},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                _url(),
                {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN},
            ).status_code,
            403,
        )

    def test_verify_token_ausente_na_config_403(self):
        with override_settings(WHATSAPP_WEBHOOK_VERIFY_TOKEN=""):
            resp = self.client.get(
                _url(),
                {
                    "hub.mode": "subscribe",
                    "hub.verify_token": VERIFY_TOKEN,
                    "hub.challenge": CHALLENGE,
                },
            )
        self.assertEqual(resp.status_code, 403)

    def test_get_nao_loga_verify_token(self):
        logger = logging.getLogger("integrations.services.whatsapp_webhook")
        with self.assertLogs(logger, level="INFO") as cm:
            self.client.get(
                _url(),
                {
                    "hub.mode": "subscribe",
                    "hub.verify_token": VERIFY_TOKEN,
                    "hub.challenge": CHALLENGE,
                },
            )
            self.client.get(
                _url(),
                {
                    "hub.mode": "subscribe",
                    "hub.verify_token": "token-errado",
                    "hub.challenge": CHALLENGE,
                },
            )
        joined = "\n".join(cm.output)
        self.assertNotIn(VERIFY_TOKEN, joined)
        self.assertNotIn(APP_SECRET, joined)


@override_settings(**_settings())
class WhatsAppWebhookPostTests(TestCase):
    def test_post_json_valido_200(self):
        body = json.dumps(VALID_PAYLOAD).encode("utf-8")
        resp = self.client.post(
            _url(),
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(body)},
        )
        self.assertEqual(resp.status_code, 200)

    def test_assinatura_x_hub_signature_256(self):
        body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
        ok = self.client.post(
            _url(),
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(body)},
        )
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post(
            _url(),
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(body, secret="outro-secret")},
        )
        self.assertEqual(bad.status_code, 403)
        missing = self.client.post(
            _url(),
            data=body,
            content_type="application/json",
        )
        self.assertEqual(missing.status_code, 403)

    def test_post_invalido_tratado_com_seguranca(self):
        body = b"nao-e-json{"
        resp = self.client.post(
            _url(),
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(body)},
        )
        self.assertEqual(resp.status_code, 400)
        empty = self.client.post(
            _url(),
            data=b"",
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(b"")},
        )
        self.assertEqual(empty.status_code, 400)

    def test_app_secret_ausente_403(self):
        body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
        with override_settings(META_WHATSAPP_APP_SECRET=""):
            resp = self.client.post(
                _url(),
                data=body,
                content_type="application/json",
                headers={"X-Hub-Signature-256": _sign(body)},
            )
        self.assertEqual(resp.status_code, 403)

    def test_nao_dispara_ia_crm_nem_http(self):
        body = json.dumps(VALID_PAYLOAD).encode("utf-8")
        with patch("urllib.request.urlopen") as urlopen:
            with patch("ia.views.webhook_whatsapp") as ia_hook:
                resp = self.client.post(
                    _url(),
                    data=body,
                    content_type="application/json",
                    headers={"X-Hub-Signature-256": _sign(body)},
                )
        self.assertEqual(resp.status_code, 200)
        urlopen.assert_not_called()
        ia_hook.assert_not_called()

    def test_post_nao_loga_segredos_nem_conversa(self):
        body = json.dumps(VALID_PAYLOAD).encode("utf-8")
        logger = logging.getLogger("integrations.services.whatsapp_webhook")
        with self.assertLogs(logger, level="INFO") as cm:
            ingest_whatsapp_event(
                raw_body=body,
                signature_header=_sign(body),
            )
        joined = "\n".join(cm.output)
        self.assertNotIn(APP_SECRET, joined)
        self.assertNotIn(VERIFY_TOKEN, joined)
        self.assertNotIn("conteudo-secreto-da-conversa", joined)
        self.assertNotIn("5511999999999", joined)
        self.assertNotIn("5511988887777", joined)
        self.assertNotIn("Nome Sensivel", joined)
        self.assertIn("messages=1", joined)
        self.assertIn("statuses=1", joined)

    def test_rotas_existentes_intactas(self):
        self.assertEqual(_url(), "/integrations/whatsapp/webhook/")
        self.assertEqual(reverse("webhook_whatsapp"), "/ia/webhook_whatsapp/")
        self.assertEqual(reverse("financeiro_dashboard"), "/financeiro/")
        self.assertEqual(reverse("financeiro_asaas_webhook", args=["00000000-0000-4000-8000-000000000001"]), "/financeiro/webhooks/asaas/00000000-0000-4000-8000-000000000001/")
        self.assertEqual(reverse("marketing_google_ads_connect"), "/marketing/google-ads/connect/")
        self.assertEqual(reverse("marketing_google_ads_accounts"), "/marketing/google-ads/contas/")
