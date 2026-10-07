"""Funil de receita CRM (não duplica CRM — apenas agrega)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse

from comercial.services.finance_org import contratos_organization
from comercial.services.money import MIN_AMOSTRAS_CONVERSAO, ZERO, money, safe_pct
from comercial.services.periodo import PeriodoComercial, filtro_datetime_campo
from marketing.definitions import FASES_PROPOSTA
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso
from usuarios.services.org_scope import (
    clientes_da_organizacao,
    compromissos_da_organizacao,
)


@dataclass(frozen=True)
class EtapaFunil:
    key: str
    label: str
    valor: int | Decimal
    tipo: str  # int | money
    url: str | None = None


@dataclass(frozen=True)
class GargaloFunil:
    de: str
    para: str
    taxa_pct: Decimal | None
    label: str


@dataclass(frozen=True)
class FunilReceita:
    leads: int
    com_consulta: int
    consultas: int
    propostas: int
    contratos: int
    receita_contratada: Decimal
    taxa_lead_consulta: Decimal | None
    taxa_consulta_proposta: Decimal | None
    taxa_proposta_contrato: Decimal | None
    taxa_lead_contrato: Decimal | None
    gargalo: GargaloFunil | None
    etapas: tuple[EtapaFunil, ...]
    dados_suficientes: bool
    mensagem: str


def _clientes_periodo(user, periodo: PeriodoComercial, *, organization=None):
    del user
    qs = clientes_da_organizacao(organization)
    return filtro_datetime_campo(
        qs, "criado_em", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )


def calcular_funil_receita(user, periodo: PeriodoComercial, *, organization=None) -> FunilReceita:
    leads_qs = _clientes_periodo(user, periodo, organization=organization)
    leads = leads_qs.count()
    ids = leads_qs.values("pk")

    consultas_qs = compromissos_da_organizacao(organization).filter(
        cliente_id__in=ids,
        tipo=TipoCompromisso.CONSULTA,
    ).exclude(status=StatusCompromisso.CANCELADO)
    consultas_qs = filtro_datetime_campo(
        consultas_qs,
        "data_hora",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    consultas = consultas_qs.count()
    com_consulta = (
        leads_qs.filter(
            id__in=compromissos_da_organizacao(organization).filter(
                tipo=TipoCompromisso.CONSULTA,
            )
            .exclude(status=StatusCompromisso.CANCELADO)
            .values("cliente_id")
        ).count()
        if leads
        else 0
    )

    propostas = leads_qs.filter(fase_funil__in=FASES_PROPOSTA).count()

    contratos_qs = contratos_organization(organization).filter(cliente_id__in=ids)
    contratos_qs = filtro_datetime_campo(
        contratos_qs,
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    contratos_agg = contratos_qs.aggregate(
        qtd=Count("pk"),
        receita=Coalesce(Sum("valor_total"), ZERO),
    )
    contratos = contratos_agg["qtd"] or 0
    receita = money(contratos_agg["receita"])

    taxa_lc = safe_pct(com_consulta, leads)
    taxa_cp = safe_pct(propostas, consultas) if consultas else safe_pct(propostas, com_consulta)
    # Proposta → contrato: propostas do período que fecharam contrato
    taxa_pc = safe_pct(contratos, propostas)
    taxa_lct = safe_pct(contratos, leads)

    suficientes = leads >= MIN_AMOSTRAS_CONVERSAO
    gargalo = None
    if suficientes:
        gargalo = _identificar_gargalo(
            [
                ("Leads", "Com consulta", taxa_lc),
                ("Consulta", "Proposta", taxa_cp),
                ("Proposta", "Contrato", taxa_pc),
            ]
        )

    if not leads:
        mensagem = "Ainda não existem leads no período para montar o funil."
    elif not suficientes:
        mensagem = (
            "Dado insuficiente para diagnóstico confiável do gargalo. "
            "Funil exibido com amostra limitada."
        )
    else:
        mensagem = ""

    etapas = (
        EtapaFunil("leads", "Leads", leads, "int", reverse("clientes")),
        EtapaFunil(
            "com_consulta",
            "Com consulta",
            com_consulta,
            "int",
            reverse("agenda") + "?view=lista",
        ),
        EtapaFunil("consultas", "Consultas", consultas, "int", reverse("agenda")),
        EtapaFunil("propostas", "Propostas", propostas, "int", reverse("clientes")),
        EtapaFunil(
            "contratos",
            "Contratos",
            contratos,
            "int",
            reverse("financeiro_contrato_listar"),
        ),
        EtapaFunil(
            "receita",
            "Receita contratada",
            receita,
            "money",
            reverse("financeiro_contrato_listar"),
        ),
    )

    return FunilReceita(
        leads=leads,
        com_consulta=com_consulta,
        consultas=consultas,
        propostas=propostas,
        contratos=contratos,
        receita_contratada=receita,
        taxa_lead_consulta=taxa_lc,
        taxa_consulta_proposta=taxa_cp,
        taxa_proposta_contrato=taxa_pc,
        taxa_lead_contrato=taxa_lct,
        gargalo=gargalo,
        etapas=etapas,
        dados_suficientes=bool(leads),
        mensagem=mensagem,
    )


def _identificar_gargalo(
    transicoes: list[tuple[str, str, Decimal | None]],
) -> GargaloFunil | None:
    candidatas = [(a, b, t) for a, b, t in transicoes if t is not None]
    if not candidatas:
        return None
    de, para, taxa = min(candidatas, key=lambda x: x[2])
    return GargaloFunil(
        de=de,
        para=para,
        taxa_pct=taxa,
        label=f"{de} → {para}",
    )


ETAPAS_HOME = ("leads", "consultas", "propostas", "contratos")


def etapas_executivas(funil: FunilReceita) -> tuple[EtapaFunil, ...]:
    mapa = {e.key: e for e in funil.etapas}
    return tuple(mapa[k] for k in ETAPAS_HOME if k in mapa)
