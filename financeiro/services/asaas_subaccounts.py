"""Estado local da subconta Asaas por Organization. Sem provisionamento externo."""

from __future__ import annotations

import re

from core.runtime_env import resolve_asaas_environment
from financeiro.models import OrganizationAsaasSubaccount
from organizacoes.models import Organization

_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")

ALLOWED_TRANSITIONS = {
    OrganizationAsaasSubaccount.Status.PROVISIONING: frozenset(
        {
            OrganizationAsaasSubaccount.Status.CONFIGURED,
            OrganizationAsaasSubaccount.Status.ERROR,
            OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
        }
    ),
    OrganizationAsaasSubaccount.Status.CONFIGURED: frozenset(
        {
            OrganizationAsaasSubaccount.Status.ERROR,
            OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
        }
    ),
    OrganizationAsaasSubaccount.Status.ERROR: frozenset(
        {
            OrganizationAsaasSubaccount.Status.PROVISIONING,
        }
    ),
    OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED: frozenset(
        {
            OrganizationAsaasSubaccount.Status.CONFIGURED,
            OrganizationAsaasSubaccount.Status.ERROR,
            OrganizationAsaasSubaccount.Status.PROVISIONING,
        }
    ),
}


class AsaasSubaccountError(Exception):
    """Falha sanitizada de estado. Sem secret."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _require_organization(organization) -> Organization:
    if organization is None or not isinstance(organization, Organization):
        raise AsaasSubaccountError("missing_org")
    if organization.pk is None:
        raise AsaasSubaccountError("missing_org")
    return organization


def sanitize_error_code(raw) -> str:
    text = str(raw or "").strip().upper().replace(" ", "_")[:64]
    if not text or not _ERROR_CODE.match(text):
        return "unknown"
    return text


def get_subaccount(*, organization) -> OrganizationAsaasSubaccount | None:
    org = _require_organization(organization)
    return (
        OrganizationAsaasSubaccount.objects.filter(organization=org).first()
    )


def start_provisioning(*, organization, environment) -> OrganizationAsaasSubaccount:
    org = _require_organization(organization)
    env = resolve_asaas_environment(environment)
    existing = get_subaccount(organization=org)
    if existing is not None:
        return existing
    return OrganizationAsaasSubaccount.objects.create(
        organization=org,
        environment=env,
        status=OrganizationAsaasSubaccount.Status.PROVISIONING,
    )


def persist_public_ids(
    *,
    organization,
    account_id: str | None = None,
    wallet_id: str | None = None,
) -> OrganizationAsaasSubaccount:
    org = _require_organization(organization)
    row = get_subaccount(organization=org)
    if row is None:
        raise AsaasSubaccountError("missing_subaccount")
    if account_id is not None:
        row.account_id = (account_id or "").strip() or None
    if wallet_id is not None:
        row.wallet_id = (wallet_id or "").strip() or None
    row.save(update_fields=["account_id", "wallet_id", "updated_at"])
    return row


def transition(
    *,
    organization,
    to_status: str,
    last_error_code: str = "",
) -> OrganizationAsaasSubaccount:
    org = _require_organization(organization)
    row = get_subaccount(organization=org)
    if row is None:
        raise AsaasSubaccountError("missing_subaccount")
    target = str(to_status or "").strip()
    allowed = ALLOWED_TRANSITIONS.get(row.status, frozenset())
    if target not in allowed:
        raise AsaasSubaccountError("invalid_transition")
    row.status = target
    fields = ["status", "updated_at"]
    if target == OrganizationAsaasSubaccount.Status.ERROR:
        row.last_error_code = sanitize_error_code(last_error_code)
        fields.append("last_error_code")
    elif last_error_code:
        row.last_error_code = sanitize_error_code(last_error_code)
        fields.append("last_error_code")
    row.save(update_fields=fields)
    return row
