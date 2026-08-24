"""
Métricas comerciais atribuídas ao Google Ads (Resultados do Negócio).

Todas as consultas são tenant-scoped e respeitam PeriodoMarketing.
Investimento real só existe em modo demonstração até integração com API Google Ads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from financeiro.choices import StatusContrato
from financeiro.models import CobrancaRecebimento, Contrato
from marketing.definitions import FASES_PROPOSTA
from marketing.services import demo
from marketing.services.atribuicao import (
    clientes_google_ads,
    ids_clientes_google_ads,
    resumo_qualidade_atribuicao,
)
from marketing.services.periodo import PeriodoMarketing, filtro_datetime_campo
from marketing.services.resultados_operacional import (
    LinksOperacionais,
    links_operacionais,
    queryset_followups_atrasados,
    queryset_leads_sem_proxima_acao,
)
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Compromisso

_QTD = Decimal("0.01")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class FunilResultados:
    investimento: Decimal | None
    investimento_demo: bool
    leads: int
    consultas_agendadas: int
    consultas_realizadas: int
    propostas: int
    contratos: int
    receita_contratada: Decimal
    receita_recebida: Decimal


@dataclass(frozen=True)
class EficienciaResultados:
    cpl: Decimal | None
    custo_por_consulta: Decimal | None
    cac_midia: Decimal | None
    conv_lead_consulta_pct: Decimal | None
    conv_lead_contrato_pct: Decimal | None
    conv_consulta_contrato_pct: Decimal | None
    ticket_medio: Decimal | None
    receita_midia_ratio: Decimal | None


@dataclass(frozen=True)
class OperacionalResultados:
    leads_sem_proxima_acao: int
    followups_atrasados: int
    leads_por_fase: dict[str, int]


@dataclass(frozen=True)
class QualidadeDados:
    total_clientes_periodo: int
    identificados: int
    nao_identificados: int
    pct_identificados: float | None
    pct_nao_identificados: float | None


@dataclass(frozen=True)
class ResultadosNegocio:
    periodo: PeriodoMarketing
    funil: FunilResultados
    eficiencia: EficienciaResultados
    operacional: OperacionalResultados
    qualidade: QualidadeDados
    tem_dados_suficientes: bool


def _dec(valor: float | int | Decimal | None) -> Decimal | None:
    if valor is None:
        return None
    return Decimal(str(valor)).quantize(_QTD, rounding=ROUND_HALF_UP)


def _safe_div(
    numerador: Decimal | int,
    denominador: int | Decimal,
) -> Decimal | None:
    if not denominador:
        return None
    return _dec(Decimal(numerador) / Decimal(denominador))


def _safe_pct(parte: int, total: int) -> Decimal | None:
    if not total:
        return None
    return _dec(100 * Decimal(parte) / Decimal(total))


def investimento_demo_periodo(periodo: PeriodoMarketing, *, nicho: str = "") -> Decimal:
    """Soma custo sintético do demo.py no intervalo (somente modo demonstração)."""
    dias_serie = max(periodo.dias + 14, 30)
    serie = demo.demo_daily_series(dias_serie, nicho=nicho)
    total_micros = 0
    for item in serie:
        try:
            d = date.fromisoformat(item.date_label)
        except ValueError:
            continue
        if periodo.contem(d):
            total_micros += item.cost_micros
    return _dec(total_micros / 1_000_000) or _ZERO


def get_investimento(
    user,
    periodo: PeriodoMarketing,
    *,
    modo_demo: bool = False,
    nicho: str = "",
) -> tuple[Decimal | None, bool]:
    """
    Retorna (valor, eh_demo).
    Produção sem API: None (nunca inventar investimento real).
    """
    del user  # reservado para integração futura por tenant
    if modo_demo:
        return investimento_demo_periodo(periodo, nicho=nicho), True
    return None, False


def get_leads(user, periodo: PeriodoMarketing) -> int:
    return clientes_google_ads(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    ).count()


def _leads_qs_periodo(user, periodo: PeriodoMarketing):
    return clientes_google_ads(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )


def _ids_leads(user, periodo: PeriodoMarketing) -> list[int]:
    return ids_clientes_google_ads(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )


def _consultas_qs(user, ids_leads, periodo: PeriodoMarketing):
    if ids_leads is not None and not hasattr(ids_leads, "query") and not ids_leads:
        return Compromisso.objects.none()
    qs = Compromisso.objects.filter(
        user=user,
        cliente_id__in=ids_leads,
        tipo=TipoCompromisso.CONSULTA,
    ).exclude(status=StatusCompromisso.CANCELADO)
    return filtro_datetime_campo(
        qs, "data_hora", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )


def _contratos_qs(user, ids_leads, periodo: PeriodoMarketing):
    if ids_leads is not None and not hasattr(ids_leads, "query") and not ids_leads:
        return Contrato.objects.none()
    qs = Contrato.objects.filter(
        usuario=user,
        cliente_id__in=ids_leads,
        status__in=(StatusContrato.ACTIVE, StatusContrato.CLOSED),
    )
    return filtro_datetime_campo(
        qs, "criado_em", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )


def get_consultas_agendadas(user, periodo: PeriodoMarketing) -> int:
    ids = _ids_leads(user, periodo)
    return _consultas_qs(user, ids, periodo).count()


def get_consultas_realizadas(user, periodo: PeriodoMarketing) -> int:
    ids = _ids_leads(user, periodo)
    return (
        _consultas_qs(user, ids, periodo)
        .filter(status=StatusCompromisso.REALIZADO)
        .count()
    )


def get_propostas(user, periodo: PeriodoMarketing) -> int:
    """
    Leads Google Ads do período que estão (ou estiveram registrados) em fase de proposta.
    Sem histórico de transições, usa fase_funil atual entre leads do período.
    """
    return clientes_google_ads(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    ).filter(fase_funil__in=FASES_PROPOSTA).count()


def get_contratos(user, periodo: PeriodoMarketing) -> int:
    ids = _leads_qs_periodo(user, periodo).values("pk")
    return _contratos_qs(user, ids, periodo).count()


def get_receita_contratada(user, periodo: PeriodoMarketing) -> Decimal:
    ids = _leads_qs_periodo(user, periodo).values("pk")
    agg = _contratos_qs(user, ids, periodo).aggregate(
        total=Coalesce(Sum("valor_total"), _ZERO)
    )
    return _dec(agg["total"]) or _ZERO


def get_receita_recebida(user, periodo: PeriodoMarketing) -> Decimal:
    ids = _leads_qs_periodo(user, periodo).values("pk")
    agg = CobrancaRecebimento.objects.filter(
        usuario=user,
        cobranca__cliente_id__in=ids,
        cancelado_em__isnull=True,
        data_recebimento__gte=periodo.data_inicio,
        data_recebimento__lte=periodo.data_fim,
    ).aggregate(total=Coalesce(Sum("valor"), _ZERO))
    return _dec(agg["total"]) or _ZERO


def get_cpl(investimento: Decimal | None, leads: int) -> Decimal | None:
    if investimento is None or not leads:
        return None
    return _safe_div(investimento, leads)


def get_custo_por_consulta(
    investimento: Decimal | None,
    consultas_realizadas: int,
) -> Decimal | None:
    """Documentado: usa consultas realizadas (não agendadas)."""
    if investimento is None or not consultas_realizadas:
        return None
    return _safe_div(investimento, consultas_realizadas)


def get_cac_midia(investimento: Decimal | None, leads: int) -> Decimal | None:
    if investimento is None or not leads:
        return None
    return _safe_div(investimento, leads)


def get_ticket_medio(receita_contratada: Decimal, contratos: int) -> Decimal | None:
    if not contratos:
        return None
    return _safe_div(receita_contratada, contratos)


def get_receita_midia_ratio(
    receita_contratada: Decimal,
    investimento: Decimal | None,
) -> Decimal | None:
    if investimento is None or investimento <= 0:
        return None
    return _safe_div(receita_contratada, investimento)


def get_conversao_lead_consulta(consultas_realizadas: int, leads: int) -> Decimal | None:
    return _safe_pct(consultas_realizadas, leads)


def get_conversao_lead_contrato(contratos: int, leads: int) -> Decimal | None:
    return _safe_pct(contratos, leads)


def get_conversao_consulta_contrato(contratos: int, consultas_realizadas: int) -> Decimal | None:
    return _safe_pct(contratos, consultas_realizadas)


def get_leads_sem_proxima_acao(user, periodo: PeriodoMarketing) -> int:
    return queryset_leads_sem_proxima_acao(user, periodo).count()


def get_followups_atrasados(user, periodo: PeriodoMarketing) -> int:
    return queryset_followups_atrasados(user, periodo).count()


def get_leads_por_fase(user, periodo: PeriodoMarketing) -> dict[str, int]:
    qs = (
        clientes_google_ads(
            user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
        )
        .values("fase_funil")
        .annotate(qtd=Count("pk"))
    )
    return {row["fase_funil"] or "": row["qtd"] for row in qs}


def calcular_eficiencia(
    funil: FunilResultados,
) -> EficienciaResultados:
    inv = funil.investimento
    return EficienciaResultados(
        cpl=get_cpl(inv, funil.leads),
        custo_por_consulta=get_custo_por_consulta(inv, funil.consultas_realizadas),
        cac_midia=get_cac_midia(inv, funil.leads),
        conv_lead_consulta_pct=get_conversao_lead_consulta(
            funil.consultas_realizadas, funil.leads
        ),
        conv_lead_contrato_pct=get_conversao_lead_contrato(funil.contratos, funil.leads),
        conv_consulta_contrato_pct=get_conversao_consulta_contrato(
            funil.contratos, funil.consultas_realizadas
        ),
        ticket_medio=get_ticket_medio(funil.receita_contratada, funil.contratos),
        receita_midia_ratio=get_receita_midia_ratio(funil.receita_contratada, inv),
    )


def calcular_funil(
    user,
    periodo: PeriodoMarketing,
    *,
    modo_demo: bool = False,
    nicho: str = "",
) -> FunilResultados:
    investimento, demo_flag = get_investimento(
        user, periodo, modo_demo=modo_demo, nicho=nicho
    )
    leads_qs = _leads_qs_periodo(user, periodo)
    ids_subquery = leads_qs.values("pk")

    leads_agg = leads_qs.aggregate(
        leads=Count("pk"),
        propostas=Count("pk", filter=Q(fase_funil__in=FASES_PROPOSTA)),
    )

    consultas_agg = _consultas_qs(user, ids_subquery, periodo).aggregate(
        agendadas=Count("pk"),
        realizadas=Count("pk", filter=Q(status=StatusCompromisso.REALIZADO)),
    )

    contratos_agg = _contratos_qs(user, ids_subquery, periodo).aggregate(
        qtd=Count("pk"),
        receita=Coalesce(Sum("valor_total"), _ZERO),
    )

    receita_rec = _ZERO
    if leads_agg["leads"]:
        receita_rec_agg = CobrancaRecebimento.objects.filter(
            usuario=user,
            cobranca__cliente_id__in=ids_subquery,
            cancelado_em__isnull=True,
            data_recebimento__gte=periodo.data_inicio,
            data_recebimento__lte=periodo.data_fim,
        ).aggregate(total=Coalesce(Sum("valor"), _ZERO))
        receita_rec = _dec(receita_rec_agg["total"]) or _ZERO

    return FunilResultados(
        investimento=investimento,
        investimento_demo=demo_flag,
        leads=leads_agg["leads"],
        consultas_agendadas=consultas_agg["agendadas"],
        consultas_realizadas=consultas_agg["realizadas"],
        propostas=leads_agg["propostas"],
        contratos=contratos_agg["qtd"],
        receita_contratada=_dec(contratos_agg["receita"]) or _ZERO,
        receita_recebida=receita_rec,
    )


def calcular_resultados_negocio(
    user,
    periodo: PeriodoMarketing | None = None,
    *,
    modo_demo: bool = False,
    nicho: str = "",
) -> ResultadosNegocio:
    """Ponto de entrada principal para o painel Resultados do Negócio."""
    if periodo is None:
        periodo = PeriodoMarketing.ultimos_dias(30)

    funil = calcular_funil(user, periodo, modo_demo=modo_demo, nicho=nicho)
    eficiencia = calcular_eficiencia(funil)
    qualidade_raw = resumo_qualidade_atribuicao(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )
    qualidade = QualidadeDados(
        total_clientes_periodo=qualidade_raw["total"],
        identificados=qualidade_raw["identificados"],
        nao_identificados=qualidade_raw["nao_identificados"],
        pct_identificados=qualidade_raw["pct_identificados"],
        pct_nao_identificados=qualidade_raw["pct_nao_identificados"],
    )
    operacional = OperacionalResultados(
        leads_sem_proxima_acao=get_leads_sem_proxima_acao(user, periodo),
        followups_atrasados=get_followups_atrasados(user, periodo),
        leads_por_fase=get_leads_por_fase(user, periodo),
    )
    tem_dados = (
        funil.leads > 0
        or funil.consultas_agendadas > 0
        or funil.contratos > 0
        or funil.propostas > 0
        or funil.receita_contratada > 0
        or funil.receita_recebida > 0
    )
    return ResultadosNegocio(
        periodo=periodo,
        funil=funil,
        eficiencia=eficiencia,
        operacional=operacional,
        qualidade=qualidade,
        tem_dados_suficientes=tem_dados,
    )


def _pct_trend(current: float, previous: float) -> tuple[float | None, str]:
    if previous == 0:
        if current == 0:
            return 0.0, "flat"
        return None, "up"
    pct = (current - previous) / previous * 100.0
    if pct > 0.5:
        return pct, "up"
    if pct < -0.5:
        return pct, "down"
    return pct, "flat"


def calcular_tendencias_resultados(
    atual: ResultadosNegocio,
    anterior: ResultadosNegocio,
) -> dict[str, dict]:
    """Comparação período atual vs. anterior (padrão do painel de mídia)."""
    f_a, f_p = atual.funil, anterior.funil

    def _t(curr: float, prev: float) -> dict:
        pct, trend = _pct_trend(curr, prev)
        return {"pct": pct, "trend": trend}

    inv_a = float(f_a.investimento or 0)
    inv_p = float(f_p.investimento or 0)
    rec_a = float(f_a.receita_contratada)
    rec_p = float(f_p.receita_contratada)

    return {
        "investimento": _t(inv_a, inv_p),
        "leads": _t(float(f_a.leads), float(f_p.leads)),
        "consultas_realizadas": _t(
            float(f_a.consultas_realizadas), float(f_p.consultas_realizadas)
        ),
        "contratos": _t(float(f_a.contratos), float(f_p.contratos)),
        "receita_contratada": _t(rec_a, rec_p),
        "receita_recebida": _t(
            float(f_a.receita_recebida), float(f_p.receita_recebida)
        ),
    }


@dataclass(frozen=True)
class ContextoResultadosDashboard:
    resultados: ResultadosNegocio
    resultados_anterior: ResultadosNegocio
    tendencias: dict[str, dict]
    compare_note: str
    ocultar_financeiro: bool
    ocultar_resultados_negocio: bool
    funil_etapas: tuple[dict, ...]
    leads_por_fase: tuple[dict, ...]
    links: LinksOperacionais
    evolucao: object
    comparacao: tuple
    granularidade: str
    ranking_campanhas: object


def _contexto_resultados_oculto(periodo: PeriodoMarketing) -> ContextoResultadosDashboard:
    """Stub leve quando o usuário não tem permissão de analytics."""
    from marketing.services.resultados_campanhas import RankingCampanhas
    from marketing.services.resultados_evolucao import SerieEvolucaoResultados
    from marketing.services.resultados_operacional import LinksOperacionais

    funil_vazio = FunilResultados(
        investimento=None,
        investimento_demo=False,
        leads=0,
        consultas_agendadas=0,
        consultas_realizadas=0,
        propostas=0,
        contratos=0,
        receita_contratada=_ZERO,
        receita_recebida=_ZERO,
    )
    ef_vazio = EficienciaResultados(
        cpl=None,
        custo_por_consulta=None,
        cac_midia=None,
        conv_lead_consulta_pct=None,
        conv_lead_contrato_pct=None,
        conv_consulta_contrato_pct=None,
        ticket_medio=None,
        receita_midia_ratio=None,
    )
    op_vazio = OperacionalResultados(
        leads_sem_proxima_acao=0,
        followups_atrasados=0,
        leads_por_fase={},
    )
    qual_vazio = QualidadeDados(
        total_clientes_periodo=0,
        identificados=0,
        nao_identificados=0,
        pct_identificados=None,
        pct_nao_identificados=None,
    )
    resultados_vazio = ResultadosNegocio(
        periodo=periodo,
        funil=funil_vazio,
        eficiencia=ef_vazio,
        operacional=op_vazio,
        qualidade=qual_vazio,
        tem_dados_suficientes=False,
    )
    evolucao_vazia = SerieEvolucaoResultados(
        labels=(),
        leads=(),
        consultas=(),
        contratos=(),
        receita=(),
        granularidade="auto",
        ocultar_receita=True,
    )
    ranking_vazio = RankingCampanhas(
        exibir=False,
        aviso="",
        campanhas=(),
        ordenacao="contratos",
        rateio_investimento_demo=False,
    )
    links_vazios = LinksOperacionais(
        leads_sem_acao=None,
        followups_atrasados=None,
        leads_sem_acao_qtd=0,
        followups_atrasados_qtd=0,
    )

    return ContextoResultadosDashboard(
        resultados=resultados_vazio,
        resultados_anterior=resultados_vazio,
        tendencias={},
        compare_note=f"{periodo.dias} dias vs. período anterior",
        ocultar_financeiro=True,
        ocultar_resultados_negocio=True,
        funil_etapas=(),
        leads_por_fase=(),
        links=links_vazios,
        evolucao=evolucao_vazia,
        comparacao=(),
        granularidade="auto",
        ranking_campanhas=ranking_vazio,
    )


def _rotulos_fase_funil() -> dict[str, str]:
    from usuarios.models import Cliente

    return dict(Cliente.FASE_FUNIL_CHOICES)


def _leads_por_fase_ui(leads_por_fase: dict[str, int]) -> tuple[dict, ...]:
    rotulos = _rotulos_fase_funil()
    itens = [
        {
            "codigo": codigo,
            "label": rotulos.get(codigo, codigo or "Sem fase"),
            "qtd": qtd,
        }
        for codigo, qtd in leads_por_fase.items()
        if qtd
    ]
    itens.sort(key=lambda x: x["qtd"], reverse=True)
    return tuple(itens)


def contexto_dashboard_resultados(
    user,
    periodo: PeriodoMarketing,
    *,
    modo_demo: bool = True,
    nicho: str = "",
    granularidade: str = "auto",
    orden_campanhas: str = "contratos",
) -> ContextoResultadosDashboard:
    """
    Contexto pronto para o template — Fases 3–8 integradas ao painel Google Ads.
    Investimento em modo demo; demais métricas vêm de dados reais do tenant.
    """
    from marketing.permissions import (
        pode_ver_metricas_financeiras_marketing,
        pode_ver_resultados_marketing,
    )

    if not pode_ver_resultados_marketing(user):
        return _contexto_resultados_oculto(periodo)

    ocultar_fin = not pode_ver_metricas_financeiras_marketing(user)

    atual = calcular_resultados_negocio(
        user, periodo, modo_demo=modo_demo, nicho=nicho
    )
    anterior = calcular_resultados_negocio(
        user,
        periodo.periodo_anterior(),
        modo_demo=modo_demo,
        nicho=nicho,
    )

    if ocultar_fin:
        funil = FunilResultados(
            investimento=atual.funil.investimento,
            investimento_demo=atual.funil.investimento_demo,
            leads=atual.funil.leads,
            consultas_agendadas=atual.funil.consultas_agendadas,
            consultas_realizadas=atual.funil.consultas_realizadas,
            propostas=atual.funil.propostas,
            contratos=atual.funil.contratos,
            receita_contratada=_ZERO,
            receita_recebida=_ZERO,
        )
        atual = ResultadosNegocio(
            periodo=atual.periodo,
            funil=funil,
            eficiencia=calcular_eficiencia(funil),
            operacional=atual.operacional,
            qualidade=atual.qualidade,
            tem_dados_suficientes=atual.tem_dados_suficientes,
        )

    f = atual.funil
    etapas_raw = (
        {
            "key": "investimento",
            "label": "Investimento",
            "valor": f.investimento,
            "demo": f.investimento_demo,
            "tipo": "money",
        },
        {"key": "leads", "label": "Leads", "valor": f.leads, "tipo": "int"},
        {
            "key": "consultas",
            "label": "Consultas",
            "valor": f.consultas_realizadas,
            "sub": f"agendadas: {f.consultas_agendadas}",
            "tipo": "int",
        },
        {"key": "propostas", "label": "Propostas", "valor": f.propostas, "tipo": "int"},
        {"key": "contratos", "label": "Contratos", "valor": f.contratos, "tipo": "int"},
        {
            "key": "receita",
            "label": "Receita contratada",
            "valor": f.receita_contratada,
            "tipo": "money",
            "oculto": ocultar_fin,
        },
    )
    funil_etapas = tuple(e for e in etapas_raw if not e.get("oculto"))

    op = atual.operacional
    links = links_operacionais(
        user,
        periodo,
        leads_sem_acao=op.leads_sem_proxima_acao,
        followups_atrasados=op.followups_atrasados,
    )

    from marketing.services.resultados_evolucao import (
        calcular_evolucao_resultados,
        linhas_comparacao_periodo,
        tendencias_completas,
    )

    tendencias = tendencias_completas(atual, anterior)
    evolucao = calcular_evolucao_resultados(
        user,
        periodo,
        granularidade=granularidade,
        ocultar_financeiro=ocultar_fin,
    )
    comparacao = linhas_comparacao_periodo(
        atual, anterior, tendencias, ocultar_financeiro=ocultar_fin
    )

    from marketing.services.resultados_campanhas import calcular_ranking_campanhas

    ranking_campanhas = calcular_ranking_campanhas(
        user,
        periodo,
        ordenacao=orden_campanhas,
        modo_demo=modo_demo,
        nicho=nicho,
        ocultar_financeiro=ocultar_fin,
    )

    return ContextoResultadosDashboard(
        resultados=atual,
        resultados_anterior=anterior,
        tendencias=tendencias,
        compare_note=f"{periodo.dias} dias vs. período anterior",
        ocultar_financeiro=ocultar_fin,
        ocultar_resultados_negocio=False,
        funil_etapas=funil_etapas,
        leads_por_fase=_leads_por_fase_ui(atual.operacional.leads_por_fase),
        links=links,
        evolucao=evolucao,
        comparacao=comparacao,
        granularidade=evolucao.granularidade,
        ranking_campanhas=ranking_campanhas,
    )
