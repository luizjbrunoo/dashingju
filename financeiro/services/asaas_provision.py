"""Provisionamento de subconta Asaas por Organization. Web-only. Sem retry POST."""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction

from core.runtime_env import (
    PROCESS_WORKER,
    AsaasParentKeyError,
    asaas_baas_enabled,
    build_asaas_webhook_ingress_url,
    get_asaas_parent_api_key,
    resolve_asaas_environment,
    resolve_public_base_url,
)
from financeiro.models import OrganizationAsaasSubaccount
from financeiro.permissions import pode_provisionar_subconta_asaas
from financeiro.services.asaas_client import (
    AsaasClient,
    AsaasClientError,
    AsaasHttpError,
    AsaasHttpResponse,
    AsaasTimeoutError,
    CONCLUSIVE_CLIENT_STATUSES,
    LIST_ACCOUNT_FILTERS,
)
from financeiro.services.asaas_onboarding import (
    AsaasOnboardingError,
    build_create_account_payload,
    get_onboarding,
    missing_onboarding_fields,
)
from financeiro.services.asaas_subaccounts import (
    ALLOWED_TRANSITIONS,
    persist_public_ids,
    sanitize_error_code,
    transition,
)
from organizacoes.models import Organization
from organizacoes.provider_credentials import get as vault_get
from organizacoes.provider_credentials import put as vault_put

logger = logging.getLogger(__name__)

WEBHOOK_TOKEN_BYTES = 32


class AsaasProvisionError(Exception):
    """Falha sanitizada de provisionamento. Sem secret."""

    def __init__(self, reason: str, missing_fields: tuple[str, ...] = ()):
        self.reason = reason
        self.missing_fields = tuple(missing_fields)
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"<AsaasProvisionError reason={self.reason}>"


@dataclass(frozen=True)
class ProvisionResult:
    status: str
    reason: str
    missing_fields: tuple[str, ...] = field(default_factory=tuple)


def _require_organization(organization) -> Organization:
    if organization is None or not isinstance(organization, Organization):
        raise AsaasProvisionError("missing_org")
    if organization.pk is None:
        raise AsaasProvisionError("missing_org")
    return organization


def _require_finance_write(*, user) -> None:
    if not pode_provisionar_subconta_asaas(user):
        raise AsaasProvisionError("forbidden")


def _require_web_parent_key() -> str:
    try:
        return get_asaas_parent_api_key()
    except AsaasParentKeyError as exc:
        reason = exc.reason
        if reason == PROCESS_WORKER:
            raise AsaasProvisionError("worker") from None
        if reason in {"not_web", "missing", "invalid_process", "worker"}:
            raise AsaasProvisionError(reason) from None
        raise AsaasProvisionError("not_web") from None


def _new_webhook_token() -> str:
    token = secrets.token_urlsafe(WEBHOOK_TOKEN_BYTES)
    if " " in token or len(token) < 32:
        raise AsaasProvisionError("invalid_webhook_token")
    return token


def _new_public_id():
    return uuid.uuid4()


def _safe_vault_get(*, organization) -> dict:
    try:
        return vault_get(organization=organization, provider="asaas") or {}
    except Exception:
        return {}


def _existing_webhook_token(*, organization) -> str:
    payload = _safe_vault_get(organization=organization)
    return str(payload.get("webhook_token") or "").strip()


def _store_vault(*, organization, api_key: str | None, webhook_token: str) -> None:
    existing = _safe_vault_get(organization=organization)
    secrets_payload = {"webhook_token": webhook_token}
    stored_key = str(api_key or existing.get("api_key") or "").strip()
    if stored_key:
        secrets_payload["api_key"] = stored_key
    if stored_key and stored_key == webhook_token:
        raise AsaasProvisionError("invalid_webhook_token")
    last_error = None
    for _ in range(2):
        try:
            vault_put(organization=organization, provider="asaas", secrets=secrets_payload)
            return
        except Exception as exc:
            last_error = exc
    raise AsaasProvisionError("vault_persist_failed") from last_error


def _lock_subaccount(*, organization) -> tuple[OrganizationAsaasSubaccount | None, Organization]:
    last_exc = None
    for attempt in range(8):
        try:
            org = Organization.objects.select_for_update().get(pk=organization.pk)
            row = (
                OrganizationAsaasSubaccount.objects.select_for_update()
                .filter(organization=org)
                .first()
            )
            return row, org
        except Organization.DoesNotExist:
            raise AsaasProvisionError("missing_org")
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_exc = exc
            time.sleep(0.02 * (attempt + 1))
    org = Organization.objects.get(pk=organization.pk)
    row = OrganizationAsaasSubaccount.objects.filter(organization=org).first()
    if row is None:
        raise AsaasProvisionError("already_provisioning") from last_exc
    if row.status == OrganizationAsaasSubaccount.Status.CONFIGURED:
        raise AsaasProvisionError("already_configured")
    raise AsaasProvisionError("already_provisioning") from last_exc


