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


def demo_daily_series(days: int = 30) -> list[DailyTotals]:
    """Série diária sintética com pequena variação (últimos `days` dias)."""
    today = date.today()
    out: list[DailyTotals] = []
    base_clicks = 40
    base_impr = 4200
    base_cost_micros = 95_000_000
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
