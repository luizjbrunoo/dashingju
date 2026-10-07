"""Insights e alertas comerciais — regras determinísticas."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from usuarios.br_format import format_currency_br, format_number_br

from comercial.services.funnel import calcular_funil_receita
from comercial.services.goals import meta_do_ano
from comercial.services.metrics import (
    meta_vs_realizado,
    periodo_ano,
    receita_no_periodo,
)
from comercial.services.origem import origem_para_receita
from comercial.services.periodo import PeriodoComercial
from comercial.services.revenue_risk import calcular_receita_em_risco
from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization


@dataclass(frozen=True)
class InsightComercial:
    key: str
    o_que: str
    impacto: str
    acao: str
    severidade: str  # info | atencao | risco | positivo


@dataclass(frozen=True)
class AlertaComercial:
    key: str
    titulo: str
    detalhe: str
    severidade: str


@dataclass(frozen=True)
class PainelInsights:
    insights: tuple[InsightComercial, ...]
    alertas: tuple[AlertaComercial, ...]


def gerar_insights_e_alertas(
    user,
    *,
    organization=None,
    ticket_valor: Decimal | None,
    funil=None,
    meta_vs=None,
    risco=None,
) -> PainelInsights:
    periodo = PeriodoComercial.ultimos_dias(30)
    periodo_ant = periodo.periodo_anterior()
    funil = funil or calcular_funil_receita(user, periodo, organization=organization)
    funil_ant = calcular_funil_receita(user, periodo_ant, organization=organization)
    meta = meta_do_ano(user, organization=organization)
    meta_vs = meta_vs or meta_vs_realizado(organization, meta, ticket_valor)
    risco = risco or calcular_receita_em_risco(
        user, organization=organization, ticket_medio=ticket_valor
    )
    kpis_cob = calcular_kpis_cobrancas_organization(organization)
    origem = origem_para_receita(user, periodo, organization=organization)

    insights: list[InsightComercial] = []
    alertas: list[AlertaComercial] = []

    if (
        funil.taxa_proposta_contrato is not None
        and funil_ant.taxa_proposta_contrato is not None
    ):
        delta = float(funil.taxa_proposta_contrato - funil_ant.taxa_proposta_contrato)
        if abs(delta) >= 5:
            insights.append(
                InsightComercial(
                    key="conv_proposta",
                    o_que=(
                        f"Conversão de propostas "
                        f"{'caiu' if delta < 0 else 'subiu'} {abs(delta):.0f}% "
                        f"nos últimos 30 dias."
                    ),
                    impacto=(
                        f"Atual {funil.taxa_proposta_contrato}% vs "
                        f"{funil_ant.taxa_proposta_contrato}% no período anterior."
                    ),
                    acao="Revisar follow-up de propostas e tempo de resposta.",
                    severidade="atencao" if delta < 0 else "positivo",
                )
            )

    if len(origem.linhas) >= 2:
        por_leads = max(origem.linhas, key=lambda l: l.leads)
        com_conv = [
            l for l in origem.linhas if l.conversao_pct is not None and l.leads >= 2
        ]
        if com_conv:
            por_conv = max(com_conv, key=lambda l: float(l.conversao_pct or 0))
            if por_leads.origem != por_conv.origem and por_leads.leads > 0:
                insights.append(
                    InsightComercial(
                        key="origem_vs_conv",
                        o_que=(
                            f"{por_leads.label} gerou mais leads, porém "
                            f"{por_conv.label} apresentou maior conversão."
                        ),
                        impacto="Volume ≠ qualidade de canal.",
                        acao="Priorizar canais com melhor conversão em contratos/receita.",
                        severidade="info",
                    )
                )

    n_prop = next(
        (i.quantidade for i in risco.itens if i.key == "propostas_sem_followup"),
        0,
    )
    if n_prop:
        insights.append(
            InsightComercial(
                key="prop_sem_fu",
                o_que=f"Existem {n_prop} proposta(s) sem follow-up.",
                impacto="Risco de perda de oportunidade comercial.",
                acao="Agendar follow-ups nas próximas melhores ações.",
                severidade="atencao",
            )
        )
        alertas.append(
            AlertaComercial(
                key="proposta_sem_followup",
                titulo="PROPOSTA SEM FOLLOW-UP",
                detalhe=f"{n_prop} proposta(s) aguardando acompanhamento",
                severidade="atencao",
            )
        )

    if (
        funil.contratos >= 2
        and funil_ant.contratos >= 2
        and funil.receita_contratada
        and funil_ant.receita_contratada
    ):
        t_a = float(funil.receita_contratada) / funil.contratos
        t_p = float(funil_ant.receita_contratada) / funil_ant.contratos
        if t_p > 0:
            var_t = (t_a - t_p) / t_p * 100
            if abs(var_t) >= 8:
                insights.append(
                    InsightComercial(
                        key="ticket",
                        o_que=(
                            f"Seu ticket médio "
                            f"{'aumentou' if var_t > 0 else 'diminuiu'} {abs(var_t):.0f}%."
                        ),
                        impacto=f"Ticket atual estimado {format_currency_br(t_a)}.",
                        acao="Validar mix de áreas e pacotes de honorários.",
                        severidade="positivo" if var_t > 0 else "atencao",
                    )
                )

    if meta_vs.contratos_faltantes is not None and meta_vs.contratos_faltantes > 0:
        insights.append(
            InsightComercial(
                key="faltam_contratos",
                o_que=(
                    f"Você precisa de mais {format_number_br(meta_vs.contratos_faltantes, 0)} "
                    f"contrato(s) para atingir a meta do mês."
                ),
                impacto=(
                    f"Realizado: {format_number_br(meta_vs.contratos_realizados, 0)} · "
                    f"necessários: {format_number_br(meta_vs.contratos_necessarios, 0)}"
                ),
                acao="Priorizar propostas quentes e leads com consulta.",
                severidade="atencao",
            )
        )
        if meta_vs.pct_atingido is not None and meta_vs.pct_atingido < 70:
            alertas.append(
                AlertaComercial(
                    key="meta_em_risco",
                    titulo="META EM RISCO",
                    detalhe=f"{meta_vs.pct_atingido:.0f}% da meta mensal atingida",
                    severidade="risco",
                )
            )

    if kpis_cob.vencido > 0:
        insights.append(
            InsightComercial(
                key="cobrancas_vencidas",
                o_que=f"Há {format_currency_br(kpis_cob.vencido)} em cobranças vencidas.",
                impacto="Receita confirmada em risco de inadimplência.",
                acao="Acionar cobranças vencidas no módulo Financeiro.",
                severidade="risco",
            )
        )
        alertas.append(
            AlertaComercial(
                key="cobranca_vencida",
                titulo="COBRANÇA VENCIDA",
                    detalhe=f"{format_currency_br(kpis_cob.vencido)} em atraso",
                severidade="risco",
            )
        )

    n_leads = next((i.quantidade for i in risco.itens if i.key == "leads_parados"), 0)
    if n_leads:
        alertas.append(
            AlertaComercial(
                key="lead_sem_resposta",
                titulo="LEAD SEM RESPOSTA",
                detalhe=f"{format_number_br(n_leads, 0)} lead(s) sem atividade recente",
                severidade="atencao",
            )
        )

    ano_rec = receita_no_periodo(organization, periodo_ano())
    if (
        meta
        and meta.meta_anual
        and ano_rec.recebido < Decimal(meta.meta_anual) * Decimal("0.4")
        and timezone.localdate().month >= 4
    ):
        alertas.append(
            AlertaComercial(
                key="receita_abaixo_meta",
                titulo="RECEITA ABAIXO DA META",
                detalhe="Ritmo anual abaixo de 40% da meta após o 1º trimestre",
                severidade="risco",
            )
        )

    if funil.taxa_lead_contrato is not None and funil_ant.taxa_lead_contrato is not None:
        d = float(funil.taxa_lead_contrato - funil_ant.taxa_lead_contrato)
        if d <= -5:
            alertas.append(
                AlertaComercial(
                    key="conversao_queda",
                    titulo="CONVERSÃO EM QUEDA",
                    detalhe=f"Lead→contrato caiu {abs(d):.0f} p.p. vs período anterior",
                    severidade="atencao",
                )
            )

    return PainelInsights(
        insights=tuple(insights[:8]),
        alertas=tuple(alertas[:5]),
    )
