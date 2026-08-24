"""RBAC do módulo Marketing — Google Ads, resultados e conteúdo com IA."""

from __future__ import annotations

from dataclasses import dataclass

PERM_VIEW_MARKETING = "marketing.view_marketing"
PERM_VIEW_RESULTADOS = "marketing.view_resultados_marketing"
PERM_VIEW_CONTEUDO = "marketing.view_conteudo_marketing"
PERM_EDIT_CONTEUDO = "marketing.edit_conteudo_marketing"

TODAS_PERMISSOES_MARKETING = (
    PERM_VIEW_MARKETING,
    PERM_VIEW_RESULTADOS,
    PERM_VIEW_CONTEUDO,
    PERM_EDIT_CONTEUDO,
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
class PermissoesMarketing:
    ver_marketing: bool
    ver_resultados: bool
    ver_conteudo: bool
    editar_conteudo: bool
    rbac_ativo: bool


def permissoes_marketing(user) -> PermissoesMarketing:
    return PermissoesMarketing(
        ver_marketing=_tem_perm(user, PERM_VIEW_MARKETING),
        ver_resultados=_tem_perm(user, PERM_VIEW_RESULTADOS),
        ver_conteudo=_tem_perm(user, PERM_VIEW_CONTEUDO),
        editar_conteudo=_tem_perm(user, PERM_EDIT_CONTEUDO),
        rbac_ativo=rbac_restritivo(user),
    )


def pode_ver_marketing(user) -> bool:
    return _tem_perm(user, PERM_VIEW_MARKETING)


def pode_ver_resultados_marketing(user) -> bool:
    return _tem_perm(user, PERM_VIEW_RESULTADOS)


def pode_ver_conteudo_marketing(user) -> bool:
    return _tem_perm(user, PERM_VIEW_CONTEUDO)


def pode_editar_conteudo_marketing(user) -> bool:
    return _tem_perm(user, PERM_EDIT_CONTEUDO)


def pode_acessar_modulo_marketing(user) -> bool:
    return pode_ver_marketing(user) or pode_ver_conteudo_marketing(user)


def pode_ver_metricas_financeiras_marketing(user) -> bool:
    """Receita no painel: reutiliza RBAC financeiro (spec Fase 13)."""
    from financeiro.permissions import pode_ver_cobrancas, pode_ver_recebimentos

    return pode_ver_cobrancas(user) and pode_ver_recebimentos(user)
