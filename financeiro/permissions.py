"""RBAC fail-closed do módulo Financeiro.

Capability = Django Permission explícita.
TenantContext continua determinado em outro lugar.
Membership.role / Group vazio / Cliente.user / responsavel NÃO autorizam.
"""

from __future__ import annotations

from dataclasses import dataclass

# Permissões customizadas (app_label.codename) — nomes reais do produto
PERM_VIEW_COBRANCAS = "financeiro.view_cobrancas"
PERM_CREATE_COBRANCAS = "financeiro.create_cobrancas"
PERM_EDIT_COBRANCAS = "financeiro.edit_cobrancas"
PERM_CANCEL_COBRANCAS = "financeiro.cancel_cobrancas"
PERM_VIEW_RELATORIOS = "financeiro.view_relatorios_cobrancas"
PERM_VIEW_RECEBIMENTOS = "financeiro.view_recebimentos"
PERM_CREATE_RECEBIMENTOS = "financeiro.create_recebimentos"
PERM_VIEW_CAIXA = "financeiro.view_caixa"
PERM_MANAGE_CAIXA = "financeiro.manage_caixa"

TODAS_PERMISSOES_FINANCEIRO = (
    PERM_VIEW_COBRANCAS,
    PERM_CREATE_COBRANCAS,
    PERM_EDIT_COBRANCAS,
    PERM_CANCEL_COBRANCAS,
    PERM_VIEW_RELATORIOS,
    PERM_VIEW_RECEBIMENTOS,
    PERM_CREATE_RECEBIMENTOS,
    PERM_VIEW_CAIXA,
    PERM_MANAGE_CAIXA,
)

PERMS_BILLING = (
    PERM_VIEW_COBRANCAS,
    PERM_CREATE_COBRANCAS,
    PERM_EDIT_COBRANCAS,
    PERM_CANCEL_COBRANCAS,
    PERM_VIEW_RELATORIOS,
    PERM_VIEW_RECEBIMENTOS,
    PERM_CREATE_RECEBIMENTOS,
)

PERMS_CAIXA = (
    PERM_VIEW_CAIXA,
    PERM_MANAGE_CAIXA,
)


def rbac_restritivo(user) -> bool:
    """Compat: RBAC financeiro é sempre restritivo (fail-closed)."""
    return True


def has_finance_permission(user, perm: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_perm(perm)


def _tem_perm(user, perm: str) -> bool:
    """Fail-closed. Sem Group / Group vazio / Membership não concedem acesso."""
    return has_finance_permission(user, perm)


@dataclass(frozen=True)
class PermissoesFinanceiro:
    ver_cobrancas: bool
    criar_cobrancas: bool
    editar_cobrancas: bool
    cancelar_cobrancas: bool
    ver_recebimentos: bool
    registrar_recebimentos: bool
    ver_relatorios: bool
    ver_caixa: bool
    gerir_caixa: bool
    rbac_ativo: bool


def permissoes_financeiro(user) -> PermissoesFinanceiro:
    return PermissoesFinanceiro(
        ver_cobrancas=pode_ver_cobrancas(user),
        criar_cobrancas=pode_criar_cobrancas(user),
        editar_cobrancas=pode_editar_cobrancas(user),
        cancelar_cobrancas=pode_cancelar_cobrancas(user),
        ver_recebimentos=pode_ver_recebimentos(user),
        registrar_recebimentos=pode_registrar_recebimentos(user),
        ver_relatorios=pode_ver_relatorios(user),
        ver_caixa=pode_ver_caixa(user),
        gerir_caixa=pode_gerir_caixa(user),
        rbac_ativo=True,
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


def pode_ver_caixa(user) -> bool:
    """manage_caixa implica view_caixa de forma explícita (UX operacional)."""
    return _tem_perm(user, PERM_VIEW_CAIXA) or _tem_perm(user, PERM_MANAGE_CAIXA)


def pode_gerir_caixa(user) -> bool:
    return _tem_perm(user, PERM_MANAGE_CAIXA)


def tem_capability_financeira(user) -> bool:
    return (
        pode_ver_caixa(user)
        or pode_gerir_caixa(user)
        or pode_ver_cobrancas(user)
        or pode_criar_cobrancas(user)
        or pode_editar_cobrancas(user)
        or pode_cancelar_cobrancas(user)
        or pode_ver_recebimentos(user)
        or pode_registrar_recebimentos(user)
        or pode_ver_relatorios(user)
    )
