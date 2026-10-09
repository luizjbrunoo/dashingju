"""Helpers de teste — Django Permissions do Marketing."""

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

MARKETING_CODENAMES = (
    "view_marketing",
    "view_resultados_marketing",
    "view_conteudo_marketing",
    "edit_conteudo_marketing",
    "manage_integracoes_marketing",
)


def grant_marketing_permissions(user, *codenames):
    codes = codenames or MARKETING_CODENAMES
    ct = ContentType.objects.get(app_label="marketing", model="marketingintegracao")
    perms = []
    for code in codes:
        perm = Permission.objects.get(content_type=ct, codename=code)
        perms.append(perm)
    user.user_permissions.add(*perms)