def _claim_provisioning(*, organization, environment, parent_key: str) -> OrganizationAsaasSubaccount:
    """Transação curta: lock, PROVISIONING, webhook_token e webhook_public_id. Sem HTTP."""
    with transaction.atomic():
        row, org = _lock_subaccount(organization=organization)
        if row is None:
            last_exc = None
            row = None
            for attempt in range(8):
                try:
                    row = OrganizationAsaasSubaccount.objects.create(
                        organization=org,
                        environment=environment,
                        status=OrganizationAsaasSubaccount.Status.PROVISIONING,
                        webhook_public_id=_new_public_id(),
                    )
                    break
                except IntegrityError as exc:
                    raise AsaasProvisionError("already_provisioning") from exc
                except OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    last_exc = exc
                    existing = (
                        OrganizationAsaasSubaccount.objects.filter(organization=org).first()
                    )
                    if existing is not None:
                        raise AsaasProvisionError("already_provisioning") from exc
                    time.sleep(0.02 * (attempt + 1))
            if row is None:
                raise AsaasProvisionError("already_provisioning") from last_exc
        elif row.status == OrganizationAsaasSubaccount.Status.CONFIGURED:
            raise AsaasProvisionError("already_configured")
        elif row.status == OrganizationAsaasSubaccount.Status.PROVISIONING:
            raise AsaasProvisionError("already_provisioning")
        elif row.status == OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED:
            raise AsaasProvisionError("recovery_required")
        elif row.status == OrganizationAsaasSubaccount.Status.ERROR:
            row = transition(
                organization=org,
                to_status=OrganizationAsaasSubaccount.Status.PROVISIONING,
            )
            row = (
                OrganizationAsaasSubaccount.objects.select_for_update()
                .filter(pk=row.pk)
                .first()
            )
        else:
            raise AsaasProvisionError("invalid_status")
        if row.webhook_public_id is None:
            row.webhook_public_id = _new_public_id()
            row.save(update_fields=["webhook_public_id", "updated_at"])
        token = _existing_webhook_token(organization=org) or _new_webhook_token()
        if token == parent_key:
            token = _new_webhook_token()
        _store_vault(organization=org, api_key=None, webhook_token=token)
        return row


def _apply_locked_outcome(
    row: OrganizationAsaasSubaccount,
    *,
    to_status: str,
    last_error_code: str = "",
    account_id=None,
    wallet_id=None,
) -> OrganizationAsaasSubaccount:
    allowed = ALLOWED_TRANSITIONS.get(row.status, frozenset())
    if to_status not in allowed:
        raise AsaasProvisionError("invalid_status")
    if account_id is not None:
        row.account_id = (account_id or "").strip() or None
    if wallet_id is not None:
        row.wallet_id = (wallet_id or "").strip() or None
    row.status = to_status
    fields = ["status", "account_id", "wallet_id", "updated_at"]
    if last_error_code or to_status == OrganizationAsaasSubaccount.Status.ERROR:
        row.last_error_code = sanitize_error_code(last_error_code)
        fields.append("last_error_code")
    row.save(update_fields=fields)
    return row


def _finalize_provision(
    *,
    organization,
    row_id: int,
    to_status: str,
    last_error_code: str = "",
    account_id=None,
    wallet_id=None,
    api_key: str | None = None,
    webhook_token: str | None = None,
) -> OrganizationAsaasSubaccount:
    """Transação curta após o HTTP. Relê/locka a linha e persiste o resultado."""
    with transaction.atomic():
        row = (
            OrganizationAsaasSubaccount.objects.select_for_update()
            .filter(pk=row_id, organization=organization)
            .first()
        )
        if row is None:
            raise AsaasProvisionError("missing_subaccount")
        if row.status != OrganizationAsaasSubaccount.Status.PROVISIONING:
            if (
                to_status == OrganizationAsaasSubaccount.Status.CONFIGURED
                and row.status == OrganizationAsaasSubaccount.Status.CONFIGURED
            ):
                return row
            raise AsaasProvisionError("invalid_status")
        if api_key and webhook_token:
            try:
                _store_vault(
                    organization=organization,
                    api_key=api_key,
                    webhook_token=webhook_token,
                )
            except AsaasProvisionError:
                return _apply_locked_outcome(
                    row,
                    to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
                    last_error_code="VAULT_PERSIST_FAILED",
                    account_id=account_id,
                    wallet_id=wallet_id,
                )
        return _apply_locked_outcome(
            row,
            to_status=to_status,
            last_error_code=last_error_code,
            account_id=account_id,
            wallet_id=wallet_id,
        )


