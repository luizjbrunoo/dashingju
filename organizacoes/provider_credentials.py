"""Cofre de credenciais por Organization. Único ponto de encrypt/decrypt."""

from __future__ import annotations

import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from organizacoes.models import Organization, OrganizationProviderCredential

PROVIDER_GOOGLE_ADS = OrganizationProviderCredential.Provider.GOOGLE_ADS
PROVIDER_ASAAS = OrganizationProviderCredential.Provider.ASAAS

GOOGLE_ADS_FIELDS = frozenset({"refresh_token"})
GOOGLE_ADS_SECRET_FIELDS = frozenset({"refresh_token"})
ASAAS_FIELDS = frozenset({"api_key", "wallet_id", "environment"})
ASAAS_ENVIRONMENTS = frozenset({"sandbox", "production"})

_ALLOWED = {
    PROVIDER_GOOGLE_ADS: GOOGLE_ADS_FIELDS,
    PROVIDER_ASAAS: ASAAS_FIELDS,
}


class CredentialAccessError(Exception):
    """Fail-closed. Mensagem sem secret."""


class OrganizationRequired(CredentialAccessError):
    pass


class InvalidProvider(CredentialAccessError):
    pass


class InvalidPayload(CredentialAccessError):
    pass


def _require_organization(organization) -> Organization:
    if organization is None:
        raise OrganizationRequired("Organization é obrigatória.")
    if not isinstance(organization, Organization):
        raise OrganizationRequired("Organization é obrigatória.")
    if organization.pk is None:
        raise OrganizationRequired("Organization é obrigatória.")
    return organization


def _normalize_provider(provider: str) -> str:
    value = str(provider or "").strip()
    if value not in _ALLOWED:
        raise InvalidProvider("Provider inválido.")
    return value


def _normalize_secrets(provider: str, secrets) -> dict[str, str]:
    if not isinstance(secrets, dict):
        raise InvalidPayload("Payload inválido.")
    allowed = _ALLOWED[provider]
    cleaned: dict[str, str] = {}
    for key, raw in secrets.items():
        name = str(key).strip()
        if name not in allowed:
            raise InvalidPayload("Payload inválido.")
        text = str(raw or "").strip()
        if not text:
            raise InvalidPayload("Payload inválido.")
        cleaned[name] = text
    if not cleaned:
        raise InvalidPayload("Payload inválido.")
    if provider == PROVIDER_GOOGLE_ADS:
        if cleaned.keys() != GOOGLE_ADS_FIELDS or "refresh_token" not in cleaned:
            raise InvalidPayload("Payload inválido.")
    elif provider == PROVIDER_ASAAS:
        if "api_key" not in cleaned:
            raise InvalidPayload("Payload inválido.")
        env = cleaned.get("environment")
        if env is not None and env not in ASAAS_ENVIRONMENTS:
            raise InvalidPayload("Payload inválido.")
    return cleaned


def _fernet():
    from cryptography.fernet import Fernet

    from core.runtime_env import resolve_integration_credentials_key

    raw = getattr(settings, "INTEGRATION_CREDENTIALS_KEY", None)
    try:
        resolved = resolve_integration_credentials_key(
            debug=bool(settings.DEBUG),
            raw=raw,
            secret_key=settings.SECRET_KEY,
        )
        return Fernet(resolved.value.encode("utf-8"))
    except (ImproperlyConfigured, ValueError, TypeError) as exc:
        raise CredentialAccessError("Cofre indisponível.") from exc


def _encrypt(payload: dict[str, str]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _fernet().encrypt(raw)


def _decrypt(blob: bytes) -> dict[str, str]:
    from cryptography.fernet import InvalidToken

    try:
        parsed = json.loads(_fernet().decrypt(bytes(blob)).decode("utf-8"))
    except CredentialAccessError:
        raise
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CredentialAccessError("Credencial ilegível.") from exc
    if not isinstance(parsed, dict):
        raise CredentialAccessError("Credencial ilegível.")
    return {str(k): str(v) for k, v in parsed.items()}


def put(*, organization, provider: str, secrets: dict) -> None:
    org = _require_organization(organization)
    prov = _normalize_provider(provider)
    payload = _normalize_secrets(prov, secrets)
    blob = _encrypt(payload)
    OrganizationProviderCredential.objects.update_or_create(
        organization=org,
        provider=prov,
        defaults={
            "ciphertext": blob,
            "status": OrganizationProviderCredential.Status.CONFIGURED,
            "key_id": "v1",
        },
    )


def get(*, organization, provider: str) -> dict[str, str] | None:
    org = _require_organization(organization)
    prov = _normalize_provider(provider)
    row = (
        OrganizationProviderCredential.objects.filter(
            organization=org,
            provider=prov,
            status=OrganizationProviderCredential.Status.CONFIGURED,
        )
        .only("ciphertext")
        .first()
    )
    if row is None:
        return None
    return _decrypt(row.ciphertext)


def delete(*, organization, provider: str) -> None:
    org = _require_organization(organization)
    prov = _normalize_provider(provider)
    OrganizationProviderCredential.objects.filter(
        organization=org, provider=prov
    ).delete()


def has(*, organization, provider: str) -> bool:
    org = _require_organization(organization)
    prov = _normalize_provider(provider)
    return OrganizationProviderCredential.objects.filter(
        organization=org,
        provider=prov,
        status=OrganizationProviderCredential.Status.CONFIGURED,
    ).exists()
