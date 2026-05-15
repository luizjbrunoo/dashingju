from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services import demo


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


@login_required
def dashboard(request):
    daily_raw = demo.demo_daily_series(30)

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

    chart_payload = {
        "historico": {
            "labels": [d.date_label for d in daily_raw],
            "clicks": [d.clicks for d in daily_raw],
            "impressions": [d.impressions for d in daily_raw],
            "cost": [round(d.cost_micros / 1_000_000, 4) for d in daily_raw],
        }
    }

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
            "kpi_trends": kpi_trends,
            "compare_note": "15 dias vs. 15 anteriores",
        },
    )
