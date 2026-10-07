"""Formatação numérica pt-BR apenas para apresentação."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils.formats import number_format


def format_number_br(value, decimal_pos=None) -> str:
    """15.000 ou 15.000,50 — sem alterar o valor original."""
    if value is None:
        return ""
    if isinstance(value, str) and not value.strip():
        return ""
    if decimal_pos is not None and decimal_pos != "":
        try:
            decimal_pos = int(decimal_pos)
        except (TypeError, ValueError):
            decimal_pos = None
    else:
        decimal_pos = None
    try:
        if isinstance(value, float):
            value = Decimal(str(value))
        return number_format(
            value,
            decimal_pos=decimal_pos,
            force_grouping=True,
            use_l10n=True,
        )
    except (TypeError, ValueError, InvalidOperation):
        return str(value)


def format_currency_br(value, decimal_pos=2) -> str:
    """R$ 15.000,50 — prefixo alinhado aos templates atuais (R$ + número)."""
    formatted = format_number_br(value, decimal_pos=decimal_pos)
    if not formatted:
        return ""
    return f"R$ {formatted}"
