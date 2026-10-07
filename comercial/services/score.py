"""
ADV Growth Score 0–100 — critérios objetivos documentados (sem IA).

Pesos (soma 100):
  Aquisição 15 | Atendimento 15 | Conversão 25 | Gestão 15 | Financeiro 20 | Dados 10

Dimensões (cada uma 0–100):
  Aquisição  — volume de leads nos últimos 30d vs. 30d anteriores (crescimento)
  Atendimento — % consultas realizadas / agendadas no período
  Conversão  — taxa lead → contrato (escala: 0%→0, ≥20%→100)
  Gestão     — % leads do período com próxima ação (compromisso futuro ou tarefa)
  Financeiro — % da meta mensal atingida (recebido); se sem meta: ritmo de recebimentos
  Dados      — % clientes do período com origem preenchida
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from comercial.models import AdvGrowthScoreSnapshot
from comercial.services.funnel import calcular_funil_receita
from comercial.services.goals import meta_do_ano
from comercial.services.metrics import periodo_mes, receita_no_periodo
from comercial.services.money import money, safe_pct
from comercial.services.periodo import PeriodoComercial
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso


PESOS = {
    "aquisicao": 15,
    "atendimento": 15,
    "conversao": 25,
    "gestao": 15,
    "financeiro": 20,
    "dados": 10,
}


@dataclass(frozen=True)
class DimensaoScore:
    codigo: str
    label: str
    pontos: int
    criterio: str


@dataclass(frozen=True)
class AdvGrowthScore:
    score: int
    dimensoes: tuple[DimensaoScore, ...]
    delta_30: int | None
    delta_60: int | None
    delta_90: int | None
    historico_labels: tuple[str, ...]
    historico_scores: tuple[int, ...]
    criterios_doc: str


def _clamp(n: int) -> int:
    return max(0, min(100, int(n)))


def _score_aquisicao(user, periodo: PeriodoComercial) -> tuple[int, str]:
    atual = Cliente.objects.filter(user=user)
    from comercial.services.periodo import filtro_datetime_campo

    atual_n = filtro_datetime_campo(
        atual, "criado_em", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    ).count()
    ant = periodo.periodo_anterior()
    ant_n = filtro_datetime_campo(
        Cliente.objects.filter(user=user),
        "criado_em",
        data_inicio=ant.data_inicio,
        data_fim=ant.data_fim,
    ).count()
    if atual_n == 0 and ant_n == 0:
        return 40, "Sem leads recentes — pontuação neutra."
    if ant_n == 0:
        return _clamp(50 + min(atual_n * 5, 50)), f"{atual_n} lead(s) no período (sem base anterior)."
    var = (atual_n - ant_n) / ant_n * 100
    # -50% → 20, 0% → 55, +50% → 90, +100% → 100
    pts = _clamp(int(55 + var * 0.7))
    return pts, f"Leads {atual_n} vs {ant_n} no período anterior ({var:+.0f}%)."


def _score_atendimento(user, periodo: PeriodoComercial) -> tuple[int, str]:
    from comercial.services.periodo import filtro_datetime_campo

    qs = Compromisso.objects.filter(
        user=user, tipo=TipoCompromisso.CONSULTA
    ).exclude(status=StatusCompromisso.CANCELADO)
    qs = filtro_datetime_campo(
        qs, "data_hora", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )
    agendadas = qs.count()
    realizadas = qs.filter(status=StatusCompromisso.REALIZADO).count()
    if agendadas == 0:
        return 35, "Sem consultas no período."
    pct = float(safe_pct(realizadas, agendadas) or 0)
    return _clamp(int(pct)), f"{realizadas}/{agendadas} consultas realizadas ({pct:.0f}%)."


def _score_conversao(funil) -> tuple[int, str]:
    if funil.leads < 3:
        return 30, "Amostra insuficiente de leads para conversão."
    taxa = float(funil.taxa_lead_contrato or 0)
    # 0%→0, 10%→50, 20%→100
    pts = _clamp(int(taxa * 5))
    return pts, f"Taxa lead→contrato: {taxa:.1f}%."


def _score_gestao(user, periodo: PeriodoComercial) -> tuple[int, str]:
    from comercial.services.periodo import filtro_datetime_campo
    from django.utils import timezone as tz
    from usuarios.choices import StatusTarefa
    from usuarios.models import Tarefa

    leads = filtro_datetime_campo(
        Cliente.objects.filter(user=user),
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    total = leads.count()
    if total == 0:
        return 40, "Sem leads no período."
    agora = tz.now()
    hoje = tz.localdate()
    ids = leads.values("pk")
    com_comp = set(
        Compromisso.objects.filter(
            user=user, cliente_id__in=ids, data_hora__gte=agora
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
    )
    com_tarefa = set(
        Tarefa.objects.filter(
            user=user,
            cliente_id__in=ids,
            status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
            prazo__gte=hoje,
        ).values_list("cliente_id", flat=True)
    )
    com_acao = len(set(leads.values_list("pk", flat=True)) & (com_comp | com_tarefa))
    pct = float(safe_pct(com_acao, total) or 0)
    return _clamp(int(pct)), f"{com_acao}/{total} leads com próxima ação ({pct:.0f}%)."


def _score_financeiro(user, organization) -> tuple[int, str]:
    meta = meta_do_ano(user, organization=organization)
    mes = receita_no_periodo(organization, periodo_mes())
    if meta and meta.meta_mensal > 0:
        pct = float(money(100 * mes.recebido / Decimal(meta.meta_mensal)))
        return _clamp(int(pct)), f"Meta mensal: {pct:.0f}% atingida (recebido)."
    if mes.recebido > 0:
        return 55, "Há recebimentos no mês, mas sem meta cadastrada."
    return 25, "Sem recebimentos no mês e/ou sem meta."


def _score_dados(user, periodo: PeriodoComercial) -> tuple[int, str]:
    from comercial.services.periodo import filtro_datetime_campo

    qs = filtro_datetime_campo(
        Cliente.objects.filter(user=user),
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    total = qs.count()
    if total == 0:
        return 40, "Sem clientes novos no período."
    com_origem = qs.exclude(origem="").count()
    pct = float(safe_pct(com_origem, total) or 0)
    return _clamp(int(pct)), f"{com_origem}/{total} com origem preenchida ({pct:.0f}%)."


def calcular_adv_growth_score(user, *, organization=None, persistir: bool = True) -> AdvGrowthScore:
    periodo = PeriodoComercial.ultimos_dias(30)
    funil = calcular_funil_receita(user, periodo, organization=organization)

    aq, aq_c = _score_aquisicao(user, periodo)
    at, at_c = _score_atendimento(user, periodo)
    cv, cv_c = _score_conversao(funil)
    ge, ge_c = _score_gestao(user, periodo)
    fi, fi_c = _score_financeiro(user, organization)
    da, da_c = _score_dados(user, periodo)

    dimensoes = (
        DimensaoScore("aquisicao", "Aquisição", aq, aq_c),
        DimensaoScore("atendimento", "Atendimento", at, at_c),
        DimensaoScore("conversao", "Conversão", cv, cv_c),
        DimensaoScore("gestao", "Gestão", ge, ge_c),
        DimensaoScore("financeiro", "Financeiro", fi, fi_c),
        DimensaoScore("dados", "Dados", da, da_c),
    )
    score = _clamp(
        round(
            sum(d.pontos * PESOS[d.codigo] for d in dimensoes) / 100
        )
    )

    hoje = timezone.localdate()
    if persistir:
        AdvGrowthScoreSnapshot.objects.update_or_create(
            usuario=user,
            data_ref=hoje,
            defaults={
                "score": score,
                "aquisicao": aq,
                "atendimento": at,
                "conversao": cv,
                "gestao": ge,
                "financeiro": fi,
                "dados": da,
                "detalhe": {d.codigo: {"pontos": d.pontos, "criterio": d.criterio} for d in dimensoes},
            },
        )

    snaps = list(
        AdvGrowthScoreSnapshot.objects.filter(usuario=user)
        .order_by("data_ref")
        .values_list("data_ref", "score")[:90]
    )
    # garantir snapshot de hoje na série mesmo se query antes do save
    if snaps and snaps[-1][0] != hoje:
        snaps.append((hoje, score))
    elif not snaps:
        snaps = [(hoje, score)]

    def _delta(dias: int) -> int | None:
        alvo = hoje - timedelta(days=dias)
        antigos = [s for s in snaps if s[0] <= alvo]
        if not antigos:
            return None
        return score - antigos[-1][1]

    return AdvGrowthScore(
        score=score,
        dimensoes=dimensoes,
        delta_30=_delta(30),
        delta_60=_delta(60),
        delta_90=_delta(90),
        historico_labels=tuple(d.strftime("%d/%m") for d, _ in snaps[-30:]),
        historico_scores=tuple(s for _, s in snaps[-30:]),
        criterios_doc=(
            "Pesos: Aquisição 15, Atendimento 15, Conversão 25, "
            "Gestão 15, Financeiro 20, Dados 10. Sem uso de IA."
        ),
    )
