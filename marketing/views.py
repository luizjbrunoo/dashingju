from __future__ import annotations

from django.shortcuts import render

from marketing.decorators import login_e_perm_google_ads
from django.utils.dateparse import parse_date

from marketing.services.atribuicao import capturar_atribuicao_sessao
from marketing.services.resultados_campanhas import (
    ORDEN_CAC,
    ORDEN_CONTRATOS,
    ORDEN_CONVERSAO,
    ORDEN_LEADS,
    ORDEN_RECEITA,
    ORDEN_RECEITA_MIDIA,
)
from marketing.services.google_ads_resultados import contexto_dashboard_resultados
from marketing.services.periodo import PeriodoMarketing
from financeiro.tenancy_write import organization_for_finance_write

from . import nichos
from .services import campanha_demo, demo


def _sum_daily_slice(daily_slice: list[demo.DailyTotals]) -> tuple[int, int, int]:
    clicks = sum(d.clicks for d in daily_slice)
    impressions = sum(d.impressions for d in daily_slice)
    cost_micros = sum(d.cost_micros for d in daily_slice)
    return clicks, impressions, cost_micros


def _cpc(cost_micros: int, clicks: int) -> float | None:
    if clicks <= 0:
        return None
    return (cost_micros / 1_000_000) / clicks


def _ctr(clicks: int, impressions: int) -> float | None:
    if impressions <= 0:
        return None
    return 100.0 * clicks / impressions


def _pct_trend(current: float, previous: float) -> tuple[float | None, str]:
    """Devolve (percentagem de variação, 'up'|'down'|'flat')."""
    if previous == 0:
        if current == 0:
            return 0.0, "flat"
        return None, "up"
    pct = (current - previous) / previous * 100.0
    if pct > 0.5:
        return pct, "up"
    if pct < -0.5:
        return pct, "down"
    return pct, "flat"


def _periodo_request(request) -> PeriodoMarketing:
    dias_raw = (request.GET.get("dias") or "30").strip()
    try:
        dias = int(dias_raw)
    except ValueError:
        dias = 30
    dias = max(7, min(dias, 90))
    data_inicio = parse_date((request.GET.get("data_inicio") or "").strip())
    data_fim = parse_date((request.GET.get("data_fim") or "").strip())
    return PeriodoMarketing.from_parametros(
        data_inicio, data_fim, padrao_dias=dias
    )


