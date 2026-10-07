"""Tenancy SQL de Documentos: Documento → Cliente → Organization.

SQL é a fonte de verdade. User/Group/responsavel não são tenant.
"""

from __future__ import annotations

import logging

from django.http import Http404
from django.shortcuts import get_object_or_404

from organizacoes.services import CONTEXT_RESOLVED
from usuarios.models import Cliente, Documentos

logger = logging.getLogger(__name__)

REASON_MISSING_ORGANIZATION = "MISSING_ORGANIZATION"
REASON_ORGANIZATION_CONFLICT = "ORGANIZATION_CONFLICT"
REASON_NOT_FOUND = "NOT_FOUND"


def organization_from_request(request):
    if not hasattr(request, "organization_context") or not hasattr(
        request, "organization"
    ):
        return None
    if request.organization_context != CONTEXT_RESOLVED:
        return None
    if request.organization is None:
        return None
    return request.organization


def organization_of_documento(documento):
    cliente = getattr(documento, "cliente", None)
    if cliente is None:
        return None
    return getattr(cliente, "organization", None)


def documento_tenant_safe(documento) -> bool:
    return organization_of_documento(documento) is not None


def load_documento(documento_id: int):
    try:
        return Documentos.objects.select_related(
            "cliente", "cliente__organization"
        ).get(pk=documento_id)
    except Documentos.DoesNotExist:
        return None


def log_skip_documento(documento_id, reason: str) -> None:
    logger.info("skip model=Documentos pk=%s reason=%s", documento_id, reason)


def cliente_do_tenant_or_404(request, cliente_id):
    organization = organization_from_request(request)
    if organization is None:
        raise Http404()
    return get_object_or_404(Cliente, pk=cliente_id, organization=organization)


def documento_do_tenant_or_404(request, documento_id):
    organization = organization_from_request(request)
    if organization is None:
        raise Http404()
    return get_object_or_404(
        Documentos,
        pk=documento_id,
        cliente__organization=organization,
    )
