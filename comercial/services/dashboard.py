"""Montagem do contexto do dashboard Comercial (visão executiva + abas)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from comercial.permissions import pode_ver_receita
from comercial.services.advisor import PainelAdvisor, montar_advisor
from comercial.services.area_performance import PerformanceArea, performance_por_area
from comercial.services.funnel import FunilReceita, calcular_funil_receita, etapas_executivas
from comercial.services.goals import (
    MetaReversa,
    TicketMedioInfo,
    calcular_meta_reversa,
    meta_do_ano,
    ticket_efetivo,
)
from comercial.services.insights import PainelInsights, gerar_insights_e_alertas
from comercial.services.metrics import (
    KpisComerciais,
    MetaVsRealizado,
    calcular_kpis,
    periodo_do_mes,
)
from comercial.services.opportunities import PainelOportunidades
from comercial.services.origem import OrigemResultados, origem_para_receita
from comercial.services.pipeline import (
    PIPELINE_VAZIO,
    PainelPipeline,
    analisar_pipeline,
    ocultar_valores_pipeline,
)
from comercial.services.portfolio import InteligenciaCarteira, oportunidades_na_base
from comercial.services.projections import (
    ProjecaoFaturamento,
    ProjecaoMensal,
    projetar_faturamento_anual,
    projetar_faturamento_mensal,
)
from comercial.services.recommendations import PainelRecomendacoes, listar_recomendacoes
from comercial.services.revenue_risk import ItemRisco, ReceitaRisco, calcular_receita_em_risco
from comercial.services.roi import MetricasMidia, calcular_metricas_midia
from comercial.services.score import AdvGrowthScore, calcular_adv_growth_score
from comercial.services.simulator import (
    ResultadoSimulador,
    premissas_padrao_do_funil,
    simular_crescimento,
)
from comercial.services.money import ZERO

ABAS = ("visao", "oportunidades", "origens", "score", "simulador")
_MESES_PT = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


@dataclass(frozen=True)
class MesOpcao:
    valor: str
    label: str


@dataclass(frozen=True)
class ContextoDashboardComercial:
    aba: str
    mes_slug: str
    mes_label: str
    meses_opcoes: tuple[MesOpcao, ...]
    periodo: PeriodoComercial
    meta: object
    ticket: TicketMedioInfo
    meta_reversa: MetaReversa
    kpis: KpisComerciais
    meta_vs: MetaVsRealizado
    funil: FunilReceita
    funil_etapas_home: tuple
    risco: ReceitaRisco
    projecao: ProjecaoFaturamento
    projecao_mes: ProjecaoMensal
    pipeline: PainelPipeline
    advisor: PainelAdvisor
    oportunidades: PainelOportunidades
    recomendacoes: PainelRecomendacoes
    simulador: ResultadoSimulador
    sim_inputs: dict
    performance_area: PerformanceArea
    origem: OrigemResultados
    adv_score: AdvGrowthScore
    insights: PainelInsights
    midia: MetricasMidia
    carteira: InteligenciaCarteira
    pode_gerenciar_metas: bool
    ocultar_financeiro: bool


def _dec_param(raw, default: Decimal) -> Decimal:
    try:
        if raw is None or raw == "":
            return default
        return Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _int_param(raw, default: int) -> int:
    try:
        if raw is None or raw == "":
            return default
        return max(0, int(raw))
    except (ValueError, TypeError):
        return default


def parse_aba(raw) -> str:
    valor = (raw or "visao").strip()
    return valor if valor in ABAS else "visao"


def parse_mes(raw, *, referencia: date | None = None) -> date:
    hoje = referencia or timezone.localdate()
    texto = (raw or "").strip()
    if len(texto) == 7 and texto[4] == "-":
        try:
            ano = int(texto[:4])
            mes = int(texto[5:7])
            if 1 <= mes <= 12 and 2000 <= ano <= hoje.year + 1:
                return date(ano, mes, 1)
        except ValueError:
            pass
    return date(hoje.year, hoje.month, 1)


def meses_opcoes(*, referencia: date | None = None, quantidade: int = 12) -> tuple[MesOpcao, ...]:
    hoje = referencia or timezone.localdate()
    itens: list[MesOpcao] = []
    ano, mes = hoje.year, hoje.month
    for _ in range(quantidade):
        itens.append(
            MesOpcao(
                valor=f"{ano:04d}-{mes:02d}",
                label=f"{_MESES_PT[mes - 1]}/{ano}",
            )
        )
        mes -= 1
        if mes == 0:
            mes = 12
            ano -= 1
    return tuple(itens)


def _vazios():
    funil_vazio = FunilReceita(
        leads=0,
        com_consulta=0,
        consultas=0,
        propostas=0,
        contratos=0,
        receita_contratada=ZERO,
        taxa_lead_consulta=None,
        taxa_consulta_proposta=None,
        taxa_proposta_contrato=None,
        taxa_lead_contrato=None,
        gargalo=None,
        etapas=(),
        dados_suficientes=False,
        mensagem="",
    )
    return {
        "funil": funil_vazio,
        "oportunidades": PainelOportunidades(itens=(), mensagem=""),
        "recomendacoes": PainelRecomendacoes(itens=(), mensagem=""),
        "simulador": simular_crescimento(
            leads_atual=0,
            taxa_atual_pct=ZERO,
            ticket_atual=ZERO,
        ),
        "sim_inputs": {
            "leads": 0,
            "taxa": ZERO,
            "ticket": ZERO,
            "leads_s": 0,
            "taxa_s": ZERO,
            "ticket_s": ZERO,
        },
        "performance_area": PerformanceArea(
            linhas=(), ordenacao="receita", disponivel=False, mensagem=""
        ),
        "origem": OrigemResultados(linhas=(), disponivel=False, mensagem=""),
        "adv_score": AdvGrowthScore(
            score=0,
            dimensoes=(),
            delta_30=None,
            delta_60=None,
            delta_90=None,
            historico_labels=(),
            historico_scores=(),
            criterios_doc="",
        ),
        "insights": PainelInsights(insights=(), alertas=()),
        "midia": MetricasMidia(
            investimento=None,
            receita_atribuida=None,
            leads=0,
            novos_clientes=0,
            roi_pct=None,
            cac=None,
            cpl=None,
            disponivel=False,
            mensagem="",
        ),
        "carteira": InteligenciaCarteira(itens=(), mensagem=""),
        "pipeline": PIPELINE_VAZIO,
        "advisor": PainelAdvisor(
            principal=None,
            secundarias=(),
            acoes_hoje=(),
            insight=None,
            criterios_doc="",
            mensagem="",
        ),
    }


def contexto_dashboard(
    user,
    *,
    organization=None,
    periodo: PeriodoComercial | None = None,
    get_params=None,
):
    from comercial.permissions import pode_gerenciar_metas

    get_params = get_params or {}
    aba = parse_aba(get_params.get("aba"))
    mes_ref = parse_mes(get_params.get("mes"))
    periodo = periodo or periodo_do_mes(mes_ref.year, mes_ref.month, cortar_hoje=True)
    ocultar = not pode_ver_receita(user)
    meta = meta_do_ano(user, mes_ref.year, organization=organization)
    ticket = ticket_efetivo(user, meta, organization=organization)
    meta_reversa = calcular_meta_reversa(user, meta, ticket, organization=organization)
    pipeline = analisar_pipeline(organization, periodo, ticket=ticket.valor)
    proj = projetar_faturamento_anual(organization, meta.meta_anual if meta else None)
    proj_mes = projetar_faturamento_mensal(
        organization,
        meta.meta_mensal if meta else None,
        ano=mes_ref.year,
        mes=mes_ref.month,
        ticket=ticket.valor,
        pipeline=pipeline,
    )
    risco = calcular_receita_em_risco(
        user, organization=organization, ticket_medio=ticket.valor
    )
    total_risco = risco.total_confirmado_risco + risco.total_potencial_risco
    kpis = calcular_kpis(
        organization,
        meta=meta,
        receita_risco=total_risco,
        ocultar_financeiro=ocultar,
        projecao_anual=proj.projecao_anual,
    )
    meta_vs = meta_vs_realizado_periodo(organization, meta, ticket.valor, periodo)
    extras = _vazios()
    extras["pipeline"] = ocultar_valores_pipeline(pipeline) if ocultar else pipeline
    funil = extras["funil"]

    if aba in ("visao", "simulador"):
        funil = calcular_funil_receita(user, periodo, organization=organization)
        extras["funil"] = funil

    if aba == "visao":
        extras["advisor"] = montar_advisor(
            user,
            organization=organization,
            risco=risco,
            ticket=ticket.valor,
            ocultar_financeiro=ocultar,
            meta_vs=meta_vs,
            funil=funil,
            pipeline=pipeline,
        )
        extras["recomendacoes"] = PainelRecomendacoes(
            itens=extras["advisor"].acoes_hoje,
            mensagem=extras["advisor"].mensagem,
        )

    if aba == "oportunidades":
        extras["recomendacoes"] = listar_recomendacoes(
            user, organization=organization, limite=40
        )
        extras["carteira"] = oportunidades_na_base(user, organization=organization)

    if aba == "origens":
        extras["origem"] = origem_para_receita(user, periodo, organization=organization)
        extras["performance_area"] = performance_por_area(
            user,
            periodo,
            organization=organization,
            ordenacao=(get_params.get("orden_area") or "receita").strip(),
        )

    if aba == "score":
        extras["adv_score"] = calcular_adv_growth_score(
            user, organization=organization, persistir=True
        )
        extras["insights"] = gerar_insights_e_alertas(
            user,
            organization=organization,
            ticket_valor=ticket.valor,
            funil=funil if funil.dados_suficientes else None,
            meta_vs=meta_vs,
            risco=risco,
        )
        extras["midia"] = calcular_metricas_midia(
            user, periodo, organization=organization
        )

    if aba == "simulador":
        padrao = premissas_padrao_do_funil(funil, ticket.valor)
        leads_a = _int_param(get_params.get("sim_leads"), padrao["leads"] or 100)
        taxa_a = _dec_param(get_params.get("sim_taxa"), padrao["taxa"] or Decimal("20"))
        ticket_a = _dec_param(
            get_params.get("sim_ticket"),
            padrao["ticket"] or Decimal("5000"),
        )
        leads_s = _int_param(get_params.get("sim_leads_s"), leads_a)
        taxa_s = _dec_param(get_params.get("sim_taxa_s"), taxa_a)
        ticket_s = _dec_param(get_params.get("sim_ticket_s"), ticket_a)
        extras["simulador"] = simular_crescimento(
            leads_atual=leads_a,
            taxa_atual_pct=taxa_a,
            ticket_atual=ticket_a,
            leads_sim=leads_s,
            taxa_sim_pct=taxa_s,
            ticket_sim=ticket_s,
        )
        extras["sim_inputs"] = {
            "leads": leads_a,
            "taxa": taxa_a,
            "ticket": ticket_a,
            "leads_s": leads_s,
            "taxa_s": taxa_s,
            "ticket_s": ticket_s,
        }

    if ocultar:
        risco = ReceitaRisco(
            total_confirmado_risco=ZERO,
            total_potencial_risco=ZERO,
            itens=tuple(
                ItemRisco(
                    key=i.key,
                    titulo=i.titulo,
                    detalhe=i.detalhe,
                    valor=None,
                    quantidade=i.quantidade,
                    url=i.url,
                    severidade=i.severidade,
                )
                for i in risco.itens
            ),
            mensagem=risco.mensagem
            or "Valores financeiros ocultos conforme suas permissões.",
        )
        extras["midia"] = MetricasMidia(
            investimento=None,
            receita_atribuida=None,
            leads=extras["midia"].leads,
            novos_clientes=extras["midia"].novos_clientes,
            roi_pct=None,
            cac=None,
            cpl=None,
            disponivel=False,
            mensagem="Valores financeiros ocultos conforme suas permissões.",
        )

    mes_slug = f"{mes_ref.year:04d}-{mes_ref.month:02d}"
    return ContextoDashboardComercial(
        aba=aba,
        mes_slug=mes_slug,
        mes_label=f"{_MESES_PT[mes_ref.month - 1]}/{mes_ref.year}",
        meses_opcoes=meses_opcoes(),
        periodo=periodo,
        meta=meta,
        ticket=ticket,
        meta_reversa=meta_reversa,
        kpis=kpis,
        meta_vs=meta_vs,
        funil=extras["funil"],
        funil_etapas_home=etapas_executivas(extras["funil"]),
        risco=risco,
        projecao=proj,
        projecao_mes=proj_mes,
        pipeline=extras["pipeline"],
        advisor=extras["advisor"],
        oportunidades=extras["oportunidades"],
        recomendacoes=extras["recomendacoes"],
        simulador=extras["simulador"],
        sim_inputs=extras["sim_inputs"],
        performance_area=extras["performance_area"],
        origem=extras["origem"],
        adv_score=extras["adv_score"],
        insights=extras["insights"],
        midia=extras["midia"],
        carteira=extras["carteira"],
        pode_gerenciar_metas=pode_gerenciar_metas(user),
        ocultar_financeiro=ocultar,
    )


def meta_vs_realizado_periodo(organization, meta, ticket_valor: Decimal | None, periodo: PeriodoComercial):
    from comercial.services.metrics import MetaVsRealizado, receita_no_periodo
    from decimal import ROUND_UP

    mes = receita_no_periodo(organization, periodo)
    if meta is None:
        return MetaVsRealizado(
            contratos_necessarios=None,
            contratos_realizados=mes.contratos,
            contratos_faltantes=None,
            receita_necessaria=None,
            receita_realizada=mes.contratado,
            gap_receita=None,
            pct_atingido=None,
            mensagem="Defina uma meta para acompanhar o crescimento.",
        )

    nec = None
    faltam = None
    if ticket_valor and ticket_valor > 0:
        nec = int(
            (Decimal(meta.meta_mensal) / Decimal(ticket_valor)).to_integral_value(
                rounding=ROUND_UP
            )
        )
        faltam = max(0, nec - mes.contratos)

    from comercial.services.money import money as money_q

    gap = money_q(max(ZERO, Decimal(meta.meta_mensal) - mes.contratado))
    pct = None
    if meta.meta_mensal > 0:
        pct = money_q(100 * mes.contratado / Decimal(meta.meta_mensal))

    return MetaVsRealizado(
        contratos_necessarios=nec,
        contratos_realizados=mes.contratos,
        contratos_faltantes=faltam,
        receita_necessaria=money_q(meta.meta_mensal),
        receita_realizada=mes.contratado,
        gap_receita=gap,
        pct_atingido=pct,
        mensagem="",
    )