@login_e_perm_google_ads
def dashboard(request):
    capturar_atribuicao_sessao(request)
    nicho_chave, nicho_label = nichos.resolver_nicho(request.GET.get("nicho"))
    periodo = _periodo_request(request)
    dias = periodo.dias

    campaign_plan = campanha_demo.demo_campaign_plan(nicho_chave)
    daily_raw = demo.demo_daily_series(max(dias, 14), nicho=nicho_chave)

    half = len(daily_raw) // 2
    prev_days = daily_raw[:half]
    recent_days = daily_raw[half:]

    p_clicks, p_impr, p_cost = _sum_daily_slice(prev_days)
    r_clicks, r_impr, r_cost = _sum_daily_slice(recent_days)

    totals_clicks, totals_impr, totals_cost_micros = _sum_daily_slice(daily_raw)
    totals = {
        "clicks": totals_clicks,
        "impressions": totals_impr,
        "cost_micros": totals_cost_micros,
    }
    totals_cost_value = totals_cost_micros / 1_000_000
    cost_per_click = _cpc(totals_cost_micros, totals_clicks)
    ctr_value = _ctr(totals_clicks, totals_impr)

    cpc_prev = _cpc(p_cost, p_clicks)
    cpc_recent = _cpc(r_cost, r_clicks)
    cpc_pct, cpc_trend = (None, "flat")
    if cpc_prev is not None and cpc_recent is not None and cpc_prev > 0:
        cpc_pct, cpc_trend = _pct_trend(cpc_recent, cpc_prev)
    elif cpc_prev is None and cpc_recent is not None and cpc_recent > 0:
        cpc_pct, cpc_trend = None, "up"

    clicks_pct, clicks_trend = _pct_trend(float(r_clicks), float(p_clicks))
    impr_pct, impr_trend = _pct_trend(float(r_impr), float(p_impr))
    cost_pct, cost_trend = _pct_trend(float(r_cost), float(p_cost))

    ctr_prev = _ctr(p_clicks, p_impr)
    ctr_recent = _ctr(r_clicks, r_impr)
    ctr_pct, ctr_trend = (None, "flat")
    if ctr_prev is not None and ctr_recent is not None and ctr_prev > 0:
        ctr_pct, ctr_trend = _pct_trend(ctr_recent, ctr_prev)
    elif (ctr_prev is None or ctr_prev == 0) and ctr_recent is not None and ctr_recent > 0:
        ctr_pct, ctr_trend = None, "up"

    conversion_funnel = demo.demo_conversion_funnel(totals_impr, totals_clicks, nicho=nicho_chave)
    funnel_shape = [(100, 84), (84, 68), (68, 52), (52, 36), (36, 22)]
    funnel_tiers = [
        {"stage": stage, "top_w": top_w, "bottom_w": bottom_w}
        for stage, (top_w, bottom_w) in zip(conversion_funnel, funnel_shape)
    ]

    ctx_resultados = contexto_dashboard_resultados(
        request.user,
        periodo,
        organization=organization_for_finance_write(request),
        modo_demo=True,
        nicho=nicho_chave,
        granularidade=(request.GET.get("granularidade") or "auto").strip(),
        orden_campanhas=(request.GET.get("orden_campanhas") or "contratos").strip(),
    )

    chart_payload = {
        "historico": {
            "labels": [d.date_label for d in daily_raw],
            "clicks": [d.clicks for d in daily_raw],
            "impressions": [d.impressions for d in daily_raw],
            "cost": [round(d.cost_micros / 1_000_000, 4) for d in daily_raw],
        },
    }
    if not ctx_resultados.ocultar_resultados_negocio:
        chart_payload["evolucao"] = ctx_resultados.evolucao.as_chart_dict()

    kpi_trends = {
        "clicks": {"pct": clicks_pct, "trend": clicks_trend},
        "impressions": {"pct": impr_pct, "trend": impr_trend},
        "cost": {"pct": cost_pct, "trend": cost_trend},
        "cpc": {"pct": cpc_pct, "trend": cpc_trend},
        "ctr": {"pct": ctr_pct, "trend": ctr_trend},
    }

    return render(
        request,
        "marketing/dashboard.html",
        {
            "totals": totals,
            "totals_cost_value": totals_cost_value,
            "cost_per_click": cost_per_click,
            "ctr_value": ctr_value,
            "chart_payload": chart_payload,
            "funnel_tiers": funnel_tiers,
            "nichos": nichos.NICHOS_ATUACAO,
            "nicho_selecionado": nicho_chave,
            "nicho_label": nicho_label,
            "campaign_plan": campaign_plan,
            "kpi_trends": kpi_trends,
            "compare_note": ctx_resultados.compare_note,
            "subnav_section": "google_ads",
            "periodo_dias": dias,
            "periodo_label": periodo.label(),
            "granularidade": ctx_resultados.granularidade,
            "orden_campanhas": ctx_resultados.ranking_campanhas.ordenacao,
            "orden_campanhas_opcoes": (
                (ORDEN_CONTRATOS, "Contratos"),
                (ORDEN_RECEITA, "Receita"),
                (ORDEN_LEADS, "Leads"),
                (ORDEN_CONVERSAO, "Conversão"),
                (ORDEN_CAC, "CAC mídia"),
                (ORDEN_RECEITA_MIDIA, "Receita / mídia"),
            ),
            "ctx_resultados": ctx_resultados,
        },
    )
