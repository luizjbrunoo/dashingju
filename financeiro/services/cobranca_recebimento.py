"""Registro e estorno de recebimentos de cobranças."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError

from financeiro.choices import AcaoCobrancaHistorico, StatusCobranca
from financeiro.models import CobrancaRecebimento
from financeiro.services.cobrancas import saldo_cobranca
from financeiro.services.cobranca_agenda import cancelar_lembrete_cobranca, sincronizar_lembrete_cobranca
from financeiro.services.historico_cobranca import registrar_historico_cobranca


def validar_valor_recebimento(cobranca, valor: Decimal) -> None:
    if valor <= 0:
        raise ValidationError("O valor recebido deve ser maior que zero.")
    if cobranca.status == StatusCobranca.CANCELED:
        raise ValidationError("Não é possível registrar recebimento em cobrança cancelada.")
    if cobranca.status == StatusCobranca.DRAFT:
        raise ValidationError("Confirme a cobrança antes de registrar recebimentos.")
    saldo = saldo_cobranca(cobranca)
    if valor > saldo:
        raise ValidationError(
            f"O valor recebido (R$ {valor}) excede o saldo em aberto (R$ {saldo})."
        )


def registrar_recebimento(
    cobranca,
    *,
    valor: Decimal,
    data_recebimento,
    forma_pagamento: str,
    referencia: str = "",
    observacao: str = "",
    autor,
) -> CobrancaRecebimento:
    validar_valor_recebimento(cobranca, valor)

    recebimento = CobrancaRecebimento.objects.create(
        cobranca=cobranca,
        usuario=cobranca.usuario,
        valor=valor,
        data_recebimento=data_recebimento,
        forma_pagamento=forma_pagamento,
        referencia=referencia,
        observacao=observacao,
        registrado_por=autor,
    )
    cobranca.atualizar_status(salvar=True)
    if cobranca.saldo <= 0:
        cancelar_lembrete_cobranca(cobranca)
    else:
        sincronizar_lembrete_cobranca(cobranca)
    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.RECEBIMENTO_REGISTRADO,
        descricao=f"Recebimento de R$ {valor:.2f} registrado.",
        autor=autor,
        metadados={
            "recebimento_id": recebimento.pk,
            "valor": str(valor),
            "data_recebimento": data_recebimento.isoformat(),
            "forma_pagamento": forma_pagamento,
        },
    )
    return recebimento


def estornar_recebimento(
    recebimento: CobrancaRecebimento,
    *,
    autor,
    motivo: str = "",
) -> CobrancaRecebimento:
    if not recebimento.ativo:
        raise ValidationError("Este recebimento já foi estornado.")
    cobranca = recebimento.cobranca
    if cobranca.status == StatusCobranca.CANCELED:
        raise ValidationError("Não é possível estornar recebimento de cobrança cancelada.")

    valor = recebimento.valor
    recebimento.estornar(motivo=motivo)
    cobranca.atualizar_status(salvar=True)
    sincronizar_lembrete_cobranca(cobranca)
    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.RECEBIMENTO_ESTORNADO,
        descricao=f"Estorno de R$ {valor:.2f}. {motivo}".strip(),
        autor=autor,
        metadados={
            "recebimento_id": recebimento.pk,
            "valor": str(valor),
            "motivo": motivo,
        },
    )
    return recebimento
