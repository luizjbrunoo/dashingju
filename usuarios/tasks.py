"""Tarefas assíncronas da agenda (Django-Q)."""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

REASON_MISSING_ORGANIZATION = "MISSING_ORGANIZATION"


def disparar_lembrete_compromisso(compromisso_id: int) -> None:
    from usuarios.choices import StatusCompromisso
    from usuarios.models import AgendaLembrete, Compromisso

    try:
        compromisso = Compromisso.objects.select_related("user").get(pk=compromisso_id)
    except Compromisso.DoesNotExist:
        return

    if compromisso.organization_id is None:
        logger.info(
            "skip model=Compromisso pk=%s reason=%s",
            compromisso.pk,
            REASON_MISSING_ORGANIZATION,
        )
        return

    if compromisso.status == StatusCompromisso.CANCELADO:
        return
    if compromisso.data_hora <= timezone.now():
        return

    when = timezone.localtime(compromisso.data_hora)
    AgendaLembrete.objects.create(
        user=compromisso.user,
        compromisso=compromisso,
        titulo=compromisso.titulo,
        mensagem=f"Compromisso em {when:%d/%m/%Y às %H:%M}.",
    )


def manter_series_recorrentes() -> int:
    from usuarios.services.compromisso_recorrencia import manter_series_recorrentes as _manter

    total = _manter()
    logger.info("Agenda: %s ocorrência(s) recorrente(s) gerada(s).", total)
    return total
