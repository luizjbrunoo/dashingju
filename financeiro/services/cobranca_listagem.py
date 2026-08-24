"""Listagem, filtros, KPIs e ordenação de cobranças."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db.models import (
    Case,
    DecimalField,
    F,
    IntegerField,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_date

from financeiro.choices import (
    CategoriaCobranca,
    DIAS_VENCE_EM_BREVE,
    FormaPagamento,
    StatusCobranca,
)
from financeiro.models import Cobranca, CobrancaRecebimento
from financeiro.services.cobranca_auditoria import registrar_mudanca_status_automatica
from financeiro.services.cobrancas import resolver_status_cobranca

User = get_user_model()

ORDEM_VENCIMENTO = "vencimento"
ORDEM_VALOR = "valor"
ORDEM_CLIENTE = "cliente"
ORDEM_STATUS = "status"
ORDEM_CRIADO = "criado_em"
ORDENS_VALIDAS = frozenset(
    {ORDEM_VENCIMENTO, ORDEM_VALOR, ORDEM_CLIENTE, ORDEM_STATUS, ORDEM_CRIADO}
)


@dataclass
class CobrancaKpis:
    a_receber: Decimal
    vence_mes: Decimal
    vencido: Decimal
    recebido_mes: Decimal
    taxa_recebimento: Decimal


@dataclass
class CobrancaFiltros:
    status: str = ""
    cliente_id: Optional[int] = None
    responsavel_id: Optional[int] = None
    categoria: str = ""
    vencimento_de: Optional[date] = None
    vencimento_ate: Optional[date] = None
    forma_prevista: str = ""
    q: str = ""
    ordem: str = ORDEM_VENCIMENTO

    @classmethod
    def from_request(cls, request: HttpRequest) -> CobrancaFiltros:
        return cls.from_get(request.GET)

    @classmethod
    def from_get(cls, get_params) -> CobrancaFiltros:
        ordem = (get_params.get("ordem") or ORDEM_VENCIMENTO).strip()
        if ordem not in ORDENS_VALIDAS:
            ordem = ORDEM_VENCIMENTO
        return cls(
            status=(get_params.get("status") or "").strip(),
            cliente_id=_int_param(get_params, "cliente"),
            responsavel_id=_int_param(get_params, "responsavel"),
            categoria=(get_params.get("categoria") or "").strip(),
            vencimento_de=_date_param(get_params, "vencimento_de"),
            vencimento_ate=_date_param(get_params, "vencimento_ate"),
            forma_prevista=(get_params.get("forma_prevista") or "").strip(),
            q=(get_params.get("q") or "").strip(),
            ordem=ordem,
        )

    def to_query_dict(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.status:
            params["status"] = self.status
        if self.cliente_id:
            params["cliente"] = str(self.cliente_id)
        if self.responsavel_id:
            params["responsavel"] = str(self.responsavel_id)
        if self.categoria:
            params["categoria"] = self.categoria
        if self.vencimento_de:
            params["vencimento_de"] = self.vencimento_de.isoformat()
        if self.vencimento_ate:
            params["vencimento_ate"] = self.vencimento_ate.isoformat()
        if self.forma_prevista:
            params["forma_prevista"] = self.forma_prevista
        if self.q:
            params["q"] = self.q
        if self.ordem != ORDEM_VENCIMENTO:
            params["ordem"] = self.ordem
        return params

    def query_string(self) -> str:
        return urlencode(self.to_query_dict())


def _int_param(get_params, key: str) -> Optional[int]:
    raw = (get_params.get(key) or "").strip()
    if raw.isdigit():
        return int(raw)
    return None


def _date_param(get_params, key: str) -> Optional[date]:
    raw = (get_params.get(key) or "").strip()
    return parse_date(raw) if raw else None


def queryset_anotado(usuario):
    return (
        Cobranca.objects.filter(usuario=usuario)
        .select_related("cliente", "contrato", "responsavel", "criado_por")
        .annotate(
            total_recebido_calc=Coalesce(
                Sum(
                    "recebimentos__valor",
                    filter=Q(recebimentos__cancelado_em__isnull=True),
                ),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
        .annotate(saldo_calc=F("valor_original") - F("total_recebido_calc"))
    )


def aplicar_filtros(qs, filtros: CobrancaFiltros, *, hoje: date | None = None):
    hoje = hoje or timezone.localdate()

    if filtros.status:
        qs = _filtrar_por_status(qs, filtros.status, hoje=hoje)
    if filtros.cliente_id:
        qs = qs.filter(cliente_id=filtros.cliente_id)
    if filtros.responsavel_id:
        qs = qs.filter(responsavel_id=filtros.responsavel_id)
    if filtros.categoria:
        qs = qs.filter(categoria=filtros.categoria)
    if filtros.vencimento_de:
        qs = qs.filter(data_vencimento__gte=filtros.vencimento_de)
    if filtros.vencimento_ate:
        qs = qs.filter(data_vencimento__lte=filtros.vencimento_ate)
    if filtros.forma_prevista:
        qs = qs.filter(forma_prevista_pagamento=filtros.forma_prevista)
    if filtros.q:
        qs = qs.filter(
            Q(descricao__icontains=filtros.q)
            | Q(contrato_referencia__icontains=filtros.q)
            | Q(contrato__referencia__icontains=filtros.q)
            | Q(cliente__nome__icontains=filtros.q)
        )
    return qs


def _filtrar_por_status(qs, status: str, *, hoje: date):
    if status == StatusCobranca.CANCELED:
        return qs.filter(status=StatusCobranca.CANCELED)
    if status == StatusCobranca.DRAFT:
        return qs.filter(status=StatusCobranca.DRAFT)

    qs = qs.exclude(status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT])
    limite_breve = hoje + timedelta(days=DIAS_VENCE_EM_BREVE)

    if status == StatusCobranca.PAID:
        return qs.filter(saldo_calc__lte=0)
    if status == StatusCobranca.OVERDUE:
        return qs.filter(saldo_calc__gt=0, data_vencimento__lt=hoje)
    if status == StatusCobranca.DUE_SOON:
        return qs.filter(
            saldo_calc__gt=0,
            data_vencimento__gte=hoje,
            data_vencimento__lte=limite_breve,
        )
    if status == StatusCobranca.PARTIALLY_PAID:
        return qs.filter(saldo_calc__gt=0, saldo_calc__lt=F("valor_original"))
    if status == StatusCobranca.PENDING:
        return qs.filter(
            saldo_calc__gt=0,
            saldo_calc=F("valor_original"),
            data_vencimento__gt=limite_breve,
        )
    return qs.filter(status=status)


def ordenar_queryset(qs, ordem: str, *, hoje: date | None = None):
    hoje = hoje or timezone.localdate()
    limite_breve = hoje + timedelta(days=DIAS_VENCE_EM_BREVE)

    if ordem == ORDEM_VALOR:
        return qs.order_by("-valor_original", "data_vencimento")
    if ordem == ORDEM_CLIENTE:
        return qs.order_by("cliente__nome", "data_vencimento")
    if ordem == ORDEM_STATUS:
        return qs.order_by("status", "data_vencimento")
    if ordem == ORDEM_CRIADO:
        return qs.order_by("-criado_em")

    return qs.annotate(
        _prio_lista=Case(
            When(
                saldo_calc__gt=0,
                data_vencimento__lt=hoje,
                then=Value(0),
            ),
            When(
                saldo_calc__gt=0,
                data_vencimento__gte=hoje,
                data_vencimento__lte=limite_breve,
                then=Value(1),
            ),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by("_prio_lista", "data_vencimento", "id")


def sincronizar_statuses(cobrancas, *, hoje: date | None = None) -> None:
    hoje = hoje or timezone.localdate()
    atualizar: list[Cobranca] = []
    for cobranca in cobrancas:
        saldo = cobranca.saldo_calc
        cobranca.saldo_exibicao = saldo
        cobranca.status_exibicao = resolver_status_cobranca(
            cobranca, hoje=hoje, saldo=saldo
        )
        if cobranca.status not in (StatusCobranca.CANCELED, StatusCobranca.DRAFT):
            status_anterior = cobranca.status
            if cobranca.status != cobranca.status_exibicao:
                cobranca.status = cobranca.status_exibicao
                atualizar.append(cobranca)
                registrar_mudanca_status_automatica(cobranca, status_anterior)
    if atualizar:
        Cobranca.objects.bulk_update(atualizar, ["status", "atualizado_em"])


def cobrancas_para_listagem(usuario, filtros: CobrancaFiltros):
    hoje = timezone.localdate()
    qs = queryset_anotado(usuario)
    qs = aplicar_filtros(qs, filtros, hoje=hoje)
    qs = ordenar_queryset(qs, filtros.ordem, hoje=hoje)
    cobrancas = list(qs)
    sincronizar_statuses(cobrancas, hoje=hoje)
    return cobrancas


def calcular_kpis_cobrancas(usuario, *, hoje: date | None = None) -> CobrancaKpis:
    """
    Taxa de recebimento = recebido_no_mês / (recebido_no_mês + saldo_vencido) × 100
    quando o denominador > 0.
    """
    hoje = hoje or timezone.localdate()
    inicio_mes = date(hoje.year, hoje.month, 1)
    fim_mes = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])

    abertas = queryset_anotado(usuario).exclude(
        status__in=[StatusCobranca.CANCELED, StatusCobranca.DRAFT]
    )

    a_receber = Decimal("0")
    vence_mes = Decimal("0")
    vencido = Decimal("0")

    for row in abertas.values("saldo_calc", "data_vencimento"):
        saldo = row["saldo_calc"] or Decimal("0")
        if saldo <= 0:
            continue
        a_receber += saldo
        venc = row["data_vencimento"]
        if venc < hoje:
            vencido += saldo
        if inicio_mes <= venc <= fim_mes:
            vence_mes += saldo

    recebido_mes = CobrancaRecebimento.objects.filter(
        usuario=usuario,
        cancelado_em__isnull=True,
        data_recebimento__gte=inicio_mes,
        data_recebimento__lte=fim_mes,
    ).aggregate(total=Sum("valor"))["total"] or Decimal("0")

    denominador = recebido_mes + vencido
    if denominador > 0:
        taxa = (recebido_mes / denominador * Decimal("100")).quantize(Decimal("0.1"))
    else:
        taxa = Decimal("0")

    return CobrancaKpis(
        a_receber=a_receber,
        vence_mes=vence_mes,
        vencido=vencido,
        recebido_mes=recebido_mes,
        taxa_recebimento=taxa,
    )


def opcoes_filtro_clientes(usuario):
    from usuarios.models import Cliente

    return Cliente.objects.filter(user=usuario).order_by("nome")


def opcoes_filtro_responsaveis(usuario):
    return User.objects.filter(pk=usuario.pk)
