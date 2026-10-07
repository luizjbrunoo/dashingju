"""Inteligência da carteira — oportunidades na base (somente dados registrados)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from comercial.services.periodo import datetime_inicio
from usuarios.choices import StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa
from usuarios.services.org_scope import (
    clientes_da_organizacao,
    compromissos_da_organizacao,
    tarefas_da_organizacao,
)


@dataclass(frozen=True)
class ItemCarteira:
    key: str
    titulo: str
    quantidade: int
    detalhe: str
    url: str


@dataclass(frozen=True)
class InteligenciaCarteira:
    itens: tuple[ItemCarteira, ...]
    mensagem: str


def oportunidades_na_base(user, *, organization=None) -> InteligenciaCarteira:
    """
    Não infere necessidade jurídica sensível.
    Apenas organiza informações já registradas no CRM/Agenda.
    """
    agora = timezone.now()
    hoje = timezone.localdate()
    itens: list[ItemCarteira] = []

    ativos = clientes_da_organizacao(organization).filter(status="ativo")

    limite_60 = agora - timedelta(days=60)
    sem_contato = ativos.exclude(
        id__in=compromissos_da_organizacao(organization).filter(data_hora__gte=limite_60).values(
            "cliente_id"
        )
    ).count()
    if sem_contato:
        itens.append(
            ItemCarteira(
                key="sem_contato_pos",
                titulo="Clientes sem contato pós-atendimento",
                quantidade=sem_contato,
                detalhe="Ativos sem compromisso nos últimos 60 dias — potencial de relacionamento",
                url=reverse("clientes"),
            )
        )

    ids_consulta = set(
        compromissos_da_organizacao(organization).filter(
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora__lt=agora - timedelta(days=14),
            cliente__status="ativo",
        ).values_list("cliente_id", flat=True)
    )
    ids_avaliacao = set(
        tarefas_da_organizacao(organization).filter(
            cliente_id__in=list(ids_consulta)[:500],
            titulo__icontains="avalia",
        ).values_list("cliente_id", flat=True)
    )
    sem_aval = len(ids_consulta - ids_avaliacao)
    if sem_aval:
        itens.append(
            ItemCarteira(
                key="sem_avaliacao",
                titulo="Atendimento sem solicitação de avaliação",
                quantidade=sem_aval,
                detalhe="Clientes ativos com consulta realizada e sem tarefa de avaliação registrada",
                url=reverse("agenda") + "?view=lista",
            )
        )

    tarefas_rel = tarefas_da_organizacao(organization).filter(
        status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
        cliente__status="ativo",
    ).count()
    if tarefas_rel:
        itens.append(
            ItemCarteira(
                key="tarefas_relacionamento",
                titulo="Relacionamento que necessita acompanhamento",
                quantidade=tarefas_rel,
                detalhe="Tarefas pendentes vinculadas a clientes ativos",
                url=reverse("agenda") + "?view=lista",
            )
        )

    limite_90 = hoje - timedelta(days=90)
    aptos = ativos.filter(criado_em__lt=datetime_inicio(limite_90)).count()
    if aptos:
        itens.append(
            ItemCarteira(
                key="aptos_indicacao",
                titulo="Clientes potencialmente aptos a indicação/relacionamento",
                quantidade=aptos,
                detalhe="Base ativa com mais de 90 dias de cadastro — oportunidade de relacionamento",
                url=reverse("clientes"),
            )
        )

    mensagem = ""
    if not itens:
        mensagem = (
            "Nenhuma oportunidade na carteira identificada com os dados registrados."
        )

    return InteligenciaCarteira(itens=tuple(itens), mensagem=mensagem)
