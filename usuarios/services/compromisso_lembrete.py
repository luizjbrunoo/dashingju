"""Agendamento de lembretes de compromissos via Django-Q."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from usuarios.choices import LembreteMinutos, StatusCompromisso
from usuarios.models import Compromisso

logger = logging.getLogger(__name__)


def rotulo_lembrete(minutos: int | None) -> str:
    if not minutos:
        return ""
    for valor, rotulo in LembreteMinutos.choices:
        if valor == minutos:
            return rotulo
    return f"{minutos} min antes"


def _nome_schedule(compromisso_id: int) -> str:
    return f"agenda-lembrete-{compromisso_id}"


def cancelar_lembrete_compromisso(compromisso: Compromisso) -> None:
    try:
        from django_q.models import Schedule
    except ModuleNotFoundError:
        return

    Schedule.objects.filter(name=_nome_schedule(compromisso.pk)).delete()


def sincronizar_lembrete_compromisso(compromisso: Compromisso) -> None:
    cancelar_lembrete_compromisso(compromisso)

    if not compromisso.lembrete_minutos:
        return
    if compromisso.status == StatusCompromisso.CANCELADO:
        return

    disparo = compromisso.data_hora - timedelta(minutes=compromisso.lembrete_minutos)
    if disparo <= timezone.now():
        return

    try:
        from django_q.models import Schedule
        from django_q.tasks import schedule
    except ModuleNotFoundError:
        logger.warning("Django-Q indisponível; lembrete não agendado.")
        return

    try:
        schedule(
            "usuarios.tasks.disparar_lembrete_compromisso",
            compromisso.pk,
            name=_nome_schedule(compromisso.pk),
            schedule_type=Schedule.ONCE,
            next_run=disparo,
        )
    except Exception:
        logger.exception(
            "Falha ao agendar lembrete Django-Q para Compromisso id=%s",
            compromisso.pk,
        )


def sincronizar_lembretes_serie(compromissos) -> None:
    for compromisso in compromissos:
        sincronizar_lembrete_compromisso(compromisso)


def garantir_cron_series_recorrentes() -> None:
    """Registra tarefa diária para manter séries recorrentes."""
    try:
        from django_q.models import Schedule
        from django_q.tasks import schedule
    except ModuleNotFoundError:
        return

    func = "usuarios.tasks.manter_series_recorrentes"
    if Schedule.objects.filter(func=func).exists():
        return

    try:
        schedule(
            func,
            name="agenda-manter-series",
            schedule_type=Schedule.DAILY,
            repeats=-1,
        )
    except Exception:
        logger.exception("Falha ao registrar cron de séries recorrentes.")
