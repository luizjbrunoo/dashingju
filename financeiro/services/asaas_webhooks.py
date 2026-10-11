"""Ingress seguro e idempotente de webhook Asaas. Sem regras de pagamento."""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from django.db import IntegrityError

from financeiro.models import AsaasWebhookEvent, OrganizationAsaasSubaccount
from financeiro.services.asaas_client import WEBHOOK_TOKEN_HEADER
from organizacoes.provider_credentials import get as vault_get

logger = logging.getLogger(__name__)

INCONSISTENT_ACCOUNT_MISMATCH = "ACCOUNT_ID_MISMATCH"

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "accesstoken",
        "access_token",
        "webhook_token",
        "webhooktoken",
        "authtoken",
        "auth_token",
        "asaas-access-token",
        "asaas_access_token",
    }
)


class AsaasWebhookError(Exception):
    """Falha sanitizada de ingress. Sem secret."""

    def __init__(self, reason: str, http_status: int = 403):
        self.reason = reason
        self.http_status = http_status
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"<AsaasWebhookError reason={self.reason}>"


def sanitize_webhook_payload(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key)
        if name.lower() in _SECRET_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[name] = sanitize_webhook_payload(value)
        elif isinstance(value, list):
            cleaned[name] = [
                sanitize_webhook_payload(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[name] = value
    return cleaned


def _token_matches(*, received: str, expected: str) -> bool:
    got = (received or "").encode("utf-8")
    want = (expected or "").encode("utf-8")
    if not got or not want:
        return False
    if len(got) != len(want):
        hmac.compare_digest(got, got)
        return False
    return hmac.compare_digest(got, want)


def _vault_webhook_token(*, organization) -> str:
    try:
        payload = vault_get(organization=organization, provider="asaas") or {}
    except Exception:
        return ""
    return str(payload.get("webhook_token") or "").strip()


def _extract_account_id(payload: dict) -> str | None:
    account = payload.get("account")
    if isinstance(account, dict):
        value = str(account.get("id") or "").strip()
        return value or None
    value = str(payload.get("account_id") or "").strip()
    return value or None


def ingest_asaas_webhook(*, public_id, header_token: str, raw_body=None, payload=None) -> AsaasWebhookEvent:
    """Localiza a Org pelo UUID público; autentica pelo webhook_token do vault."""
    row = (
        OrganizationAsaasSubaccount.objects.select_related("organization")
        .filter(webhook_public_id=public_id)
        .first()
    )
    if row is None or row.organization_id is None:
        raise AsaasWebhookError("not_found", http_status=404)
    organization = row.organization
    expected = _vault_webhook_token(organization=organization)
    if not _token_matches(received=header_token, expected=expected):
        raise AsaasWebhookError("forbidden", http_status=403)
    if payload is None:
        try:
            if raw_body in (None, b"", ""):
                raise AsaasWebhookError("invalid_json", http_status=400)
            if isinstance(raw_body, bytes):
                text = raw_body.decode("utf-8")
            else:
                text = str(raw_body)
            payload = json.loads(text)
        except AsaasWebhookError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AsaasWebhookError("invalid_json", http_status=400) from exc
    if not isinstance(payload, dict):
        raise AsaasWebhookError("invalid_json", http_status=400)
    event_id = str(payload.get("id") or "").strip()
    event_type = str(payload.get("event") or "").strip()
    if not event_id or not event_type:
        raise AsaasWebhookError("invalid_payload", http_status=400)
    sanitized = sanitize_webhook_payload(payload)
    received_account_id = _extract_account_id(sanitized)
    inconsistent_reason = ""
    configured = row.status == OrganizationAsaasSubaccount.Status.CONFIGURED
    if (
        configured
        and row.account_id
        and received_account_id
        and received_account_id != row.account_id
    ):
        inconsistent_reason = INCONSISTENT_ACCOUNT_MISMATCH
        logger.info("asaas.webhook inconsistent reason=%s", inconsistent_reason)
    existing = AsaasWebhookEvent.objects.filter(
        organization=organization,
        event_id=event_id,
    ).first()
    if existing is not None:
        return existing
    event = AsaasWebhookEvent(
        organization=organization,
        event_id=event_id,
        event_type=event_type[:80],
        account_id=received_account_id,
        payload=sanitized,
        inconsistent_reason=inconsistent_reason,
    )
    try:
        event.save()
    except IntegrityError:
        duplicate = AsaasWebhookEvent.objects.filter(
            organization=organization,
            event_id=event_id,
        ).first()
        if duplicate is not None:
            return duplicate
        raise
    logger.info(
        "asaas.webhook ingested type=%s org_id=%s",
        event.event_type,
        organization.pk,
    )
    return event


def webhook_header_name() -> str:
    return WEBHOOK_TOKEN_HEADER
