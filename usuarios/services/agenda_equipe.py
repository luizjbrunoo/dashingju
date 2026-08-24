"""Membros da agenda, validação de tenant e processos vinculados."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from usuarios.models import Cliente, Compromisso, Tarefa

User = get_user_model()


def membros_agenda(user) -> QuerySet:
    """Usuários elegíveis como responsável/participante (mesmo grupo Django)."""
    if not user.groups.exists():
        return User.objects.filter(pk=user.pk).order_by("first_name", "username")
    group_ids = user.groups.values_list("pk", flat=True)
    return (
        User.objects.filter(groups__in=group_ids)
        .distinct()
        .order_by("first_name", "username")
    )


def responsavel_permitido(user, responsavel) -> bool:
    if responsavel is None:
        return True
    return membros_agenda(user).filter(pk=responsavel.pk).exists()


def participantes_permitidos(user, participantes) -> bool:
    if not participantes:
        return True
    ids = {u.pk for u in participantes}
    return membros_agenda(user).filter(pk__in=ids).count() == len(ids)


def cliente_do_tenant(user, cliente) -> bool:
    if cliente is None:
        return True
    return cliente.user_id == user.pk


def processos_distintos(user, *, cliente_id: int | None = None, limit: int = 50) -> list[str]:
    refs: set[str] = set()
    cq = Compromisso.objects.filter(user=user).exclude(processo_referencia="")
    tq = Tarefa.objects.filter(user=user).exclude(processo_referencia="")
    if cliente_id:
        cq = cq.filter(cliente_id=cliente_id)
        tq = tq.filter(cliente_id=cliente_id)
    for ref in cq.values_list("processo_referencia", flat=True).distinct()[:limit]:
        if ref:
            refs.add(ref)
    for ref in tq.values_list("processo_referencia", flat=True).distinct()[:limit]:
        if ref:
            refs.add(ref)
    return sorted(refs)


def processos_por_cliente(user) -> dict[str, list[str]]:
    resultado: dict[str, list[str]] = {}
    for cliente in Cliente.objects.filter(user=user).only("id"):
        refs = processos_distintos(user, cliente_id=cliente.pk)
        if refs:
            resultado[str(cliente.pk)] = refs
    return resultado


def _ids_equipe(user) -> list[int]:
    return list(membros_agenda(user).values_list("pk", flat=True))


def filtrar_compromissos_escopo(qs: QuerySet, user, escopo: str) -> QuerySet:
    from usuarios.choices import EscopoAgenda

    if escopo == EscopoAgenda.TODOS:
        return qs
    if escopo == EscopoAgenda.MINHA:
        return qs.filter(
            Q(responsavel=user) | Q(participantes__usuario=user)
        ).distinct()
    if escopo == EscopoAgenda.EQUIPE:
        team_ids = _ids_equipe(user)
        return qs.filter(
            Q(responsavel_id__in=team_ids) | Q(participantes__usuario_id__in=team_ids)
        ).distinct()
    return qs


def filtrar_tarefas_escopo(qs: QuerySet, user, escopo: str) -> QuerySet:
    from usuarios.choices import EscopoAgenda

    if escopo == EscopoAgenda.TODOS:
        return qs
    if escopo == EscopoAgenda.MINHA:
        return qs.filter(responsavel=user)
    if escopo == EscopoAgenda.EQUIPE:
        return qs.filter(responsavel_id__in=_ids_equipe(user))
    return qs
