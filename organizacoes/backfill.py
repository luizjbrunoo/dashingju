"""Backfill tenant: somente Membership ativa única → Organization.

Nunca first(), User 1, DEMO ou tenant arbitrário.
"""

from __future__ import annotations

from organizacoes.models import Membership, Organization


def organization_id_from_unique_active_membership(user_id) -> int | None:
    if user_id is None:
        return None
    ids = list(
        Membership.objects.filter(
            user_id=user_id,
            status=Membership.Status.ACTIVE,
            organization__status=Organization.Status.ACTIVE,
        ).values_list("organization_id", flat=True)[:2]
    )
    if len(ids) != 1:
        return None
    return ids[0]