def _capture_create_response(payload) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise AsaasProvisionError("invalid_response")
    api_key = str(payload.get("apiKey") or "").strip()
    access = payload.get("accessToken")
    if not api_key and isinstance(access, dict):
        api_key = str(access.get("apiKey") or "").strip()
    wallet_id = str(payload.get("walletId") or "").strip()
    account_id = str(payload.get("id") or "").strip()
    if not api_key or not wallet_id or not account_id:
        raise AsaasProvisionError("invalid_response")
    return api_key, account_id, wallet_id


def _parent_client(*, api_key: str, environment: str, transport) -> AsaasClient:
    return AsaasClient(
        environment=environment,
        api_key=api_key,
        transport=transport,
    )


def _webhook_url_for(row: OrganizationAsaasSubaccount) -> str:
    debug = bool(getattr(settings, "DEBUG", False))
    base = getattr(settings, "PUBLIC_BASE_URL", "") or ""
    return build_asaas_webhook_ingress_url(
        public_id=row.webhook_public_id,
        base_url=base,
        debug=debug,
    )


def provision_organization_subaccount(
    *,
    organization,
    user,
    transport=None,
) -> ProvisionResult:
    if not asaas_baas_enabled():
        raise AsaasProvisionError("baas_disabled")
    org = _require_organization(organization)
    _require_finance_write(user=user)
    parent_key = _require_web_parent_key()
    environment = resolve_asaas_environment(getattr(settings, "ASAAS_ENVIRONMENT", None))
    onboarding = get_onboarding(organization=org)
    missing = missing_onboarding_fields(onboarding)
    if missing:
        raise AsaasProvisionError("missing_onboarding_fields", missing_fields=tuple(missing))
    try:
        resolve_public_base_url(
            getattr(settings, "PUBLIC_BASE_URL", "") or "",
            debug=bool(getattr(settings, "DEBUG", False)),
        )
    except Exception as exc:
        raise AsaasProvisionError("missing_webhook_url") from exc
    dummy_token = _new_webhook_token()
    try:
        build_create_account_payload(
            organization=org,
            webhook_token=dummy_token,
            webhook_url=build_asaas_webhook_ingress_url(
                public_id=_new_public_id(),
                base_url=getattr(settings, "PUBLIC_BASE_URL", "") or "",
                debug=bool(getattr(settings, "DEBUG", False)),
            ),
        )
    except AsaasOnboardingError as exc:
        raise AsaasProvisionError(exc.reason, missing_fields=exc.missing_fields) from exc

    try:
        row = _claim_provisioning(
            organization=org, environment=environment, parent_key=parent_key
        )
    except AsaasProvisionError as exc:
        if exc.reason == "already_configured":
            return ProvisionResult(
                status=OrganizationAsaasSubaccount.Status.CONFIGURED,
                reason="already_configured",
            )
        raise
    logger.info(
        "asaas.provision claimed status=%s org_id=%s",
        row.status,
        org.pk,
    )
    webhook_token = _existing_webhook_token(organization=org)
    if not webhook_token:
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.ERROR,
            last_error_code="VAULT_PERSIST_FAILED",
        )
        raise AsaasProvisionError("vault_persist_failed")
    payload = build_create_account_payload(
        organization=org,
        webhook_token=webhook_token,
        webhook_url=_webhook_url_for(row),
    )
    if payload["webhooks"][0]["authToken"] == parent_key:
        raise AsaasProvisionError("invalid_webhook_token")

    client = _parent_client(api_key=parent_key, environment=environment, transport=transport)
    try:
        response = client.create_account(payload)
    except AsaasTimeoutError:
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code="TIMEOUT",
        )
        raise AsaasProvisionError("timeout") from None
    except AsaasHttpError as exc:
        status = int(exc.status or 0)
        code = sanitize_error_code(exc.error_code or "") or exc.reason
        logger.info(
            "asaas.provision http_error status=%s code=%s step=create_account org_id=%s",
            status,
            code,
            org.pk,
        )
        if status in CONCLUSIVE_CLIENT_STATUSES:
            _finalize_provision(
                organization=org,
                row_id=row.pk,
                to_status=OrganizationAsaasSubaccount.Status.ERROR,
                last_error_code=exc.error_code or exc.reason,
            )
            raise AsaasProvisionError("http_error") from None
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code=exc.reason or "INCONCLUSIVE",
        )
        raise AsaasProvisionError("inconclusive") from None
    except AsaasClientError as exc:
        code = "HTTP_DISABLED" if exc.reason == "http_disabled" else exc.reason
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code=code,
        )
        raise AsaasProvisionError(
            "http_disabled" if exc.reason == "http_disabled" else "inconclusive"
        ) from None

    if not isinstance(response, AsaasHttpResponse) or response.status != 200:
        status = getattr(response, "status", 0)
        if status in CONCLUSIVE_CLIENT_STATUSES:
            _finalize_provision(
                organization=org,
                row_id=row.pk,
                to_status=OrganizationAsaasSubaccount.Status.ERROR,
                last_error_code=f"HTTP_{status}",
            )
            raise AsaasProvisionError("http_error")
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code=f"HTTP_{status}" if status else "INCONCLUSIVE",
        )
        raise AsaasProvisionError("inconclusive")

    account_id = wallet_id = None
    try:
        api_key, account_id, wallet_id = _capture_create_response(response.payload)
    except AsaasProvisionError:
        if isinstance(response.payload, dict):
            account_id = str(response.payload.get("id") or "").strip() or None
            wallet_id = str(response.payload.get("walletId") or "").strip() or None
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code="MISSING_API_KEY",
            account_id=account_id,
            wallet_id=wallet_id,
        )
        raise AsaasProvisionError("invalid_response") from None

    if api_key == webhook_token:
        _finalize_provision(
            organization=org,
            row_id=row.pk,
            to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
            last_error_code="WEBHOOK_TOKEN_COLLISION",
            account_id=account_id,
            wallet_id=wallet_id,
        )
        raise AsaasProvisionError("invalid_webhook_token")

    finalized = _finalize_provision(
        organization=org,
        row_id=row.pk,
        to_status=OrganizationAsaasSubaccount.Status.CONFIGURED,
        account_id=account_id,
        wallet_id=wallet_id,
        api_key=api_key,
        webhook_token=webhook_token,
    )
    if finalized.status != OrganizationAsaasSubaccount.Status.CONFIGURED:
        raise AsaasProvisionError("vault_persist_failed")
    logger.info(
        "asaas.provision configured org_id=%s status=%s",
        org.pk,
        finalized.status,
    )
    return ProvisionResult(status=finalized.status, reason="configured")


