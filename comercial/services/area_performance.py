"""Performance por área jurídica (quando houver dados em Compromisso.area_juridica)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.db.models.functions import Coalesce

from comercial.services.finance_org import contratos_organization
from comercial.services.money import ZERO, money, safe_pct
from comercial.services.periodo import PeriodoComercial, filtro_datetime_campo
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Compromisso
from usuarios.services.org_scope import compromissos_da_organizacao


@dataclass(frozen=True)
class LinhaArea:
    area: str
    leads_proxy: int
    consultas: int
    contratos: int
    conversao_pct: Decimal | None
    ticket_medio: Decimal | None
    receita: Decimal


@dataclass(frozen=True)
class PerformanceArea:
    linhas: tuple[LinhaArea, ...]
    ordenacao: str
    disponivel: bool
    mensagem: str


_ORDENS = frozenset({"receita", "contratos", "conversao", "ticket", "consultas", "area"})


def performance_por_area(
    user,
    periodo: PeriodoComercial,
    *,
    organization=None,
    ordenacao: str = "receita",
) -> PerformanceArea:
    orden = ordenacao if ordenacao in _ORDENS else "receita"

    comps = compromissos_da_organizacao(organization).exclude(area_juridica="")
    comps = filtro_datetime_campo(
        comps, "data_hora", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )
    if not comps.exists():
        return PerformanceArea(
            linhas=(),
            ordenacao=orden,
            disponivel=False,
            mensagem=(
                "Performance por área indisponível. "
                "Preencha a área jurídica nos compromissos/consultas para habilitar esta visão."
            ),
        )

    area_nomes = [
        r["area_juridica"]
        for r in comps.values("area_juridica").annotate(n=Count("pk")).order_by("-n")
        if r["area_juridica"]
    ]

    linhas: list[LinhaArea] = []
    for area in area_nomes:
        qs_area = comps.filter(area_juridica=area)
        clientes = qs_area.exclude(cliente_id=None).values("cliente_id").distinct().count()
        consultas = (
            qs_area.filter(tipo=TipoCompromisso.CONSULTA)
            .exclude(status=StatusCompromisso.CANCELADO)
            .count()
        )
        ids_cli = qs_area.exclude(cliente_id=None).values("cliente_id")
        contratos_qs = contratos_organization(organization).filter(cliente_id__in=ids_cli)
        contratos_qs = filtro_datetime_campo(
            contratos_qs,
            "criado_em",
            data_inicio=periodo.data_inicio,
            data_fim=periodo.data_fim,
        )
        agg = contratos_qs.aggregate(
            qtd=Count("pk"),
            receita=Coalesce(Sum("valor_total"), ZERO),
            ticket=Avg("valor_total"),
        )
        qtd = agg["qtd"] or 0
        receita = money(agg["receita"])
        ticket = money(agg["ticket"]) if agg["ticket"] else None
        conv = safe_pct(qtd, clientes)
        linhas.append(
            LinhaArea(
                area=area,
                leads_proxy=clientes,
                consultas=consultas,
                contratos=qtd,
                conversao_pct=conv,
                ticket_medio=ticket,
                receita=receita,
            )
        )

    def _key(linha: LinhaArea):
        if orden == "contratos":
            return linha.contratos
        if orden == "conversao":
            return float(linha.conversao_pct or 0)
        if orden == "ticket":
            return float(linha.ticket_medio or 0)
        if orden == "consultas":
            return linha.consultas
        if orden == "area":
            return linha.area.lower()
        return float(linha.receita)

    linhas.sort(key=_key, reverse=(orden != "area"))

    return PerformanceArea(
        linhas=tuple(linhas),
        ordenacao=orden,
        disponivel=True,
        mensagem="",
    )
