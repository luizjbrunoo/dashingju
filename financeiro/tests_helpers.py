"""Helpers de teste para conceder Django Permissions do Financeiro."""

from django.contrib.auth.models import Permission

BILLING_CODENAMES = (
    "view_cobrancas",
    "create_cobrancas",
    "edit_cobrancas",
    "cancel_cobrancas",
    "view_relatorios_cobrancas",
    "view_recebimentos",
    "create_recebimentos",
)

CAIXA_CODENAMES = (
    "view_caixa",
    "manage_caixa",
)


def grant_finance_permissions(user, *codenames):
    perms = Permission.objects.filter(
        content_type__app_label="financeiro",
        codename__in=codenames,
    )
    user.user_permissions.add(*perms)


def grant_billing_permissions(user):
    grant_finance_permissions(user, *BILLING_CODENAMES)


def grant_caixa_permissions(user, *, manage=True, view=True):
    codes = []
    if view:
        codes.append("view_caixa")
    if manage:
        codes.append("manage_caixa")
    grant_finance_permissions(user, *codes)


def grant_all_finance_permissions(user):
    grant_billing_permissions(user)
    grant_caixa_permissions(user)
