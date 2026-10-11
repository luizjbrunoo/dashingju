"""Context processors do app financeiro."""

from core.runtime_env import asaas_baas_enabled
from financeiro.permissions import permissoes_financeiro


def financeiro_permissoes(request):
    ctx = {"asaas_baas_enabled": asaas_baas_enabled()}
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return ctx
    ctx["fin_perms"] = permissoes_financeiro(request.user)
    return ctx
