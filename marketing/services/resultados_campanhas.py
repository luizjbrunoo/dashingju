"""
Ranking de campanhas por utm_campaign — somente atribuição confiável.

Só exibe dados quando leads Google Ads possuem utm_campaign preenchido
com origem confiável (gclid ou UTM google+cpc). Não inventa campanhas.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from marketing.services.atribuicao import clientes_google_ads
from marketing.services.google_ads_resultados import (
    _ZERO,
    _contratos_qs,
    _consultas_qs,
    _dec,
    _safe_pct,
    get_cac_midia,
    get_cpl,
    get_investimento,
    get_receita_midia_ratio,
)
from marketing.services.periodo import PeriodoMarketing
from usuarios.choices import StatusCompromisso

ORDEN_CONTRATOS = "contratos"
ORDEN_RECEITA = "receita"
ORDEN_LEADS = "leads"
ORDEN_CONVERSAO = "conversao"
ORDEN_CAC = "cac"
ORDEN_RECEITA_MIDIA = "receita_midia"

_ORDENS = frozenset(
    {
        ORDEN_CONTRATOS,
        ORDEN_RECEITA,
        ORDEN_LEADS,
        ORDEN_CONVERSAO,
        ORDEN_CAC,
        ORDEN_RECEITA_MIDIA,
    }
)


def _leads_campanha_identificada(user, periodo: PeriodoMarketing, *, organization=None):
    """Leads Google Ads confiáveis no período com utm_campaign preenchido."""
    return (
        clientes_google_ads(
            user,
            data_inicio=periodo.data_inicio,
            data_fim=periodo.data_fim,
            organization=organization,
        )
        .exclude(utm_campaign="")
        .exclude(utm_campaign__isnull=True)
    )


def campanhas_distintas(user, periodo: PeriodoMarketing, *, organization=None) -> list[str]:
    qs = (
        _leads_campanha_identificada(user, periodo, organization=organization)
        .values("utm_campaign")
        .annotate(qtd=Count("pk"))
        .order_by("-qtd", "utm_campaign")
    )
    return [row["utm_campaign"] for row in qs if row["utm_campaign"]]


def _mapa_leads_por_campanha(leads_qs) -> dict[str, int]:
    return {
        row["utm_campaign"]: row["qtd"]
        for row in leads_qs.values("utm_campaign").annotate(qtd=Count("pk"))
        if row["utm_campaign"]
    }


def _mapa_consultas_por_campanha(
    user, ids_subquery, periodo: PeriodoMarketing, *, organization=None
) -> dict[str, int]:
    rows = (
        _consultas_qs(user, ids_subquery, periodo, organization=organization)
        .filter(status=StatusCompromisso.REALIZADO)
        .values("cliente__utm_campaign")
        .annotate(qtd=Count("pk"))
    )
    return {
        row["cliente__utm_campaign"]: row["qtd"]
        for row in rows
        if row["cliente__utm_campaign"]
    }


def _mapa_contratos_por_campanha(
    organization, ids_subquery, periodo: PeriodoMarketing
) -> dict[str, tuple[int, Decimal]]:
    rows = (
        _contratos_qs(organization, ids_subquery, periodo)
        .values("cliente__utm_campaign")
        .annotate(qtd=Count("pk"), receita=Coalesce(Sum("valor_total"), _ZERO))
    )
    return {
        row["cliente__utm_campaign"]: (row["qtd"], _dec(row["receita"]) or _ZERO)
        for row in rows
        if row["cliente__utm_campaign"]
    }


@dataclass(frozen=True)
class CampanhaMetricas:
    utm_campaign: str
    label: str
    investimento: Decimal | None
    investimento_demo: bool
    leads: int
    consultas_realizadas: int
    contratos: int
    receita_contratada: Decimal
    cpl: Decimal | None
    cac_midia: Decimal | None
    conv_lead_contrato_pct: Decimal | None
    receita_midia_ratio: Decimal | None


@dataclass(frozen=True)
class RankingCampanhas:
    exibir: bool
    aviso: str
    campanhas: tuple[CampanhaMetricas, ...]
    ordenacao: str
    rateio_investimento_demo: bool


def _ratear_investimento(
    investimento_total: Decimal | None,
    *,
    leads_campanha: int,
    leads_com_utm: int,
    eh_demo: bool,
) -> tuple[Decimal | None, bool]:
    if investimento_total is None or not leads_com_utm or not leads_campanha:
        return None, eh_demo and investimento_total is not None
    parte = investimento_total * Decimal(leads_campanha) / Decimal(leads_com_utm)
    return _dec(parte), eh_demo


def _montar_metricas_campanha(
    utm_campaign: str,
    *,
    leads: int,
    consultas: int,
    contratos: int,
    receita: Decimal,
    investimento_total: Decimal | None,
    investimento_demo: bool,
    leads_com_utm: int,
    ocultar_financeiro: bool,
) -> CampanhaMetricas:
    if ocultar_financeiro:
        receita = _ZERO

    inv, demo = _ratear_investimento(
        investimento_total,
        leads_campanha=leads,
        leads_com_utm=leads_com_utm,
        eh_demo=investimento_demo,
    )

    return CampanhaMetricas(
        utm_campaign=utm_campaign,
        label=utm_campaign,
        investimento=inv,
        investimento_demo=demo,
        leads=leads,
        consultas_realizadas=consultas,
        contratos=contratos,
        receita_contratada=receita,
        cpl=get_cpl(inv, leads),
        cac_midia=get_cac_midia(inv, leads),
        conv_lead_contrato_pct=_safe_pct(contratos, leads),
        receita_midia_ratio=(
            None if ocultar_financeiro else get_receita_midia_ratio(receita, inv)
        ),
    )


def _ordenar_campanhas(
    campanhas: list[CampanhaMetricas], orden: str
) -> list[CampanhaMetricas]:
    if orden == ORDEN_RECEITA:
        return sorted(campanhas, key=lambda c: float(c.receita_contratada), reverse=True)
    if orden == ORDEN_LEADS:
        return sorted(campanhas, key=lambda c: c.leads, reverse=True)
    if orden == ORDEN_CONVERSAO:
        return sorted(
            campanhas,
            key=lambda c: float(c.conv_lead_contrato_pct or 0),
            reverse=True,
        )
    if orden == ORDEN_CAC:
        return sorted(
            campanhas,
            key=lambda c: float(c.cac_midia if c.cac_midia is not None else Decimal("999999999")),
        )
    if orden == ORDEN_RECEITA_MIDIA:
        return sorted(
            campanhas,
            key=lambda c: float(c.receita_midia_ratio or 0),
            reverse=True,
        )
    return sorted(campanhas, key=lambda c: c.contratos, reverse=True)


def calcular_ranking_campanhas(
    user,
    periodo: PeriodoMarketing,
    *,
    organization=None,
    ordenacao: str = ORDEN_CONTRATOS,
    modo_demo: bool = False,
    nicho: str = "",
    ocultar_financeiro: bool = False,
) -> RankingCampanhas:
    orden = ordenacao if ordenacao in _ORDENS else ORDEN_CONTRATOS
    leads_qs = _leads_campanha_identificada(user, periodo, organization=organization)
    leads_por_camp = _mapa_leads_por_campanha(leads_qs)
    leads_com_utm = sum(leads_por_camp.values())
    nomes = sorted(
        leads_por_camp.keys(),
        key=lambda c: (-leads_por_camp[c], c),
    )

    if not nomes or leads_com_utm == 0:
        return RankingCampanhas(
            exibir=False,
            aviso=(
                "Ranking por campanha disponível quando leads Google Ads "
                "possuem utm_campaign com atribuição confiável."
            ),
            campanhas=(),
            ordenacao=orden,
            rateio_investimento_demo=False,
        )

    ids_subquery = leads_qs.values("pk")
    consultas_map = _mapa_consultas_por_campanha(
        user, ids_subquery, periodo, organization=organization
    )
    contratos_map = _mapa_contratos_por_campanha(
        organization, ids_subquery, periodo
    )

    inv_total, inv_demo = get_investimento(
        user, periodo, modo_demo=modo_demo, nicho=nicho
    )

    linhas = [
        _montar_metricas_campanha(
            nome,
            leads=leads_por_camp[nome],
            consultas=consultas_map.get(nome, 0),
            contratos=contratos_map.get(nome, (0, _ZERO))[0],
            receita=contratos_map.get(nome, (0, _ZERO))[1],
            investimento_total=inv_total,
            investimento_demo=inv_demo,
            leads_com_utm=leads_com_utm,
            ocultar_financeiro=ocultar_financeiro,
        )
        for nome in nomes
    ]

    return RankingCampanhas(
        exibir=True,
        aviso=(
            "Investimento por campanha rateado proporcionalmente aos leads com "
            "utm_campaign (demonstração até integração Google Ads)."
            if inv_demo
            else ""
        ),
        campanhas=tuple(_ordenar_campanhas(linhas, orden)),
        ordenacao=orden,
        rateio_investimento_demo=inv_demo,
    )
