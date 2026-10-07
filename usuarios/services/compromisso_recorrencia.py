"""Geração de ocorrências recorrentes de compromissos."""

from __future__ import annotations

import calendar
import copy
import logging
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from usuarios.choices import Recorrencia, StatusCompromisso
from usuarios.models import Compromisso, CompromissoParticipante

logger = logging.getLogger(__name__)

OCORRENCIAS_PADRAO = 3
FUTUROS_MINIMOS = 2
REASON_MISSING_ORGANIZATION = "MISSING_ORGANIZATION"
REASON_CLIENT_ORGANIZATION_CONFLICT = "CLIENT_ORGANIZATION_CONFLICT"
REASON_RESPONSAVEL_NOT_IN_ORG = "RESPONSAVEL_NOT_IN_ORG"


def _chave_serie_raiz() -> str:
    return "serie_raiz_id"


def id_serie_raiz(compromisso: Compromisso) -> int | None:
    if compromisso.recorrencia != Recorrencia.NAO_REPETIR:
        return compromisso.pk
    valor = (compromisso.metadados or {}).get(_chave_serie_raiz())
    return int(valor) if valor else None


def deslocar_data_recorrencia(
    momento: datetime, recorrencia: str, passos: int = 1
) -> datetime:
    if recorrencia == Recorrencia.SEMANAL:
        return momento + timedelta(weeks=passos)
    if recorrencia == Recorrencia.QUINZENAL:
        return momento + timedelta(weeks=2 * passos)
    if recorrencia == Recorrencia.MENSAL:
        return _somar_meses(momento, passos)
    return momento


def _somar_meses(dt: datetime, meses: int) -> datetime:
    mes = dt.month - 1 + meses
    ano = dt.year + mes // 12
    mes = mes % 12 + 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    dia = min(dt.day, ultimo_dia)
    return dt.replace(year=ano, month=mes, day=dia)


def _duracao_compromisso(compromisso: Compromisso) -> timedelta | None:
    if compromisso.data_hora_fim:
        return compromisso.data_hora_fim - compromisso.data_hora
    return None


def _log_skip_serie(compromisso: Compromisso, reason: str) -> None:
    logger.info("skip model=Compromisso pk=%s reason=%s", compromisso.pk, reason)


def _motivo_bloqueio_serie(origem: Compromisso) -> str | None:
    if origem.organization_id is None:
        return REASON_MISSING_ORGANIZATION
    if origem.cliente_id:
        cliente = origem.cliente
        if cliente is None or cliente.organization_id != origem.organization_id:
            return REASON_CLIENT_ORGANIZATION_CONFLICT
    if origem.responsavel_id:
        from usuarios.services.agenda_equipe import responsavel_permitido

        if not responsavel_permitido(origem.organization, origem.responsavel):
            return REASON_RESPONSAVEL_NOT_IN_ORG
    return None


def _compromissos_da_serie(raiz_id: int, organization=None):
    qs = Compromisso.objects.filter(
        Q(pk=raiz_id) | Q(metadados__serie_raiz_id=raiz_id)
    ).exclude(status=StatusCompromisso.CANCELADO)
    if organization is not None:
        qs = qs.filter(organization=organization)
    return qs


def _clonar_compromisso(
    origem: Compromisso,
    data_hora: datetime,
    *,
    raiz_id: int,
    recorrencia: str = Recorrencia.NAO_REPETIR,
) -> Compromisso:
    duracao = _duracao_compromisso(origem)
    data_hora_fim = data_hora + duracao if duracao else None
    metadados = copy.deepcopy(origem.metadados or {})
    metadados[_chave_serie_raiz()] = raiz_id

    clone = Compromisso(
        user=origem.user,
        organization=origem.organization,
        titulo=origem.titulo,
        descricao=origem.descricao,
        tipo=origem.tipo,
        status=StatusCompromisso.AGENDADO,
        prioridade=origem.prioridade,
        data_hora=data_hora,
        data_hora_fim=data_hora_fim,
        cliente=origem.cliente,
        responsavel=origem.responsavel,
        processo_referencia=origem.processo_referencia,
        prazo_oficial=origem.prazo_oficial,
        prazo_interno=origem.prazo_interno,
        area_juridica=origem.area_juridica,
        recorrencia=recorrencia,
        lembrete_minutos=origem.lembrete_minutos,
        metadados=metadados,
    )
    clone.save()
    for participante in origem.participantes.all():
        CompromissoParticipante.objects.get_or_create(
            compromisso=clone,
            usuario=participante.usuario,
        )
    return clone


def gerar_ocorrencias_serie(
    compromisso: Compromisso, quantidade: int = OCORRENCIAS_PADRAO
) -> list[Compromisso]:
    """Gera cópias futuras a partir de um compromisso recorrente."""
    if compromisso.recorrencia == Recorrencia.NAO_REPETIR:
        return []
    motivo = _motivo_bloqueio_serie(compromisso)
    if motivo:
        _log_skip_serie(compromisso, motivo)
        return []

    metadados = copy.deepcopy(compromisso.metadados or {})
    metadados[_chave_serie_raiz()] = compromisso.pk
    if compromisso.metadados != metadados:
        compromisso.metadados = metadados
        compromisso.save(update_fields=["metadados", "atualizado_em"])

    existentes = set(
        _compromissos_da_serie(
            compromisso.pk, organization=compromisso.organization
        ).values_list("data_hora", flat=True)
    )

    criados: list[Compromisso] = []
    momento = compromisso.data_hora
    for _passo in range(1, quantidade + 1):
        momento = deslocar_data_recorrencia(momento, compromisso.recorrencia, 1)
        if momento in existentes:
            continue
        clone = _clonar_compromisso(
            compromisso,
            momento,
            raiz_id=compromisso.pk,
        )
        criados.append(clone)
        existentes.add(momento)
    return criados


def manter_series_recorrentes() -> int:
    """Garante ocorrências futuras mínimas para séries recorrentes ativas."""
    agora = timezone.now()
    raizes = Compromisso.objects.filter(
        recorrencia__in=(
            Recorrencia.SEMANAL,
            Recorrencia.QUINZENAL,
            Recorrencia.MENSAL,
        )
    ).exclude(status=StatusCompromisso.CANCELADO)

    gerados = 0
    for raiz in raizes:
        motivo = _motivo_bloqueio_serie(raiz)
        if motivo:
            _log_skip_serie(raiz, motivo)
            continue

        serie = _compromissos_da_serie(raiz.pk, organization=raiz.organization)
        futuros = serie.filter(data_hora__gt=agora).count()
        faltam = max(0, FUTUROS_MINIMOS - futuros)
        if not faltam:
            continue

        ultimo = serie.order_by("-data_hora").first()
        if not ultimo:
            continue

        existentes = set(serie.values_list("data_hora", flat=True))
        momento = ultimo.data_hora
        seguranca = 0
        while (
            _compromissos_da_serie(raiz.pk, organization=raiz.organization)
            .filter(data_hora__gt=agora)
            .count()
            < FUTUROS_MINIMOS
            and seguranca < 52
        ):
            seguranca += 1
            momento = deslocar_data_recorrencia(momento, raiz.recorrencia, 1)
            if momento in existentes:
                continue
            _clonar_compromisso(raiz, momento, raiz_id=raiz.pk)
            existentes.add(momento)
            gerados += 1
    return gerados
