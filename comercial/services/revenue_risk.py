"""Receita em risco — regras determinísticas sobre dados existentes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from comercial.services.money import ZERO, money
from financeiro.choices import StatusCobranca
from financeiro.services.cobranca_listagem import queryset_anotado_organization
from marketing.definitions import FASES_PROPOSTA
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso
from usuarios.services.org_scope import (
    clientes_da_organizacao,
    compromissos_da_organizacao,
)


@dataclass(frozen=True)
class ItemRisco:
    key: str
    titulo: str
    detalhe: str
    valor: Decimal | None  # None = sem valor monetário confiável
    quantidade: int
    url: str
    severidade: str  # risco | atencao


@dataclass(frozen=True)
class ReceitaRisco:
    total_confirmado_risco: Decimal  # cobranças vencidas (saldo)
    total_potencial_risco: Decimal  # estimado por ticket quando aplicável
    itens: tuple[ItemRisco, ...]
    mensagem: str


def calcular_receita_em_risco(
    user,
    *,
    organization=None,
    ticket_medio: Decimal | None = None,
    dias_lead_parado: int = 7,
) -> ReceitaRisco:
    hoje = timezone.localdate()
    agora = timezone.now()
    itens: list[ItemRisco] = []

    # 1) Cobranças vencidas com saldo > 0 (receita confirmada em risco)
    vencidas = (
        queryset_anotado_organization(organization)
        .exclude(status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT, StatusCobranca.PAID])
        .filter(data_vencimento__lt=hoje, saldo_calc__gt=0)
    )
    vencido_total = money(
        vencidas.aggregate(t=Coalesce(Sum("saldo_calc"), ZERO))["t"] or ZERO
    )
    qtd_vencidas = vencidas.count()
    if qtd_vencidas:
        itens.append(
            ItemRisco(
                key="cobrancas_vencidas",
                titulo="Cobranças vencidas",
                detalhe=f"{qtd_vencidas} cobrança(s) com saldo em atraso",
                valor=vencido_total,
                quantidade=qtd_vencidas,
                url=reverse("financeiro_cobranca_listar") + "?status=overdue",
                severidade="risco",
            )
        )

    # 2) Propostas sem follow-up futuro
    propostas = clientes_da_organizacao(organization).filter(fase_funil__in=FASES_PROPOSTA)
    com_followup_futuro = (
        compromissos_da_organizacao(organization).filter(
            cliente_id__in=propostas.values("pk"),
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
        .distinct()
    )
    propostas_sem_fu = propostas.exclude(id__in=com_followup_futuro)
    qtd_prop = propostas_sem_fu.count()
    potencial_prop = ZERO
    if qtd_prop and ticket_medio:
        potencial_prop = money(Decimal(qtd_prop) * Decimal(ticket_medio))
    if qtd_prop:
        itens.append(
            ItemRisco(
                key="propostas_sem_followup",
                titulo="Propostas sem follow-up",
                detalhe=f"{qtd_prop} cliente(s) em proposta sem follow-up agendado",
                valor=potencial_prop if ticket_medio else None,
                quantidade=qtd_prop,
                url=reverse("clientes"),
                severidade="atencao",
            )
        )

    # 3) Consultas realizadas sem próximo passo (sem compromisso futuro nem fase proposta/contrato)
    consultas_realizadas = (
        compromissos_da_organizacao(organization).filter(
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            cliente__isnull=False,
        )
        .select_related("cliente")
        .order_by("-data_hora")
    )
    ids_consulta = list(
        consultas_realizadas.values_list("cliente_id", flat=True).distinct()[:200]
    )
    futuros = set(
        compromissos_da_organizacao(organization).filter(
            cliente_id__in=ids_consulta,
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
    )
    sem_passo = 0
    for cid in ids_consulta:
        if cid in futuros:
            continue
        cli = clientes_da_organizacao(organization).filter(pk=cid).first()
        if not cli:
            continue
        if cli.fase_funil in FASES_PROPOSTA:
            continue
        if cli.status == "ativo":
            continue
        sem_passo += 1
    potencial_consulta = (
        money(Decimal(sem_passo) * Decimal(ticket_medio))
        if sem_passo and ticket_medio
        else ZERO
    )
    if sem_passo:
        itens.append(
            ItemRisco(
                key="consultas_sem_proximo_passo",
                titulo="Consultas sem próximo passo",
                detalhe=f"{sem_passo} cliente(s) com consulta realizada e sem ação seguinte",
                valor=potencial_consulta if ticket_medio else None,
                quantidade=sem_passo,
                url=reverse("agenda") + "?view=lista",
                severidade="atencao",
            )
        )

    # 4) Leads sem atividade recente
    limite = agora - timedelta(days=dias_lead_parado)
    leads_parados = clientes_da_organizacao(organization).filter(
        status="em_prospeccao",
        criado_em__lt=limite,
    ).exclude(
        id__in=compromissos_da_organizacao(organization).filter(data_hora__gte=limite).values(
            "cliente_id"
        )
    )
    qtd_leads = leads_parados.count()
    if qtd_leads:
        itens.append(
            ItemRisco(
                key="leads_parados",
                titulo="Leads sem atividade",
                detalhe=f"{qtd_leads} lead(s) sem movimentação há {dias_lead_parado}+ dias",
                valor=None,
                quantidade=qtd_leads,
                url=reverse("clientes"),
                severidade="atencao",
            )
        )

    potencial = money(
        sum((i.valor or ZERO) for i in itens if i.key != "cobrancas_vencidas")
    )

    mensagem = ""
    if not itens:
        mensagem = "Nenhuma situação de risco identificada com os dados atuais."

    return ReceitaRisco(
        total_confirmado_risco=vencido_total,
        total_potencial_risco=potencial,
        itens=tuple(itens),
        mensagem=mensagem,
    )
