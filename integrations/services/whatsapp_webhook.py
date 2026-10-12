"""Ingress do webhook oficial Meta Cloud API. Sem HTTP de saída. Sem IA/CRM/envio."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from core.runtime_env import get_meta_whatsapp_app_secret, get_whatsapp_webhook_verify_token

logger = logging.getLogger(__name__)

WHATSAPP_OBJECT = "whatsapp_business_account"
SIGNATURE_PREFIX = "sha256="
HUB_MODE_SUBSCRIBE = "subscribe"


class WhatsAppWebhookError(Exception):
    """Falha sanitizada de ingress. Sem secret nem payload."""

    def __init__(self, reason: str, http_status: int = 403):
        self.reason = reason
        self.http_status = http_status
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"<WhatsAppWebhookError reason={self.reason}>"


def _secrets_equal(received: str, expected: str) -> bool:
    got = (received or "").encode("utf-8")
    want = (expected or "").encode("utf-8")
    if not got or not want:
        return False
    if len(got) != len(want):
        hmac.compare_digest(got, got)
        return False
    return hmac.compare_digest(got, want)


def verify_subscription(*, mode: str, token: str, challenge: str) -> str:
    """GET hub.mode/hub.verify_token/hub.challenge. Nunca loga o token."""
    expected = get_whatsapp_webhook_verify_token()
    if not expected:
        logger.info("whatsapp.webhook subscribe rejected reason=missing_verify_token")
        raise WhatsAppWebhookError("missing_verify_token", http_status=403)
    if (mode or "").strip() != HUB_MODE_SUBSCRIBE:
        logger.info("whatsapp.webhook subscribe rejected reason=bad_mode")
        raise WhatsAppWebhookError("bad_mode", http_status=403)
    if not _secrets_equal((token or "").strip(), expected):
        logger.info("whatsapp.webhook subscribe rejected reason=verify_token")
        raise WhatsAppWebhookError("verify_token", http_status=403)
    value = str(challenge or "")
    if not value:
        logger.info("whatsapp.webhook subscribe rejected reason=missing_challenge")
        raise WhatsAppWebhookError("missing_challenge", http_status=403)
    logger.info("whatsapp.webhook subscribe ok")
    return value


def verify_signature(*, raw_body: bytes, signature_header: str) -> None:
    """Valida X-Hub-Signature-256 no corpo cru. Fail-closed sem App Secret."""
    secret = get_meta_whatsapp_app_secret()
    if not secret:
        logger.info("whatsapp.webhook rejected reason=missing_app_secret")
        raise WhatsAppWebhookError("missing_app_secret", http_status=403)
    header = (signature_header or "").strip()
    if not header.startswith(SIGNATURE_PREFIX):
        logger.info("whatsapp.webhook rejected reason=bad_signature")
        raise WhatsAppWebhookError("bad_signature", http_status=403)
    received = header[len(SIGNATURE_PREFIX) :].strip().lower()
    digest = hmac.new(
        secret.encode("utf-8"),
        raw_body or b"",
        hashlib.sha256,
    ).hexdigest()
    if not _secrets_equal(received, digest):
        logger.info("whatsapp.webhook rejected reason=bad_signature")
        raise WhatsAppWebhookError("bad_signature", http_status=403)


def _parse_payload(raw_body: bytes) -> dict:
    if raw_body in (None, b"", ""):
        raise WhatsAppWebhookError("invalid_json", http_status=400)
    try:
        if isinstance(raw_body, bytes):
            text = raw_body.decode("utf-8")
        else:
            text = str(raw_body)
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise WhatsAppWebhookError("invalid_json", http_status=400) from exc
    if not isinstance(payload, dict):
        raise WhatsAppWebhookError("invalid_json", http_status=400)
    return payload


def _handle_messages(*, field: str, value: dict) -> dict:
    messages = value.get("messages") if isinstance(value.get("messages"), list) else []
    types = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip() or "unknown"
        if kind not in types:
            types.append(kind)
    return {"field": field, "count": len(messages), "types": types}


def _handle_statuses(*, field: str, value: dict) -> dict:
    statuses = value.get("statuses") if isinstance(value.get("statuses"), list) else []
    kinds = []
    for item in statuses:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("status") or "").strip() or "unknown"
        if kind not in kinds:
            kinds.append(kind)
    return {"field": field, "count": len(statuses), "statuses": kinds}


def _summarize_entry(entry) -> dict:
    if not isinstance(entry, dict):
        return {"fields": [], "messages": 0, "statuses": 0}
    fields: list[str] = []
    messages = 0
    statuses = 0
    changes = entry.get("changes") if isinstance(entry.get("changes"), list) else []
    for change in changes:
        if not isinstance(change, dict):
            continue
        field = str(change.get("field") or "").strip() or "unknown"
        fields.append(field)
        value = change.get("value") if isinstance(change.get("value"), dict) else {}
        if field == "messages":
            summary = _handle_messages(field=field, value=value)
            messages += int(summary["count"])
        elif field == "statuses":
            summary = _handle_statuses(field=field, value=value)
            statuses += int(summary["count"])
    return {"fields": fields, "messages": messages, "statuses": statuses}


def ingest_whatsapp_event(*, raw_body: bytes, signature_header: str) -> dict:
    """POST autenticado por assinatura. Só metadados seguros. Sem side-effects."""
    verify_signature(raw_body=raw_body, signature_header=signature_header)
    payload = _parse_payload(raw_body)
    object_type = str(payload.get("object") or "").strip()
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    fields: list[str] = []
    message_count = 0
    status_count = 0
    if object_type == WHATSAPP_OBJECT:
        for entry in entries:
            summary = _summarize_entry(entry)
            for field in summary["fields"]:
                if field not in fields:
                    fields.append(field)
            message_count += int(summary["messages"])
            status_count += int(summary["statuses"])
    else:
        logger.info("whatsapp.webhook ignored object=%s", object_type or "empty")
    logger.info(
        "whatsapp.webhook ingested object=%s entries=%s fields=%s messages=%s statuses=%s",
        object_type or "empty",
        len(entries),
        ",".join(fields) if fields else "-",
        message_count,
        status_count,
    )
    return {
        "object": object_type,
        "entries": len(entries),
        "fields": tuple(fields),
        "messages": message_count,
        "statuses": status_count,
    }
