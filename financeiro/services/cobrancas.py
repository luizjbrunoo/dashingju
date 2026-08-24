"""Utilitários de domínio para cobranças."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from financeiro.choices import DIAS_VENCE_EM_BREVE, StatusCobranca


def total_recebido(cobranca) -> Decimal:
    if not cobranca.pk:
        return Decimal("0")
    agg = cobranca.recebimentos.filter(cancelado_em__isnull=True).aggregate(
        total=Sum("valor")
    )
    return agg["total"] or Decimal("0")


def saldo_cobranca(cobranca) -> Decimal:
    return cobranca.valor_original - total_recebido(cobranca)


def resolver_status_cobranca(cobranca, *, hoje=None, saldo=None) -> str:
    if cobranca.status == StatusCobranca.CANCELED:
        return StatusCobranca.CANCELED
    if cobranca.status == StatusCobranca.DRAFT:
        return StatusCobranca.DRAFT

    if saldo is None:
        saldo = saldo_cobranca(cobranca)
    if saldo <= Decimal("0"):
        return StatusCobranca.PAID
    if saldo < cobranca.valor_original:
        base = StatusCobranca.PARTIALLY_PAID
    else:
        base = StatusCobranca.PENDING

    hoje = hoje or timezone.localdate()
    if cobranca.data_vencimento < hoje:
        return StatusCobranca.OVERDUE
    dias = (cobranca.data_vencimento - hoje).days
    if dias <= DIAS_VENCE_EM_BREVE and base in (
        StatusCobranca.PENDING,
        StatusCobranca.PARTIALLY_PAID,
    ):
        return StatusCobranca.DUE_SOON
    return base


def sincronizar_status_cobranca(cobranca, *, hoje=None, salvar: bool = False) -> str:
    novo = resolver_status_cobranca(cobranca, hoje=hoje)
    if cobranca.status != StatusCobranca.CANCELED and cobranca.status != StatusCobranca.DRAFT:
        cobranca.status = novo
        if salvar:
            cobranca.save(update_fields=["status", "atualizado_em"])
    return novo
