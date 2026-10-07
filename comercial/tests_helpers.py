"""Helpers de teste — Django Permissions do Comercial."""

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from organizacoes.models import Membership, Organization

COMERCIAL_CODENAMES = (
    "view_dashboard",
    "manage_goals",
    "view_revenue",
    "view_team",
    "export_reports",
)


def grant_comercial_permissions(user, *codenames):
    codes = codenames or COMERCIAL_CODENAMES
    ct = ContentType.objects.get(app_label="comercial", model="metacomercial")
    perms = []
    for code in codes:
        perm = Permission.objects.get(content_type=ct, codename=code)
        perms.append(perm)
    user.user_permissions.add(*perms)


def provision_org(user, name=None, *, role=Membership.Role.MEMBER):
    org = Organization.objects.create(name=name or f"Org {user.username}")
    Membership.objects.create(
        user=user,
        organization=org,
        role=role,
        status=Membership.Status.ACTIVE,
    )
    return org
