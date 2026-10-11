"""Monta payload de subconta Asaas só com dados validados do onboarding."""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core.runtime_env import resolve_asaas_webhook_email, resolve_asaas_webhook_url
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

COMMON_FIELD_KEYS = (
    "legal_name",
    "email",
    "cpf_cnpj",
    "person_type",
    "mobile_phone",
    "income_value",
    "address",
    "address_number",
    "province",
    "postal_code",
)

WEBHOOK_CONFIG_NAME = "ADV Growth 90 - Account Status"
WEBHOOK_REQUIRED_KEYS = frozenset(
    {
        "name",
        "url",
        "email",
        "interrupted",
        "enabled",
        "authToken",
        "events",
        "sendType",
        "apiVersion",
    }
)

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


_BR_UF = frozenset(
    {
        "ac",
        "al",
        "ap",
        "am",
        "ba",
        "ce",
        "df",
        "es",
        "go",
        "ma",
        "mt",
        "ms",
        "mg",
        "pa",
        "pb",
        "pr",
        "pe",
        "pi",
        "rj",
        "rn",
        "rs",
        "ro",
        "rr",
        "sc",
        "sp",
        "se",
        "to",
    }
)
_BR_STATES = frozenset(
    {
        "acre",
        "alagoas",
        "amapa",
        "amazonas",
        "bahia",
        "ceara",
        "distrito federal",
        "espirito santo",
        "goias",
        "maranhao",
        "mato grosso",
        "mato grosso do sul",
        "minas gerais",
        "para",
        "paraiba",
        "parana",
        "pernambuco",
        "piaui",
        "rio de janeiro",
        "rio grande do norte",
        "rio grande do sul",
        "rondonia",
        "roraima",
        "santa catarina",
        "sao paulo",
        "sergipe",
        "tocantins",
    }
)
_BR_STATES_COMPACT = frozenset(name.replace(" ", "") for name in _BR_STATES)


def _fold_place(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def is_brazilian_state_or_uf(value) -> bool:
    """True se o texto for UF ou nome de estado, não um bairro."""
    folded = _fold_place(value)
    if not folded:
        return False
    for prefix in ("estado da ", "estado do ", "estado de ", "estado "):
        if folded.startswith(prefix):
            folded = folded[len(prefix) :].strip()
            break
    compact = folded.replace(" ", "")
    if folded in _BR_UF or compact in _BR_UF:
        return True
    if folded in _BR_STATES or compact in _BR_STATES_COMPACT:
        return True
    return False


def neighborhood_for_payload(raw) -> str:
    """Bairro local (OrganizationFinancialOnboarding.province) para o campo Asaas province."""
    value = _text(raw)
    if not value:
        raise ValueError("missing_neighborhood")
    if is_brazilian_state_or_uf(value):
        raise ValueError("state_as_neighborhood")
    return value


def normalize_optional_site(raw) -> str:
    """Normaliza site opcional. Vazio permanece vazio. Sem esquema → https://."""
    value = str(raw or "").strip()
    if not value:
        return ""
    if any(ch.isspace() for ch in value):
        raise ValueError("invalid_site")
    lowered = value.lower()
    if lowered.startswith(("javascript:", "data:", "file:", "vbscript:", "about:")):
        raise ValueError("invalid_site")
    candidate = value
    parsed = urlparse(candidate)
    if not parsed.scheme:
        if "://" in value or value.startswith("//"):
            raise ValueError("invalid_site")
        candidate = f"https://{value.lstrip('/')}"
        parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("invalid_site")
    if not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("invalid_site")
    host = parsed.hostname or ""
    if "." not in host or host.startswith(".") or host.endswith("."):
        raise ValueError("invalid_site")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "", "", parsed.query, ""))


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


def pending_fields_message(missing: list[str]) -> str:
    base = "Preencha os dados obrigatórios para ativar sua conta financeira."
    if not missing:
        return base
    return f"{base} Faltam: {', '.join(missing)}."


def missing_onboarding_fields(
    onboarding: OrganizationFinancialOnboarding | None,
) -> list[str]:
    if onboarding is None:
        return [FIELD_LABELS[key] for key in COMMON_FIELD_KEYS]
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
    try:
        neighborhood_for_payload(onboarding.province)
    except ValueError:
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
    try:
        webhook_email = resolve_asaas_webhook_email(
            getattr(settings, "ASAAS_WEBHOOK_EMAIL", "") or ""
        )
    except ImproperlyConfigured as exc:
        raise AsaasOnboardingError("missing_webhook_email") from exc
    try:
        site = normalize_optional_site(org_onboarding.site)
    except ValueError as exc:
        raise AsaasOnboardingError("invalid_site") from exc
    cpf_cnpj = _digits(org_onboarding.cpf_cnpj)
    payload: dict = {
        "name": _text(org_onboarding.legal_name),
        "email": _text(org_onboarding.email),
        "cpfCnpj": cpf_cnpj,
        "mobilePhone": _text(org_onboarding.mobile_phone),
        "incomeValue": _income_number(org_onboarding.income_value),
        "address": _text(org_onboarding.address),
        "addressNumber": _text(org_onboarding.address_number),
        "province": neighborhood_for_payload(org_onboarding.province),
        "postalCode": _digits(org_onboarding.postal_code),
        "webhooks": [
            {
                "name": WEBHOOK_CONFIG_NAME,
                "url": url,
                "email": webhook_email,
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
    if site:
        payload["site"] = site
    if org_onboarding.person_type == OrganizationFinancialOnboarding.PersonType.FISICA:
        payload["birthDate"] = org_onboarding.birth_date.isoformat()
    elif org_onboarding.person_type == OrganizationFinancialOnboarding.PersonType.JURIDICA:
        payload["companyType"] = org_onboarding.company_type
        payload["taxRegime"] = org_onboarding.tax_regime
    return payload
