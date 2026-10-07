"""Geração de cobranças a partir de contratos."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from financeiro.choices import AcaoCobrancaHistorico, StatusContrato
from financeiro.models import Contrato
from financeiro.services.cobranca_crud import criar_cobranca
from financeiro.services.cobranca_parcelamento import criar_cobrancas_parceladas
from financeiro.services.historico_cobranca import registrar_historico_cobranca


def _validar_contrato_para_geracao(contrato: Contrato) -> None:
    if contrato.status == StatusContrato.CANCELED:
        raise ValidationError("Contrato cancelado não gera cobranças.")
    if contrato.cobrancas.exclude(status="canceled").exists():
        raise ValidationError(
            "Este contrato já possui cobranças vinculadas. "
            "Cancele-as antes de gerar novamente."
        )


@transaction.atomic
def gerar_cobrancas_do_contrato(
    contrato: Contrato,
    *,
    autor,
    organization,
    tipo_lancamento: str,
    primeiro_vencimento,
    categoria: str,
    descricao: str = "",
    num_parcelas: int | None = None,
    periodicidade: str = "mensal",
    forma_prevista_pagamento: str = "",
    observacoes_internas: str = "",
    salvar_como_rascunho: bool = False,
):
    from financeiro.tenancy_write import assert_parent_organization

    _validar_contrato_para_geracao(contrato)
    assert_parent_organization(contrato, organization)
    assert_parent_organization(contrato.cliente, organization)
    descricao_final = descricao or contrato.descricao

    if tipo_lancamento == "parcelada":
        if not num_parcelas or num_parcelas < 2:
            raise ValidationError("Informe pelo menos 2 parcelas.")
        parcelas = criar_cobrancas_parceladas(
            usuario=contrato.usuario,
            autor=autor,
            cliente=contrato.cliente,
            descricao=descricao_final,
            valor_total=contrato.valor_total,
            primeiro_vencimento=primeiro_vencimento,
            num_parcelas=num_parcelas,
            periodicidade=periodicidade,
            categoria=categoria,
            organization=organization,
            responsavel=contrato.responsavel,
            contrato=contrato,
            forma_prevista_pagamento=forma_prevista_pagamento,
            observacoes_internas=observacoes_internas,
            salvar_como_rascunho=salvar_como_rascunho,
        )
        _registrar_historico_geracao(contrato, parcelas, autor, tipo_lancamento)
        if contrato.status == StatusContrato.DRAFT:
            contrato.status = StatusContrato.ACTIVE
            contrato.save(update_fields=["status", "atualizado_em"])
        return parcelas

    from financeiro.models import Cobranca
    from financeiro.choices import StatusCobranca

    status_inicial = StatusCobranca.DRAFT if salvar_como_rascunho else StatusCobranca.PENDING
    cobranca = Cobranca(
        usuario=contrato.usuario,
        organization=organization,
        cliente=contrato.cliente,
        contrato=contrato,
        contrato_referencia=contrato.referencia,
        descricao=descricao_final,
        valor_original=contrato.valor_total,
        data_vencimento=primeiro_vencimento,
        categoria=categoria,
        status=status_inicial,
        responsavel=contrato.responsavel or autor,
        forma_prevista_pagamento=forma_prevista_pagamento,
        observacoes_internas=observacoes_internas,
        criado_por=autor,
    )
    criar_cobranca(cobranca, autor=autor, organization=organization)
    _registrar_historico_geracao(contrato, [cobranca], autor, tipo_lancamento)
    if contrato.status == StatusContrato.DRAFT:
        contrato.status = StatusContrato.ACTIVE
        contrato.save(update_fields=["status", "atualizado_em"])
    return [cobranca]


def _registrar_historico_geracao(contrato, cobrancas, autor, tipo_lancamento: str) -> None:
    registrar_historico_cobranca(
        cobrancas[0],
        AcaoCobrancaHistorico.COBRANCAS_GERADAS_CONTRATO,
        descricao=(
            f"{len(cobrancas)} cobrança(s) gerada(s) a partir do contrato "
            f"{contrato.referencia} ({tipo_lancamento})."
        ),
        autor=autor,
        metadados={
            "contrato_id": contrato.pk,
            "contrato_referencia": contrato.referencia,
            "tipo_lancamento": tipo_lancamento,
            "cobranca_ids": [c.pk for c in cobrancas],
        },
    )
