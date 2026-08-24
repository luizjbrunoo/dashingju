"""Indicadores de cobranças para o dashboard financeiro."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from financeiro.services.cobranca_inadimplencia import calcular_inadimplencia
from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas, queryset_anotado
from financeiro.services.cobranca_previsao import calcular_previsao
from financeiro.choices import StatusCobranca
from financeiro.models import CobrancaRecebimento


@dataclass(frozen=True)
class CobrancaDashboardResumo:
    a_receber: Decimal
    recebido_mes: Decimal
    vencido: Decimal
    taxa_inadimplencia: Decimal
    previsao_30: Decimal
    ticket_medio: Decimal
    prazo_medio_recebimento: Decimal | None
    clientes_inadimplentes: list
    proximos_vencimentos: list


def calcular_dashboard_cobrancas(usuario, *, hoje: date | None = None) -> CobrancaDashboardResumo:
    """
    Indicadores compactos para o painel financeiro.

    Taxa de inadimplência = saldo vencido / total a receber × 100
    (percentual do contas a receber que está em atraso).
    """
    hoje = hoje or timezone.localdate()
    kpis = calcular_kpis_cobrancas(usuario, hoje=hoje)
    previsao = calcular_previsao(usuario, hoje=hoje)
    inadimplencia = calcular_inadimplencia(usuario, hoje=hoje)

    if kpis.a_receber > 0:
        taxa = (kpis.vencido / kpis.a_receber * Decimal("100")).quantize(Decimal("0.1"))
    else:
        taxa = Decimal("0")

    abertas_qtd = (
        queryset_anotado(usuario)
        .exclude(status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT])
        .filter(saldo_calc__gt=0)
        .count()
    )
    ticket = (
        (kpis.a_receber / abertas_qtd).quantize(Decimal("0.01"))
        if abertas_qtd
        else Decimal("0")
    )

    prazo = _prazo_medio_recebimento(usuario)

    return CobrancaDashboardResumo(
        a_receber=kpis.a_receber,
        recebido_mes=kpis.recebido_mes,
        vencido=kpis.vencido,
        taxa_inadimplencia=taxa,
        previsao_30=previsao.cumulativo_30,
        ticket_medio=ticket,
        prazo_medio_recebimento=prazo,
        clientes_inadimplentes=inadimplencia.clientes[:5],
        proximos_vencimentos=previsao.cobrancas[:5],
    )


def _prazo_medio_recebimento(usuario) -> Decimal | None:
    """Média de dias entre vencimento e recebimento (recebimentos ativos)."""
    recebimentos = CobrancaRecebimento.objects.filter(
        usuario=usuario,
        cancelado_em__isnull=True,
    ).select_related("cobranca")
    total_dias = 0
    qtd = 0
    for rec in recebimentos.iterator():
        delta = (rec.data_recebimento - rec.cobranca.data_vencimento).days
        total_dias += delta
        qtd += 1
    if not qtd:
        return None
    return Decimal(str(round(total_dias / qtd, 1)))
