"""
Home Executiva — orquestra snapshots e sinais dos módulos PRO.

Não recalcula Comercial/Financeiro/Marketing/Clientes/Agenda.
Capability filtra ANTES da priorização. Sem LLM/ML. Sem score geral.
Organization vem do TenantContext do caller — nunca do frontend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from usuarios.br_format import format_currency_br

logger = logging.getLogger(__name__)

DOMAIN_MARKETING = "marketing"
DOMAIN_COMERCIAL = "comercial"
DOMAIN_CLIENTES = "clientes"
DOMAIN_AGENDA = "agenda"
DOMAIN_FINANCEIRO = "financeiro"

SEV_CRITICA = "critica"
SEV_ALTA = "alta"
SEV_ATENCAO = "atencao"
SEV_INFO = "info"

_SEV_RANK = {SEV_CRITICA: 4, SEV_ALTA: 3, SEV_ATENCAO: 2, SEV_INFO: 1}
_DOMAIN_ORDER = (
    DOMAIN_AGENDA,
    DOMAIN_FINANCEIRO,
    DOMAIN_CLIENTES,
    DOMAIN_COMERCIAL,
    DOMAIN_MARKETING,
)
_MESES = (
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
MAX_SECUNDARIOS = 3
CRITERIOS_TIEBREAK = (
    "Precedência: severity normalizada → urgência temporal → CTA executável "
    "→ ordem estável de domínio (Agenda, Financeiro, Clientes, Comercial, Marketing) "
    "→ título. Sem pesos percentuais, sem LLM/ML, sem ranking só por R$."
)

_SECURITY = (PermissionDenied, SuspiciousOperation, Http404)
_ZERO = Decimal("0")


@dataclass(frozen=True)
class CapsExecutivas:
    marketing: bool = False
    comercial: bool = False
    comercial_receita: bool = False
    clientes: bool = True
    agenda: bool = False
    financeiro: bool = False
    financeiro_recebimentos: bool = False
    marketing_financeiro: bool = False


@dataclass(frozen=True)
class ExecutiveSignal:
    domain: str
    signal_type: str
    severity: str
    title: str
    evidence: str
    business_context: str
    recommended_action: str
    cta_label: str
    cta_url: str
    capability_required: str
    observed_at: str
    period_start: date | None
    period_end: date | None
    organization_id: int
    urgency: int = 1
    eligible: bool = True


@dataclass(frozen=True)
class SnapshotCard:
    domain: str
    title: str
    period_label: str
    period_start: date | None
    period_end: date | None
    primary_label: str
    primary_value: str
    secondary_label: str
    secondary_value: str
    hint: str


@dataclass(frozen=True)
class PulseItem:
    domain: str
    label: str
    estado: str
    detalhe: str


@dataclass(frozen=True)
class TrajectoryStep:
    key: str
    label: str
    value: str | None
    disponivel: bool
    nota: str


@dataclass(frozen=True)
class AdvScoreResumo:
    score: int
    classificacao: str
    criterios: str


@dataclass(frozen=True)
class HomeExecutiva:
    organization_nome: str
    organization_id: int
    period_label: str
    period_start: date
    period_end: date
    snapshot: tuple[SnapshotCard, ...]
    advisor: ExecutiveSignal | None
    advisor_status: str
    secundarios: tuple[ExecutiveSignal, ...]
    pulse: tuple[PulseItem, ...]
    trajectory: tuple[TrajectoryStep, ...]
    adv_score: AdvScoreResumo | None
    criterios: str
    tenant_ok: bool
    mensagem: str = ""


def resolver_caps_executivas(user) -> CapsExecutivas:
    from comercial.permissions import pode_ver_dashboard, pode_ver_receita
    from financeiro.permissions import pode_ver_cobrancas, pode_ver_recebimentos
    from marketing.permissions import (
        pode_ver_marketing,
        pode_ver_metricas_financeiras_marketing,
    )
    from usuarios.permissions import pode_ver_agenda

    fin = pode_ver_cobrancas(user)
    return CapsExecutivas(
        marketing=pode_ver_marketing(user),
        comercial=pode_ver_dashboard(user),
        comercial_receita=pode_ver_receita(user),
        clientes=True,
        agenda=pode_ver_agenda(user),
        financeiro=fin,
        financeiro_recebimentos=pode_ver_recebimentos(user),
        marketing_financeiro=pode_ver_metricas_financeiras_marketing(user) and fin,
    )


def _norm_sev(raw: str) -> str:
    r = (raw or "").strip().lower()
    if r in {"critica", "crítico", "critico", "urgente"}:
        return SEV_CRITICA
    if r == "alta":
        return SEV_ALTA
    if r in {"atencao", "atenção", "media", "média"}:
        return SEV_ATENCAO
    return SEV_INFO


def _mes_label(d: date) -> str:
    return f"{_MESES[d.month - 1]} {d.year}"


def _money(valor) -> str:
    if valor is None:
        return ""
    return format_currency_br(valor)


def _fmt_money_or_na(valor) -> str:
    if valor is None:
        return "Não disponível"
    return format_currency_br(valor)


def _pulse_estado(status: str, *, tem_dado: bool) -> str:
    if not tem_dado:
        return "Dados insuficientes"
    s = (status or "").strip().lower()
    if s in {"vazio", "dados_insuficientes"} or "insuficient" in s:
        return "Dados insuficientes"
    if s in {"critico", "atencao", "urgente", "alta"}:
        return "Atenção"
    if s in {"saudavel", "estavel", "estável"}:
        return "Estável"
    return "Dados insuficientes"


def priorizar_sinais(
    signals: list[ExecutiveSignal],
) -> tuple[ExecutiveSignal | None, tuple[ExecutiveSignal, ...]]:
    """Engine determinística. Não usa dinheiro como critério primário."""
    elegiveis = [s for s in signals if s.eligible and s.severity != SEV_INFO]
    if not elegiveis:
        return None, ()

    def chave(s: ExecutiveSignal):
        return (
            -_SEV_RANK.get(s.severity, 0),
            -int(s.urgency or 0),
            0 if s.cta_url else 1,
            _DOMAIN_ORDER.index(s.domain) if s.domain in _DOMAIN_ORDER else 99,
            s.title,
        )

    ordenados = sorted(elegiveis, key=chave)
    principal = ordenados[0]
    secundarios = tuple(
        s for s in ordenados[1:] if s.signal_type != principal.signal_type
    )[:MAX_SECUNDARIOS]
    return principal, secundarios


def _signal(
    *,
    organization_id: int,
    domain: str,
    signal_type: str,
    severity: str,
    title: str,
    evidence: str,
    action: str,
    cta_label: str,
    cta_url: str,
    period_start: date | None,
    period_end: date | None,
    observed_at: str,
    context: str = "",
    urgency: int = 1,
    eligible: bool = True,
) -> ExecutiveSignal:
    return ExecutiveSignal(
        domain=domain,
        signal_type=signal_type,
        severity=_norm_sev(severity),
        title=title,
        evidence=evidence,
        business_context=context,
        recommended_action=action,
        cta_label=cta_label,
        cta_url=cta_url,
        capability_required=domain,
        observed_at=observed_at,
        period_start=period_start,
        period_end=period_end,
        organization_id=organization_id,
        urgency=urgency,
        eligible=eligible,
    )


def _adapter_financeiro(organization, caps: CapsExecutivas, hoje: date, observed: str):
    if not caps.financeiro:
        return None, None, [], None

    from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization
    from financeiro.services.financeiro_pro import montar_financeiro_pro

    kpis = calcular_kpis_cobrancas_organization(organization, hoje=hoje)
    painel = montar_financeiro_pro(
        organization,
        hoje=hoje,
        ver_cobrancas=True,
        ver_recebimentos=caps.financeiro_recebimentos,
    )
    ini, fim = hoje.replace(day=1), hoje
    signals: list[ExecutiveSignal] = []
    sem_dado_fin = painel.health_label == "Dados insuficientes"
    if sem_dado_fin:
        recebido_txt = "Não disponível"
        aberto_txt = "Não disponível"
    else:
        recebido_txt = (
            _fmt_money_or_na(kpis.recebido_mes)
            if caps.financeiro_recebimentos
            else "Sem permissão"
        )
        aberto_txt = f"{_fmt_money_or_na(kpis.a_receber)} · {_fmt_money_or_na(kpis.vencido)} vencido"
    snapshot = SnapshotCard(
        domain=DOMAIN_FINANCEIRO,
        title="Financeiro",
        period_label=f"Mês atual · {_mes_label(hoje)}",
        period_start=ini,
        period_end=fim,
        primary_label="Recebido no mês",
        primary_value=recebido_txt,
        secondary_label="A receber · vencido",
        secondary_value=aberto_txt,
        hint="Recebido = dinheiro efetivo. Vencido ≠ perda automática. Ausência de cobrança ≠ zero.",
    )
    pulse = PulseItem(
        DOMAIN_FINANCEIRO,
        "Financeiro",
        painel.health_label,
        painel.health_motivo,
    )
    prio = painel.prioridade
    if prio:
        ctx = ""
        if caps.financeiro_recebimentos and prio.valor is not None:
            ctx = f"Saldo observado: {_money(prio.valor)} (não é receita garantida além do já recebido)."
        signals.append(
            _signal(
                organization_id=organization.pk,
                domain=DOMAIN_FINANCEIRO,
                signal_type=prio.type or "financeiro",
                severity=prio.severity,
                title=prio.title,
                evidence=" ".join(prio.evidence) or prio.reason,
                action=prio.recommended_action,
                cta_label=prio.cta or "Ver cobranças",
                cta_url=prio.cta_url or reverse("financeiro_cobranca_listar"),
                period_start=ini,
                period_end=fim,
                observed_at=observed,
                context=ctx,
                urgency=3 if (kpis.vencido or 0) > 0 else 2,
            )
        )
    recebido_traj = None
    if caps.financeiro_recebimentos and not sem_dado_fin:
        recebido_traj = kpis.recebido_mes
    return snapshot, pulse, signals, recebido_traj


def _adapter_comercial(organization, user, caps: CapsExecutivas, hoje: date, observed: str):
    if not caps.comercial:
        return None, None, [], None

    from comercial.services.advisor import montar_advisor
    from comercial.services.goals import meta_do_ano
    from comercial.services.metrics import calcular_kpis, periodo_mes
    from comercial.services.money import ZERO

    meta = meta_do_ano(user, hoje.year, organization=organization)
    ocultar = not caps.comercial_receita
    kpis = calcular_kpis(
        organization,
        meta=meta,
        receita_risco=ZERO,
        ocultar_financeiro=ocultar,
        projecao_anual=None,
    )
    periodo = periodo_mes(hoje)
    signals: list[ExecutiveSignal] = []
    advisor = montar_advisor(
        user,
        organization=organization,
        ocultar_financeiro=ocultar or not caps.financeiro,
    )
    candidatos = []
    if advisor.principal:
        candidatos.append(advisor.principal)
    candidatos.extend(advisor.secundarias)
    if not caps.financeiro:
        candidatos = [p for p in candidatos if p.key != "cobrancas_vencidas"]
    escolhido = candidatos[0] if candidatos else None
    if ocultar:
        snapshot = SnapshotCard(
            domain=DOMAIN_COMERCIAL,
            title="Comercial",
            period_label=f"Mês atual · {_mes_label(hoje)}",
            period_start=periodo.data_inicio,
            period_end=periodo.data_fim,
            primary_label="Realizado Comercial",
            primary_value="Valores ocultos",
            secondary_label="Gap da meta mensal",
            secondary_value="Sem permissão de receita",
            hint="Realizado Comercial = contratado/fechado. Não é recebimento.",
        )
    else:
        gap = kpis.gap_meta_mensal
        snapshot = SnapshotCard(
            domain=DOMAIN_COMERCIAL,
            title="Comercial",
            period_label=f"Mês atual · {_mes_label(hoje)}",
            period_start=periodo.data_inicio,
            period_end=periodo.data_fim,
            primary_label="Realizado Comercial",
            primary_value=_fmt_money_or_na(kpis.realizado_mes),
            secondary_label="Gap da meta mensal",
            secondary_value=_fmt_money_or_na(gap) if kpis.tem_meta else "Não configurada",
            hint="Realizado Comercial = valor contratado no período. Não é recebimento. Gap exige meta real. Não é projeção nem garantia.",
        )
    tem_dado_com = bool(escolhido) or ((kpis.contratado_mes or 0) > 0)
    if escolhido:
        pulse = PulseItem(
            DOMAIN_COMERCIAL,
            "Comercial",
            _pulse_estado(escolhido.prioridade, tem_dado=True),
            escolhido.titulo,
        )
        ctx = ""
        if escolhido.valor_potencial is not None and not ocultar:
            ctx = (
                f"Valor potencial observado: {_money(escolhido.valor_potencial)}. "
                "Potencial ≠ receita."
            )
        signals.append(
            _signal(
                organization_id=organization.pk,
                domain=DOMAIN_COMERCIAL,
                signal_type=escolhido.key,
                severity=escolhido.prioridade,
                title=escolhido.titulo,
                evidence=escolhido.motivo,
                action=escolhido.acao,
                cta_label=escolhido.cta_ver or "Ver Comercial",
                cta_url=escolhido.url or reverse("comercial_dashboard"),
                period_start=periodo.data_inicio,
                period_end=periodo.data_fim,
                observed_at=observed,
                context=ctx,
                urgency=2,
            )
        )
    else:
        pulse = PulseItem(
            DOMAIN_COMERCIAL,
            "Comercial",
            _pulse_estado("saudavel", tem_dado=tem_dado_com),
            advisor.mensagem or "Sem prioridade comercial no período.",
        )
    return snapshot, pulse, signals, kpis.contratado_mes if not ocultar else None


def _adapter_agenda(organization, user, caps: CapsExecutivas, hoje: date, observed: str):
    if not caps.agenda:
        return None, None, []

    from usuarios.services.agenda_pro import CapsAgendaPro, montar_agenda_pro

    painel = montar_agenda_pro(
        organization,
        user=user,
        ref=hoje,
        caps=CapsAgendaPro(
            ver_financeiro=caps.financeiro,
            ver_cliente=caps.clientes,
        ),
    )
    k = painel.kpis
    signals: list[ExecutiveSignal] = []
    snapshot = SnapshotCard(
        domain=DOMAIN_AGENDA,
        title="Agenda",
        period_label=f"Hoje · {hoje.strftime('%d/%m/%Y')}",
        period_start=hoje,
        period_end=hoje,
        primary_label="Críticos operacionais",
        primary_value=str(k.criticos),
        secondary_label="Atenção · vencidos",
        secondary_value=f"{k.atencao} atenção · {k.vencidos} vencidos",
        hint="Criticidade operacional ≠ conclusão jurídica. Vencido ≠ prazo perdido.",
    )
    adv = painel.advisor
    tem_dado_ag = bool(k.criticos or k.atencao or k.vencidos or k.proximos) or adv.status not in {
        "vazio",
        "dados_insuficientes",
    }
    estado = _pulse_estado(adv.status, tem_dado=tem_dado_ag)
    if adv.principal:
        pulse = PulseItem(DOMAIN_AGENDA, "Agenda", estado, adv.principal.titulo)
        urg = 3 if adv.principal.nivel == "critico" else 2
        signals.append(
            _signal(
                organization_id=organization.pk,
                domain=DOMAIN_AGENDA,
                signal_type=adv.principal.key,
                severity=adv.principal.nivel,
                title=adv.principal.titulo,
                evidence=adv.principal.evidence,
                action=adv.principal.acao,
                cta_label=adv.principal.cta or "Ver agenda",
                cta_url=adv.principal.url or reverse("agenda"),
                period_start=hoje,
                period_end=hoje,
                observed_at=observed,
                context=adv.principal.contexto,
                urgency=urg,
            )
        )
    else:
        pulse = PulseItem(
            DOMAIN_AGENDA,
            "Agenda",
            estado,
            "Sem prioridade operacional relevante agora.",
        )
    return snapshot, pulse, signals


def _adapter_clientes(organization, caps: CapsExecutivas, hoje: date, observed: str):
    if not caps.clientes:
        return None, None, []

    from usuarios.models import Cliente
    from usuarios.services.clientes_pro import CapsClientePro, sinais_listagem_clientes

    ids = list(
        Cliente.objects.filter(organization=organization).values_list("pk", flat=True)
    )
    pro_map = sinais_listagem_clientes(
        organization,
        ids,
        caps=CapsClientePro(
            agenda=caps.agenda,
            financeiro=caps.financeiro,
            recebimentos=caps.financeiro_recebimentos,
            documentos=False,
        ),
    )
    atencao = sum(1 for s in pro_map.values() if s.atencao)
    criticos = sum(1 for s in pro_map.values() if s.health == "critico")
    signals: list[ExecutiveSignal] = []
    snapshot = SnapshotCard(
        domain=DOMAIN_CLIENTES,
        title="Clientes",
        period_label="Relacionamentos atuais",
        period_start=hoje,
        period_end=hoje,
        primary_label="Exigem atenção",
        primary_value=str(atencao),
        secondary_label="Relationship Health crítico",
        secondary_value=str(criticos),
        hint="Relationship Health operacional do relacionamento. Não avalia a pessoa nem performance financeira.",
    )
    if not ids:
        pulse = PulseItem(
            DOMAIN_CLIENTES,
            "Clientes",
            "Dados insuficientes",
            "Sem clientes neste escritório.",
        )
    elif atencao:
        pulse = PulseItem(
            DOMAIN_CLIENTES,
            "Clientes",
            "Atenção",
            f"{atencao} relacionamento(s) exigem atenção.",
        )
        url = reverse("clientes") + "?atencao=1"
        signals.append(
            _signal(
                organization_id=organization.pk,
                domain=DOMAIN_CLIENTES,
                signal_type="relacionamentos_atencao",
                severity=SEV_CRITICA if criticos else SEV_ATENCAO,
                title="Relacionamentos exigem atenção",
                evidence=(
                    f"{atencao} cliente(s) com sinal de atenção"
                    + (f", {criticos} em health crítico." if criticos else ".")
                    + " Relationship Health ≠ performance financeira."
                ),
                action="Abrir a lista filtrada e tratar a próxima ação de cada relacionamento.",
                cta_label="Ver clientes em atenção",
                cta_url=url,
                period_start=hoje,
                period_end=hoje,
                observed_at=observed,
                urgency=2 if criticos else 1,
            )
        )
    else:
        pulse = PulseItem(
            DOMAIN_CLIENTES,
            "Clientes",
            "Estável",
            "Nenhum relacionamento com sinal de atenção na listagem.",
        )
    return snapshot, pulse, signals


def _adapter_marketing(organization, user, caps: CapsExecutivas, hoje: date, observed: str):
    if not caps.marketing:
        return None, None, [], None

    from marketing.services.marketing_pro import montar_marketing_pro
    from marketing.services.periodo import PeriodoMarketing

    ini = hoje.replace(day=1)
    periodo = PeriodoMarketing(data_inicio=ini, data_fim=hoje)
    ocultar = not caps.marketing_financeiro
    painel = montar_marketing_pro(
        user,
        periodo,
        organization=organization,
        ocultar_financeiro=ocultar,
    )
    cadeia = painel.cadeia
    snapshot = None
    signals: list[ExecutiveSignal] = []
    tem_dado = bool(cadeia.leads or cadeia.oportunidades or cadeia.contratos)
    if tem_dado:
        snapshot = SnapshotCard(
            domain=DOMAIN_MARKETING,
            title="Marketing",
            period_label=f"Mês atual · {_mes_label(hoje)}",
            period_start=ini,
            period_end=hoje,
            primary_label="Leads atribuídos",
            primary_value=str(cadeia.leads),
            secondary_label="Oportunidades",
            secondary_value=str(cadeia.oportunidades),
            hint="Atribuição ≠ causalidade. Volume de leads sozinho não define prioridade.",
        )
    diag = painel.diagnostico
    pulse = PulseItem(
        DOMAIN_MARKETING,
        "Marketing",
        _pulse_estado(painel.advisor.status, tem_dado=tem_dado),
        diag.detalhe if diag else "",
    )
    prio = painel.advisor.principal
    if prio and prio.key not in {"dados_insuficientes", "tracking_insuficiente"}:
        signals.append(
            _signal(
                organization_id=organization.pk,
                domain=DOMAIN_MARKETING,
                signal_type=prio.key,
                severity=prio.prioridade,
                title=prio.titulo,
                evidence=prio.evidence,
                action=prio.acao,
                cta_label=prio.cta or "Ver Marketing",
                cta_url=prio.url or reverse("marketing_dashboard"),
                period_start=ini,
                period_end=hoje,
                observed_at=observed,
                context=prio.impacto or "",
                urgency=1,
            )
        )
    return snapshot, pulse, signals, cadeia


def _trajectory(caps: CapsExecutivas, cadeia, contratado, recebido) -> tuple[TrajectoryStep, ...]:
    def passo(key, label, valor, nota_ok, money=False):
        if valor is None:
            return TrajectoryStep(key, label, None, False, "Não disponível")
        texto = _money(valor) if money else str(valor)
        return TrajectoryStep(key, label, texto, True, nota_ok)

    leads = cadeia.leads if cadeia is not None else None
    opes = cadeia.oportunidades if cadeia is not None else None
    contratos_mkt = cadeia.contratos if cadeia is not None else None
    rec = recebido
    passos: list[TrajectoryStep] = []
    if caps.marketing:
        passos.append(
            passo(
                "marketing",
                "Marketing / leads",
                leads,
                "Origem registrada no período. Atribuição ≠ causalidade.",
            )
        )
        passos.append(passo("oportunidades", "Oportunidades", opes, "Lead ≠ oportunidade."))
        passos.append(
            passo("contratos", "Contratos", contratos_mkt, "Contrato ≠ recebimento.")
        )
    elif caps.comercial:
        passos.append(
            passo(
                "contratos",
                "Contratos",
                contratado if caps.comercial_receita else None,
                "Contrato ≠ recebimento. Valor contratado, não contagem.",
                money=True,
            )
        )
    if caps.financeiro_recebimentos:
        passos.append(
            passo(
                "recebimentos",
                "Recebimentos",
                rec,
                "Somente dinheiro efetivamente recebido.",
                money=True,
            )
        )
    return tuple(passos)


def _run_adapter(nome: str, fn):
    try:
        return fn()
    except _SECURITY:
        raise
    except Exception:
        logger.exception("Home executiva: falha no domínio %s", nome)
        return None


def montar_home_executiva(
    organization,
    *,
    user,
    caps: CapsExecutivas | None = None,
    hoje: date | None = None,
) -> HomeExecutiva:
    if organization is None:
        return HomeExecutiva(
            organization_nome="",
            organization_id=0,
            period_label="",
            period_start=timezone.localdate(),
            period_end=timezone.localdate(),
            snapshot=(),
            advisor=None,
            advisor_status="vazio",
            secundarios=(),
            pulse=(),
            trajectory=(),
            adv_score=None,
            criterios=CRITERIOS_TIEBREAK,
            tenant_ok=False,
            mensagem="Não foi possível determinar o escritório ativo.",
        )

    hoje = hoje or timezone.localdate()
    caps = caps or resolver_caps_executivas(user)
    observed = timezone.now().isoformat()
    ini_mes = hoje.replace(day=1)

    snapshots: list[SnapshotCard] = []
    pulses: list[PulseItem] = []
    signals: list[ExecutiveSignal] = []
    cadeia = None
    contratado = None
    recebido = None

    fin = _run_adapter(
        "financeiro",
        lambda: _adapter_financeiro(organization, caps, hoje, observed),
    )
    if fin:
        snap, pulse, sigs, recebido = fin
        if snap:
            snapshots.append(snap)
        if pulse:
            pulses.append(pulse)
        signals.extend(sigs)

    com = _run_adapter(
        "comercial",
        lambda: _adapter_comercial(organization, user, caps, hoje, observed),
    )
    if com:
        snap, pulse, sigs, contratado = com
        if snap:
            snapshots.append(snap)
        if pulse:
            pulses.append(pulse)
        if not caps.financeiro or any(s.domain == DOMAIN_FINANCEIRO for s in signals):
            sigs = [s for s in sigs if s.signal_type != "cobrancas_vencidas"]
        signals.extend(sigs)

    cli = _run_adapter(
        "clientes",
        lambda: _adapter_clientes(organization, caps, hoje, observed),
    )
    if cli:
        snap, pulse, sigs = cli
        if snap:
            snapshots.append(snap)
        if pulse:
            pulses.append(pulse)
        signals.extend(sigs)

    age = _run_adapter(
        "agenda",
        lambda: _adapter_agenda(organization, user, caps, hoje, observed),
    )
    if age:
        snap, pulse, sigs = age
        if snap:
            snapshots.append(snap)
        if pulse:
            pulses.append(pulse)
        signals.extend(sigs)

    mkt = _run_adapter(
        "marketing",
        lambda: _adapter_marketing(organization, user, caps, hoje, observed),
    )
    if mkt:
        snap, pulse, sigs, cadeia = mkt
        if snap and len(snapshots) < 5:
            snapshots.append(snap)
        if pulse:
            pulses.append(pulse)
        signals.extend(sigs)

    ordem_snap = {
        DOMAIN_COMERCIAL: 0,
        DOMAIN_FINANCEIRO: 1,
        DOMAIN_CLIENTES: 2,
        DOMAIN_AGENDA: 3,
        DOMAIN_MARKETING: 4,
    }
    snapshots.sort(key=lambda c: ordem_snap.get(c.domain, 9))
    snapshots = snapshots[:5]

    # Capability já aplicada nos adapters: signals só dos domínios autorizados.
    principal, secundarios = priorizar_sinais(signals)
    status = "vazio"
    if principal:
        status = principal.severity
    elif signals:
        status = "saudavel"

    return HomeExecutiva(
        organization_nome=organization.name,
        organization_id=organization.pk,
        period_label=f"Mês atual · {_mes_label(hoje)}",
        period_start=ini_mes,
        period_end=hoje,
        snapshot=tuple(snapshots),
        advisor=principal,
        advisor_status=status,
        secundarios=secundarios,
        pulse=tuple(pulses),
        trajectory=_trajectory(caps, cadeia, contratado, recebido),
        adv_score=None,
        criterios=CRITERIOS_TIEBREAK,
        tenant_ok=True,
    )
