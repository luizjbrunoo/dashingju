"""Membros da agenda, validação de tenant e processos vinculados.

Organization = tenant. Group não participa de query tenant.
responsavel = responsabilidade operacional, não ownership.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Compromisso, Tarefa

User = get_user_model()


def membership_ativa(user, organization) -> bool:
    if user is None or organization is None:
        return False
    return Membership.objects.filter(
        user=user,
        organization=organization,
        status=Membership.Status.ACTIVE,
        organization__status=Organization.Status.ACTIVE,
    ).exists()


def membros_agenda(organization) -> QuerySet:
    """Usuários com Membership ativa na Organization (responsável/participante)."""
    if organization is None:
        return User.objects.none()
    return (
        User.objects.filter(
            organization_memberships__organization=organization,
            organization_memberships__status=Membership.Status.ACTIVE,
            organization_memberships__organization__status=Organization.Status.ACTIVE,
        )
        .distinct()
        .order_by("first_name", "username")
    )


def responsavel_permitido(organization, responsavel) -> bool:
    if responsavel is None:
        return True
    return membros_agenda(organization).filter(pk=responsavel.pk).exists()


def participantes_permitidos(organization, participantes) -> bool:
    if not participantes:
        return True
    ids = {u.pk for u in participantes}
    return membros_agenda(organization).filter(pk__in=ids).count() == len(ids)


def cliente_do_tenant(organization, cliente) -> bool:
    if cliente is None:
        return True
    if organization is None:
        return False
    return cliente.organization_id == organization.pk


def processos_distintos(organization, *, cliente_id: int | None = None, limit: int = 50) -> list[str]:
    if organization is None:
        return []
    refs: set[str] = set()
    cq = Compromisso.objects.filter(organization=organization).exclude(
        processo_referencia=""
    )
    tq = Tarefa.objects.filter(organization=organization).exclude(processo_referencia="")
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


def processos_por_cliente(organization) -> dict[str, list[str]]:
    resultado: dict[str, list[str]] = {}
    if organization is None:
        return resultado
    for cliente in Cliente.objects.filter(organization=organization).only("id"):
        refs = processos_distintos(organization, cliente_id=cliente.pk)
        if refs:
            resultado[str(cliente.pk)] = refs
    return resultado


def _ids_equipe(organization) -> list[int]:
    return list(membros_agenda(organization).values_list("pk", flat=True))


def filtrar_compromissos_escopo(
    qs: QuerySet, user, escopo: str, *, organization=None
) -> QuerySet:
    from usuarios.choices import EscopoAgenda

    if escopo == EscopoAgenda.TODOS:
        return qs
    if escopo == EscopoAgenda.MINHA:
        if user is None:
            return qs.none()
        return qs.filter(
            Q(responsavel=user) | Q(participantes__usuario=user)
        ).distinct()
    if escopo == EscopoAgenda.EQUIPE:
        team_ids = _ids_equipe(organization)
        if not team_ids:
            return qs.none()
        return qs.filter(
            Q(responsavel_id__in=team_ids) | Q(participantes__usuario_id__in=team_ids)
        ).distinct()
    return qs


def filtrar_tarefas_escopo(
    qs: QuerySet, user, escopo: str, *, organization=None
) -> QuerySet:
    from usuarios.choices import EscopoAgenda

    if escopo == EscopoAgenda.TODOS:
        return qs
    if escopo == EscopoAgenda.MINHA:
        if user is None:
            return qs.none()
        return qs.filter(responsavel=user)
    if escopo == EscopoAgenda.EQUIPE:
        team_ids = _ids_equipe(organization)
        if not team_ids:
            return qs.none()
        return qs.filter(responsavel_id__in=team_ids)
    return qs
