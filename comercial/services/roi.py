"""ROI / CAC / CPL — somente com investimento real registrado (nunca demo)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from comercial.models import InvestimentoMidia
from comercial.services.finance_org import contratos_organization
from comercial.services.money import ZERO, money, safe_div
from comercial.services.periodo import PeriodoComercial, filtro_datetime_campo
from usuarios.services.org_scope import clientes_da_organizacao


@dataclass(frozen=True)
class MetricasMidia:
    investimento: Decimal | None
    receita_atribuida: Decimal | None
    leads: int
    novos_clientes: int
    roi_pct: Decimal | None
    cac: Decimal | None
    cpl: Decimal | None
    disponivel: bool
    mensagem: str


def investimento_registrado(user, periodo: PeriodoComercial, *, organization=None) -> Decimal:
    """Soma investimentos cujo intervalo intersecta o período analisado."""
    del user
    if organization is None:
        return ZERO
    qs = InvestimentoMidia.objects.filter(organization=organization).filter(
        data_inicio__lte=periodo.data_fim,
        data_fim__gte=periodo.data_inicio,
    )
    total = qs.aggregate(t=Coalesce(Sum("valor"), ZERO))["t"] or ZERO
    return money(total)


def calcular_metricas_midia(user, periodo: PeriodoComercial, *, organization=None) -> MetricasMidia:
    """
    ROI = (Receita atribuída - Investimento) / Investimento × 100
    CAC = Investimento / novos clientes (contratos active/closed no período)
    CPL = Investimento / leads

    Receita atribuída: contratos de clientes com origem preenchida no período.
    Não usa modo demo do Google Ads.
    """
    inv = investimento_registrado(user, periodo, organization=organization)
    if inv <= 0:
        # Também tenta API Google Ads real (sem demo)
        from marketing.services.google_ads_resultados import get_investimento

        inv_api, eh_demo = get_investimento(user, periodo, modo_demo=False)
        if eh_demo or inv_api is None or inv_api <= 0:
            return MetricasMidia(
                investimento=None,
                receita_atribuida=None,
                leads=0,
                novos_clientes=0,
                roi_pct=None,
                cac=None,
                cpl=None,
                disponivel=False,
                mensagem=(
                    "ROI indisponível — registre investimento e origem dos contratos."
                ),
            )
        inv = money(inv_api)

    leads_qs = filtro_datetime_campo(
        clientes_da_organizacao(organization).exclude(origem=""),
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    leads = leads_qs.count()

    contratos_qs = contratos_organization(organization).exclude(cliente__origem="")
    contratos_qs = filtro_datetime_campo(
        contratos_qs,
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    agg = contratos_qs.aggregate(
        qtd=Count("pk"),
        receita=Coalesce(Sum("valor_total"), ZERO),
    )
    novos = agg["qtd"] or 0
    receita = money(agg["receita"])

    roi = None
    if inv > 0:
        roi = money((receita - inv) / inv * Decimal("100"))

    return MetricasMidia(
        investimento=inv,
        receita_atribuida=receita,
        leads=leads,
        novos_clientes=novos,
        roi_pct=roi,
        cac=safe_div(inv, novos),
        cpl=safe_div(inv, leads),
        disponivel=True,
        mensagem="",
    )