def lookup_existing_subaccount(
    *,
    organization,
    user,
    transport=None,
) -> dict | None:
    """GET /v3/accounts só com filtros oficiais. Não gera apiKey. Não faz POST."""
    if not asaas_baas_enabled():
        raise AsaasProvisionError("baas_disabled")
    org = _require_organization(organization)
    _require_finance_write(user=user)
    parent_key = _require_web_parent_key()
    environment = resolve_asaas_environment(getattr(settings, "ASAAS_ENVIRONMENT", None))
    onboarding = get_onboarding(organization=org)
    row = OrganizationAsaasSubaccount.objects.filter(organization=org).first()
    filters: dict[str, str] = {}
    if onboarding is not None:
        cpf = "".join(ch for ch in (onboarding.cpf_cnpj or "") if ch.isdigit())
        if cpf:
            filters["cpfCnpj"] = cpf
        if onboarding.email:
            filters["email"] = str(onboarding.email).strip()
        if onboarding.legal_name:
            filters["name"] = str(onboarding.legal_name).strip()
    if row and row.wallet_id:
        filters["walletId"] = row.wallet_id
    unsupported = set(filters) - LIST_ACCOUNT_FILTERS
    if unsupported:
        raise AsaasProvisionError("unsupported_filter")
    if "externalReference" in filters:
        raise AsaasProvisionError("unsupported_filter")
    client = _parent_client(api_key=parent_key, environment=environment, transport=transport)
    try:
        response = client.list_accounts(**filters)
    except AsaasTimeoutError:
        raise AsaasProvisionError("timeout") from None
    except AsaasClientError:
        raise AsaasProvisionError("inconclusive") from None
    payload = response.payload if isinstance(response, AsaasHttpResponse) else None
    data = []
    if isinstance(payload, dict):
        raw_data = payload.get("data")
        if isinstance(raw_data, list):
            data = raw_data
    match = data[0] if data and isinstance(data[0], dict) else None
    if match and row is not None:
        persist_public_ids(
            organization=org,
            account_id=str(match.get("id") or "").strip() or None,
            wallet_id=str(match.get("walletId") or "").strip() or None,
        )
        vault = vault_get(organization=org, provider="asaas") or {}
        if not vault.get("api_key") and row.status != OrganizationAsaasSubaccount.Status.CONFIGURED:
            if row.status != OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED:
                try:
                    transition(
                        organization=org,
                        to_status=OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
                        last_error_code="MISSING_API_KEY",
                    )
                except Exception:
                    pass
    return match
