"""Provisioning RBAC canônico.

Membership NÃO autoriza em runtime.
OWNER recebe Groups/permissions explícitos.
MEMBER não recebe capabilities automaticamente.
Role não mapeada = fail-closed (erro de provisioning, sem fallback).
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.db import transaction

from comercial.permissions import TODAS_PERMISSOES_COMERCIAL
from financeiro.permissions import TODAS_PERMISSOES_FINANCEIRO
from marketing.permissions import TODAS_PERMISSOES_MARKETING
from organizacoes.models import Membership, Organization
from usuarios.permissions import TODAS_PERMISSOES_AGENDA, TODAS_PERMISSOES_DOCUMENTOS

GRUPO_COMERCIAL = "Comercial — acesso completo"
GRUPO_MARKETING = "Marketing — acesso completo"
GRUPO_FINANCEIRO = "Financeiro — acesso completo"
GRUPO_AGENDA = "Agenda — acesso completo"
GRUPO_DOCUMENTOS = "Documentos — acesso completo"

MANAGED_GROUPS: dict[str, tuple[str, ...]] = {
    GRUPO_AGENDA: TODAS_PERMISSOES_AGENDA,
    GRUPO_FINANCEIRO: TODAS_PERMISSOES_FINANCEIRO,
    GRUPO_COMERCIAL: TODAS_PERMISSOES_COMERCIAL,
    GRUPO_MARKETING: TODAS_PERMISSOES_MARKETING,
    GRUPO_DOCUMENTOS: TODAS_PERMISSOES_DOCUMENTOS,
}

OWNER_MODULE_GROUPS = (
    GRUPO_AGENDA,
    GRUPO_FINANCEIRO,
    GRUPO_COMERCIAL,
    GRUPO_MARKETING,
    GRUPO_DOCUMENTOS,
)

ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    Membership.Role.OWNER: OWNER_MODULE_GROUPS,
    Membership.Role.MEMBER: (),
}


class ProvisioningError(Exception):
    """Catálogo incompleto ou permission Django ausente."""


class UnmappedRoleError(ProvisioningError):
    """Role sem mapping explícito: fail-closed, sem fallback."""


def _clear_perm_cache(user) -> None:
    for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)


def permission_for(perm_string: str) -> Permission:
    if not perm_string or "." not in perm_string:
        raise ProvisioningError(f"PERMISSION_INVALID: {perm_string}")
    app_label, codename = perm_string.split(".", 1)
    perm = Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()
    if perm is None:
        raise ProvisioningError(f"PERMISSION_NOT_FOUND: {perm_string}")
    return perm


def groups_for_role(role: str) -> tuple[str, ...]:
    if role not in ROLE_GROUPS:
        raise UnmappedRoleError(f"ROLE_UNMAPPED: {role}")
    return ROLE_GROUPS[role]


def ensure_module_rbac_groups() -> dict[str, Group]:
    """ENSURE required permissions nos Groups gerenciados. Não substitui extras."""
    resolved_by_group: dict[str, list[Permission]] = {}
    missing: list[str] = []
    for name, perms in MANAGED_GROUPS.items():
        resolved: list[Permission] = []
        for perm_string in perms:
            try:
                resolved.append(permission_for(perm_string))
            except ProvisioningError as exc:
                missing.append(str(exc))
        resolved_by_group[name] = resolved
    if missing:
        raise ProvisioningError("; ".join(missing))

    groups: dict[str, Group] = {}
    for name, resolved in resolved_by_group.items():
        grupo, _ = Group.objects.get_or_create(name=name)
        if resolved:
            grupo.permissions.add(*resolved)
        groups[name] = grupo
    return groups


def grant_owner_module_capabilities(user) -> list[str]:
    """Atribui Groups do papel OWNER. Idempotente. Não é bypass de runtime."""
    groups = ensure_module_rbac_groups()
    to_add = [groups[name] for name in OWNER_MODULE_GROUPS]
    user.groups.add(*to_add)
    _clear_perm_cache(user)
    return list(OWNER_MODULE_GROUPS)


def grant_capabilities_for_membership(membership: Membership) -> list[str]:
    if membership is None:
        return []
    if membership.status != Membership.Status.ACTIVE:
        return []
    names = groups_for_role(membership.role)
    if not names:
        return []
    groups = ensure_module_rbac_groups()
    user = membership.user
    user.groups.add(*[groups[name] for name in names])
    _clear_perm_cache(user)
    return list(names)


def create_organization_with_owner(*, name: str, user):
    """Onboarding reutilizável: Organization + Membership OWNER + Groups.

    Não é autorização em runtime. DEMO e testes usam o mesmo serviço.
    """
    with transaction.atomic():
        org = Organization.objects.create(
            name=name,
            status=Organization.Status.ACTIVE,
            created_by=user,
        )
        membership = Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        granted = grant_capabilities_for_membership(membership)
        return org, membership, granted
