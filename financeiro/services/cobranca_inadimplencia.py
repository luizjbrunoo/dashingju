"""Análise de inadimplência e faixas de atraso."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from financeiro.choices import StatusCobranca
from financeiro.services.cobranca_listagem import queryset_anotado, sincronizar_statuses

FAIXAS_ATRASO = (
    ("1_30", "1–30 dias", 1, 30),
    ("31_60", "31–60 dias", 31, 60),
    ("61_90", "61–90 dias", 61, 90),
    ("90_mais", "Mais de 90 dias", 91, None),
)


@dataclass
class FaixaInadimplencia:
    codigo: str
    rotulo: str
    total: Decimal
    quantidade: int


@dataclass
class ClienteInadimplente:
    cliente_id: int
    cliente_nome: str
    saldo_vencido: Decimal
    cobrancas: int
    dias_atraso_max: int


@dataclass
class ResumoInadimplencia:
    total_vencido: Decimal
    quantidade: int
    faixas: list[FaixaInadimplencia]
    clientes: list[ClienteInadimplente]
    cobrancas: list


def _cobrancas_com_saldo(usuario):
    return queryset_anotado(usuario).exclude(
        status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT]
    ).filter(saldo_calc__gt=0)


def calcular_inadimplencia(usuario, *, hoje: date | None = None) -> ResumoInadimplencia:
    hoje = hoje or timezone.localdate()
    qs = (
        _cobrancas_com_saldo(usuario)
        .filter(data_vencimento__lt=hoje)
        .order_by("data_vencimento", "id")
    )
    cobrancas = list(qs)
    sincronizar_statuses(cobrancas, hoje=hoje)

    totais_faixa = {codigo: Decimal("0") for codigo, _, _, _ in FAIXAS_ATRASO}
    qtd_faixa = {codigo: 0 for codigo, _, _, _ in FAIXAS_ATRASO}
    por_cliente: dict[int, dict] = defaultdict(
        lambda: {"nome": "", "saldo": Decimal("0"), "qtd": 0, "max_atraso": 0}
    )
    total = Decimal("0")

    for cobranca in cobrancas:
        saldo = cobranca.saldo_calc
        dias = (hoje - cobranca.data_vencimento).days
        cobranca.dias_atraso = dias
        cobranca.saldo_exibicao = saldo
        total += saldo
        codigo = _classificar_faixa(dias)
        totais_faixa[codigo] += saldo
        qtd_faixa[codigo] += 1
        cid = cobranca.cliente_id
        por_cliente[cid]["nome"] = cobranca.cliente.nome
        por_cliente[cid]["saldo"] += saldo
        por_cliente[cid]["qtd"] += 1
        por_cliente[cid]["max_atraso"] = max(por_cliente[cid]["max_atraso"], dias)

    faixas = [
        FaixaInadimplencia(
            codigo=codigo,
            rotulo=rotulo,
            total=totais_faixa[codigo],
            quantidade=qtd_faixa[codigo],
        )
        for codigo, rotulo, _, _ in FAIXAS_ATRASO
    ]
    clientes = sorted(
        [
            ClienteInadimplente(
                cliente_id=cid,
                cliente_nome=dados["nome"],
                saldo_vencido=dados["saldo"],
                cobrancas=dados["qtd"],
                dias_atraso_max=dados["max_atraso"],
            )
            for cid, dados in por_cliente.items()
        ],
        key=lambda c: (-c.saldo_vencido, c.cliente_nome),
    )

    return ResumoInadimplencia(
        total_vencido=total,
        quantidade=len(cobrancas),
        faixas=faixas,
        clientes=clientes,
        cobrancas=cobrancas,
    )


def _classificar_faixa(dias_atraso: int) -> str:
    for codigo, _, min_d, max_d in FAIXAS_ATRASO:
        if max_d is None and dias_atraso >= min_d:
            return codigo
        if max_d and min_d <= dias_atraso <= max_d:
            return codigo
    return "1_30"
