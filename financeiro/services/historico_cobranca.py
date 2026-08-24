"""Registro de eventos e helpers de histórico de cobranças."""

from __future__ import annotations

from financeiro.choices import AcaoCobrancaHistorico
from financeiro.models import CobrancaHistorico


def registrar_historico_cobranca(
    cobranca,
    acao: str,
    *,
    descricao: str = "",
    autor=None,
    metadados=None,
) -> CobrancaHistorico:
    return CobrancaHistorico.objects.create(
        cobranca=cobranca,
        usuario=cobranca.usuario,
        acao=acao,
        descricao=descricao,
        autor=autor,
        metadados=metadados or {},
    )
