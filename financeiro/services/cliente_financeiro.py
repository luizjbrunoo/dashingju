"""Resumo financeiro na ficha do cliente."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.urls import reverse
from urllib.parse import urlencode

from financeiro.choices import StatusCobranca, StatusContrato
from financeiro.models import Cobranca, Contrato
from financeiro.services.cobranca_listagem import queryset_anotado_organization
from financeiro.services.cobrancas import saldo_cobranca
from financeiro.services.contrato_crud import contratos_queryset


@dataclass(frozen=True)
class ResumoFinanceiroCliente:
    a_receber: Decimal
    vencido: Decimal
    contratos_ativos: int
    cobrancas_abertas: int


def _resumo_vazio() -> ResumoFinanceiroCliente:
    return ResumoFinanceiroCliente(
        a_receber=Decimal("0"),
        vencido=Decimal("0"),
        contratos_ativos=0,
        cobrancas_abertas=0,
    )


def _cliente_na_organization(organization, cliente) -> bool:
    if organization is None or cliente is None:
        return False
    org_id = getattr(cliente, "organization_id", None)
    if org_id is None:
        return False
    return org_id == organization.pk


def _cobrancas_qs_organization(organization, cliente):
    if not _cliente_na_organization(organization, cliente):
        return Cobranca.objects.none()
    return queryset_anotado_organization(organization).filter(cliente=cliente)


def _contratos_qs_organization(organization, cliente):
    if not _cliente_na_organization(organization, cliente):
        return Contrato.objects.none()
    return contratos_queryset(organization).filter(cliente=cliente)


def resumo_financeiro_cliente_organization(organization, cliente) -> ResumoFinanceiroCliente:
    if not _cliente_na_organization(organization, cliente):
        return _resumo_vazio()
    cobrancas = _cobrancas_qs_organization(organization, cliente).exclude(
        status=StatusCobranca.CANCELED
    )

    a_receber = Decimal("0")
    vencido = Decimal("0")
    abertas = 0
    for c in cobrancas:
        saldo = getattr(c, "saldo_calc", None)
        if saldo is None:
            saldo = saldo_cobranca(c)
        if saldo <= 0:
            continue
        abertas += 1
        a_receber += saldo
        if c.status == StatusCobranca.OVERDUE:
            vencido += saldo

    contratos_ativos = _contratos_qs_organization(organization, cliente).filter(
        status=StatusContrato.ACTIVE
    ).count()

    return ResumoFinanceiroCliente(
        a_receber=a_receber,
        vencido=vencido,
        contratos_ativos=contratos_ativos,
        cobrancas_abertas=abertas,
    )


def cobrancas_cliente_organization(organization, cliente, *, limit: int = 5):
    if not _cliente_na_organization(organization, cliente):
        return Cobranca.objects.none()
    return (
        _cobrancas_qs_organization(organization, cliente)
        .select_related("contrato", "responsavel")
        .order_by("data_vencimento", "id")[:limit]
    )


def contratos_cliente_organization(organization, cliente, *, limit: int = 5):
    if not _cliente_na_organization(organization, cliente):
        return Contrato.objects.none()
    return _contratos_qs_organization(organization, cliente).order_by("-criado_em")[:limit]


def resumo_financeiro_cliente(usuario, cliente) -> ResumoFinanceiroCliente:
    """LEGACY ONLY — Agenda ainda usa User como tenant. Não usar em Cliente 360."""
    cobrancas = Cobranca.objects.filter(
        usuario=usuario,
        cliente=cliente,
    ).exclude(status=StatusCobranca.CANCELED)

    a_receber = Decimal("0")
    vencido = Decimal("0")
    abertas = 0
    for c in cobrancas:
        saldo = saldo_cobranca(c)
        if saldo <= 0:
            continue
        abertas += 1
        a_receber += saldo
        if c.status == StatusCobranca.OVERDUE:
            vencido += saldo

    contratos_ativos = Contrato.objects.filter(
        usuario=usuario,
        cliente=cliente,
        status=StatusContrato.ACTIVE,
    ).count()

    return ResumoFinanceiroCliente(
        a_receber=a_receber,
        vencido=vencido,
        contratos_ativos=contratos_ativos,
        cobrancas_abertas=abertas,
    )


def cobrancas_cliente(usuario, cliente, *, limit: int = 5):
    """LEGACY ONLY — Agenda / callers W5b+."""
    return (
        Cobranca.objects.filter(usuario=usuario, cliente=cliente)
        .select_related("contrato", "responsavel")
        .order_by("data_vencimento", "id")[:limit]
    )


def contratos_cliente(usuario, cliente, *, limit: int = 5):
    """LEGACY ONLY — Agenda / callers W5b+."""
    return (
        Contrato.objects.filter(usuario=usuario, cliente=cliente)
        .order_by("-criado_em")[:limit]
    )


def url_cobrancas_cliente(cliente_id: int) -> str:
    return f"{reverse('financeiro_cobranca_listar')}?{urlencode({'cliente': cliente_id})}"


def url_nova_cobranca_cliente(cliente_id: int) -> str:
    return f"{reverse('financeiro_cobranca_nova')}?{urlencode({'cliente': cliente_id})}"


def url_novo_contrato_cliente(cliente_id: int) -> str:
    return f"{reverse('financeiro_contrato_novo')}?{urlencode({'cliente': cliente_id})}"
