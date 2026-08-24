"""Context processors do app marketing."""

from marketing.permissions import permissoes_marketing


def marketing_permissoes(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"mkt_perms": permissoes_marketing(request.user)}
