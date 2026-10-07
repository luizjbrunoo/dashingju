"""Previsão de recebimentos por janelas de vencimento."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from financeiro.choices import StatusCobranca
from financeiro.services.cobranca_listagem import queryset_anotado_organization

JANELAS_PREVISAO = (
    ("0_30", "Próximos 30 dias", 0, 30),
    ("31_60", "31–60 dias", 31, 60),
    ("61_90", "61–90 dias", 61, 90),
)


@dataclass
class JanelaPrevisao:
    codigo: str
    rotulo: str
    total: Decimal
    quantidade: int
    data_inicio: date
    data_fim: date


@dataclass
class ResumoPrevisao:
    total_previsto: Decimal
    quantidade: int
    janelas: list[JanelaPrevisao]
    cumulativo_30: Decimal
    cumulativo_60: Decimal
    cumulativo_90: Decimal
    cobrancas: list


def _resumo_vazio(hoje: date) -> ResumoPrevisao:
    janelas = []
    for codigo, rotulo, offset_min, offset_max in JANELAS_PREVISAO:
        janelas.append(
            JanelaPrevisao(
                codigo=codigo,
                rotulo=rotulo,
                total=Decimal("0"),
                quantidade=0,
                data_inicio=hoje + timedelta(days=offset_min),
                data_fim=hoje + timedelta(days=offset_max),
            )
        )
    return ResumoPrevisao(
        total_previsto=Decimal("0"),
        quantidade=0,
        janelas=janelas,
        cumulativo_30=Decimal("0"),
        cumulativo_60=Decimal("0"),
        cumulativo_90=Decimal("0"),
        cobrancas=[],
    )


def calcular_previsao(organization, *, hoje: date | None = None) -> ResumoPrevisao:
    hoje = hoje or timezone.localdate()
    if organization is None:
        return _resumo_vazio(hoje)
    fim = hoje + timedelta(days=90)
    qs = (
        queryset_anotado_organization(organization)
        .exclude(status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT])
        .filter(saldo_calc__gt=0, data_vencimento__gte=hoje, data_vencimento__lte=fim)
        .select_related("cliente")
        .order_by("data_vencimento", "id")
    )
    cobrancas = list(qs)

    totais = {codigo: Decimal("0") for codigo, _, _, _ in JANELAS_PREVISAO}
    qtds = {codigo: 0 for codigo, _, _, _ in JANELAS_PREVISAO}
    total = Decimal("0")

    for cobranca in cobrancas:
        saldo = cobranca.saldo_calc
        cobranca.saldo_exibicao = saldo
        dias = (cobranca.data_vencimento - hoje).days
        codigo = _classificar_janela(dias)
        cobranca.janela_rotulo = next(
            (rotulo for c, rotulo, _, _ in JANELAS_PREVISAO if c == codigo),
            "",
        )
        totais[codigo] += saldo
        qtds[codigo] += 1
        total += saldo

    janelas = []
    for codigo, rotulo, offset_min, offset_max in JANELAS_PREVISAO:
        janelas.append(
            JanelaPrevisao(
                codigo=codigo,
                rotulo=rotulo,
                total=totais[codigo],
                quantidade=qtds[codigo],
                data_inicio=hoje + timedelta(days=offset_min),
                data_fim=hoje + timedelta(days=offset_max),
            )
        )

    cum_30 = totais["0_30"]
    cum_60 = cum_30 + totais["31_60"]
    cum_90 = cum_60 + totais["61_90"]

    return ResumoPrevisao(
        total_previsto=total,
        quantidade=len(cobrancas),
        janelas=janelas,
        cumulativo_30=cum_30,
        cumulativo_60=cum_60,
        cumulativo_90=cum_90,
        cobrancas=cobrancas,
    )


def _classificar_janela(dias_ate_vencimento: int) -> str:
    for codigo, _, min_d, max_d in JANELAS_PREVISAO:
        if min_d <= dias_ate_vencimento <= max_d:
            return codigo
    return "61_90"
