"""Integração de cobranças com a Agenda (tarefas, compromissos e lembretes).

Tenant da Agenda: Cobranca.organization.
cobranca.usuario NÃO é tenant.
Job global: discovery em todas as cobranças; side effect só com Organization válida.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import AcaoCobrancaHistorico, StatusCobranca
from financeiro.services.cobrancas import saldo_cobranca
from financeiro.services.historico_cobranca import registrar_historico_cobranca
from usuarios.choices import (
    LembreteMinutos,
    Prioridade,
    StatusCompromisso,
    StatusTarefa,
    TipoCompromisso,
)
from usuarios.models import Compromisso, Tarefa
from usuarios.services.agenda_equipe import membership_ativa
from usuarios.services.compromisso_lembrete import (
    cancelar_lembrete_compromisso,
    sincronizar_lembrete_compromisso,
)

logger = logging.getLogger(__name__)

META_COBRANCA_ID = "cobranca_id"
META_LEMBRETE_AUTO = "lembrete_cobranca_automatico"
META_ORIGEM = "origem_financeiro"
MSG_ORG_COBRANCA = "Não foi possível determinar o escritório da cobrança para a agenda."
REASON_MISSING_ORGANIZATION = "MISSING_ORGANIZATION"
REASON_ORGANIZATION_CONFLICT = "ORGANIZATION_CONFLICT"


def _organization_cobranca(cobranca):
    return getattr(cobranca, "organization", None)


def _assert_organization_cobranca(cobranca):
    organization = _organization_cobranca(cobranca)
    if organization is None:
        raise ValidationError(MSG_ORG_COBRANCA)
    return organization


def _responsavel_na_org(cobranca, autor, organization):
    candidatos = [cobranca.responsavel, autor]
    for cand in candidatos:
        if cand is not None and membership_ativa(cand, organization):
            return cand
    return None


def _cobranca_ativa_para_lembrete(cobranca) -> bool:
    if cobranca.status in (StatusCobranca.CANCELED, StatusCobranca.DRAFT):
        return False
    return saldo_cobranca(cobranca) > 0


def _titulo_cobranca(cobranca) -> str:
    rotulo = cobranca.parcela_rotulo
    base = cobranca.descricao
    if rotulo:
        return f"Cobrança: {base} ({rotulo})"
    return f"Cobrança: {base}"


def _log_skip_cobranca(cobranca, reason: str) -> None:
    logger.info("skip model=Cobranca pk=%s reason=%s", cobranca.pk, reason)


def _compromissos_auto_globais(cobranca):
    return Compromisso.objects.filter(
        metadados__cobranca_id=cobranca.pk,
        metadados__lembrete_cobranca_automatico=True,
    ).exclude(status=StatusCompromisso.CANCELADO)


def compromisso_lembrete_automatico(cobranca):
    organization = _organization_cobranca(cobranca)
    if organization is None:
        return None
    return (
        _compromissos_auto_globais(cobranca)
        .filter(organization=organization)
        .first()
    )


def tarefas_vinculadas_cobranca(cobranca):
    organization = _organization_cobranca(cobranca)
    if organization is None:
        return Tarefa.objects.none()
    return (
        Tarefa.objects.filter(
            organization=organization,
            metadados__cobranca_id=cobranca.pk,
        )
        .exclude(status=StatusTarefa.CANCELADA)
        .select_related("cliente", "responsavel")
        .order_by("prazo", "id")
    )


def compromissos_vinculados_cobranca(cobranca):
    organization = _organization_cobranca(cobranca)
    if organization is None:
        return Compromisso.objects.none()
    return (
        Compromisso.objects.filter(
            organization=organization,
            metadados__cobranca_id=cobranca.pk,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .exclude(metadados__lembrete_cobranca_automatico=True)
        .select_related("cliente", "responsavel")
        .order_by("data_hora")
    )


def criar_tarefa_cobranca(cobranca, *, autor) -> Tarefa:
    if not _cobranca_ativa_para_lembrete(cobranca):
        raise ValidationError("Esta cobrança não aceita tarefa na agenda.")
    organization = _assert_organization_cobranca(cobranca)
    saldo = saldo_cobranca(cobranca)
    hoje = timezone.localdate()
    prazo = cobranca.data_vencimento
    if prazo < hoje:
        prazo = hoje
    prioridade = Prioridade.URGENTE if cobranca.status == StatusCobranca.OVERDUE else Prioridade.ALTA
    tarefa = Tarefa.objects.create(
        user=autor,
        organization=organization,
        titulo=f"Follow-up: {_titulo_cobranca(cobranca)}",
        descricao=(
            f"Cliente: {cobranca.cliente.nome}. "
            f"Saldo em aberto: R$ {saldo:.2f}. "
            f"Vencimento: {cobranca.data_vencimento:%d/%m/%Y}."
        ),
        prazo=prazo,
        cliente=cobranca.cliente,
        responsavel=_responsavel_na_org(cobranca, autor, organization),
        prioridade=prioridade,
        metadados={
            META_COBRANCA_ID: cobranca.pk,
            META_ORIGEM: True,
        },
    )
    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.AGENDA_VINCULADA,
        descricao="Tarefa de follow-up criada na agenda.",
        autor=autor,
        metadados={"tarefa_id": tarefa.pk, "tipo": "tarefa"},
    )
    return tarefa


def criar_compromisso_cobranca(
    cobranca,
    *,
    autor,
    lembrete_minutos: int = LembreteMinutos.DIA_1,
) -> Compromisso:
    if not _cobranca_ativa_para_lembrete(cobranca):
        raise ValidationError("Esta cobrança não aceita compromisso na agenda.")
    organization = _assert_organization_cobranca(cobranca)
    saldo = saldo_cobranca(cobranca)
    dt = timezone.make_aware(
        datetime.combine(cobranca.data_vencimento, time(hour=9, minute=0))
    )
    compromisso = Compromisso.objects.create(
        user=autor,
        organization=organization,
        titulo=_titulo_cobranca(cobranca),
        descricao=(
            f"Retorno de cobrança — {cobranca.cliente.nome}. "
            f"Saldo: R$ {saldo:.2f}."
        ),
        tipo=TipoCompromisso.COBRANCA,
        data_hora=dt,
        cliente=cobranca.cliente,
        responsavel=_responsavel_na_org(cobranca, autor, organization),
        prioridade=Prioridade.ALTA,
        lembrete_minutos=lembrete_minutos,
        metadados={
            META_COBRANCA_ID: cobranca.pk,
            META_ORIGEM: True,
        },
    )
    sincronizar_lembrete_compromisso(compromisso)
    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.AGENDA_VINCULADA,
        descricao="Compromisso de cobrança criado na agenda.",
        autor=autor,
        metadados={"compromisso_id": compromisso.pk, "tipo": "compromisso"},
    )
    return compromisso


def cancelar_lembrete_cobranca(cobranca) -> None:
    compromisso = compromisso_lembrete_automatico(cobranca)
    if compromisso:
        cancelar_lembrete_compromisso(compromisso)
        compromisso.cancelar(motivo="Cobrança quitada ou cancelada.")


def sincronizar_lembrete_cobranca(cobranca) -> None:
    """Compromisso automático no vencimento + lembrete 1 dia antes (django-q)."""
    if not _cobranca_ativa_para_lembrete(cobranca):
        cancelar_lembrete_cobranca(cobranca)
        return

    organization = _organization_cobranca(cobranca)
    if organization is None:
        _log_skip_cobranca(cobranca, REASON_MISSING_ORGANIZATION)
        return

    existentes = list(_compromissos_auto_globais(cobranca))
    if any(item.organization_id != organization.pk for item in existentes):
        _log_skip_cobranca(cobranca, REASON_ORGANIZATION_CONFLICT)
        return

    saldo = saldo_cobranca(cobranca)
    dt = timezone.make_aware(
        datetime.combine(cobranca.data_vencimento, time(hour=9, minute=0))
    )
    compromisso = existentes[0] if existentes else None
    if compromisso:
        compromisso.titulo = _titulo_cobranca(cobranca)
        compromisso.descricao = (
            f"Lembrete automático — {cobranca.cliente.nome}. Saldo: R$ {saldo:.2f}."
        )
        compromisso.data_hora = dt
        compromisso.cliente = cobranca.cliente
        compromisso.lembrete_minutos = LembreteMinutos.DIA_1
        compromisso.save(
            update_fields=[
                "titulo",
                "descricao",
                "data_hora",
                "cliente",
                "lembrete_minutos",
                "atualizado_em",
            ]
        )
    else:
        compromisso = Compromisso.objects.create(
            user=cobranca.usuario,
            organization=organization,
            titulo=_titulo_cobranca(cobranca),
            descricao=(
                f"Lembrete automático — {cobranca.cliente.nome}. Saldo: R$ {saldo:.2f}."
            ),
            tipo=TipoCompromisso.COBRANCA,
            data_hora=dt,
            cliente=cobranca.cliente,
            responsavel=_responsavel_na_org(cobranca, cobranca.usuario, organization),
            prioridade=Prioridade.NORMAL,
            lembrete_minutos=LembreteMinutos.DIA_1,
            metadados={
                META_COBRANCA_ID: cobranca.pk,
                META_LEMBRETE_AUTO: True,
                META_ORIGEM: True,
            },
        )
    sincronizar_lembrete_compromisso(compromisso)


def cobrancas_itens_atencao(organization, ref=None, limit: int = 4):
    from usuarios.services.agenda import ItemAtencao

    from financeiro.models import Cobranca

    if organization is None:
        return []
    ref = ref or timezone.localdate()
    resultado = []
    qs = (
        Cobranca.objects.filter(organization=organization)
        .exclude(status__in=(StatusCobranca.CANCELED, StatusCobranca.DRAFT, StatusCobranca.PAID))
        .select_related("cliente")
        .order_by("data_vencimento", "id")
    )
    for cobranca in qs:
        saldo = saldo_cobranca(cobranca)
        if saldo <= 0:
            continue
        if cobranca.status == StatusCobranca.OVERDUE:
            dias = (ref - cobranca.data_vencimento).days
            motivo = f"Vencida há {dias} dia{'s' if dias != 1 else ''}"
            prioridade = Prioridade.URGENTE
        elif cobranca.status == StatusCobranca.DUE_SOON:
            dias = (cobranca.data_vencimento - ref).days
            motivo = f"Vence em {dias} dia{'s' if dias != 1 else ''}"
            prioridade = Prioridade.ALTA
        else:
            continue
        resultado.append(
            ItemAtencao(
                item_tipo="cobranca",
                item_id=cobranca.pk,
                titulo=f"{cobranca.cliente.nome} — {cobranca.descricao}",
                motivo=motivo,
                prioridade=prioridade,
                quando=cobranca.data_vencimento.strftime("%d/%m/%Y"),
                url=reverse("financeiro_cobranca_detalhe", args=[cobranca.pk]),
            )
        )
        if len(resultado) >= limit:
            break
    return resultado


def garantir_cron_lembretes_cobrancas() -> None:
    """Cron diário: re-sincroniza lembretes de cobranças em aberto."""
    try:
        from django_q.models import Schedule
        from django_q.tasks import schedule
    except ModuleNotFoundError:
        return

    func = "financeiro.tasks.sincronizar_lembretes_cobrancas_abertas"
    if Schedule.objects.filter(func=func).exists():
        return
    try:
        schedule(
            func,
            name="financeiro-lembretes-cobrancas",
            schedule_type=Schedule.DAILY,
            repeats=-1,
        )
    except Exception:
        logger.exception("Falha ao registrar cron de lembretes de cobranças.")
