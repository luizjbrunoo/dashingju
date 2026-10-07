"""Origem → contrato → receita (canais que geram resultado)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.db.models.functions import Coalesce

from comercial.services.finance_org import contratos_organization
from comercial.services.money import ZERO, money, safe_pct
from comercial.services.periodo import PeriodoComercial, filtro_datetime_campo
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso
from usuarios.services.org_scope import (
    clientes_da_organizacao,
    compromissos_da_organizacao,
)


@dataclass(frozen=True)
class LinhaOrigem:
    origem: str
    label: str
    leads: int
    consultas: int
    contratos: int
    receita: Decimal
    conversao_pct: Decimal | None
    ticket_medio: Decimal | None


@dataclass(frozen=True)
class OrigemResultados:
    linhas: tuple[LinhaOrigem, ...]
    disponivel: bool
    mensagem: str


_LABELS = dict(OrigemLead.choices)


def origem_para_receita(user, periodo: PeriodoComercial, *, organization=None) -> OrigemResultados:
    leads_qs = clientes_da_organizacao(organization)
    leads_qs = filtro_datetime_campo(
        leads_qs, "criado_em", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )
    if not leads_qs.exists():
        return OrigemResultados(
            linhas=(),
            disponivel=False,
            mensagem="Associe a origem aos novos clientes para acompanhar o valor comercial atribuído a cada canal.",
        )

    # Origens presentes (inclui vazia = não identificada)
    origens = [
        r["origem"]
        for r in leads_qs.values("origem").annotate(n=Count("pk")).order_by("-n")
    ]

    linhas: list[LinhaOrigem] = []
    for origem in origens:
        qs = leads_qs.filter(origem=origem)
        leads = qs.count()
        ids = qs.values("pk")
        consultas = (
            compromissos_da_organizacao(organization).filter(
                cliente_id__in=ids,
                tipo=TipoCompromisso.CONSULTA,
            )
            .exclude(status=StatusCompromisso.CANCELADO)
        )
        consultas = filtro_datetime_campo(
            consultas,
            "data_hora",
            data_inicio=periodo.data_inicio,
            data_fim=periodo.data_fim,
        ).count()

        contratos_qs = contratos_organization(organization).filter(cliente_id__in=ids)
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
        label = _LABELS.get(origem, origem or "Origem não identificada")
        linhas.append(
            LinhaOrigem(
                origem=origem or "",
                label=label,
                leads=leads,
                consultas=consultas,
                contratos=qtd,
                receita=money(agg["receita"]),
                conversao_pct=safe_pct(qtd, leads),
                ticket_medio=money(agg["ticket"]) if agg["ticket"] else None,
            )
        )

    linhas.sort(key=lambda x: (x.contratos, float(x.receita)), reverse=True)
    tem_origem = any(l.origem for l in linhas)
    mensagem = ""
    if not tem_origem:
        mensagem = (
            "Nenhuma origem preenchida nos leads do período. "
            "Associe a origem aos novos clientes para comparar canais."
        )

    return OrigemResultados(
        linhas=tuple(linhas),
        disponivel=True,
        mensagem=mensagem,
    )
