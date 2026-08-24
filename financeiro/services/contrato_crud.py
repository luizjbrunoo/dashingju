"""CRUD de contratos financeiros."""

from __future__ import annotations

from financeiro.models import Contrato


def contratos_queryset(usuario):
    return (
        Contrato.objects.filter(usuario=usuario)
        .select_related("cliente", "responsavel", "criado_por")
        .order_by("-criado_em")
    )


def criar_contrato(contrato: Contrato, *, autor) -> Contrato:
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
