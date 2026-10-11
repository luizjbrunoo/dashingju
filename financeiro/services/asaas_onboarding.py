"""Monta payload de subconta Asaas só com dados validados do onboarding."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured

from core.runtime_env import resolve_asaas_webhook_url
from financeiro.models import OrganizationFinancialOnboarding
from organizacoes.models import Organization

FIELD_LABELS = {
    "legal_name": "nome",
    "email": "e-mail",
    "cpf_cnpj": "CPF/CNPJ",
    "mobile_phone": "celular",
    "income_value": "renda/faturamento mensal",
    "address": "endereço",
    "address_number": "número",
    "province": "bairro",
    "postal_code": "CEP",
    "birth_date": "data de nascimento",
    "company_type": "tipo de empresa",
    "tax_regime": "regime tributário",
    "person_type": "tipo de pessoa",
}

# Eventos oficiais de situação cadastral (docs Asaas). Sem pagamentos nesta fase.
WEBHOOK_EVENTS = (
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


class AsaasOnboardingError(Exception):
    """Onboarding incompleto ou Organization ausente. Sem secret."""

    def __init__(self, reason: str, missing_fields: tuple[str, ...] = ()):
        self.reason = reason
        self.missing_fields = tuple(missing_fields)
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"<AsaasOnboardingError reason={self.reason}>"


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _text(value) -> str:
    return str(value or "").strip()


def _income_number(value) -> int | float:
    amount = Decimal(str(value))
    if amount <= 0:
        raise AsaasOnboardingError(
            "missing_onboarding_fields",
            missing_fields=(FIELD_LABELS["income_value"],),
        )
    as_int = int(amount)
    if Decimal(as_int) == amount:
        return as_int
    return float(amount)


def get_onboarding(*, organization) -> OrganizationFinancialOnboarding | None:
    if organization is None or not isinstance(organization, Organization):
        raise AsaasOnboardingError("missing_org")
    if organization.pk is None:
        raise AsaasOnboardingError("missing_org")
    return (
        OrganizationFinancialOnboarding.objects.filter(organization=organization).first()
    )


def missing_onboarding_fields(
    onboarding: OrganizationFinancialOnboarding | None,
) -> list[str]:
    if onboarding is None:
        return list(FIELD_LABELS.values())
    missing: list[str] = []
    if not _text(onboarding.legal_name):
        missing.append(FIELD_LABELS["legal_name"])
    if not _text(onboarding.email) or "@" not in _text(onboarding.email):
        missing.append(FIELD_LABELS["email"])
    cpf_cnpj = _digits(onboarding.cpf_cnpj)
    if len(cpf_cnpj) not in {11, 14}:
        missing.append(FIELD_LABELS["cpf_cnpj"])
    person_type = _text(onboarding.person_type)
    if person_type not in OrganizationFinancialOnboarding.PersonType.values:
        missing.append(FIELD_LABELS["person_type"])
    elif len(cpf_cnpj) == 11 and person_type != OrganizationFinancialOnboarding.PersonType.FISICA:
        missing.append(FIELD_LABELS["person_type"])
    elif len(cpf_cnpj) == 14 and person_type != OrganizationFinancialOnboarding.PersonType.JURIDICA:
        missing.append(FIELD_LABELS["person_type"])
    mobile = _digits(onboarding.mobile_phone)
    if len(mobile) < 10:
        missing.append(FIELD_LABELS["mobile_phone"])
    try:
        income = Decimal(str(onboarding.income_value or 0))
        if income <= 0:
            missing.append(FIELD_LABELS["income_value"])
    except Exception:
        missing.append(FIELD_LABELS["income_value"])
    if not _text(onboarding.address):
        missing.append(FIELD_LABELS["address"])
    if not _text(onboarding.address_number):
        missing.append(FIELD_LABELS["address_number"])
    if not _text(onboarding.province):
        missing.append(FIELD_LABELS["province"])
    if len(_digits(onboarding.postal_code)) != 8:
        missing.append(FIELD_LABELS["postal_code"])
    if person_type == OrganizationFinancialOnboarding.PersonType.FISICA:
        if onboarding.birth_date is None:
            missing.append(FIELD_LABELS["birth_date"])
    if person_type == OrganizationFinancialOnboarding.PersonType.JURIDICA:
        if onboarding.company_type not in OrganizationFinancialOnboarding.CompanyType.values:
            missing.append(FIELD_LABELS["company_type"])
        if onboarding.tax_regime not in OrganizationFinancialOnboarding.TaxRegime.values:
            missing.append(FIELD_LABELS["tax_regime"])
    return missing


def build_create_account_payload(
    *,
    organization,
    webhook_token: str,
    webhook_url: str | None = None,
) -> dict:
    org_onboarding = get_onboarding(organization=organization)
    missing = missing_onboarding_fields(org_onboarding)
    if missing:
        raise AsaasOnboardingError(
            "missing_onboarding_fields",
            missing_fields=tuple(missing),
        )
    assert org_onboarding is not None
    token = _text(webhook_token)
    if len(token) < 32 or " " in token:
        raise AsaasOnboardingError("invalid_webhook_token")
    try:
        url = resolve_asaas_webhook_url(webhook_url)
    except ImproperlyConfigured as exc:
        raise AsaasOnboardingError("missing_webhook_url") from exc
    cpf_cnpj = _digits(org_onboarding.cpf_cnpj)
    payload: dict = {
        "name": _text(org_onboarding.legal_name),
        "email": _text(org_onboarding.email),
        "cpfCnpj": cpf_cnpj,
        "mobilePhone": _text(org_onboarding.mobile_phone),
        "incomeValue": _income_number(org_onboarding.income_value),
        "address": _text(org_onboarding.address),
        "addressNumber": _text(org_onboarding.address_number),
        "province": _text(org_onboarding.province),
        "postalCode": _digits(org_onboarding.postal_code),
        "webhooks": [
            {
                "name": "ADV Growth 90",
                "url": url,
                "email": _text(org_onboarding.email),
                "sendType": "SEQUENTIALLY",
                "interrupted": False,
                "enabled": True,
                "apiVersion": 3,
                "authToken": token,
                "events": list(WEBHOOK_EVENTS),
            }
        ],
    }
    if _text(org_onboarding.phone):
        payload["phone"] = _text(org_onboarding.phone)
    if _text(org_onboarding.complement):
        payload["complement"] = _text(org_onboarding.complement)
    if _text(org_onboarding.site):
        payload["site"] = _text(org_onboarding.site)
    if org_onboarding.person_type == OrganizationFinancialOnboarding.PersonType.FISICA:
        payload["birthDate"] = org_onboarding.birth_date.isoformat()
    else:
        payload["companyType"] = org_onboarding.company_type
        payload["taxRegime"] = org_onboarding.tax_regime
    return payload
