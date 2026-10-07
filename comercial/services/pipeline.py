"""Pipeline comercial: etapa, temperatura e desfecho (sem CRM paralelo)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from comercial.services.finance_org import contratos_organization
from comercial.services.money import MIN_AMOSTRAS_CONVERSAO, ZERO, money, safe_pct
from comercial.services.periodo import PeriodoComercial, filtro_datetime_campo
from marketing.definitions import FASES_PROPOSTA
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

TEMP_FRIO = "frio"
TEMP_MORNO = "morno"
TEMP_QUENTE = "quente"
TEMP_FERVENDO = "fervendo"
TEMP_NAO_CLASSIFICADO = "nao_classificado"

TEMP_LABELS = {
    TEMP_FRIO: "Frio",
    TEMP_MORNO: "Morno",
    TEMP_QUENTE: "Quente",
    TEMP_FERVENDO: "Fervendo",
    TEMP_NAO_CLASSIFICADO: "Não classificado",
}

DESFECHO_ABERTO = "aberto"
DESFECHO_GANHO = "ganho"
DESFECHO_PERDIDO = "perdido"

MOTIVO_PREFIXO = "motivo_perda:"
MOTIVO_NAO_INFORMADO = "nao_informado"
MOTIVOS_PERDA = {
    "honorarios": "Honorários / preço",
    "sem_retorno": "Sem retorno do prospect",
    "outro_escritorio": "Escolheu outro escritório",
    "nao_qualificado": "Sem aderência / não qualificado",
    "desistiu": "Desistiu / não quis prosseguir",
    "conflito": "Conflito / impedimento",
    "atendimento": "Atendimento / follow-up",
    "outro": "Outro",
    MOTIVO_NAO_INFORMADO: "Motivo não informado",
}

_ETAPA_LABEL = dict(Cliente.FASE_FUNIL_CHOICES)
DIAS_SINAL_RECENTE = 7


def clientes_organization(organization):
    if organization is None:
        return Cliente.objects.none()
    return Cliente.objects.filter(organization=organization)


def compromissos_organization(organization):
    if organization is None:
        return Compromisso.objects.none()
    return Compromisso.objects.filter(cliente__organization=organization)


def parse_motivo_perda(relatorio: str | None) -> str:
    texto = (relatorio or "").strip()
    if not texto.lower().startswith(MOTIVO_PREFIXO):
        return MOTIVO_NAO_INFORMADO
    resto = texto[len(MOTIVO_PREFIXO) :].strip().splitlines()[0].strip().lower()
    codigo = resto.split()[0] if resto else MOTIVO_NAO_INFORMADO
    return codigo if codigo in MOTIVOS_PERDA else MOTIVO_NAO_INFORMADO


def motivo_perda_label(codigo: str) -> str:
    return MOTIVOS_PERDA.get(codigo, MOTIVOS_PERDA[MOTIVO_NAO_INFORMADO])


def formatar_motivo_perda(codigo: str, observacao: str = "") -> str:
    codigo = codigo if codigo in MOTIVOS_PERDA else "outro"
    linha = f"{MOTIVO_PREFIXO}{codigo}"
    extra = (observacao or "").strip()
    return f"{linha}\n{extra}" if extra else linha


@dataclass(frozen=True)
class SinaisTemperatura:
    etapa_proposta: bool
    aguardando_decisao: bool
    consulta_realizada: bool
    atividade_recente: bool
    proxima_acao: bool
    idade_dias: int


@dataclass(frozen=True)
class TemperaturaFaixa:
    key: str
    label: str
    quantidade: int
    valor_potencial: Decimal | None


@dataclass(frozen=True)
class PerdaEtapa:
    etapa: str
    label: str
    quantidade: int


@dataclass(frozen=True)
class PerdaMotivo:
    codigo: str
    label: str
    quantidade: int


@dataclass(frozen=True)
class PainelPipeline:
    abertos: int
    ganhos: int
    perdidos: int
    taxa_ganho_pct: Decimal | None
    taxa_perda_pct: Decimal | None
    valor_ganho: Decimal
    valor_potencial_perdido: Decimal | None
    temperaturas: tuple[TemperaturaFaixa, ...]
    avancados_sem_acao: int
    valor_avancados_sem_acao: Decimal | None
    perdas_por_etapa: tuple[PerdaEtapa, ...]
    perdas_por_motivo: tuple[PerdaMotivo, ...]
    perda_etapa_principal: PerdaEtapa | None
    perda_motivo_principal: PerdaMotivo | None
    amostra_perdas_suficiente: bool
    pipeline_total: Decimal
    pipeline_qualificado: Decimal
    mensagem: str


def classificar_temperatura(fase: str, sinais: SinaisTemperatura) -> tuple[str, tuple[str, ...]]:
    """Determinístico. Ausência de sinais ≠ frio."""
    motivos: list[str] = []
    if sinais.aguardando_decisao:
        motivos.append("aguardando decisão")
    if sinais.etapa_proposta and not sinais.aguardando_decisao:
        motivos.append("proposta enviada")
    if sinais.consulta_realizada:
        motivos.append("consulta realizada")
    if sinais.atividade_recente:
        motivos.append("interação recente")
    if sinais.proxima_acao:
        motivos.append("próxima ação agendada")

    if sinais.aguardando_decisao and (sinais.proxima_acao or sinais.atividade_recente):
        return TEMP_FERVENDO, tuple(motivos)
    if sinais.etapa_proposta:
        return TEMP_QUENTE, tuple(motivos or ("proposta em aberto",))
    if sinais.consulta_realizada or (sinais.atividade_recente and not sinais.etapa_proposta):
        return TEMP_MORNO, tuple(motivos or ("consulta ou interação observada",))

    tem_sinal_fraco = sinais.idade_dias >= DIAS_SINAL_RECENTE and (fase or "primeiro_contato") in {
        "primeiro_contato",
        "novo_contato",
        "",
    }
    if tem_sinal_fraco and not sinais.consulta_realizada and not sinais.proxima_acao:
        return TEMP_FRIO, ("baixa maturidade observada — sem consulta/proposta",)
    return TEMP_NAO_CLASSIFICADO, ("sinais insuficientes para classificar",)


def _ids_com(qs) -> set[int]:
    return {i for i in qs.values_list("cliente_id", flat=True) if i}


def _valor_ticket(ticket: Decimal | None, quantidade: int) -> Decimal | None:
    if ticket is None or ticket <= 0 or quantidade <= 0:
        return None
    return money(Decimal(ticket) * Decimal(quantidade))


def analisar_pipeline(
    organization,
    periodo: PeriodoComercial,
    *,
    ticket: Decimal | None = None,
) -> PainelPipeline:
    if organization is None:
        return _vazio("Contexto de organização ausente — pipeline indisponível.")

    clientes = clientes_organization(organization)
    if not clientes.exists():
        return _vazio("Ainda não há oportunidades nesta organização.")

    contratos = contratos_organization(organization)
    contratos_periodo = filtro_datetime_campo(
        contratos,
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )
    ids_com_contrato = set(contratos.values_list("cliente_id", flat=True))
    valor_ganho = _sum_contratos(contratos_periodo)
    ganhos = contratos_periodo.count()

    abertos_qs = clientes.filter(status="em_prospeccao")
    perdidos_qs = clientes.filter(status="inativo").exclude(id__in=ids_com_contrato)
    perdidos_periodo = filtro_datetime_campo(
        perdidos_qs,
        "criado_em",
        data_inicio=periodo.data_inicio,
        data_fim=periodo.data_fim,
    )

    abertos = abertos_qs.count()
    n_perdidos = perdidos_periodo.count()
    encerrados = ganhos + n_perdidos
    taxa_ganho = safe_pct(ganhos, encerrados) if encerrados else None
    taxa_perda = safe_pct(n_perdidos, encerrados) if encerrados else None

    agora = timezone.now()
    limite_recente = agora - timedelta(days=DIAS_SINAL_RECENTE)
    comps = compromissos_organization(organization).exclude(
        status=StatusCompromisso.CANCELADO
    )
    consulta_ids = _ids_com(
        comps.filter(tipo=TipoCompromisso.CONSULTA, status=StatusCompromisso.REALIZADO)
    )
    recente_ids = _ids_com(comps.filter(data_hora__gte=limite_recente, data_hora__lte=agora))
    futuro_ids = _ids_com(comps.filter(data_hora__gte=agora))
    followup_ids = _ids_com(
        comps.filter(tipo=TipoCompromisso.FOLLOWUP_COMERCIAL, data_hora__gte=agora)
    )

    contagem_temp = Counter()
    avancados_sem_acao = 0
    for cli in abertos_qs.only("id", "fase_funil", "criado_em"):
        idade = max(0, (agora.date() - cli.criado_em.date()).days)
        sinais = SinaisTemperatura(
            etapa_proposta=cli.fase_funil in FASES_PROPOSTA,
            aguardando_decisao=cli.fase_funil == "aguardando_decisao",
            consulta_realizada=cli.id in consulta_ids,
            atividade_recente=cli.id in recente_ids,
            proxima_acao=cli.id in futuro_ids or cli.id in followup_ids,
            idade_dias=idade,
        )
        temp, _motivos = classificar_temperatura(cli.fase_funil, sinais)
        contagem_temp[temp] += 1
        if temp in {TEMP_QUENTE, TEMP_FERVENDO} and not sinais.proxima_acao:
            avancados_sem_acao += 1

    temperaturas = tuple(
        TemperaturaFaixa(
            key=key,
            label=TEMP_LABELS[key],
            quantidade=contagem_temp.get(key, 0),
            valor_potencial=_valor_ticket(ticket, contagem_temp.get(key, 0)),
        )
        for key in (
            TEMP_FRIO,
            TEMP_MORNO,
            TEMP_QUENTE,
            TEMP_FERVENDO,
            TEMP_NAO_CLASSIFICADO,
        )
    )

    n_qualificados = contagem_temp.get(TEMP_QUENTE, 0) + contagem_temp.get(TEMP_FERVENDO, 0)
    pipeline_total = _valor_ticket(ticket, abertos) or ZERO
    pipeline_qualificado = _valor_ticket(ticket, n_qualificados) or ZERO
    valor_perdido = _valor_ticket(ticket, n_perdidos)
    valor_avancados = _valor_ticket(ticket, avancados_sem_acao)

    etapas_counter: Counter[str] = Counter()
    motivos_counter: Counter[str] = Counter()
    for cli in perdidos_periodo.only("fase_funil", "relatorio_prospeccao"):
        etapa = cli.fase_funil or "primeiro_contato"
        etapas_counter[etapa] += 1
        motivos_counter[parse_motivo_perda(cli.relatorio_prospeccao)] += 1

    perdas_etapa = tuple(
        PerdaEtapa(etapa=k, label=_ETAPA_LABEL.get(k, k), quantidade=v)
        for k, v in etapas_counter.most_common()
    )
    perdas_motivo = tuple(
        PerdaMotivo(codigo=k, label=motivo_perda_label(k), quantidade=v)
        for k, v in motivos_counter.most_common()
    )
    amostra_ok = n_perdidos >= MIN_AMOSTRAS_CONVERSAO

    mensagem = ""
    if abertos == 0 and ganhos == 0 and n_perdidos == 0:
        mensagem = "Sem movimentação comercial no período."

    return PainelPipeline(
        abertos=abertos,
        ganhos=ganhos,
        perdidos=n_perdidos,
        taxa_ganho_pct=taxa_ganho if amostra_ok or encerrados >= MIN_AMOSTRAS_CONVERSAO else None,
        taxa_perda_pct=taxa_perda if amostra_ok or encerrados >= MIN_AMOSTRAS_CONVERSAO else None,
        valor_ganho=valor_ganho,
        valor_potencial_perdido=valor_perdido,
        temperaturas=temperaturas,
        avancados_sem_acao=avancados_sem_acao,
        valor_avancados_sem_acao=valor_avancados,
        perdas_por_etapa=perdas_etapa if amostra_ok else (),
        perdas_por_motivo=perdas_motivo if amostra_ok else (),
        perda_etapa_principal=perdas_etapa[0] if amostra_ok and perdas_etapa else None,
        perda_motivo_principal=perdas_motivo[0] if amostra_ok and perdas_motivo else None,
        amostra_perdas_suficiente=amostra_ok,
        pipeline_total=pipeline_total,
        pipeline_qualificado=pipeline_qualificado,
        mensagem=mensagem,
    )


def _sum_contratos(qs) -> Decimal:
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    return money(qs.aggregate(total=Coalesce(Sum("valor_total"), ZERO))["total"])


def _vazio(mensagem: str) -> PainelPipeline:
    temperaturas = tuple(
        TemperaturaFaixa(key=k, label=TEMP_LABELS[k], quantidade=0, valor_potencial=None)
        for k in (
            TEMP_FRIO,
            TEMP_MORNO,
            TEMP_QUENTE,
            TEMP_FERVENDO,
            TEMP_NAO_CLASSIFICADO,
        )
    )
    return PainelPipeline(
        abertos=0,
        ganhos=0,
        perdidos=0,
        taxa_ganho_pct=None,
        taxa_perda_pct=None,
        valor_ganho=ZERO,
        valor_potencial_perdido=None,
        temperaturas=temperaturas,
        avancados_sem_acao=0,
        valor_avancados_sem_acao=None,
        perdas_por_etapa=(),
        perdas_por_motivo=(),
        perda_etapa_principal=None,
        perda_motivo_principal=None,
        amostra_perdas_suficiente=False,
        pipeline_total=ZERO,
        pipeline_qualificado=ZERO,
        mensagem=mensagem,
    )


def url_oportunidades() -> str:
    return reverse("comercial_dashboard") + "?aba=oportunidades"


def ocultar_valores_pipeline(painel: PainelPipeline) -> PainelPipeline:
    from dataclasses import replace

    temps = tuple(replace(t, valor_potencial=None) for t in painel.temperaturas)
    return replace(
        painel,
        valor_ganho=ZERO,
        valor_potencial_perdido=None,
        valor_avancados_sem_acao=None,
        pipeline_total=ZERO,
        pipeline_qualificado=ZERO,
        temperaturas=temps,
    )


PIPELINE_VAZIO = _vazio("")
