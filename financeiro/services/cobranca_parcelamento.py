"""Geração de cobranças parceladas."""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction

from financeiro.choices import AcaoCobrancaHistorico, PeriodicidadeParcela, StatusCobranca
from financeiro.models import Cobranca, novo_grupo_parcelamento
from financeiro.services.cobranca_crud import criar_cobranca
from financeiro.services.cobranca_agenda import (
    garantir_cron_lembretes_cobrancas,
    sincronizar_lembrete_cobranca,
)
from financeiro.services.historico_cobranca import registrar_historico_cobranca

MAX_PARCELAS = 60


def calcular_valores_parcelas(valor_total: Decimal, num_parcelas: int) -> list[Decimal]:
    """Divide o valor; a última parcela absorve centavos restantes."""
    if num_parcelas < 2:
        raise ValidationError("Parcelamento exige pelo menos 2 parcelas.")
    if num_parcelas > MAX_PARCELAS:
        raise ValidationError(f"Máximo de {MAX_PARCELAS} parcelas.")
    if valor_total <= 0:
        raise ValidationError("O valor total deve ser maior que zero.")

    parcela_base = (valor_total / num_parcelas).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    valores = [parcela_base] * (num_parcelas - 1)
    valores.append(valor_total - parcela_base * (num_parcelas - 1))
    return valores


def calcular_vencimentos_parcelas(
    primeiro_vencimento,
    num_parcelas: int,
    periodicidade: str,
) -> list:
    if periodicidade not in PeriodicidadeParcela.values:
        raise ValidationError("Periodicidade inválida.")
    datas = []
    for i in range(num_parcelas):
        if periodicidade == PeriodicidadeParcela.QUINZENAL:
            datas.append(primeiro_vencimento + timedelta(days=14 * i))
        else:
            datas.append(primeiro_vencimento + relativedelta(months=i))
    return datas


def parcelas_do_grupo(cobranca: Cobranca):
    if not cobranca.grupo_parcelamento_id:
        return Cobranca.objects.none()
    return (
        Cobranca.objects.filter(
            usuario=cobranca.usuario,
            grupo_parcelamento_id=cobranca.grupo_parcelamento_id,
        )
        .select_related("cliente", "responsavel")
        .order_by("parcela_numero", "id")
    )


@transaction.atomic
def criar_cobrancas_parceladas(
    *,
    usuario,
    autor,
    cliente,
    descricao: str,
    valor_total: Decimal,
    primeiro_vencimento,
    num_parcelas: int,
    periodicidade: str,
    categoria: str,
    responsavel=None,
    contrato_referencia: str = "",
    contrato=None,
    forma_prevista_pagamento: str = "",
    observacoes_internas: str = "",
    salvar_como_rascunho: bool = False,
) -> list[Cobranca]:
    valores = calcular_valores_parcelas(valor_total, num_parcelas)
    vencimentos = calcular_vencimentos_parcelas(
        primeiro_vencimento, num_parcelas, periodicidade
    )
    grupo_id = novo_grupo_parcelamento()
    status_inicial = StatusCobranca.DRAFT if salvar_como_rascunho else StatusCobranca.PENDING

    parcelas: list[Cobranca] = []
    for numero, (valor, vencimento) in enumerate(zip(valores, vencimentos), start=1):
        cobranca = Cobranca(
            usuario=usuario,
            cliente=cliente,
            contrato=contrato,
            contrato_referencia=contrato.referencia if contrato else contrato_referencia,
            descricao=descricao,
            valor_original=valor,
            data_vencimento=vencimento,
            categoria=categoria,
            status=status_inicial,
            responsavel=responsavel or autor,
            forma_prevista_pagamento=forma_prevista_pagamento,
            observacoes_internas=observacoes_internas,
            grupo_parcelamento_id=grupo_id,
            parcela_numero=numero,
            parcela_total=num_parcelas,
            criado_por=autor,
        )
        criar_cobranca(cobranca, autor=autor)
        parcelas.append(cobranca)

    garantir_cron_lembretes_cobrancas()
    registrar_historico_cobranca(
        parcelas[0],
        AcaoCobrancaHistorico.PARCELAMENTO_CRIADO,
        descricao=(
            f"Parcelamento de {num_parcelas}x criado "
            f"(total R$ {valor_total:.2f}, {PeriodicidadeParcela(periodicidade).label.lower()})."
        ),
        autor=autor,
        metadados={
            "grupo_parcelamento_id": str(grupo_id),
            "num_parcelas": num_parcelas,
            "valor_total": str(valor_total),
            "periodicidade": periodicidade,
            "cobranca_ids": [p.pk for p in parcelas],
        },
    )
    return parcelas
