"""Context processor — permissões comerciais em todos os templates autenticados."""

from comercial.permissions import permissoes_comercial


def comercial_permissoes(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"com_perms": permissoes_comercial(request.user)}
