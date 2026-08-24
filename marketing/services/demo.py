"""Dados fictícios para o dashboard de marketing (demonstração, sem API Google Ads)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class DailyTotals:
    date_label: str
    clicks: int
    impressions: int
    cost_micros: int


@dataclass
class FunnelStage:
    key: str
    label: str
    value: int
    rate_from_previous: float | None
    rate_from_top: float | None


def _nicho_multiplier(nicho: str) -> float:
    if not nicho:
        return 1.0
    seed = sum(ord(c) for c in nicho)
    return 0.55 + (seed % 45) / 100.0


def demo_conversion_funnel(
    impressions: int,
    clicks: int,
    *,
    nicho: str = "",
) -> list[FunnelStage]:
    """Funil sintético coerente com impressões/cliques (demonstração)."""
    mult = _nicho_multiplier(nicho)
    lead_rate = 0.11 * (0.85 + (mult - 0.55) * 0.5)
    schedule_rate = 0.38 * (0.9 + (mult - 0.55) * 0.4)
    contract_rate = 0.45 * (0.88 + (mult - 0.55) * 0.35)

    leads = max(0, round(clicks * lead_rate)) if clicks else 0
    agendamentos = max(0, round(leads * schedule_rate)) if leads else 0
    contratos = max(0, round(agendamentos * contract_rate)) if agendamentos else 0

    stages = [
        ("impressions", "Impressões", impressions),
        ("clicks", "Cliques", clicks),
        ("leads", "Leads", leads),
        ("appointments", "Agendamentos", agendamentos),
        ("contracts", "Contratos fechados", contratos),
    ]

    out: list[FunnelStage] = []
    top = impressions if impressions > 0 else 1
    prev_value: int | None = None
    for key, label, value in stages:
        rate_prev = (
            (100.0 * value / prev_value) if prev_value is not None and prev_value > 0 else None
        )
        rate_top = (100.0 * value / top) if top > 0 else None
        out.append(
            FunnelStage(
                key=key,
                label=label,
                value=value,
                rate_from_previous=rate_prev,
                rate_from_top=rate_top,
            )
        )
        prev_value = value
    return out


def demo_daily_series(days: int = 30, *, nicho: str = "") -> list[DailyTotals]:
    """Série diária sintética com pequena variação (últimos `days` dias)."""
    mult = _nicho_multiplier(nicho)
    today = date.today()
    out: list[DailyTotals] = []
    base_clicks = int(40 * mult)
    base_impr = int(4200 * mult)
    base_cost_micros = int(95_000_000 * mult)
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        label = d.isoformat()
        w = 1.0 + 0.12 * ((i % 7) - 3) / 3
        noise = (i * 17) % 23
        clicks = max(5, int(base_clicks * w + noise % 15))
        impressions = max(500, int(base_impr * w + noise * 80))
        cost_micros = max(10_000_000, int(base_cost_micros * w + noise * 1_200_000))
        out.append(
            DailyTotals(
                date_label=label,
                clicks=clicks,
                impressions=impressions,
                cost_micros=cost_micros,
            )
        )
    return out
