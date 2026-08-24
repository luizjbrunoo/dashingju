"""Auditoria e registro automático de eventos de cobrança."""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from financeiro.choices import AcaoCobrancaHistorico, StatusCobranca
from financeiro.models import CobrancaHistorico
from financeiro.services.historico_cobranca import registrar_historico_cobranca


def registrar_mudanca_status_automatica(cobranca, status_anterior: str) -> None:
    """Registra vencimento automático (uma vez por data de vencimento)."""
    if status_anterior == cobranca.status:
        return
    if cobranca.status != StatusCobranca.OVERDUE:
        return

    chave_venc = cobranca.data_vencimento.isoformat()
    if CobrancaHistorico.objects.filter(
        cobranca=cobranca,
        acao=AcaoCobrancaHistorico.VENCIDA,
        metadados__vencimento=chave_venc,
    ).exists():
        return

    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.VENCIDA,
        descricao=f"Cobrança vencida em {cobranca.data_vencimento:%d/%m/%Y}.",
        autor=None,
        metadados={
            "vencimento": chave_venc,
            "status_anterior": status_anterior,
            "origem": "automatico",
        },
    )


def historico_auditoria_usuario(usuario, *, limite: int = 100):
    return (
        CobrancaHistorico.objects.filter(usuario=usuario)
        .select_related("cobranca", "cobranca__cliente", "autor")
        .order_by("-criado_em")[:limite]
    )


def resumo_auditoria_usuario(usuario, *, hoje: date | None = None):
    hoje = hoje or timezone.localdate()
    qs = CobrancaHistorico.objects.filter(usuario=usuario, criado_em__date=hoje)
    return {
        "eventos_hoje": qs.count(),
        "total_registrado": CobrancaHistorico.objects.filter(usuario=usuario).count(),
    }
