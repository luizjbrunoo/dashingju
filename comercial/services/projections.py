"""Projeções comerciais — estimativas, não garantia."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from calendar import monthrange

from comercial.services.money import MIN_AMOSTRAS_CONVERSAO, ZERO, money, safe_div
from comercial.services.metrics import periodo_ano, periodo_do_mes, receita_no_periodo
from comercial.services.pipeline import analisar_pipeline


@dataclass(frozen=True)
class ProjecaoFaturamento:
    realizado_ano: Decimal
    projecao_anual: Decimal | None
    meta_anual: Decimal | None
    gap_projecao: Decimal | None
    metodo: str
    mensagem: str


def projetar_faturamento_anual(organization, meta_anual: Decimal | None) -> ProjecaoFaturamento:
    """Estimativa linear sobre valor contratado YTD (resultado comercial)."""
    hoje = timezone.localdate()
    ano = receita_no_periodo(organization, periodo_ano(hoje))
    dias_ano = 366 if _bissexto(hoje.year) else 365
    dias_decorridos = (hoje - date(hoje.year, 1, 1)).days + 1
    realizado = ano.contratado

    if realizado <= 0 or dias_decorridos <= 0:
        return ProjecaoFaturamento(
            realizado_ano=realizado,
            projecao_anual=None,
            meta_anual=money(meta_anual) if meta_anual else None,
            gap_projecao=None,
            metodo="indisponivel",
            mensagem=(
                "Dados insuficientes para projeção. "
                "Registre contratos para estimar o ritmo anual."
            ),
        )

    media_diaria = safe_div(realizado, dias_decorridos)
    proj = money(Decimal(media_diaria) * Decimal(dias_ano)) if media_diaria else None
    gap = None
    if meta_anual is not None and proj is not None:
        gap = money(max(ZERO, Decimal(meta_anual) - proj))

    return ProjecaoFaturamento(
        realizado_ano=realizado,
        projecao_anual=proj,
        meta_anual=money(meta_anual) if meta_anual else None,
        gap_projecao=gap,
        metodo="ritmo_linear_contratado_ytd",
        mensagem=(
            "Projeção / estimativa com base no ritmo de contratos do ano atual. "
            "Não representa garantia de resultado."
        ),
    )


def _bissexto(ano: int) -> bool:
    return ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0)


@dataclass(frozen=True)
class ProjecaoMensal:
    realizado: Decimal
    projecao: Decimal | None
    meta: Decimal | None
    gap: Decimal | None
    restante_meta: Decimal | None
    pct_atingido: Decimal | None
    meta_projetada_atingida: bool
    mes_encerrado: bool
    metodo: str
    mensagem: str
    pipeline_total: Decimal = ZERO
    pipeline_qualificado: Decimal = ZERO
    taxa_fechamento_pct: Decimal | None = None


def projetar_faturamento_mensal(
    organization,
    meta_mensal: Decimal | None,
    *,
    ano: int | None = None,
    mes: int | None = None,
    ticket: Decimal | None = None,
    pipeline=None,
) -> ProjecaoMensal:
    """
    Realizado = valor contratado no período (fechamento comercial).
    Projeção = realizado + (pipeline qualificado × taxa histórica de fechamento),
    somente com amostra suficiente. Perdidos não entram. Sem garantia.
    """
    hoje = timezone.localdate()
    ano = ano or hoje.year
    mes = mes or hoje.month
    periodo = periodo_do_mes(ano, mes, cortar_hoje=True)
    rec = receita_no_periodo(organization, periodo)
    realizado = rec.contratado
    meta = money(meta_mensal) if meta_mensal is not None else None
    restante = money(max(ZERO, Decimal(meta) - realizado)) if meta is not None else None
    pct = None
    if meta is not None and meta > 0:
        pct = money(100 * realizado / Decimal(meta))

    ultimo_dia = date(ano, mes, monthrange(ano, mes)[1])
    mes_encerrado = ultimo_dia < hoje
    mes_atual = ano == hoje.year and mes == hoje.month

    if pipeline is None:
        pipeline = analisar_pipeline(organization, periodo, ticket=ticket)

    pipeline_total = money(pipeline.pipeline_total)
    pipeline_qualificado = money(pipeline.pipeline_qualificado)
    encerrados = pipeline.ganhos + pipeline.perdidos
    taxa = None
    if encerrados >= MIN_AMOSTRAS_CONVERSAO:
        taxa = safe_div(pipeline.ganhos, encerrados)

    def _fechar(proj, metodo, mensagem, atingida=False, gap=None):
        if meta is not None and proj is not None:
            if proj >= meta:
                gap = ZERO
                atingida = True
            else:
                gap = money(meta - proj)
        return ProjecaoMensal(
            realizado=realizado,
            projecao=proj,
            meta=meta,
            gap=gap,
            restante_meta=restante,
            pct_atingido=pct,
            meta_projetada_atingida=atingida,
            mes_encerrado=mes_encerrado,
            metodo=metodo,
            mensagem=mensagem,
            pipeline_total=pipeline_total,
            pipeline_qualificado=pipeline_qualificado,
            taxa_fechamento_pct=money(100 * taxa) if taxa is not None else None,
        )

    if mes_encerrado or not mes_atual:
        return _fechar(
            realizado if realizado > 0 else None,
            "realizado_mes" if realizado > 0 else "indisponivel",
            (
                "Mês encerrado — valores comerciais realizados (sem estimativa adicional)."
                if realizado > 0
                else "Dados insuficientes para projeção."
            ),
        )

    if taxa is None:
        return _fechar(
            None,
            "indisponivel",
            (
                "Dados insuficientes para projeção. "
                "Pipeline e desfechos continuam visíveis quando existirem. "
                "Não representa garantia de resultado."
            ),
        )

    extra = money(Decimal(pipeline_qualificado) * Decimal(taxa)) if pipeline_qualificado else ZERO
    proj = money(realizado + extra)
    mensagem = (
        "Projeção / estimativa baseada no realizado comercial, no pipeline qualificado "
        f"(Quente/Fervendo) e na taxa observada de fechamento ({money(100 * taxa)}%). "
        "Oportunidades perdidas não entram. Não representa garantia de resultado."
    )
    return _fechar(proj, "pipeline_taxa_historica", mensagem)
