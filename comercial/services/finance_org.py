"""Raiz financeira Organization-scoped para consumers do Comercial."""

from financeiro.choices import StatusContrato
from financeiro.models import CobrancaRecebimento
from financeiro.services.contrato_crud import contratos_queryset


def recebimentos_organization(organization):
    if organization is None:
        return CobrancaRecebimento.objects.none()
    return CobrancaRecebimento.objects.filter(organization=organization)


def contratos_organization(organization):
    return contratos_queryset(organization).filter(
        status__in=(StatusContrato.ACTIVE, StatusContrato.CLOSED),
    )
