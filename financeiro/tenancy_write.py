"""Dual-write Financeiro: Organization vem só do TenantContext do request.

Não relê Membership. Não lê POST/GET. Não infere tenant pelo User.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.messages import constants
from django.core.exceptions import ValidationError
from django.shortcuts import redirect

from organizacoes.services import CONTEXT_RESOLVED

MSG_TENANT_INDETERMINADO = (
    "Não foi possível determinar o escritório ativo para esta operação."
)
MSG_PARENT_TENANT = "Não foi possível concluir esta operação para o escritório ativo."


def organization_for_finance_write(request):
    """TenantContext RESOLVED + request.organization. Qualquer outro estado: None."""
    if not hasattr(request, "organization_context") or not hasattr(
        request, "organization"
    ):
        return None
    if request.organization_context != CONTEXT_RESOLVED:
        return None
    if request.organization is None:
        return None
    return request.organization


def reject_or_organization(request, redirect_to, *args, **kwargs):
    """Retorna (organization, None) ou (None, HttpResponse de fail-closed)."""
    organization = organization_for_finance_write(request)
    if organization is None:
        messages.add_message(request, constants.ERROR, MSG_TENANT_INDETERMINADO)
        return None, redirect(redirect_to, *args, **kwargs)
    return organization, None


def parent_in_organization(parent, organization) -> bool:
    if parent is None or organization is None:
        return False
    parent_org_id = getattr(parent, "organization_id", None)
    if parent_org_id is None:
        return False
    return parent_org_id == organization.pk


def assert_parent_organization(parent, organization) -> None:
    if not parent_in_organization(parent, organization):
        raise ValidationError(MSG_PARENT_TENANT)


def update_allowed(instance, organization) -> bool:
    """Organization existente é imutável. Legado NULL não é backfillado."""
    existing = getattr(instance, "organization_id", None)
    if existing is None:
        return True
    return existing == organization.pk


def preserve_organization(instance, original_organization_id) -> None:
    instance.organization_id = original_organization_id
