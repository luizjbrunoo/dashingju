"""Métricas de receita e KPIs do Comercial."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from comercial.services.finance_org import contratos_organization, recebimentos_organization
from comercial.services.money import ZERO, money
from comercial.services.periodo import PeriodoComercial, filtro_datetime_campo
from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization


@dataclass(frozen=True)
class ReceitaPeriodo:
    recebido: Decimal
    contratado: Decimal
    contratos: int


@dataclass(frozen=True)
class KpisComerciais:
    meta_anual: Decimal | None
    meta_mensal: Decimal | None
    realizado_mes: Decimal
    realizado_ano: Decimal
    contratado_mes: Decimal
    contratado_ano: Decimal
    projecao_anual: Decimal | None
    gap_meta_anual: Decimal | None
    gap_meta_mensal: Decimal | None
    receita_em_aberto: Decimal
    receita_em_risco: Decimal
    tem_meta: bool
    ocultar_financeiro: bool


def receita_no_periodo(organization, periodo: PeriodoComercial) -> ReceitaPeriodo:
    recebido = (
        recebimentos_organization(organization)
        .filter(
            cancelado_em__isnull=True,
            data_recebimento__gte=periodo.data_inicio,
            data_recebimento__lte=periodo.data_fim,
        )
        .aggregate(total=Coalesce(Sum("valor"), ZERO))["total"]
        or ZERO
    )

    contratos_qs = contratos_organization(organization)
    contratos_qs = filtro_datetime_campo(
        contratos_qs,
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    agg = contratos_qs.aggregate(
        qtd=Count("pk"),
        total=Coalesce(Sum("valor_total"), ZERO),
    )
    return ReceitaPeriodo(
        recebido=money(recebido),
        contratado=money(agg["total"]),
        contratos=agg["qtd"] or 0,
    )


def periodo_mes(referencia: date | None = None) -> PeriodoComercial:
    hoje = referencia or timezone.localdate()
    return periodo_do_mes(hoje.year, hoje.month, cortar_hoje=True)


def periodo_do_mes(ano: int, mes: int, *, cortar_hoje: bool = True) -> PeriodoComercial:
    """Mês calendário. No mês corrente, o fim não passa de hoje."""
    ini = date(ano, mes, 1)
    fim = date(ano, mes, monthrange(ano, mes)[1])
    hoje = timezone.localdate()
    if cortar_hoje and ano == hoje.year and mes == hoje.month:
        fim = min(fim, hoje)
    return PeriodoComercial(data_inicio=ini, data_fim=fim)


def periodo_ano(referencia: date | None = None) -> PeriodoComercial:
    hoje = referencia or timezone.localdate()
    return PeriodoComercial(data_inicio=date(hoje.year, 1, 1), data_fim=hoje)


def calcular_kpis(
    organization,
    *,
    meta,
    receita_risco: Decimal,
    ocultar_financeiro: bool,
    projecao_anual: Decimal | None,
) -> KpisComerciais:
    mes = receita_no_periodo(organization, periodo_mes())
    ano = receita_no_periodo(organization, periodo_ano())
    kpis_cob = calcular_kpis_cobrancas_organization(organization)

    meta_anual = money(meta.meta_anual) if meta else None
    meta_mensal = money(meta.meta_mensal) if meta else None

    gap_anual = None
    gap_mensal = None
    if meta_anual is not None:
        gap_anual = money(max(ZERO, meta_anual - (projecao_anual or ano.contratado)))
    if meta_mensal is not None:
        gap_mensal = money(max(ZERO, meta_mensal - mes.contratado))

    if ocultar_financeiro:
        return KpisComerciais(
            meta_anual=None,
            meta_mensal=None,
            realizado_mes=ZERO,
            realizado_ano=ZERO,
            contratado_mes=ZERO,
            contratado_ano=ZERO,
            projecao_anual=None,
            gap_meta_anual=None,
            gap_meta_mensal=None,
            receita_em_aberto=ZERO,
            receita_em_risco=ZERO,
            tem_meta=bool(meta),
            ocultar_financeiro=True,
        )

    return KpisComerciais(
        meta_anual=meta_anual,
        meta_mensal=meta_mensal,
        realizado_mes=mes.contratado,
        realizado_ano=ano.contratado,
        contratado_mes=mes.contratado,
        contratado_ano=ano.contratado,
        projecao_anual=projecao_anual,
        gap_meta_anual=gap_anual,
        gap_meta_mensal=gap_mensal,
        receita_em_aberto=money(kpis_cob.a_receber),
        receita_em_risco=money(receita_risco),
        tem_meta=bool(meta),
        ocultar_financeiro=False,
    )


@dataclass(frozen=True)
class MetaVsRealizado:
    contratos_necessarios: int | None
    contratos_realizados: int
    contratos_faltantes: int | None
    receita_necessaria: Decimal | None
    receita_realizada: Decimal
    gap_receita: Decimal | None
    pct_atingido: Decimal | None
    mensagem: str


def meta_vs_realizado(organization, meta, ticket_valor: Decimal | None) -> MetaVsRealizado:
    mes = receita_no_periodo(organization, periodo_mes())
    if meta is None:
        return MetaVsRealizado(
            contratos_necessarios=None,
            contratos_realizados=mes.contratos,
            contratos_faltantes=None,
            receita_necessaria=None,
            receita_realizada=mes.contratado,
            gap_receita=None,
            pct_atingido=None,
            mensagem="Cadastre sua meta para acompanhar o progresso do mês.",
        )

    nec = None
    faltam = None
    if ticket_valor and ticket_valor > 0:
        from decimal import ROUND_UP

        nec = int(
            (Decimal(meta.meta_mensal) / Decimal(ticket_valor)).to_integral_value(
                rounding=ROUND_UP
            )
        )
        faltam = max(0, nec - mes.contratos)

    gap = money(max(ZERO, Decimal(meta.meta_mensal) - mes.contratado))
    pct = None
    if meta.meta_mensal > 0:
        pct = money(100 * mes.contratado / Decimal(meta.meta_mensal))

    return MetaVsRealizado(
        contratos_necessarios=nec,
        contratos_realizados=mes.contratos,
        contratos_faltantes=faltam,
        receita_necessaria=money(meta.meta_mensal),
        receita_realizada=mes.contratado,
        gap_receita=gap,
        pct_atingido=pct,
        mensagem="",
    )
