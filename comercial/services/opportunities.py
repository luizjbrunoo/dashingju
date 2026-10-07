"""Oportunidades de receita — agregados determinísticos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

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
class OportunidadeReceita:
    key: str
    titulo: str
    quantidade: int
    detalhe: str
    url: str
    tom: str  # oportunidade | atencao | risco


@dataclass(frozen=True)
class PainelOportunidades:
    itens: tuple[OportunidadeReceita, ...]
    mensagem: str


def listar_oportunidades(user, *, organization=None, dias_lead_parado: int = 7) -> PainelOportunidades:
    hoje = timezone.localdate()
    agora = timezone.now()
    itens: list[OportunidadeReceita] = []

    propostas = clientes_da_organizacao(organization).filter(fase_funil__in=FASES_PROPOSTA)
    com_fu = (
        compromissos_da_organizacao(organization).filter(
            cliente_id__in=propostas.values("pk"),
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
    )
    q_prop = propostas.exclude(id__in=com_fu).count()
    if q_prop:
        itens.append(
            OportunidadeReceita(
                key="propostas_aguardando",
                titulo="Propostas aguardando decisão",
                quantidade=q_prop,
                detalhe="Requer acompanhamento — sem follow-up futuro agendado",
                url=reverse("clientes"),
                tom="oportunidade",
            )
        )

    # Consultas realizadas sem próximo compromisso
    ids_consulta = list(
        compromissos_da_organizacao(organization).filter(
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            cliente__isnull=False,
        )
        .values_list("cliente_id", flat=True)
        .distinct()[:300]
    )
    futuros = set(
        compromissos_da_organizacao(organization).filter(
            cliente_id__in=ids_consulta,
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
    )
    sem_fu_consulta = 0
    for cid in ids_consulta:
        if cid in futuros:
            continue
        cli = clientes_da_organizacao(organization).filter(pk=cid).only("fase_funil", "status").first()
        if not cli or cli.fase_funil in FASES_PROPOSTA or cli.status == "ativo":
            continue
        sem_fu_consulta += 1
    if sem_fu_consulta:
        itens.append(
            OportunidadeReceita(
                key="consultas_sem_followup",
                titulo="Consultas sem follow-up",
                quantidade=sem_fu_consulta,
                detalhe="Potencial — consulta feita sem próximo passo registrado",
                url=reverse("agenda") + "?view=lista",
                tom="atencao",
            )
        )

    # Leads em primeiro contato sem consulta agendada
    leads_sem_agenda = clientes_da_organizacao(organization).filter(
        status="em_prospeccao",
        fase_funil="primeiro_contato",
    ).exclude(
        id__in=compromissos_da_organizacao(organization).filter(
            tipo=TipoCompromisso.CONSULTA,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values("cliente_id")
    )
    q_leads = leads_sem_agenda.count()
    if q_leads:
        itens.append(
            OportunidadeReceita(
                key="leads_sem_agendamento",
                titulo="Leads sem agendamento",
                quantidade=q_leads,
                detalhe="Oportunidade — ainda sem consulta vinculada",
                url=reverse("clientes"),
                tom="oportunidade",
            )
        )

    vencidas = (
        queryset_anotado_organization(organization)
        .exclude(status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT, StatusCobranca.PAID])
        .filter(data_vencimento__lt=hoje, saldo_calc__gt=0)
        .count()
    )
    if vencidas:
        itens.append(
            OportunidadeReceita(
                key="cobrancas_vencidas",
                titulo="Cobranças vencidas",
                quantidade=vencidas,
                detalhe="Requer acompanhamento financeiro",
                url=reverse("financeiro_cobranca_listar") + "?status=overdue",
                tom="risco",
            )
        )

    # Clientes ativos sem contato recente (relacionamento)
    limite = agora - timedelta(days=45)
    ativos_sem_contato = (
        clientes_da_organizacao(organization).filter(status="ativo")
        .exclude(
            id__in=compromissos_da_organizacao(organization).filter(data_hora__gte=limite).values(
                "cliente_id"
            )
        )
        .count()
    )
    if ativos_sem_contato:
        itens.append(
            OportunidadeReceita(
                key="relacionamento",
                titulo="Clientes aptos a ação de relacionamento",
                quantidade=ativos_sem_contato,
                detalhe="Potencial — sem compromisso nos últimos 45 dias",
                url=reverse("clientes"),
                tom="oportunidade",
            )
        )

    mensagem = ""
    if not itens:
        mensagem = "Nenhuma oportunidade comercial destacada no momento."

    return PainelOportunidades(itens=tuple(itens), mensagem=mensagem)
