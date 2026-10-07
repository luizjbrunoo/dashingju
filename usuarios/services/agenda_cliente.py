"""Agenda vinculada à ficha do cliente e hooks preparatórios CRM/Financeiro."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Case, IntegerField, QuerySet, Value, When
from django.urls import reverse
from django.utils import timezone

from usuarios.choices import StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa
from usuarios.br_format import format_currency_br

LIMITE_ITENS = 5
META_COBRANCA_ID = "cobranca_id"


@dataclass(frozen=True)
class ResumoAgendaCliente:
    compromissos_ativos: int
    tarefas_pendentes: int
    tarefas_atrasadas: int


@dataclass(frozen=True)
class SugestaoCrm:
    codigo: str
    titulo: str
    descricao: str
    url: str
    rotulo_acao: str


@dataclass(frozen=True)
class HookFinanceiroCliente:
    disponivel: bool
    mensagem: str
    url: str
    rotulo: str
    vencido: Decimal = field(default_factory=lambda: Decimal("0"))
    url_agenda_tarefa: str = ""


def _compromissos_cliente_qs(organization, cliente: Cliente) -> QuerySet[Compromisso]:
    if organization is None:
        return Compromisso.objects.none()
    return (
        Compromisso.objects.filter(organization=organization, cliente=cliente)
        .exclude(status=StatusCompromisso.CANCELADO)
        .select_related("responsavel")
        .order_by("data_hora")
    )


def _tarefas_cliente_qs(organization, cliente: Cliente) -> QuerySet[Tarefa]:
    if organization is None:
        return Tarefa.objects.none()
    return (
        Tarefa.objects.filter(organization=organization, cliente=cliente)
        .exclude(status=StatusTarefa.CANCELADA)
        .select_related("responsavel")
        .order_by("prazo", "criado_em")
    )


def resumo_agenda_cliente(organization, cliente: Cliente) -> ResumoAgendaCliente:
    agora = timezone.now()
    hoje = timezone.localdate()
    pendentes = _tarefas_cliente_qs(organization, cliente).filter(
        status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO)
    )
    return ResumoAgendaCliente(
        compromissos_ativos=_compromissos_cliente_qs(organization, cliente)
        .filter(data_hora__gte=agora)
        .count(),
        tarefas_pendentes=pendentes.count(),
        tarefas_atrasadas=pendentes.filter(prazo__lt=hoje).count(),
    )


def compromissos_cliente(organization, cliente: Cliente, limit: int = LIMITE_ITENS):
    agora = timezone.now()
    return (
        _compromissos_cliente_qs(organization, cliente)
        .filter(data_hora__gte=agora)
        .order_by("data_hora")[:limit]
    )


def tarefas_cliente(organization, cliente: Cliente, limit: int = LIMITE_ITENS):
    hoje = timezone.localdate()
    return (
        _tarefas_cliente_qs(organization, cliente)
        .filter(status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO))
        .annotate(
            atrasada_ordem=Case(
                When(prazo__lt=hoje, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("atrasada_ordem", "prazo", "criado_em")[:limit]
    )


def url_agenda_cliente(
    cliente_id: int,
    *,
    modal: str = "",
    view: str = "lista",
    tipo: str = "",
) -> str:
    params: dict[str, str] = {"cliente": str(cliente_id), "view": view}
    if modal:
        params["modal"] = modal
    if tipo:
        params["tipo"] = tipo
    return f"{reverse('agenda')}?{urlencode(params)}"


def _cobrancas_vencidas_cliente(organization, cliente: Cliente):
    from financeiro.choices import StatusCobranca
    from financeiro.models import Cobranca

    if organization is None:
        return Cobranca.objects.none()
    return Cobranca.objects.filter(
        organization=organization,
        cliente=cliente,
        status=StatusCobranca.OVERDUE,
    ).order_by("data_vencimento")


def _tem_followup_cobranca_pendente(organization, cliente: Cliente) -> bool:
    if organization is None:
        return False
    return Tarefa.objects.filter(
        organization=organization,
        cliente=cliente,
        status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
        metadados__has_key=META_COBRANCA_ID,
    ).exists()


def sugestoes_crm_cliente(organization, cliente: Cliente) -> list[SugestaoCrm]:
    sugestoes: list[SugestaoCrm] = []
    cid = cliente.pk
    tem_itens = (
        _compromissos_cliente_qs(organization, cliente).exists()
        or _tarefas_cliente_qs(organization, cliente).exists()
    )

    if cliente.status == "em_prospeccao":
        if cliente.fase_funil in {"aguardando_decisao", "proposta_enviada"}:
            sugestoes.append(
                SugestaoCrm(
                    codigo="followup_prospeccao",
                    titulo="Agendar follow-up comercial",
                    descricao=(
                        "Cliente em prospecção aguardando retorno — "
                        "registre um compromisso de follow-up."
                    ),
                    url=url_agenda_cliente(
                        cid,
                        modal="compromisso",
                        tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
                    ),
                    rotulo_acao="Criar follow-up",
                )
            )
        if not tem_itens:
            sugestoes.append(
                SugestaoCrm(
                    codigo="primeiro_contato_agenda",
                    titulo="Vincular agenda ao cliente",
                    descricao=(
                        "Nenhum compromisso ou tarefa vinculado — "
                        "organize o próximo passo na agenda."
                    ),
                    url=url_agenda_cliente(cid, modal="compromisso"),
                    rotulo_acao="Novo compromisso",
                )
            )
    elif cliente.status == "ativo":
        if not _compromissos_cliente_qs(organization, cliente).filter(
            data_hora__gte=timezone.now()
        ).exists():
            sugestoes.append(
                SugestaoCrm(
                    codigo="retorno_cliente_ativo",
                    titulo="Agendar retorno ao cliente",
                    descricao="Cliente ativo sem compromissos futuros na agenda.",
                    url=url_agenda_cliente(cid, modal="compromisso"),
                    rotulo_acao="Agendar retorno",
                )
            )

    cobrancas_vencidas = _cobrancas_vencidas_cliente(organization, cliente)
    if cobrancas_vencidas.exists() and not _tem_followup_cobranca_pendente(
        organization, cliente
    ):
        from financeiro.services.cliente_financeiro import (
            resumo_financeiro_cliente_organization,
        )

        resumo_fin = resumo_financeiro_cliente_organization(organization, cliente)
        sugestoes.append(
            SugestaoCrm(
                codigo="followup_cobranca",
                titulo="Registrar follow-up de cobrança",
                descricao=(
                    f"Existem cobranças vencidas ({format_currency_br(resumo_fin.vencido)}) — "
                    "crie uma tarefa de cobrança na agenda."
                ),
                url=url_agenda_cliente(cid, modal="tarefa"),
                rotulo_acao="Criar tarefa",
            )
        )

    return sugestoes


def hook_financeiro_cliente(organization, cliente: Cliente) -> HookFinanceiroCliente:
    from financeiro.services.cliente_financeiro import (
        resumo_financeiro_cliente_organization,
        url_cobrancas_cliente,
    )

    resumo = resumo_financeiro_cliente_organization(organization, cliente)
    url_tarefa = ""
    if resumo.vencido > 0:
        url_tarefa = url_agenda_cliente(cliente.pk, modal="tarefa")
    return HookFinanceiroCliente(
        disponivel=True,
        mensagem=(
            f"A receber: {format_currency_br(resumo.a_receber)} · "
            f"{resumo.cobrancas_abertas} cobrança(s) em aberto."
        ),
        url=url_cobrancas_cliente(cliente.pk),
        rotulo="Ver cobranças",
        vencido=resumo.vencido,
        url_agenda_tarefa=url_tarefa,
    )
