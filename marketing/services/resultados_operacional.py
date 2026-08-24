"""Querysets e links operacionais — leads sem ação e follow-ups (Fase 10)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.db.models import Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from marketing.definitions import FASES_PROPOSTA
from marketing.services.atribuicao import clientes_google_ads
from marketing.services.periodo import PeriodoMarketing, datetime_inicio
from usuarios.choices import StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa
MKT_SEM_ACAO = "sem_acao"
MKT_FOLLOWUP_ATRASADO = "followup_atrasado"

_FILTROS_MKT = frozenset({MKT_SEM_ACAO, MKT_FOLLOWUP_ATRASADO})


def queryset_leads_sem_proxima_acao(user, periodo: PeriodoMarketing) -> QuerySet[Cliente]:
    """Leads Google Ads do período sem compromisso futuro nem tarefa pendente."""
    agora = timezone.now()
    hoje = timezone.localdate()
    qs = clientes_google_ads(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )
    sem_compromisso = qs.exclude(
        id__in=Compromisso.objects.filter(
            user=user,
            cliente_id__in=qs.values("pk"),
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values("cliente_id")
    )
    return (
        sem_compromisso.exclude(
            id__in=Tarefa.objects.filter(
                user=user,
                cliente_id__in=sem_compromisso.values("pk"),
                status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
                prazo__gte=hoje,
            ).values("cliente_id")
        )
        .order_by("nome")
    )


def queryset_followups_atrasados(user, periodo: PeriodoMarketing) -> QuerySet[Cliente]:
    """Follow-ups vencidos ou leads em proposta sem follow-up futuro (Google Ads)."""
    hoje = timezone.localdate()
    hoje_inicio = datetime_inicio(hoje)
    leads_qs = clientes_google_ads(
        user, data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )
    ids_sub = leads_qs.values("pk")

    com_followup_vencido = Compromisso.objects.filter(
        user=user,
        cliente_id__in=ids_sub,
        tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
        data_hora__lt=hoje_inicio,
    ).exclude(
        status__in=(StatusCompromisso.REALIZADO, StatusCompromisso.CANCELADO)
    )

    proposta_sem_followup = Cliente.objects.filter(
        pk__in=ids_sub,
        fase_funil__in=FASES_PROPOSTA,
    ).exclude(
        id__in=Compromisso.objects.filter(
            user=user,
            cliente_id__in=ids_sub,
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora__gte=hoje_inicio,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values("cliente_id")
    )

    return (
        Cliente.objects.filter(user=user)
        .filter(
            Q(pk__in=com_followup_vencido.values("cliente_id"))
            | Q(pk__in=proposta_sem_followup)
        )
        .distinct()
        .order_by("nome")
    )

def aplicar_filtro_mkt_clientes(
    user,
    qs: QuerySet[Cliente],
    mkt: str,
    periodo: PeriodoMarketing,
) -> QuerySet[Cliente]:
    """Restringe queryset de clientes ao filtro operacional de marketing."""
    if mkt == MKT_SEM_ACAO:
        ids = queryset_leads_sem_proxima_acao(user, periodo).values_list("pk", flat=True)
        return qs.filter(pk__in=ids)
    if mkt == MKT_FOLLOWUP_ATRASADO:
        ids = queryset_followups_atrasados(user, periodo).values_list("pk", flat=True)
        return qs.filter(pk__in=ids)
    return qs


def rotulo_filtro_mkt(mkt: str, periodo: PeriodoMarketing | None) -> str:
    if mkt == MKT_SEM_ACAO:
        base = "Leads Google Ads sem próxima ação"
    elif mkt == MKT_FOLLOWUP_ATRASADO:
        base = "Follow-ups atrasados (Google Ads)"
    else:
        return ""
    if periodo:
        return f"{base} · {periodo.label()}"
    return base


def periodo_from_request_get(get_params) -> PeriodoMarketing:
    from django.utils.dateparse import parse_date

    dias_raw = (get_params.get("dias") or "30").strip()
    try:
        dias = int(dias_raw)
    except ValueError:
        dias = 30
    dias = max(7, min(dias, 90))
    data_inicio = parse_date((get_params.get("data_inicio") or "").strip())
    data_fim = parse_date((get_params.get("data_fim") or "").strip())
    return PeriodoMarketing.from_parametros(
        data_inicio, data_fim, padrao_dias=dias
    )


def url_lista_clientes_mkt(periodo: PeriodoMarketing, mkt: str) -> str:
    params = {
        "mkt": mkt,
        "dias": str(periodo.dias),
    }
    return f"{reverse('clientes')}?{urlencode(params)}"


@dataclass(frozen=True)
class LinksOperacionais:
    leads_sem_acao: str | None
    followups_atrasados: str | None
    leads_sem_acao_qtd: int
    followups_atrasados_qtd: int


def links_operacionais(
    user,
    periodo: PeriodoMarketing,
    *,
    leads_sem_acao: int,
    followups_atrasados: int,
) -> LinksOperacionais:
    """URLs para listagens filtradas; None quando não há itens para ver."""
    return LinksOperacionais(
        leads_sem_acao=(
            url_lista_clientes_mkt(periodo, MKT_SEM_ACAO) if leads_sem_acao > 0 else None
        ),
        followups_atrasados=(
            url_lista_clientes_mkt(periodo, MKT_FOLLOWUP_ATRASADO)
            if followups_atrasados > 0
            else None
        ),
        leads_sem_acao_qtd=leads_sem_acao,
        followups_atrasados_qtd=followups_atrasados,
    )


def filtro_mkt_valido(mkt: str) -> bool:
    return mkt in _FILTROS_MKT
