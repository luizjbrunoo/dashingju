"""Tarefas assíncronas do financeiro (Django-Q)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def sincronizar_lembretes_cobrancas_abertas() -> int:
    """Discovery global; cada cobrança valida Organization antes do side effect."""
    from financeiro.choices import StatusCobranca
    from financeiro.models import Cobranca
    from financeiro.services.cobranca_agenda import sincronizar_lembrete_cobranca

    total = 0
    for cobranca in Cobranca.objects.exclude(
        status__in=(StatusCobranca.CANCELED, StatusCobranca.DRAFT, StatusCobranca.PAID)
    ).iterator():
        sincronizar_lembrete_cobranca(cobranca)
        total += 1
    logger.info("Financeiro: %s cobrança(s) varrida(s) para lembrete.", total)
    return total
