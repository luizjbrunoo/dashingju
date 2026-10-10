"""Domínio de IDs Google Ads. Não é cofre e não transita status OAuth."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

_CUSTOMER_ID_RE = re.compile(r"^\d{8,12}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def normalize_google_ads_customer_id(raw) -> str:
    """Remove hífens/espaços. Vazio permanece vazio. Inválido falha fechado."""
    text = str(raw or "").strip()
    if not text:
        return ""
    digits = re.sub(r"[\s-]", "", text)
    if not _CUSTOMER_ID_RE.fullmatch(digits):
        raise ValidationError("Identificador de conta Google Ads inválido.")
    return digits


def normalize_google_ads_currency_code(raw) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    if not _CURRENCY_RE.fullmatch(text):
        raise ValidationError("Moeda Google Ads inválida.")
    return text


def format_google_ads_customer_id(raw) -> str:
    """Exibição 3-3-4. Sem secret. Inválido devolve vazio."""
    try:
        digits = normalize_google_ads_customer_id(raw)
    except ValidationError:
        return ""
    if not digits:
        return ""
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits
