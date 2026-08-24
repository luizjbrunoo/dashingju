"""Consultas de auditoria da agenda."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from usuarios.models import AgendaAuditLog, Compromisso, Tarefa


@dataclass(frozen=True)
class ResumoAuditoriaAgenda:
    eventos_hoje: int
    total_registrado: int


def _ids_tenant(user) -> tuple[list[int], list[int]]:
    comp_ids = list(
        Compromisso.objects.filter(user=user).values_list("pk", flat=True)
    )
    tar_ids = list(Tarefa.objects.filter(user=user).values_list("pk", flat=True))
    return comp_ids, tar_ids


def queryset_auditoria_usuario(user):
    comp_ids, tar_ids = _ids_tenant(user)
    return (
        AgendaAuditLog.objects.filter(
            Q(item_tipo=AgendaAuditLog.ItemTipo.COMPROMISSO, item_id__in=comp_ids)
            | Q(item_tipo=AgendaAuditLog.ItemTipo.TAREFA, item_id__in=tar_ids)
        )
        .select_related("usuario")
        .order_by("-criado_em")
    )


def historico_auditoria_usuario(user, *, limit: int = 100):
    return queryset_auditoria_usuario(user)[:limit]


def resumo_auditoria_usuario(user) -> ResumoAuditoriaAgenda:
    qs = queryset_auditoria_usuario(user)
    hoje = timezone.localdate()
    return ResumoAuditoriaAgenda(
        eventos_hoje=qs.filter(criado_em__date=hoje).count(),
        total_registrado=qs.count(),
    )


def rotulo_item_auditoria(log: AgendaAuditLog) -> str:
    if log.item_tipo == AgendaAuditLog.ItemTipo.COMPROMISSO:
        titulo = (
            Compromisso.objects.filter(pk=log.item_id)
            .values_list("titulo", flat=True)
            .first()
        )
        return titulo or f"Compromisso #{log.item_id}"
    titulo = (
        Tarefa.objects.filter(pk=log.item_id).values_list("titulo", flat=True).first()
    )
    return titulo or f"Tarefa #{log.item_id}"


def detalhe_alteracao(log: AgendaAuditLog) -> str:
    if log.campo and (log.valor_anterior or log.valor_novo):
        return f"{log.campo}: {log.valor_anterior or '—'} → {log.valor_novo or '—'}"
    return ""
