"""Operações de criação, edição e cancelamento de cobranças."""

from __future__ import annotations

from financeiro.choices import AcaoCobrancaHistorico, StatusCobranca
from financeiro.models import Cobranca
from financeiro.services.historico_cobranca import registrar_historico_cobranca
from financeiro.services.cobranca_agenda import (
    cancelar_lembrete_cobranca,
    garantir_cron_lembretes_cobrancas,
    sincronizar_lembrete_cobranca,
)


def cobrancas_queryset(usuario):
    return (
        Cobranca.objects.filter(usuario=usuario)
        .select_related("cliente", "responsavel", "criado_por")
        .order_by("data_vencimento", "id")
    )


def criar_cobranca(cobranca: Cobranca, *, autor) -> Cobranca:
    if cobranca.status != StatusCobranca.DRAFT:
        cobranca.status = StatusCobranca.PENDING
    cobranca.save()
    cobranca.atualizar_status(salvar=True)
    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.CRIADA,
        descricao="Cobrança criada.",
        autor=autor,
    )
    sincronizar_lembrete_cobranca(cobranca)
    garantir_cron_lembretes_cobrancas()
    return cobranca


def atualizar_cobranca(
    cobranca: Cobranca,
    *,
    autor,
    vencimento_anterior=None,
    responsavel_anterior_id=None,
) -> Cobranca:
    if cobranca.status not in (StatusCobranca.CANCELED, StatusCobranca.DRAFT):
        cobranca.atualizar_status(salvar=True)

    if (
        vencimento_anterior is not None
        and vencimento_anterior != cobranca.data_vencimento
    ):
        registrar_historico_cobranca(
            cobranca,
            AcaoCobrancaHistorico.VENCIMENTO_ALTERADO,
            descricao=(
                f"Vencimento alterado de {vencimento_anterior:%d/%m/%Y} "
                f"para {cobranca.data_vencimento:%d/%m/%Y}."
            ),
            autor=autor,
            metadados={
                "vencimento_anterior": vencimento_anterior.isoformat(),
                "vencimento_novo": cobranca.data_vencimento.isoformat(),
            },
        )

    if (
        responsavel_anterior_id is not None
        and responsavel_anterior_id != cobranca.responsavel_id
    ):
        registrar_historico_cobranca(
            cobranca,
            AcaoCobrancaHistorico.RESPONSAVEL_ALTERADO,
            descricao="Responsável alterado.",
            autor=autor,
            metadados={
                "responsavel_anterior_id": responsavel_anterior_id,
                "responsavel_novo_id": cobranca.responsavel_id,
            },
        )

    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.EDITADA,
        descricao="Cobrança editada.",
        autor=autor,
    )
    if (
        vencimento_anterior is not None
        and vencimento_anterior != cobranca.data_vencimento
    ):
        sincronizar_lembrete_cobranca(cobranca)
    return cobranca


def cancelar_cobranca(cobranca: Cobranca, *, autor, motivo: str = "") -> Cobranca:
    cobranca.cancelar(motivo=motivo)
    cancelar_lembrete_cobranca(cobranca)
    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.CANCELADA,
        descricao=motivo or "Cobrança cancelada.",
        autor=autor,
        metadados={"motivo": motivo},
    )
    return cobranca
