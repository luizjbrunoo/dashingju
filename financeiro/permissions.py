"""RBAC do módulo Financeiro — cobranças e recebimentos."""

from __future__ import annotations

from dataclasses import dataclass

# Permissões customizadas (app_label.codename)
PERM_VIEW_COBRANCAS = "financeiro.view_cobrancas"
PERM_CREATE_COBRANCAS = "financeiro.create_cobrancas"
PERM_EDIT_COBRANCAS = "financeiro.edit_cobrancas"
PERM_CANCEL_COBRANCAS = "financeiro.cancel_cobrancas"
PERM_VIEW_RELATORIOS = "financeiro.view_relatorios_cobrancas"
PERM_VIEW_RECEBIMENTOS = "financeiro.view_recebimentos"
PERM_CREATE_RECEBIMENTOS = "financeiro.create_recebimentos"

TODAS_PERMISSOES_FINANCEIRO = (
    PERM_VIEW_COBRANCAS,
    PERM_CREATE_COBRANCAS,
    PERM_EDIT_COBRANCAS,
    PERM_CANCEL_COBRANCAS,
    PERM_VIEW_RELATORIOS,
    PERM_VIEW_RECEBIMENTOS,
    PERM_CREATE_RECEBIMENTOS,
)


def rbac_restritivo(user) -> bool:
    """Usuários em grupos Django obedecem RBAC; demais mantêm acesso legado."""
    return user.groups.exists()


def _tem_perm(user, perm: str) -> bool:
    if user.is_superuser:
        return True
    if not rbac_restritivo(user):
        return True
    return user.has_perm(perm)


@dataclass(frozen=True)
class PermissoesFinanceiro:
    ver_cobrancas: bool
    criar_cobrancas: bool
    editar_cobrancas: bool
    cancelar_cobrancas: bool
    ver_recebimentos: bool
    registrar_recebimentos: bool
    ver_relatorios: bool
    rbac_ativo: bool


def permissoes_financeiro(user) -> PermissoesFinanceiro:
    return PermissoesFinanceiro(
        ver_cobrancas=_tem_perm(user, PERM_VIEW_COBRANCAS),
        criar_cobrancas=_tem_perm(user, PERM_CREATE_COBRANCAS),
        editar_cobrancas=_tem_perm(user, PERM_EDIT_COBRANCAS),
        cancelar_cobrancas=_tem_perm(user, PERM_CANCEL_COBRANCAS),
        ver_recebimentos=_tem_perm(user, PERM_VIEW_RECEBIMENTOS),
        registrar_recebimentos=_tem_perm(user, PERM_CREATE_RECEBIMENTOS),
        ver_relatorios=_tem_perm(user, PERM_VIEW_RELATORIOS),
        rbac_ativo=rbac_restritivo(user),
    )


def pode_ver_cobrancas(user) -> bool:
    return _tem_perm(user, PERM_VIEW_COBRANCAS)


def pode_criar_cobrancas(user) -> bool:
    return _tem_perm(user, PERM_CREATE_COBRANCAS)


def pode_editar_cobrancas(user) -> bool:
    return _tem_perm(user, PERM_EDIT_COBRANCAS)


def pode_cancelar_cobrancas(user) -> bool:
    return _tem_perm(user, PERM_CANCEL_COBRANCAS)


def pode_ver_recebimentos(user) -> bool:
    return _tem_perm(user, PERM_VIEW_RECEBIMENTOS)


def pode_registrar_recebimentos(user) -> bool:
    return _tem_perm(user, PERM_CREATE_RECEBIMENTOS)


def pode_ver_relatorios(user) -> bool:
    return _tem_perm(user, PERM_VIEW_RELATORIOS)
