"""Context processors do app financeiro."""

from financeiro.permissions import permissoes_financeiro


def financeiro_permissoes(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"fin_perms": permissoes_financeiro(request.user)}
