"""RBAC do módulo Agenda — compromissos, tarefas e auditoria."""

from __future__ import annotations

from dataclasses import dataclass

PERM_VIEW_AGENDA = "usuarios.view_agenda"
PERM_CREATE_AGENDA = "usuarios.create_agenda"
PERM_EDIT_AGENDA = "usuarios.edit_agenda"
PERM_CANCEL_AGENDA = "usuarios.cancel_agenda"
PERM_VIEW_AUDIT_AGENDA = "usuarios.view_audit_agenda"
PERM_VIEW_DOCUMENTOS = "usuarios.view_documentos"

TODAS_PERMISSOES_AGENDA = (
    PERM_VIEW_AGENDA,
    PERM_CREATE_AGENDA,
    PERM_EDIT_AGENDA,
    PERM_CANCEL_AGENDA,
    PERM_VIEW_AUDIT_AGENDA,
)

TODAS_PERMISSOES_DOCUMENTOS = (PERM_VIEW_DOCUMENTOS,)


def rbac_restritivo(user) -> bool:
    """RBAC da Agenda é sempre restritivo (fail-closed). Membership ≠ capability."""
    return True


def _tem_perm(user, perm: str) -> bool:
    """Fail-closed. Sem Group / Group vazio / Membership não concedem acesso.

    Superuser segue o has_perm do Django (capability explícita), não fallback de tenant.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_perm(perm)


@dataclass(frozen=True)
class PermissoesAgenda:
    ver_agenda: bool
    criar_agenda: bool
    editar_agenda: bool
    cancelar_agenda: bool
    ver_auditoria: bool
    rbac_ativo: bool


def permissoes_agenda(user) -> PermissoesAgenda:
    return PermissoesAgenda(
        ver_agenda=_tem_perm(user, PERM_VIEW_AGENDA),
        criar_agenda=_tem_perm(user, PERM_CREATE_AGENDA),
        editar_agenda=_tem_perm(user, PERM_EDIT_AGENDA),
        cancelar_agenda=_tem_perm(user, PERM_CANCEL_AGENDA),
        ver_auditoria=_tem_perm(user, PERM_VIEW_AUDIT_AGENDA),
        rbac_ativo=rbac_restritivo(user),
    )


def pode_ver_agenda(user) -> bool:
    return _tem_perm(user, PERM_VIEW_AGENDA)


def pode_criar_agenda(user) -> bool:
    return _tem_perm(user, PERM_CREATE_AGENDA)


def pode_editar_agenda(user) -> bool:
    return _tem_perm(user, PERM_EDIT_AGENDA)


def pode_cancelar_agenda(user) -> bool:
    return _tem_perm(user, PERM_CANCEL_AGENDA)


def pode_ver_auditoria_agenda(user) -> bool:
    return _tem_perm(user, PERM_VIEW_AUDIT_AGENDA)


def pode_baixar_documento(user) -> bool:
    """Capability de download. Membership/same-org não bastam."""
    return _tem_perm(user, PERM_VIEW_DOCUMENTOS)
