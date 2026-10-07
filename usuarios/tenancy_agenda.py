"""Dual-write Agenda: Organization vem só do TenantContext do request.

Não relê Membership. Não lê POST/GET. Não infere tenant pelo User.
Não usa Group nem responsavel como tenant.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.messages import constants
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from organizacoes.services import CONTEXT_RESOLVED

MSG_TENANT_INDETERMINADO = (
    "Não foi possível determinar o escritório ativo para esta operação."
)


def organization_for_agenda(request):
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
    organization = organization_for_agenda(request)
    if organization is None:
        messages.add_message(request, constants.ERROR, MSG_TENANT_INDETERMINADO)
        return None, redirect(redirect_to, *args, **kwargs)
    return organization, None


def agenda_item_or_404(request, model, pk):
    """GET/POST de item: Organization queryset. Sem fallback User."""
    organization = organization_for_agenda(request)
    if organization is None:
        raise Http404()
    return get_object_or_404(model, pk=pk, organization=organization)
