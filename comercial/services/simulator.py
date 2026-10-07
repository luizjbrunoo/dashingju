"""Simulador de crescimento — premissas informadas pelo usuário."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from comercial.services.money import ZERO, money


@dataclass(frozen=True)
class CenarioSimulacao:
    leads: int
    taxa_conversao_pct: Decimal
    ticket_medio: Decimal
    contratos: Decimal
    receita: Decimal


@dataclass(frozen=True)
class ResultadoSimulador:
    atual: CenarioSimulacao
    simulado: CenarioSimulacao
    diff_contratos: Decimal
    diff_receita: Decimal
    aviso: str


def _cenario(leads: int, taxa_pct: Decimal, ticket: Decimal) -> CenarioSimulacao:
    taxa = Decimal(taxa_pct) / Decimal("100")
    contratos = money(Decimal(leads) * taxa)
    receita = money(contratos * Decimal(ticket))
    return CenarioSimulacao(
        leads=leads,
        taxa_conversao_pct=money(taxa_pct),
        ticket_medio=money(ticket),
        contratos=contratos,
        receita=receita,
    )


def simular_crescimento(
    *,
    leads_atual: int,
    taxa_atual_pct: Decimal,
    ticket_atual: Decimal,
    leads_sim: int | None = None,
    taxa_sim_pct: Decimal | None = None,
    ticket_sim: Decimal | None = None,
) -> ResultadoSimulador:
    leads_atual = max(0, int(leads_atual))
    leads_s = max(0, int(leads_sim if leads_sim is not None else leads_atual))
    taxa_a = Decimal(taxa_atual_pct)
    taxa_s = Decimal(taxa_sim_pct if taxa_sim_pct is not None else taxa_a)
    ticket_a = Decimal(ticket_atual)
    ticket_s = Decimal(ticket_sim if ticket_sim is not None else ticket_a)

    atual = _cenario(leads_atual, taxa_a, ticket_a)
    simulado = _cenario(leads_s, taxa_s, ticket_s)
    return ResultadoSimulador(
        atual=atual,
        simulado=simulado,
        diff_contratos=money(simulado.contratos - atual.contratos),
        diff_receita=money(simulado.receita - atual.receita),
        aviso=(
            "Simulação baseada nas premissas informadas. "
            "Não representa garantia de resultado."
        ),
    )


def premissas_padrao_do_funil(funil, ticket_valor: Decimal | None) -> dict:
    """Deriva inputs iniciais do funil real (quando houver)."""
    leads = funil.leads or 0
    taxa = funil.taxa_lead_contrato or ZERO
    ticket = ticket_valor or ZERO
    return {
        "leads": leads,
        "taxa": taxa,
        "ticket": ticket,
    }
