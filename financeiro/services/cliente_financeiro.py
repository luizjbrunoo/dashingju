"""Resumo financeiro na ficha do cliente."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.urls import reverse
from urllib.parse import urlencode

from financeiro.choices import StatusCobranca, StatusContrato
from financeiro.models import Cobranca, Contrato
from financeiro.services.cobrancas import saldo_cobranca


@dataclass(frozen=True)
class ResumoFinanceiroCliente:
    a_receber: Decimal
    vencido: Decimal
    contratos_ativos: int
    cobrancas_abertas: int


def resumo_financeiro_cliente(usuario, cliente) -> ResumoFinanceiroCliente:
    cobrancas = Cobranca.objects.filter(
        usuario=usuario,
        cliente=cliente,
    ).exclude(status=StatusCobranca.CANCELED)

    a_receber = Decimal("0")
    vencido = Decimal("0")
    abertas = 0
    for c in cobrancas:
        saldo = saldo_cobranca(c)
        if saldo <= 0:
            continue
        abertas += 1
        a_receber += saldo
        if c.status == StatusCobranca.OVERDUE:
            vencido += saldo

    contratos_ativos = Contrato.objects.filter(
        usuario=usuario,
        cliente=cliente,
        status=StatusContrato.ACTIVE,
    ).count()

    return ResumoFinanceiroCliente(
        a_receber=a_receber,
        vencido=vencido,
        contratos_ativos=contratos_ativos,
        cobrancas_abertas=abertas,
    )


def cobrancas_cliente(usuario, cliente, *, limit: int = 5):
    return (
        Cobranca.objects.filter(usuario=usuario, cliente=cliente)
        .select_related("contrato", "responsavel")
        .order_by("data_vencimento", "id")[:limit]
    )


def contratos_cliente(usuario, cliente, *, limit: int = 5):
    return (
        Contrato.objects.filter(usuario=usuario, cliente=cliente)
        .order_by("-criado_em")[:limit]
    )


def url_cobrancas_cliente(cliente_id: int) -> str:
    return f"{reverse('financeiro_cobranca_listar')}?{urlencode({'cliente': cliente_id})}"


def url_nova_cobranca_cliente(cliente_id: int) -> str:
    return f"{reverse('financeiro_cobranca_nova')}?{urlencode({'cliente': cliente_id})}"


def url_novo_contrato_cliente(cliente_id: int) -> str:
    return f"{reverse('financeiro_contrato_novo')}?{urlencode({'cliente': cliente_id})}"
