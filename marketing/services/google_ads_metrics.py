"""Métricas Google Ads read-only por Organization. Sem Mutate, sem persistir token."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from marketing.google_ads_connection import normalize_google_ads_customer_id
from marketing.models import OrganizationGoogleAdsConnection
from marketing.services.google_ads_client import (
    GoogleAdsClientError,
    google_ads_client_for_organization,
)
from marketing.services.periodo import PeriodoMarketing

logger = logging.getLogger(__name__)

ALLOWED_PERIOD_DAYS = frozenset({7, 15, 30, 90})
DEFAULT_PERIOD_DAYS = 30
MICROS = Decimal("1000000")
_MONEY = Decimal("0.0001")
_QTY = Decimal("0.01")
_ZERO = Decimal("0")

MODE_DEMO = "demo"
MODE_LIVE = "live"
MODE_LIVE_ERROR = "live_error"

MSG_UNAVAILABLE = "Não foi possível carregar as métricas do Google Ads."
MSG_NOT_CONFIGURED = "Conta Google Ads não configurada."

_TOTALS_SELECT = (
    "metrics.clicks, metrics.impressions, metrics.cost_micros, "
    "metrics.ctr, metrics.conversions"
)
_DAILY_SELECT = (
    "segments.date, metrics.clicks, metrics.impressions, "
    "metrics.cost_micros, metrics.conversions"
)


class GoogleAdsMetricsError(Exception):
    """Falha sanitizada de leitura. Sem secret."""

    def __init__(self, reason: str, user_message: str | None = None):
        self.reason = reason
        self.user_message = user_message or MSG_UNAVAILABLE
        super().__init__(reason)


@dataclass(frozen=True)
class GoogleAdsKpis:
    clicks: int
    impressions: int
    cost_micros: int
    cost: Decimal
    ctr_percent: Decimal
    average_cpc: Decimal
    conversions: Decimal

    def __repr__(self) -> str:
        return (
            f"<GoogleAdsKpis clicks={self.clicks} "
            f"impressions={self.impressions}>"
        )


@dataclass(frozen=True)
class GoogleAdsDailyPoint:
    day: date
    clicks: int
    impressions: int
    cost: Decimal
    conversions: Decimal

    @property
    def date_label(self) -> str:
        return self.day.isoformat()

    def __repr__(self) -> str:
        return f"<GoogleAdsDailyPoint {self.date_label}>"


@dataclass(frozen=True)
class GoogleAdsMetricsSnapshot:
    kpis: GoogleAdsKpis
    series: tuple[GoogleAdsDailyPoint, ...]
    period_days: int
    customer_id: str

    def __repr__(self) -> str:
        return (
            f"<GoogleAdsMetricsSnapshot days={self.period_days} "
            f"points={len(self.series)}>"
        )


def sanitize_period_days(raw) -> int:
    try:
        days = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_PERIOD_DAYS
    if days in ALLOWED_PERIOD_DAYS:
        return days
    return DEFAULT_PERIOD_DAYS


def _iso(day: date) -> str:
    return day.strftime("%Y-%m-%d")


def _date_filter(periodo: PeriodoMarketing) -> str:
    return (
        f"segments.date BETWEEN '{_iso(periodo.data_inicio)}' "
        f"AND '{_iso(periodo.data_fim)}'"
    )


def totals_gaql(periodo: PeriodoMarketing) -> str:
    return (
        f"SELECT {_TOTALS_SELECT} FROM customer "
        f"WHERE {_date_filter(periodo)}"
    )


def daily_gaql(periodo: PeriodoMarketing) -> str:
    return (
        f"SELECT {_DAILY_SELECT} FROM customer "
        f"WHERE {_date_filter(periodo)} "
        f"ORDER BY segments.date"
    )


def _log_fail(reason: str, *, organization_id=None, customer_id: str = "") -> None:
    extra = f" reason={reason}"
    if organization_id is not None:
        extra += f" organization_id={organization_id}"
    if customer_id:
        extra += f" customer_id={customer_id}"
    logger.warning("google_ads_metrics_failed%s", extra)


def _enum_name(value) -> str:
    if value is None:
        return ""
    return str(getattr(value, "name", value) or "").upper()


def _classify_google_error(exc) -> str:
    try:
        errors = getattr(getattr(exc, "failure", None), "errors", None) or []
        for err in errors:
            code = getattr(err, "error_code", None)
            if code is None:
                continue
            for attr in (
                "authorization_error",
                "authentication_error",
                "quota_error",
                "request_error",
                "internal_error",
            ):
                val = getattr(code, attr, None)
                name = _enum_name(val)
                if not name or name in {"0", "UNSPECIFIED", "UNKNOWN"}:
                    continue
                if "CLOUD_PROJECT_NOT_APPROVED" in name:
                    return "api_access"
                if any(
                    token in name
                    for token in (
                        "OAUTH_TOKEN",
                        "REFRESH_TOKEN",
                        "REVOKED",
                        "EXPIRED",
                    )
                ):
                    return "token_revoked"
                if "AUTHENTICATION" in name or "NOT_ADS_USER" in name:
                    return "auth"
                if any(
                    token in name
                    for token in (
                        "RESOURCE_EXHAUSTED",
                        "RATE_EXCEEDED",
                        "QUOTA",
                    )
                ):
                    return "quota"
                if any(
                    token in name
                    for token in (
                        "USER_PERMISSION_DENIED",
                        "CUSTOMER_NOT_ENABLED",
                        "UNAUTHORIZED",
                    )
                ):
                    return "permission"
                if "DEADLINE" in name or "TIMEOUT" in name:
                    return "timeout"
    except Exception:
        pass
    text = type(exc).__name__
    raw = str(exc)
    if "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION" in raw:
        return "api_access"
    lowered = raw.lower()
    if "invalid_grant" in lowered or "unauthenticated" in lowered:
        return "token_revoked"
    if "refresherror" in text.lower():
        return "token_revoked"
    if "timeout" in lowered or "deadline" in lowered:
        return "timeout"
    if "resource_exhausted" in lowered or "quota" in lowered:
        return "quota"
    if "permission" in lowered:
        return "permission"
    return "api_failure"


def _as_int(raw) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _as_decimal(raw) -> Decimal:
    if raw is None:
        return _ZERO
    try:
        return Decimal(str(raw))
    except Exception:
        return _ZERO


def micros_to_decimal(raw) -> Decimal:
    return (_as_decimal(raw) / MICROS).quantize(_MONEY, rounding=ROUND_HALF_UP)


def cpc_from_cost_and_clicks(cost_micros, clicks) -> Decimal:
    """CPC médio a partir de cost_micros e clicks. Sem metrics.average_cpc."""
    clicks_n = _as_int(clicks)
    if clicks_n <= 0:
        return _ZERO
    return (
        _as_decimal(cost_micros) / Decimal(clicks_n) / MICROS
    ).quantize(_MONEY, rounding=ROUND_HALF_UP)


def ctr_to_percent(raw) -> Decimal:
    """API devolve razão (0.05 = 5%). Exibir como porcentagem."""
    return (_as_decimal(raw) * Decimal("100")).quantize(
        _QTY, rounding=ROUND_HALF_UP
    )


def _empty_kpis() -> GoogleAdsKpis:
    return GoogleAdsKpis(
        clicks=0,
        impressions=0,
        cost_micros=0,
        cost=_ZERO,
        ctr_percent=_ZERO,
        average_cpc=_ZERO,
        conversions=_ZERO,
    )


def _kpis_from_metrics(metrics) -> GoogleAdsKpis:
    if metrics is None:
        return _empty_kpis()
    cost_micros = _as_int(getattr(metrics, "cost_micros", 0))
    clicks = _as_int(getattr(metrics, "clicks", 0))
    impressions = _as_int(getattr(metrics, "impressions", 0))
    return GoogleAdsKpis(
        clicks=clicks,
        impressions=impressions,
        cost_micros=cost_micros,
        cost=micros_to_decimal(cost_micros),
        ctr_percent=ctr_to_percent(getattr(metrics, "ctr", 0)),
        average_cpc=cpc_from_cost_and_clicks(cost_micros, clicks),
        conversions=_as_decimal(getattr(metrics, "conversions", 0)).quantize(
            _QTY, rounding=ROUND_HALF_UP
        ),
    )


def _parse_segment_date(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _zero_point(day: date) -> GoogleAdsDailyPoint:
    return GoogleAdsDailyPoint(
        day=day,
        clicks=0,
        impressions=0,
        cost=_ZERO,
        conversions=_ZERO,
    )


def _fill_series(
    periodo: PeriodoMarketing, points: dict[date, GoogleAdsDailyPoint]
) -> tuple[GoogleAdsDailyPoint, ...]:
    filled: list[GoogleAdsDailyPoint] = []
    cursor = periodo.data_inicio
    while cursor <= periodo.data_fim:
        filled.append(points.get(cursor) or _zero_point(cursor))
        cursor += timedelta(days=1)
    return tuple(filled)


def _configured_connection(organization) -> OrganizationGoogleAdsConnection:
    if organization is None or getattr(organization, "pk", None) is None:
        _log_fail("missing_org")
        raise GoogleAdsMetricsError("missing_org")
    conn = (
        OrganizationGoogleAdsConnection.objects.filter(organization=organization)
        .first()
    )
    customer_id = ""
    if conn is not None:
        try:
            customer_id = normalize_google_ads_customer_id(conn.customer_id)
        except Exception:
            customer_id = ""
    if (
        conn is None
        or conn.status != OrganizationGoogleAdsConnection.Status.CONFIGURED
        or not customer_id
    ):
        _log_fail(
            "not_configured",
            organization_id=organization.pk,
            customer_id=customer_id,
        )
        raise GoogleAdsMetricsError("not_configured", MSG_NOT_CONFIGURED)
    return conn


def _search(client, customer_id: str, query: str):
    service = client.get_service("GoogleAdsService")
    return service.search(customer_id=customer_id, query=query)


def fetch_google_ads_metrics(
    *, organization, days
) -> GoogleAdsMetricsSnapshot:
    conn = _configured_connection(organization)
    customer_id = normalize_google_ads_customer_id(conn.customer_id)
    login_customer_id = ""
    try:
        login_customer_id = normalize_google_ads_customer_id(
            conn.login_customer_id
        )
    except Exception:
        login_customer_id = ""
    period_days = sanitize_period_days(days)
    periodo = PeriodoMarketing.ultimos_dias(period_days)
    try:
        client = google_ads_client_for_organization(
            organization=organization,
            login_customer_id=login_customer_id,
        )
        totals_rows = list(
            _search(client, customer_id, totals_gaql(periodo))
        )
        daily_rows = list(_search(client, customer_id, daily_gaql(periodo)))
    except GoogleAdsMetricsError:
        raise
    except GoogleAdsClientError as exc:
        _log_fail(
            exc.reason,
            organization_id=organization.pk,
            customer_id=customer_id,
        )
        raise GoogleAdsMetricsError(exc.reason, MSG_UNAVAILABLE) from None
    except Exception as exc:
        reason = _classify_google_error(exc)
        _log_fail(
            reason,
            organization_id=organization.pk,
            customer_id=customer_id,
        )
        raise GoogleAdsMetricsError(reason, MSG_UNAVAILABLE) from None

    totals_metrics = getattr(totals_rows[0], "metrics", None) if totals_rows else None
    kpis = _kpis_from_metrics(totals_metrics)
    by_day: dict[date, GoogleAdsDailyPoint] = {}
    for row in daily_rows:
        day = _parse_segment_date(getattr(getattr(row, "segments", None), "date", None))
        if day is None or not periodo.contem(day):
            continue
        metrics = getattr(row, "metrics", None)
        cost_micros = _as_int(getattr(metrics, "cost_micros", 0))
        by_day[day] = GoogleAdsDailyPoint(
            day=day,
            clicks=_as_int(getattr(metrics, "clicks", 0)),
            impressions=_as_int(getattr(metrics, "impressions", 0)),
            cost=micros_to_decimal(cost_micros),
            conversions=_as_decimal(getattr(metrics, "conversions", 0)).quantize(
                _QTY, rounding=ROUND_HALF_UP
            ),
        )
    return GoogleAdsMetricsSnapshot(
        kpis=kpis,
        series=_fill_series(periodo, by_day),
        period_days=period_days,
        customer_id=customer_id,
    )
