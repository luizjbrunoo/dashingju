"""RBAC do módulo Comercial — central de crescimento e receita."""

from __future__ import annotations

from dataclasses import dataclass

PERM_VIEW_DASHBOARD = "comercial.view_dashboard"
PERM_MANAGE_GOALS = "comercial.manage_goals"
PERM_VIEW_REVENUE = "comercial.view_revenue"
PERM_VIEW_TEAM = "comercial.view_team"
PERM_EXPORT_REPORTS = "comercial.export_reports"

TODAS_PERMISSOES_COMERCIAL = (
    PERM_VIEW_DASHBOARD,
    PERM_MANAGE_GOALS,
    PERM_VIEW_REVENUE,
    PERM_VIEW_TEAM,
    PERM_EXPORT_REPORTS,
)


def rbac_restritivo(user) -> bool:
    """RBAC comercial é sempre restritivo (fail-closed). Membership ≠ capability."""
    return True


def _tem_perm(user, perm: str) -> bool:
    """Fail-closed. Sem Group / Group vazio / Membership não concedem acesso."""
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_perm(perm)


@dataclass(frozen=True)
class PermissoesComercial:
    ver_dashboard: bool
    gerenciar_metas: bool
    ver_receita: bool
    ver_equipe: bool
    exportar: bool
    rbac_ativo: bool


def permissoes_comercial(user) -> PermissoesComercial:
    return PermissoesComercial(
        ver_dashboard=_tem_perm(user, PERM_VIEW_DASHBOARD),
        gerenciar_metas=_tem_perm(user, PERM_MANAGE_GOALS),
        ver_receita=_tem_perm(user, PERM_VIEW_REVENUE),
        ver_equipe=_tem_perm(user, PERM_VIEW_TEAM),
        exportar=_tem_perm(user, PERM_EXPORT_REPORTS),
        rbac_ativo=rbac_restritivo(user),
    )


def pode_ver_dashboard(user) -> bool:
    return _tem_perm(user, PERM_VIEW_DASHBOARD)


def pode_gerenciar_metas(user) -> bool:
    return _tem_perm(user, PERM_MANAGE_GOALS)


def pode_ver_receita(user) -> bool:
    return _tem_perm(user, PERM_VIEW_REVENUE)
