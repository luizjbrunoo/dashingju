"""Financeiro PRO — interpretação determinística sobre services W2/W3.

Não recalcula recebido/a receber/vencido/previsão: reutiliza
calcular_kpis_cobrancas_organization, calcular_previsao, calcular_inadimplencia.

Priority Score alinhado ao Growth Advisor comercial (vencido base 90).
Sem LLM. Sem score 0–100 de “saúde”. Sem fallback User.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone

from comercial.services.advisor import _TEMA_BASE
from financeiro.choices import DIAS_VENCE_EM_BREVE, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.services.cobranca_inadimplencia import calcular_inadimplencia
from financeiro.services.cobranca_listagem import (
    calcular_kpis_cobrancas_organization,
    queryset_anotado_organization,
    sincronizar_statuses,
)
from financeiro.services.cobranca_previsao import calcular_previsao

ZERO = Decimal("0")

# Health — sinais objetivos sobre taxa já existente (vencido / a_receber).
HEALTH_CRITICO_TAXA = Decimal("40")
HEALTH_ATENCAO_TAXA = Decimal("15")
HEALTH_ATRASO_CRITICO_DIAS = 90

# Concentração: top 1 cliente ≥ este % do recebido no mês, com ≥2 clientes pagantes.
CONCENTRACAO_LIMITE_PCT = Decimal("50")
CONCENTRACAO_MIN_CLIENTES = 2

ATTENTION_LIMIT = 5

HEALTH_SAUDAVEL = "SAUDAVEL"
HEALTH_ATENCAO = "ATENCAO"
HEALTH_CRITICO = "CRITICO"
HEALTH_INSUFICIENTE = "DADOS_INSUFICIENTES"

TREND_MELHORANDO = "melhorando"
TREND_ESTAVEL = "estavel"
TREND_PIORANDO = "piorando"
TREND_INSUFICIENTE = "dados_insuficientes"


@dataclass(frozen=True)
class PrioridadeFinanceira:
    type: str
    severity: str
    title: str
    reason: str
    evidence: tuple[str, ...]
    recommended_action: str
    cta: str
    cta_url: str
    priority: int
    quantidade: int
    valor: Decimal | None


@dataclass(frozen=True)
class ItemAtencao:
    cobranca_id: int
    cliente_nome: str
    descricao: str
    saldo: Decimal
    data_vencimento: date
    sinal: str
    recommended_action: str
    url: str


@dataclass(frozen=True)
class ConcentracaoReceita:
    percentual: Decimal
    cliente_nome: str
    mensagem: str


@dataclass(frozen=True)
class PainelFinanceiroPro:
    disponivel: bool
    ver_cobrancas: bool
    ver_recebimentos: bool
    health: str
    health_label: str
    health_motivo: str
    tendencia: str
    tendencia_label: str
    prioridade: PrioridadeFinanceira | None
    atencao: tuple[ItemAtencao, ...]
    concentracao: ConcentracaoReceita | None
    mensagem_vazia: str
    qtd_vencidas: int
    dias_atraso_max: int | None


def _vazio(*, ver_cobrancas: bool, ver_recebimentos: bool) -> PainelFinanceiroPro:
    return PainelFinanceiroPro(
        disponivel=False,
        ver_cobrancas=ver_cobrancas,
        ver_recebimentos=ver_recebimentos,
        health=HEALTH_INSUFICIENTE,
        health_label="Dados insuficientes",
        health_motivo="Sem escritório resolvido ou sem permissão para cobranças.",
        tendencia=TREND_INSUFICIENTE,
        tendencia_label="Dados insuficientes",
        prioridade=None,
        atencao=(),
        concentracao=None,
        mensagem_vazia="Cadastre cobranças para acompanhar a saúde financeira.",
        qtd_vencidas=0,
        dias_atraso_max=None,
    )


def _ultimo_dia_mes_anterior(hoje: date) -> date:
    if hoje.month == 1:
        ano, mes = hoje.year - 1, 12
    else:
        ano, mes = hoje.year, hoje.month - 1
    return date(ano, mes, monthrange(ano, mes)[1])


def _label_health(codigo: str) -> str:
    return {
        HEALTH_SAUDAVEL: "Saudável",
        HEALTH_ATENCAO: "Atenção",
        HEALTH_CRITICO: "Crítico",
        HEALTH_INSUFICIENTE: "Dados insuficientes",
    }.get(codigo, "Dados insuficientes")


def _label_tendencia(codigo: str) -> str:
    return {
        TREND_MELHORANDO: "Melhorando",
        TREND_ESTAVEL: "Estável",
        TREND_PIORANDO: "Piorando",
        TREND_INSUFICIENTE: "Dados insuficientes",
    }.get(codigo, "Dados insuficientes")


def _tem_atividade(organization) -> bool:
    if organization is None:
        return False
    if Cobranca.objects.filter(organization=organization).exclude(
        status=StatusCobranca.CANCELED
    ).exists():
        return True
    return CobrancaRecebimento.objects.filter(
        organization=organization, cancelado_em__isnull=True
    ).exists()


def _classificar_health(*, taxa: Decimal, vencido: Decimal, atraso_max: int | None, tem_atividade: bool) -> tuple[str, str]:
    if not tem_atividade:
        return (
            HEALTH_INSUFICIENTE,
            "Ainda não há cobranças nem recebimentos neste escritório.",
        )
    if atraso_max is not None and atraso_max >= HEALTH_ATRASO_CRITICO_DIAS:
        return (
            HEALTH_CRITICO,
            f"Há cobrança com {atraso_max} dias de atraso.",
        )
    if vencido > 0 and taxa >= HEALTH_CRITICO_TAXA:
        return (
            HEALTH_CRITICO,
            f"{taxa}% do a receber está vencido.",
        )
    if vencido > 0 or taxa >= HEALTH_ATENCAO_TAXA:
        return (
            HEALTH_ATENCAO,
            "Existe saldo vencido que exige acompanhamento.",
        )
    return HEALTH_SAUDAVEL, "Não há saldo vencido no contas a receber."


def _classificar_tendencia(kpis_atual, kpis_anterior, *, tem_atividade: bool) -> str:
    """Compara recebido do mês atual com o mês anterior (métrica temporal real)."""
    if not tem_atividade:
        return TREND_INSUFICIENTE
    atual_r = kpis_atual.recebido_mes
    prev_r = kpis_anterior.recebido_mes
    if prev_r == 0 and atual_r == 0:
        return TREND_INSUFICIENTE
    if atual_r > prev_r:
        return TREND_MELHORANDO
    if atual_r < prev_r:
        return TREND_PIORANDO
    return TREND_ESTAVEL


def _prioridade_label(score: int) -> str:
    if score >= 80:
        return "urgente"
    if score >= 60:
        return "alta"
    if score >= 40:
        return "media"
    return "baixa"


def _contratos_sem_cobranca(organization) -> int:
    return (
        Contrato.objects.filter(
            organization=organization,
            status=StatusContrato.ACTIVE,
        )
        .annotate(n_cob=Count("cobrancas"))
        .filter(n_cob=0)
        .count()
    )


def _concentracao(organization, *, hoje: date, ver_recebimentos: bool) -> ConcentracaoReceita | None:
    if not ver_recebimentos or organization is None:
        return None
    inicio = date(hoje.year, hoje.month, 1)
    fim = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    rows = list(
        CobrancaRecebimento.objects.filter(
            organization=organization,
            cancelado_em__isnull=True,
            data_recebimento__gte=inicio,
            data_recebimento__lte=fim,
        )
        .values("cobranca__cliente_id", "cobranca__cliente__nome")
        .annotate(total=Sum("valor"))
        .order_by("-total")
    )
    if len(rows) < CONCENTRACAO_MIN_CLIENTES:
        return None
    total = sum((r["total"] or ZERO) for r in rows)
    if total <= 0:
        return None
    top = rows[0]
    pct = ((top["total"] or ZERO) / total * Decimal("100")).quantize(Decimal("0.1"))
    if pct < CONCENTRACAO_LIMITE_PCT:
        return None
    nome = top["cobranca__cliente__nome"] or "Cliente"
    return ConcentracaoReceita(
        percentual=pct,
        cliente_nome=nome,
        mensagem=f"Concentração elevada observada: {nome} representa {pct}% do recebido no mês.",
    )


def _nba(sinal: str, *, ver_recebimentos: bool) -> str:
    if sinal == "vencida":
        return "Revisar cobrança"
    if sinal == "parcial":
        return "Confirmar recebimento" if ver_recebimentos else "Revisar cobrança"
    return "Revisar cobrança"


def _lista_atencao(organization, *, hoje: date, ver_recebimentos: bool) -> tuple[ItemAtencao, ...]:
    qs = (
        queryset_anotado_organization(organization)
        .exclude(status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT])
        .filter(saldo_calc__gt=0)
        .select_related("cliente")
    )
    cobrancas = list(qs)
    sincronizar_statuses(cobrancas, hoje=hoje)
    limite_breve = hoje + timedelta(days=DIAS_VENCE_EM_BREVE)
    ranked = []
    for cob in cobrancas:
        saldo = cob.saldo_calc or ZERO
        venc = cob.data_vencimento
        if venc < hoje:
            sinal = "vencida"
            peso = (10_000 + (hoje - venc).days, saldo)
        elif venc <= limite_breve:
            sinal = "vence_em_breve"
            peso = (5_000 + (limite_breve - venc).days, saldo)
        else:
            continue
        if cob.status == StatusCobranca.PARTIALLY_PAID:
            sinal = "parcial"
        ranked.append((peso, cob, sinal, saldo))
    ranked.sort(key=lambda row: (-row[0][0], -row[0][1], row[1].id))
    itens = []
    for _, cob, sinal, saldo in ranked[:ATTENTION_LIMIT]:
        itens.append(
            ItemAtencao(
                cobranca_id=cob.pk,
                cliente_nome=cob.cliente.nome,
                descricao=cob.descricao or "",
                saldo=saldo,
                data_vencimento=cob.data_vencimento,
                sinal=sinal,
                recommended_action=_nba(sinal, ver_recebimentos=ver_recebimentos),
                url=reverse("financeiro_cobranca_detalhe", args=[cob.pk]),
            )
        )
    return tuple(itens)


def _montar_prioridade(
    *,
    kpis,
    inadimplencia,
    previsao,
    concentracao: ConcentracaoReceita | None,
    contratos_gap: int,
    ver_recebimentos: bool,
) -> PrioridadeFinanceira | None:
    qtd_venc = inadimplencia.quantidade
    valor_venc = kpis.vencido
    atraso_max = max((c.dias_atraso_max for c in inadimplencia.clientes), default=None)

    if qtd_venc > 0 and valor_venc > 0:
        base = _TEMA_BASE["cobrancas_vencidas"]
        volume = min(10, qtd_venc)
        score = max(0, min(100, base + volume))
        evid = [
            f"{qtd_venc} cobrança(s) vencida(s)",
            f"R$ {valor_venc} pendentes",
        ]
        if atraso_max is not None:
            evid.append(f"mais antiga há {atraso_max} dias")
        return PrioridadeFinanceira(
            type="cobrancas_vencidas",
            severity=_prioridade_label(score),
            title="Cobranças vencidas",
            reason="Há saldo vencido no contas a receber — receita confirmada em risco.",
            evidence=tuple(evid),
            recommended_action="Revisar cobranças vencidas hoje.",
            cta="Ver cobranças",
            cta_url=reverse("financeiro_cobranca_listar") + "?status=overdue",
            priority=score,
            quantidade=qtd_venc,
            valor=valor_venc,
        )

    hoje_ref = previsao.janelas[0].data_inicio if previsao.janelas else None
    qtd_breve = 0
    valor_breve = ZERO
    if hoje_ref is not None:
        for cob in previsao.cobrancas:
            dias = (cob.data_vencimento - hoje_ref).days
            if 0 <= dias <= DIAS_VENCE_EM_BREVE:
                qtd_breve += 1
                valor_breve += cob.saldo_exibicao or ZERO

    if qtd_breve > 0:
        base = 70
        score = max(0, min(100, base + min(10, qtd_breve)))
        return PrioridadeFinanceira(
            type="vencem_em_breve",
            severity=_prioridade_label(score),
            title="Cobranças próximas do vencimento",
            reason=f"{qtd_breve} cobrança(s) vencem nos próximos {DIAS_VENCE_EM_BREVE} dias.",
            evidence=(
                f"{qtd_breve} cobrança(s) nos próximos {DIAS_VENCE_EM_BREVE} dias",
                f"Previsão 30 dias: R$ {previsao.cumulativo_30}",
            ),
            recommended_action="Revisar os vencimentos da semana.",
            cta="Ver previsão",
            cta_url=reverse("financeiro_cobranca_previsao"),
            priority=score,
            quantidade=qtd_breve,
            valor=valor_breve,
        )

    if contratos_gap > 0:
        score = max(0, min(100, 55 + min(10, contratos_gap)))
        return PrioridadeFinanceira(
            type="contratos_sem_cobranca",
            severity=_prioridade_label(score),
            title="Contratos sem cobrança",
            reason="Existem contratos ativos sem cobrança gerada.",
            evidence=(f"{contratos_gap} contrato(s) ativo(s) sem cobrança",),
            recommended_action="Gerar cobranças dos contratos pendentes.",
            cta="Ver contratos",
            cta_url=reverse("financeiro_contrato_listar"),
            priority=score,
            quantidade=contratos_gap,
            valor=None,
        )

    if concentracao is not None:
        score = 50
        return PrioridadeFinanceira(
            type="concentracao",
            severity=_prioridade_label(score),
            title="Concentração de recebíveis",
            reason=concentracao.mensagem,
            evidence=(f"{concentracao.percentual}% em {concentracao.cliente_nome}",),
            recommended_action="Revisar a carteira de recebimentos do mês.",
            cta="Ver cobranças",
            cta_url=reverse("financeiro_cobranca_listar"),
            priority=score,
            quantidade=1,
            valor=None,
        )
    return None


def montar_financeiro_pro(
    organization,
    *,
    hoje: date | None = None,
    ver_cobrancas: bool = True,
    ver_recebimentos: bool = True,
) -> PainelFinanceiroPro:
    hoje = hoje or timezone.localdate()
    if organization is None or not ver_cobrancas:
        return _vazio(ver_cobrancas=ver_cobrancas, ver_recebimentos=ver_recebimentos)

    kpis = calcular_kpis_cobrancas_organization(organization, hoje=hoje)
    previsao = calcular_previsao(organization, hoje=hoje)
    inadimplencia = calcular_inadimplencia(organization, hoje=hoje)
    kpis_ant = calcular_kpis_cobrancas_organization(
        organization, hoje=_ultimo_dia_mes_anterior(hoje)
    )
    tem_atividade = _tem_atividade(organization)
    atraso_max = max((c.dias_atraso_max for c in inadimplencia.clientes), default=None)
    taxa = ZERO
    if kpis.a_receber > 0:
        taxa = (kpis.vencido / kpis.a_receber * Decimal("100")).quantize(Decimal("0.1"))

    health, health_motivo = _classificar_health(
        taxa=taxa,
        vencido=kpis.vencido,
        atraso_max=atraso_max,
        tem_atividade=tem_atividade,
    )
    tendencia = _classificar_tendencia(kpis, kpis_ant, tem_atividade=tem_atividade)
    concentracao = _concentracao(
        organization, hoje=hoje, ver_recebimentos=ver_recebimentos
    )
    contratos_gap = _contratos_sem_cobranca(organization)
    prioridade = _montar_prioridade(
        kpis=kpis,
        inadimplencia=inadimplencia,
        previsao=previsao,
        concentracao=concentracao,
        contratos_gap=contratos_gap,
        ver_recebimentos=ver_recebimentos,
    )
    if prioridade and not ver_recebimentos:
        prioridade = PrioridadeFinanceira(
            type=prioridade.type,
            severity=prioridade.severity,
            title=prioridade.title,
            reason=prioridade.reason,
            evidence=tuple(e for e in prioridade.evidence if not e.startswith("R$")),
            recommended_action=prioridade.recommended_action,
            cta=prioridade.cta,
            cta_url=prioridade.cta_url,
            priority=prioridade.priority,
            quantidade=prioridade.quantidade,
            valor=None,
        )
        concentracao = None

    mensagem = ""
    if not tem_atividade:
        mensagem = "Cadastre cobranças para acompanhar a saúde financeira."
    elif prioridade is None:
        mensagem = "Nenhuma prioridade financeira no momento."

    return PainelFinanceiroPro(
        disponivel=True,
        ver_cobrancas=True,
        ver_recebimentos=ver_recebimentos,
        health=health,
        health_label=_label_health(health),
        health_motivo=health_motivo,
        tendencia=tendencia,
        tendencia_label=_label_tendencia(tendencia),
        prioridade=prioridade,
        atencao=_lista_atencao(
            organization, hoje=hoje, ver_recebimentos=ver_recebimentos
        ),
        concentracao=concentracao,
        mensagem_vazia=mensagem,
        qtd_vencidas=inadimplencia.quantidade,
        dias_atraso_max=atraso_max,
    )
