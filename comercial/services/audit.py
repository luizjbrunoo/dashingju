"""Auditoria comercial."""

from __future__ import annotations

from comercial.models import ComercialAuditLog


def registrar_auditoria(usuario, *, ator, acao: str, detalhe: str = "") -> ComercialAuditLog:
    return ComercialAuditLog.objects.create(
        usuario=usuario,
        ator=ator,
        acao=acao,
        detalhe=detalhe[:2000],
    )
