from __future__ import annotations

from organizacoes.models import Membership, Organization

CONTEXT_NONE = "none"
CONTEXT_RESOLVED = "resolved"
CONTEXT_AMBIGUOUS = "ambiguous"


def resolver_organization(user):
    """Resolve o tenant do User a partir de Memberships válidas.

    Retorna (organization_or_none, context_status) com status:
    "none" | "resolved" | "ambiguous".

    Não desempata múltiplas memberships. Não lê request HTTP.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return (None, CONTEXT_NONE)

    candidatas = list(
        Membership.objects.filter(
            user=user,
            status=Membership.Status.ACTIVE,
            organization__status=Organization.Status.ACTIVE,
        ).select_related("organization")[:2]
    )

    if not candidatas:
        return (None, CONTEXT_NONE)
    if len(candidatas) == 1:
        return (candidatas[0].organization, CONTEXT_RESOLVED)
    return (None, CONTEXT_AMBIGUOUS)
