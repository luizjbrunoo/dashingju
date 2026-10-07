"""Helpers numéricos compartilhados."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_QTD = Decimal("0.01")
ZERO = Decimal("0")

# Amostra mínima para exibir taxas de conversão / projeção reversa.
MIN_AMOSTRAS_CONVERSAO = 5


def money(valor) -> Decimal:
    if valor is None:
        return ZERO
    return Decimal(str(valor)).quantize(_QTD, rounding=ROUND_HALF_UP)


def safe_div(numerador, denominador) -> Decimal | None:
    if not denominador:
        return None
    return money(Decimal(numerador) / Decimal(denominador))


def safe_pct(parte: int | Decimal, total: int | Decimal) -> Decimal | None:
    if not total:
        return None
    return money(100 * Decimal(parte) / Decimal(total))
