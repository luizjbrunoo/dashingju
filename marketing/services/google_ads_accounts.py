"""Listagem e seleção de contas Google Ads por Organization. Sem Mutate."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from marketing.google_ads_connection import (
    format_google_ads_customer_id,
    normalize_google_ads_currency_code,
    normalize_google_ads_customer_id,
)
from marketing.models import OrganizationGoogleAdsConnection
from marketing.services.google_ads_client import (
    GoogleAdsClientError,
    google_ads_client_for_organization,
)

logger = logging.getLogger(__name__)

_SKIP_STATUSES = frozenset({"CANCELED", "CANCELLED", "CLOSED", "REMOVED"})
_CUSTOMER_QUERY = """
    SELECT
      customer.id,
      customer.descriptive_name,
      customer.currency_code,
      customer.manager,
      customer.status,
      customer.test_account
    FROM customer
    LIMIT 1
"""
_MCC_QUERY = """
    SELECT
      customer_client.id,
      customer_client.descriptive_name,
      customer_client.currency_code,
      customer_client.manager,
      customer_client.status,
      customer_client.test_account,
      customer_client.level
    FROM customer_client
    WHERE customer_client.level >= 1
"""

MSG_GENERIC = "Não foi possível listar as contas do Google Ads."
MSG_REAUTH = (
    "A autorização do Google Ads expirou ou está ausente. Conecte novamente."
)
MSG_CLOUD_PROJECT = (
    "O projeto Google Cloud ainda não tem acesso a contas de produção. "
    "A conexão permanece autorizada."
)
MSG_INACCESSIBLE = "A conta Google Ads selecionada não está acessível."
MSG_SELECT_INVALID = (
    "A conta selecionada não está disponível para este escritório."
)
MSG_SELECT_OK = "Conta Google Ads selecionada."
MSG_NOT_READY = "Conecte o Google Ads antes de escolher uma conta."


class GoogleAdsAccountsError(Exception):
    """Erro sanitizado de listagem/seleção. Sem secret."""

    def __init__(self, reason: str, user_message: str | None = None):
        self.reason = reason
        self.user_message = user_message or MSG_GENERIC
        super().__init__(reason)


@dataclass(frozen=True)
class GoogleAdsAccount:
    customer_id: str
    login_customer_id: str
    descriptive_name: str
    currency_code: str
    manager: bool = False
    status: str = ""
    test_account: bool = False

    @property
    def via_mcc(self) -> bool:
        return bool(self.login_customer_id)

    @property
    def customer_id_display(self) -> str:
        return format_google_ads_customer_id(self.customer_id)

    def __str__(self) -> str:
        return f"google_ads_account:{self.customer_id}"

    def __repr__(self) -> str:
        return (
            f"<GoogleAdsAccount {self.customer_id}"
            f"{' mcc' if self.via_mcc else ''}>"
        )


def _enum_name(value) -> str:
    if value is None:
        return ""
    return str(getattr(value, "name", value) or "").upper()


def _safe_customer_id(raw) -> str:
    try:
        return normalize_google_ads_customer_id(raw)
    except ValidationError:
        return ""


def _safe_currency(raw) -> str:
    try:
        return normalize_google_ads_currency_code(raw)
    except ValidationError:
        return ""


def _classify_google_error(exc) -> str:
    try:
        errors = getattr(getattr(exc, "failure", None), "errors", None) or []
        for err in errors:
            code = getattr(err, "error_code", None)
            if code is None:
                continue
            for attr in (
                "authorization_error",
                "authentication_error",
                "request_error",
            ):
                val = getattr(code, attr, None)
                name = _enum_name(val)
                if not name or name in {"0", "UNSPECIFIED", "UNKNOWN"}:
                    continue
                if "CLOUD_PROJECT_NOT_APPROVED" in name:
                    return "cloud_project_not_approved"
                if any(
                    token in name
                    for token in (
                        "OAUTH_TOKEN",
                        "REFRESH_TOKEN",
                        "REVOKED",
                        "EXPIRED",
                        "CUSTOMER_NOT_FOUND",
                    )
                ):
                    return "token_revoked"
                if "AUTHENTICATION" in name or "NOT_ADS_USER" in name:
                    return "auth"
                if any(
                    token in name
                    for token in (
                        "USER_PERMISSION_DENIED",
                        "CUSTOMER_NOT_ENABLED",
                        "UNAUTHORIZED",
                    )
                ):
                    return "inaccessible"
    except Exception:
        pass
    text = type(exc).__name__
    raw = str(exc)
    if "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION" in raw:
        return "cloud_project_not_approved"
    lowered = raw.lower()
    if "invalid_grant" in lowered or "unauthenticated" in lowered:
        return "token_revoked"
    if "refresherror" in text.lower():
        return "token_revoked"
    return "unexpected"


def _user_message_for(reason: str) -> str:
    if reason in {"missing_refresh", "token_revoked", "auth"}:
        return MSG_REAUTH
    if reason == "cloud_project_not_approved":
        return MSG_CLOUD_PROJECT
    if reason == "inaccessible":
        return MSG_INACCESSIBLE
    return MSG_GENERIC


def _raise_classified(exc) -> None:
    reason = _classify_google_error(exc)
    logger.warning("google_ads_accounts_failed reason=%s", reason)
    raise GoogleAdsAccountsError(reason, _user_message_for(reason)) from None


def _list_accessible_customer_ids(client) -> list[str]:
    service = client.get_service("CustomerService")
    response = service.list_accessible_customers()
    ids: list[str] = []
    for name in getattr(response, "resource_names", None) or []:
        cid = _safe_customer_id(str(name).rsplit("/", 1)[-1])
        if cid:
            ids.append(cid)
    return ids


def _customer_snapshot(
    client, customer_id: str, *, login_customer_id: str
) -> GoogleAdsAccount | None:
    previous = getattr(client, "login_customer_id", None)
    try:
        client.login_customer_id = login_customer_id or None
        ga_service = client.get_service("GoogleAdsService")
        rows = ga_service.search(customer_id=customer_id, query=_CUSTOMER_QUERY)
        row = next(iter(rows), None)
        if row is None:
            return None
        customer = row.customer
        cid = _safe_customer_id(getattr(customer, "id", customer_id))
        if not cid:
            return None
        status = _enum_name(getattr(customer, "status", None))
        return GoogleAdsAccount(
            customer_id=cid,
            login_customer_id=login_customer_id,
            descriptive_name=str(
                getattr(customer, "descriptive_name", "") or ""
            ).strip()[:255],
            currency_code=_safe_currency(getattr(customer, "currency_code", "")),
            manager=bool(getattr(customer, "manager", False)),
            status=status,
            test_account=bool(getattr(customer, "test_account", False)),
        )
    except Exception as exc:
        reason = _classify_google_error(exc)
        if reason in {"cloud_project_not_approved", "token_revoked", "auth"}:
            _raise_classified(exc)
        logger.warning(
            "google_ads_accounts_failed reason=customer_unreadable customer_id=%s",
            customer_id,
        )
        return None
    finally:
        client.login_customer_id = previous


def _mcc_clients(client, manager_id: str) -> list[GoogleAdsAccount]:
    previous = getattr(client, "login_customer_id", None)
    found: list[GoogleAdsAccount] = []
    try:
        client.login_customer_id = manager_id
        ga_service = client.get_service("GoogleAdsService")
        rows = ga_service.search(customer_id=manager_id, query=_MCC_QUERY)
        for row in rows:
            child = row.customer_client
            cid = _safe_customer_id(getattr(child, "id", ""))
            if not cid or cid == manager_id:
                continue
            if bool(getattr(child, "manager", False)):
                continue
            status = _enum_name(getattr(child, "status", None))
            found.append(
                GoogleAdsAccount(
                    customer_id=cid,
                    login_customer_id=manager_id,
                    descriptive_name=str(
                        getattr(child, "descriptive_name", "") or ""
                    ).strip()[:255],
                    currency_code=_safe_currency(
                        getattr(child, "currency_code", "")
                    ),
                    manager=False,
                    status=status,
                    test_account=bool(getattr(child, "test_account", False)),
                )
            )
    except Exception as exc:
        reason = _classify_google_error(exc)
        if reason in {"cloud_project_not_approved", "token_revoked", "auth"}:
            _raise_classified(exc)
        logger.warning(
            "google_ads_accounts_failed reason=mcc_unreadable customer_id=%s",
            manager_id,
        )
        return []
    finally:
        client.login_customer_id = previous
    return found


def _discover_accounts(client) -> list[GoogleAdsAccount]:
    accessible = _list_accessible_customer_ids(client)
    found: dict[tuple[str, str], GoogleAdsAccount] = {}
    managers: list[str] = []
    for cid in accessible:
        snapshot = _customer_snapshot(client, cid, login_customer_id="")
        if snapshot is None:
            continue
        if snapshot.status in _SKIP_STATUSES:
            continue
        found[(snapshot.customer_id, "")] = snapshot
        if snapshot.manager:
            managers.append(snapshot.customer_id)
    for manager_id in managers:
        for child in _mcc_clients(client, manager_id):
            if child.status in _SKIP_STATUSES:
                continue
            if (child.customer_id, "") in found:
                continue
            found[(child.customer_id, child.login_customer_id)] = child
    return sorted(
        found.values(),
        key=lambda item: (item.descriptive_name.lower(), item.customer_id),
    )


def list_accessible_accounts(*, organization) -> list[GoogleAdsAccount]:
    if organization is None or getattr(organization, "pk", None) is None:
        logger.warning("google_ads_accounts_failed reason=missing_org")
        raise GoogleAdsAccountsError("missing_org")
    try:
        client = google_ads_client_for_organization(organization=organization)
        return _discover_accounts(client)
    except GoogleAdsAccountsError:
        raise
    except GoogleAdsClientError as exc:
        reason = exc.reason
        if reason == "missing_refresh":
            reason = "missing_refresh"
        logger.warning("google_ads_accounts_failed reason=%s", reason)
        raise GoogleAdsAccountsError(reason, _user_message_for(reason)) from None
    except Exception as exc:
        _raise_classified(exc)


def _match_account(
    accounts: list[GoogleAdsAccount], *, customer_id: str, login_customer_id: str
) -> GoogleAdsAccount | None:
    for account in accounts:
        if (
            account.customer_id == customer_id
            and account.login_customer_id == login_customer_id
        ):
            return account
    return None


def select_google_ads_account(
    *, organization, customer_id, login_customer_id=""
) -> OrganizationGoogleAdsConnection:
    if organization is None or getattr(organization, "pk", None) is None:
        raise GoogleAdsAccountsError("missing_org")
    try:
        wanted_cid = normalize_google_ads_customer_id(customer_id)
        wanted_login = normalize_google_ads_customer_id(login_customer_id)
    except ValidationError:
        raise GoogleAdsAccountsError("invalid_id", MSG_SELECT_INVALID) from None
    if not wanted_cid:
        raise GoogleAdsAccountsError("invalid_id", MSG_SELECT_INVALID)

    accounts = list_accessible_accounts(organization=organization)
    match = _match_account(
        accounts, customer_id=wanted_cid, login_customer_id=wanted_login
    )
    if match is None:
        logger.warning(
            "google_ads_accounts_failed reason=not_accessible customer_id=%s",
            wanted_cid,
        )
        raise GoogleAdsAccountsError("not_accessible", MSG_SELECT_INVALID)

    with transaction.atomic():
        conn, _created = OrganizationGoogleAdsConnection.objects.get_or_create(
            organization=organization,
            defaults={"status": OrganizationGoogleAdsConnection.Status.CONFIGURED},
        )
        conn.status = OrganizationGoogleAdsConnection.Status.CONFIGURED
        conn.customer_id = match.customer_id
        conn.login_customer_id = match.login_customer_id
        conn.descriptive_name = match.descriptive_name
        conn.currency_code = match.currency_code
        conn.save(
            update_fields=[
                "status",
                "customer_id",
                "login_customer_id",
                "descriptive_name",
                "currency_code",
                "updated_at",
            ]
        )
    return conn
