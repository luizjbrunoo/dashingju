"""CRUD de contratos financeiros."""

from __future__ import annotations

from financeiro.models import Contrato
from financeiro.tenancy_write import assert_parent_organization


def contratos_queryset(organization):
    if organization is None:
        return Contrato.objects.none()
    return (
        Contrato.objects.filter(organization=organization)
        .select_related("cliente", "responsavel", "criado_por")
        .order_by("-criado_em")
    )


def criar_contrato(contrato: Contrato, *, autor, organization) -> Contrato:
    contrato.organization = organization
    assert_parent_organization(contrato.cliente, organization)
    if not contrato.criado_por_id:
        contrato.criado_por = autor
    if not contrato.responsavel_id:
        contrato.responsavel = autor
    contrato.full_clean()
    contrato.save()
    return contrato


def atualizar_contrato(contrato: Contrato) -> Contrato:
    contrato.full_clean()
    contrato.save()
    return contrato
