"""Metas de faturamento e ticket médio."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Avg
from django.utils import timezone

from comercial.models import ComercialAuditLog, MetaComercial
from comercial.services.audit import registrar_auditoria
from comercial.services.finance_org import contratos_organization
from comercial.services.money import MIN_AMOSTRAS_CONVERSAO, money


@dataclass(frozen=True)
class TicketMedioInfo:
    valor: Decimal | None
    fonte: str  # "manual" | "contratos" | "indisponivel"
    amostra: int
    mensagem: str


@dataclass(frozen=True)
class MetaReversa:
    contratos_ano: int | None
    contratos_mes: int | None
    consultas_necessarias: int | None
    leads_necessarios: int | None
    dados_suficientes: bool
    mensagem: str


def meta_do_ano(user, ano: int | None = None, *, organization=None) -> MetaComercial | None:
    """Meta do escritório. Sem Organization = invisível (fail-closed). user é ator, não tenant."""
    del user
    ano = ano or timezone.localdate().year
    if organization is None:
        return None
    return MetaComercial.objects.filter(organization=organization, ano=ano).first()


def ticket_medio_contratos(organization, *, limite_amostra: int = 50) -> TicketMedioInfo:
    """Ticket médio a partir de contratos active/closed da Organization."""
    qs = contratos_organization(organization)
    amostra = qs.count()
    if amostra < 1:
        return TicketMedioInfo(
            valor=None,
            fonte="indisponivel",
            amostra=0,
            mensagem="Registre contratos para calcular o ticket médio.",
        )
    media = qs.aggregate(media=Avg("valor_total"))["media"]
    return TicketMedioInfo(
        valor=money(media),
        fonte="contratos",
        amostra=amostra,
        mensagem=f"Calculado a partir de {amostra} contrato(s).",
    )


def ticket_efetivo(user, meta: MetaComercial | None = None, *, organization=None) -> TicketMedioInfo:
    meta = meta if meta is not None else meta_do_ano(user, organization=organization)
    if meta and meta.ticket_medio_manual and meta.ticket_medio:
        return TicketMedioInfo(
            valor=money(meta.ticket_medio),
            fonte="manual",
            amostra=0,
            mensagem="Ticket médio definido manualmente na meta.",
        )
    auto = ticket_medio_contratos(organization)
    if meta and meta.ticket_medio and not meta.ticket_medio_manual:
        return TicketMedioInfo(
            valor=money(meta.ticket_medio),
            fonte="contratos",
            amostra=auto.amostra,
            mensagem=auto.mensagem,
        )
    return auto


def salvar_meta(
    user,
    *,
    ator,
    ano: int,
    meta_anual: Decimal,
    meta_mensal: Decimal | None,
    ticket_medio: Decimal | None,
    ticket_medio_manual: bool,
    vigencia_inicio: date,
    vigencia_fim: date,
    observacoes: str = "",
    organization=None,
) -> MetaComercial:
    if organization is None:
        raise ValidationError("Não foi possível determinar o escritório ativo para a meta.")
    if meta_mensal is None or meta_mensal <= 0:
        meta_mensal = money(Decimal(meta_anual) / Decimal("12"))

    existente = meta_do_ano(user, ano, organization=organization)
    criando = existente is None

    if criando:
        meta = MetaComercial(
            organization=organization,
            usuario=user,
            ano=ano,
            criado_por=ator,
        )
    else:
        meta = existente
        if meta.organization_id is None:
            meta.organization = organization

    meta.meta_anual = money(meta_anual)
    meta.meta_mensal = money(meta_mensal)
    meta.ticket_medio = money(ticket_medio) if ticket_medio else None
    meta.ticket_medio_manual = bool(ticket_medio_manual and ticket_medio)
    meta.vigencia_inicio = vigencia_inicio
    meta.vigencia_fim = vigencia_fim
    meta.observacoes = observacoes or ""
    meta.full_clean()
    meta.save()

    acao = (
        ComercialAuditLog.ACAO_META_CRIADA
        if criando
        else ComercialAuditLog.ACAO_META_ALTERADA
    )
    registrar_auditoria(
        user,
        ator=ator,
        acao=acao,
        detalhe=(
            f"ano={ano} meta_anual={meta.meta_anual} meta_mensal={meta.meta_mensal} "
            f"ticket={meta.ticket_medio} manual={meta.ticket_medio_manual}"
        ),
    )
    if meta.ticket_medio_manual:
        registrar_auditoria(
            user,
            ator=ator,
            acao=ComercialAuditLog.ACAO_TICKET_MANUAL,
            detalhe=f"ticket_medio={meta.ticket_medio}",
        )
    return meta


def taxas_conversao_historicas(user, *, organization=None, dias: int = 90) -> dict:
    """
    Taxas reais do escritório nos últimos `dias`.
    Retorna None nas taxas sem amostra suficiente.
    """
    from comercial.services.funnel import calcular_funil_receita
    from comercial.services.periodo import PeriodoComercial

    periodo = PeriodoComercial.ultimos_dias(dias)
    funil = calcular_funil_receita(user, periodo, organization=organization)
    suficientes = funil.leads >= MIN_AMOSTRAS_CONVERSAO
    return {
        "suficientes": suficientes,
        "lead_consulta": funil.taxa_lead_consulta,
        "consulta_proposta": funil.taxa_consulta_proposta,
        "proposta_contrato": funil.taxa_proposta_contrato,
        "lead_contrato": funil.taxa_lead_contrato,
        "funil": funil,
    }


def calcular_meta_reversa(
    user,
    meta: MetaComercial | None,
    ticket: TicketMedioInfo,
    *,
    organization=None,
) -> MetaReversa:
    if meta is None:
        return MetaReversa(
            contratos_ano=None,
            contratos_mes=None,
            consultas_necessarias=None,
            leads_necessarios=None,
            dados_suficientes=False,
            mensagem="Cadastre sua meta para começar.",
        )
    if not ticket.valor or ticket.valor <= 0:
        return MetaReversa(
            contratos_ano=None,
            contratos_mes=None,
            consultas_necessarias=None,
            leads_necessarios=None,
            dados_suficientes=False,
            mensagem="Dados insuficientes para projeção. Registre contratos ou informe o ticket médio.",
        )

    contratos_ano = int(
        (Decimal(meta.meta_anual) / Decimal(ticket.valor)).to_integral_value(
            rounding="ROUND_UP"
        )
    )
    contratos_mes = max(
        1,
        int(
            (Decimal(meta.meta_mensal) / Decimal(ticket.valor)).to_integral_value(
                rounding="ROUND_UP"
            )
        ),
    )

    taxas = taxas_conversao_historicas(user, organization=organization)
    if not taxas["suficientes"]:
        return MetaReversa(
            contratos_ano=contratos_ano,
            contratos_mes=contratos_mes,
            consultas_necessarias=None,
            leads_necessarios=None,
            dados_suficientes=False,
            mensagem=(
                "Contratos necessários calculados pelo ticket. "
                "Dados insuficientes para projetar consultas/leads (histórico de conversão)."
            ),
        )

    lead_contrato = taxas["lead_contrato"]
    consulta_contrato = None
    if taxas["consulta_proposta"] and taxas["proposta_contrato"]:
        # consulta → contrato ≈ (consulta→proposta) * (proposta→contrato) / 100
        consulta_contrato = money(
            Decimal(taxas["consulta_proposta"])
            * Decimal(taxas["proposta_contrato"])
            / Decimal("100")
        )

    leads_nec = None
    consultas_nec = None
    if lead_contrato and lead_contrato > 0:
        leads_nec = int(
            (Decimal(contratos_ano) * Decimal("100") / Decimal(lead_contrato)).to_integral_value(
                rounding="ROUND_UP"
            )
        )
    if consulta_contrato and consulta_contrato > 0:
        consultas_nec = int(
            (
                Decimal(contratos_ano) * Decimal("100") / Decimal(consulta_contrato)
            ).to_integral_value(rounding="ROUND_UP")
        )

    return MetaReversa(
        contratos_ano=contratos_ano,
        contratos_mes=contratos_mes,
        consultas_necessarias=consultas_nec,
        leads_necessarios=leads_nec,
        dados_suficientes=True,
        mensagem="Projeção com base no ticket e nas taxas reais do escritório.",
    )


def vigencia_padrao_ano(ano: int) -> tuple[date, date]:
    return date(ano, 1, 1), date(ano, 12, 31)


def mes_corrente_bounds(referencia: date | None = None) -> tuple[date, date]:
    hoje = referencia or timezone.localdate()
    inicio = date(hoje.year, hoje.month, 1)
    fim = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    return inicio, fim
