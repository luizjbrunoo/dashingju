"""Série temporal e comparação período a período — Fase 11."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate

from usuarios.br_format import format_currency_br, format_number_br
from marketing.services.atribuicao import clientes_google_ads
from marketing.services.google_ads_resultados import (
    ResultadosNegocio,
    _contratos_qs,
    _consultas_qs,
    calcular_tendencias_resultados,
)
from marketing.services.periodo import PeriodoMarketing
from usuarios.choices import StatusCompromisso

GRAN_DIA = "dia"
GRAN_SEMANA = "semana"
GRAN_MES = "mes"
GRAN_AUTO = "auto"

_GRANULARIDADES = frozenset({GRAN_DIA, GRAN_SEMANA, GRAN_MES, GRAN_AUTO})


def resolver_granularidade(periodo: PeriodoMarketing, param: str) -> str:
    p = (param or GRAN_AUTO).strip().lower()
    if p not in _GRANULARIDADES:
        p = GRAN_AUTO
    if p == GRAN_AUTO:
        if periodo.dias <= 31:
            return GRAN_DIA
        if periodo.dias <= 62:
            return GRAN_SEMANA
        return GRAN_MES
    return p


def _inicio_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _inicio_mes(d: date) -> date:
    return d.replace(day=1)


def _fim_mes(d: date) -> date:
    if d.month == 12:
        prox = d.replace(year=d.year + 1, month=1, day=1)
    else:
        prox = d.replace(month=d.month + 1, day=1)
    return prox - timedelta(days=1)


def iter_buckets(periodo: PeriodoMarketing, granularidade: str) -> list[tuple[str, date, date]]:
    """Rótulo e intervalo [início, fim] inclusivo de cada bucket."""
    if granularidade == GRAN_DIA:
        out: list[tuple[str, date, date]] = []
        d = periodo.data_inicio
        while d <= periodo.data_fim:
            out.append((d.strftime("%d/%m"), d, d))
            d += timedelta(days=1)
        return out

    if granularidade == GRAN_SEMANA:
        out = []
        d = _inicio_semana(periodo.data_inicio)
        while d <= periodo.data_fim:
            fim = min(d + timedelta(days=6), periodo.data_fim)
            ini = max(d, periodo.data_inicio)
            if ini <= fim:
                rotulo = (
                    ini.strftime("%d/%m")
                    if ini == fim
                    else f"{ini.strftime('%d/%m')}–{fim.strftime('%d/%m')}"
                )
                out.append((rotulo, ini, fim))
            d += timedelta(days=7)
        return out

    # mês
    out = []
    d = _inicio_mes(periodo.data_inicio)
    while d <= periodo.data_fim:
        ini = max(d, periodo.data_inicio)
        fim = min(_fim_mes(d), periodo.data_fim)
        out.append((d.strftime("%m/%Y"), ini, fim))
        if d.month == 12:
            d = d.replace(year=d.year + 1, month=1, day=1)
        else:
            d = d.replace(month=d.month + 1, day=1)
    return out


def _mapa_por_data(qs, campo_data: str) -> dict[date, int]:
    rows = (
        qs.annotate(d=TruncDate(campo_data))
        .values("d")
        .annotate(c=Count("pk"))
        .order_by("d")
    )
    return {row["d"]: row["c"] for row in rows if row["d"]}


def _mapa_receita_por_data(qs) -> dict[date, Decimal]:
    rows = (
        qs.annotate(d=TruncDate("criado_em"))
        .values("d")
        .annotate(total=Sum("valor_total"))
        .order_by("d")
    )
    return {row["d"]: row["total"] or Decimal("0") for row in rows if row["d"]}


def _somar_bucket_int(mapa: dict[date, int], ini: date, fim: date) -> int:
    total = 0
    d = ini
    while d <= fim:
        total += mapa.get(d, 0)
        d += timedelta(days=1)
    return total


def _somar_bucket_decimal(mapa: dict[date, Decimal], ini: date, fim: date) -> Decimal:
    total = Decimal("0")
    d = ini
    while d <= fim:
        total += mapa.get(d, Decimal("0"))
        d += timedelta(days=1)
    return total


@dataclass(frozen=True)
class SerieEvolucaoResultados:
    labels: tuple[str, ...]
    leads: tuple[int, ...]
    consultas: tuple[int, ...]
    contratos: tuple[int, ...]
    receita: tuple[float, ...]
    granularidade: str
    ocultar_receita: bool

    def tem_dados(self) -> bool:
        return any(self.leads) or any(self.consultas) or any(self.contratos)

    def as_chart_dict(self) -> dict:
        data: dict = {
            "labels": list(self.labels),
            "leads": list(self.leads),
            "consultas": list(self.consultas),
            "contratos": list(self.contratos),
            "granularidade": self.granularidade,
        }
        if not self.ocultar_receita:
            data["receita"] = list(self.receita)
        return data


def calcular_evolucao_resultados(
    user,
    periodo: PeriodoMarketing,
    *,
    organization=None,
    granularidade: str = GRAN_AUTO,
    ocultar_financeiro: bool = False,
) -> SerieEvolucaoResultados:
    gran = resolver_granularidade(periodo, granularidade)
    buckets = iter_buckets(periodo, gran)

    leads_qs = clientes_google_ads(
        user,
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
        organization=organization,
    )
    mapa_leads = _mapa_por_data(leads_qs, "criado_em")

    ids_subquery = leads_qs.values("pk")
    consultas_qs = (
        _consultas_qs(user, ids_subquery, periodo, organization=organization)
        .filter(status=StatusCompromisso.REALIZADO)
    )
    contratos_qs = _contratos_qs(organization, ids_subquery, periodo)

    mapa_consultas = _mapa_por_data(consultas_qs, "data_hora")
    mapa_contratos = _mapa_por_data(contratos_qs, "criado_em")
    mapa_receita = (
        {} if ocultar_financeiro else _mapa_receita_por_data(contratos_qs)
    )

    labels: list[str] = []
    leads: list[int] = []
    consultas: list[int] = []
    contratos: list[int] = []
    receita: list[float] = []

    for rotulo, ini, fim in buckets:
        labels.append(rotulo)
        leads.append(_somar_bucket_int(mapa_leads, ini, fim))
        consultas.append(_somar_bucket_int(mapa_consultas, ini, fim))
        contratos.append(_somar_bucket_int(mapa_contratos, ini, fim))
        if ocultar_financeiro:
            receita.append(0.0)
        else:
            receita.append(float(_somar_bucket_decimal(mapa_receita, ini, fim)))

    return SerieEvolucaoResultados(
        labels=tuple(labels),
        leads=tuple(leads),
        consultas=tuple(consultas),
        contratos=tuple(contratos),
        receita=tuple(receita),
        granularidade=gran,
        ocultar_receita=ocultar_financeiro,
    )


@dataclass(frozen=True)
class LinhaComparacao:
    chave: str
    metrica: str
    atual: str
    anterior: str
    tendencia: dict


def _fmt_int(val: int) -> str:
    return format_number_br(val, 0)


def _fmt_money(val: Decimal) -> str:
    return format_currency_br(val)


def linhas_comparacao_periodo(
    atual: ResultadosNegocio,
    anterior: ResultadosNegocio,
    tendencias: dict[str, dict],
    *,
    ocultar_financeiro: bool,
) -> tuple[LinhaComparacao, ...]:
    f_a, f_p = atual.funil, anterior.funil

    linhas = [
        LinhaComparacao(
            "leads",
            "Leads",
            _fmt_int(f_a.leads),
            _fmt_int(f_p.leads),
            tendencias["leads"],
        ),
        LinhaComparacao(
            "consultas_agendadas",
            "Consultas agendadas",
            _fmt_int(f_a.consultas_agendadas),
            _fmt_int(f_p.consultas_agendadas),
            tendencias.get("consultas_agendadas", {"pct": None, "trend": "flat"}),
        ),
        LinhaComparacao(
            "consultas_realizadas",
            "Consultas realizadas",
            _fmt_int(f_a.consultas_realizadas),
            _fmt_int(f_p.consultas_realizadas),
            tendencias["consultas_realizadas"],
        ),
        LinhaComparacao(
            "propostas",
            "Propostas",
            _fmt_int(f_a.propostas),
            _fmt_int(f_p.propostas),
            tendencias.get("propostas", {"pct": None, "trend": "flat"}),
        ),
        LinhaComparacao(
            "contratos",
            "Contratos",
            _fmt_int(f_a.contratos),
            _fmt_int(f_p.contratos),
            tendencias["contratos"],
        ),
    ]

    if not ocultar_financeiro:
        linhas.extend(
            [
                LinhaComparacao(
                    "receita_contratada",
                    "Receita contratada",
                    _fmt_money(f_a.receita_contratada),
                    _fmt_money(f_p.receita_contratada),
                    tendencias["receita_contratada"],
                ),
                LinhaComparacao(
                    "receita_recebida",
                    "Receita recebida",
                    _fmt_money(f_a.receita_recebida),
                    _fmt_money(f_p.receita_recebida),
                    tendencias["receita_recebida"],
                ),
            ]
        )

    return tuple(linhas)


def tendencias_completas(atual: ResultadosNegocio, anterior: ResultadosNegocio) -> dict[str, dict]:
    """Estende tendências do funil com propostas e consultas agendadas."""
    base = calcular_tendencias_resultados(atual, anterior)
    f_a, f_p = atual.funil, anterior.funil

    def _t(curr: float, prev: float) -> dict:
        from marketing.services.google_ads_resultados import _pct_trend

        pct, trend = _pct_trend(curr, prev)
        return {"pct": pct, "trend": trend}

    base = dict(base)
    base["consultas_agendadas"] = _t(
        float(f_a.consultas_agendadas), float(f_p.consultas_agendadas)
    )
    base["propostas"] = _t(float(f_a.propostas), float(f_p.propostas))
    return base
