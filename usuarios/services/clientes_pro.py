"""
CLIENTES PRO — memória inteligente do relacionamento (derivada).

Consome Comercial, Financeiro, Agenda e Documentos Organization-scoped.
Não duplica ownership. Sem LLM/ML. Sem event store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from comercial.services.pipeline import (
    TEMP_FERVENDO,
    TEMP_LABELS,
    TEMP_QUENTE,
    SinaisTemperatura,
    classificar_temperatura,
    motivo_perda_label,
    parse_motivo_perda,
)
from marketing.definitions import FASES_PROPOSTA
from usuarios.choices import StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.models import Compromisso, Documentos, Tarefa

TIMELINE_LIMIT = 20
DIAS_SEM_INTERACAO_ATENCAO = 14
CRITERIOS = (
    "Health operacional: cobrança vencida (capability financeira) 90, "
    "tarefa com prazo ultrapassado 82, proposta sem próxima ação 76, "
    "oportunidade quente/fervendo sem próxima ação 74, sem próxima ação 70, "
    "sem interação relevante 65. Ausência de registros ≠ saudável. "
    "Tarefa atrasada ≠ prazo jurídico perdido. Cobrança vencida ≠ cliente ruim. "
    "Sem LLM/ML."
)

_ZERO = Decimal("0")


@dataclass(frozen=True)
class CapsClientePro:
    agenda: bool
    financeiro: bool
    recebimentos: bool
    documentos: bool


@dataclass(frozen=True)
class InteracaoResumo:
    quando: datetime | None
    tipo: str
    label: str


@dataclass(frozen=True)
class ProximaAcaoResumo:
    existe: bool
    titulo: str
    quando_label: str
    url: str
    ja_agendada: bool


@dataclass(frozen=True)
class HealthRelacionamento:
    estado: str  # saudavel | atencao | critico | dados_insuficientes
    label: str
    motivos: tuple[str, ...]
    score: int


@dataclass(frozen=True)
class NextBestAction:
    key: str
    titulo: str
    motivo: str
    acao: str
    url: str
    cta: str
    ja_existe: bool


@dataclass(frozen=True)
class AdvisorCliente:
    principal: NextBestAction | None
    status: str
    evidence: str
    impacto: str | None
    criterios: str


@dataclass(frozen=True)
class EventoTimeline:
    timestamp: datetime
    tipo: str
    titulo: str
    resumo: str
    source: str
    source_id: int | None
    capability: str


@dataclass(frozen=True)
class ContextoComercialCliente:
    fase: str
    fase_label: str
    temperatura: str
    temperatura_label: str
    desfecho: str
    motivo_perda: str | None
    valor_potencial: Decimal | None


@dataclass(frozen=True)
class ContextoFinanceiroCliente:
    a_receber: Decimal | None
    vencido: Decimal | None
    proximo_vencimento: str | None
    ultimo_recebimento: str | None


@dataclass(frozen=True)
class SinalListagem:
    health: str
    health_label: str
    tem_proxima: bool
    atencao: bool


@dataclass(frozen=True)
class PainelClientePro:
    status: str
    status_label: str
    fase: str
    fase_label: str
    origem: str
    origem_label: str
    atribuicao_confiavel: bool
    temperatura: str
    temperatura_label: str
    ultima_interacao: InteracaoResumo
    proxima_acao: ProximaAcaoResumo
    health: HealthRelacionamento
    nba: NextBestAction
    advisor: AdvisorCliente
    comercial: ContextoComercialCliente
    financeiro: ContextoFinanceiroCliente | None
    timeline: tuple[EventoTimeline, ...]
    caps: CapsClientePro
    tenant_ok: bool


def _caps_vazias() -> CapsClientePro:
    return CapsClientePro(
        agenda=False, financeiro=False, recebimentos=False, documentos=False
    )


def painel_vazio(cliente, *, caps: CapsClientePro | None = None) -> PainelClientePro:
    caps = caps or _caps_vazias()
    return PainelClientePro(
        status=getattr(cliente, "status", "") or "",
        status_label=cliente.get_status_display() if cliente else "",
        fase=getattr(cliente, "fase_funil", "") or "",
        fase_label=cliente.get_fase_funil_display() if cliente else "",
        origem=getattr(cliente, "origem", "") or "",
        origem_label=(
            cliente.get_origem_display() if cliente else "Origem não identificada"
        ),
        atribuicao_confiavel=bool(getattr(cliente, "atribuicao_confiavel", False)),
        temperatura="nao_classificado",
        temperatura_label=TEMP_LABELS["nao_classificado"],
        ultima_interacao=InteracaoResumo(
            quando=None, tipo="", label="Sem interação registrada"
        ),
        proxima_acao=ProximaAcaoResumo(
            existe=False, titulo="", quando_label="", url="", ja_agendada=False
        ),
        health=HealthRelacionamento(
            estado="dados_insuficientes",
            label="Dados insuficientes",
            motivos=("Contexto de organização inválido.",),
            score=0,
        ),
        nba=NextBestAction(
            key="dados_insuficientes",
            titulo="Dados insuficientes",
            motivo="Sem contexto de organização não há recomendação.",
            acao="Validar o escritório ativo.",
            url=reverse("clientes"),
            cta="Ver clientes",
            ja_existe=False,
        ),
        advisor=AdvisorCliente(
            principal=None,
            status="dados_insuficientes",
            evidence="Contexto de organização inválido.",
            impacto=None,
            criterios=CRITERIOS,
        ),
        comercial=ContextoComercialCliente(
            fase="",
            fase_label="",
            temperatura="nao_classificado",
            temperatura_label=TEMP_LABELS["nao_classificado"],
            desfecho="aberto",
            motivo_perda=None,
            valor_potencial=None,
        ),
        financeiro=None,
        timeline=(),
        caps=caps,
        tenant_ok=False,
    )


def _aware(dt):
    if dt is None:
        return None
    from datetime import date as date_type

    if isinstance(dt, date_type) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _fmt_dt(dt) -> str:
    if dt is None:
        return ""
    local = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    return local.strftime("%d/%m/%Y")


def _url_cliente(cliente_id: int) -> str:
    return reverse("cliente", args=[cliente_id])


def _url_agenda(cliente_id: int, *, modal: str = "") -> str:
    params = {"cliente": str(cliente_id), "view": "lista"}
    if modal:
        params["modal"] = modal
    return reverse("agenda") + "?" + urlencode(params)


def _url_cobrancas(cliente_id: int) -> str:
    return reverse("financeiro_cobranca_listar") + "?" + urlencode(
        {"cliente": str(cliente_id)}
    )


def _compromissos_qs(organization, cliente):
    if organization is None:
        return Compromisso.objects.none()
    return Compromisso.objects.filter(
        organization=organization, cliente=cliente
    ).exclude(status=StatusCompromisso.CANCELADO)


def _tarefas_qs(organization, cliente):
    if organization is None:
        return Tarefa.objects.none()
    return Tarefa.objects.filter(organization=organization, cliente=cliente).exclude(
        status=StatusTarefa.CANCELADA
    )


def _temperatura_cliente(cliente, organization, agora) -> tuple[str, tuple[str, ...]]:
    if cliente.status != "em_prospeccao":
        return "nao_classificado", ("cliente fora do pipeline aberto",)
    limite = agora - timedelta(days=7)
    comps = _compromissos_qs(organization, cliente)
    consulta = comps.filter(
        tipo=TipoCompromisso.CONSULTA, status=StatusCompromisso.REALIZADO
    ).exists()
    recente = comps.filter(data_hora__gte=limite, data_hora__lte=agora).exists()
    futuro = comps.filter(data_hora__gte=agora).exists()
    follow = comps.filter(
        tipo=TipoCompromisso.FOLLOWUP_COMERCIAL, data_hora__gte=agora
    ).exists()
    idade = max(0, (agora.date() - cliente.criado_em.date()).days)
    sinais = SinaisTemperatura(
        etapa_proposta=cliente.fase_funil in FASES_PROPOSTA,
        aguardando_decisao=cliente.fase_funil == "aguardando_decisao",
        consulta_realizada=consulta,
        atividade_recente=recente,
        proxima_acao=futuro or follow,
        idade_dias=idade,
    )
    return classificar_temperatura(cliente.fase_funil, sinais)


def _desfecho(cliente, organization) -> str:
    if cliente.status == "ativo":
        return "ganho"
    if cliente.status == "inativo":
        from comercial.services.finance_org import contratos_organization

        if contratos_organization(organization).filter(cliente=cliente).exists():
            return "ganho"
        return "perdido"
    return "aberto"


def _coletar_eventos(
    organization,
    cliente,
    caps: CapsClientePro,
    *,
    incluir_titulos: bool,
) -> list[EventoTimeline]:
    eventos: list[EventoTimeline] = []
    criado = cliente.criado_em
    if criado:
        eventos.append(
            EventoTimeline(
                timestamp=criado,
                tipo="cadastro",
                titulo="Cadastro",
                resumo="Cliente registrado no escritório.",
                source="cliente",
                source_id=cliente.pk,
                capability="cliente",
            )
        )

    if caps.agenda:
        for c in _compromissos_qs(organization, cliente).order_by("-data_hora")[:30]:
            titulo = (
                c.titulo
                if incluir_titulos
                else c.get_tipo_display() or "Compromisso"
            )
            eventos.append(
                EventoTimeline(
                    timestamp=c.data_hora,
                    tipo="compromisso",
                    titulo=titulo,
                    resumo=f"{c.get_tipo_display()} · {c.get_status_display()}",
                    source="compromisso",
                    source_id=c.pk,
                    capability="agenda",
                )
            )
        for t in _tarefas_qs(organization, cliente).order_by("-criado_em")[:30]:
            quando = t.atualizado_em or t.criado_em
            titulo = t.titulo if incluir_titulos else "Tarefa"
            eventos.append(
                EventoTimeline(
                    timestamp=quando,
                    tipo="tarefa",
                    titulo=titulo,
                    resumo=t.get_status_display(),
                    source="tarefa",
                    source_id=t.pk,
                    capability="agenda",
                )
            )

    if caps.financeiro:
        from financeiro.choices import StatusCobranca
        from financeiro.services.contrato_crud import contratos_queryset
        from financeiro.services.cobranca_listagem import queryset_anotado_organization

        for ctr in contratos_queryset(organization).filter(cliente=cliente)[:20]:
            eventos.append(
                EventoTimeline(
                    timestamp=ctr.criado_em,
                    tipo="contrato",
                    titulo="Contrato" if not incluir_titulos else (ctr.referencia or "Contrato"),
                    resumo="Valor contratado registrado." if caps.financeiro else "",
                    source="contrato",
                    source_id=ctr.pk,
                    capability="financeiro",
                )
            )
        from financeiro.models import Cobranca
        from financeiro.choices import StatusCobranca
        from financeiro.services.cobranca_listagem import queryset_anotado_organization

        for cob in (
            queryset_anotado_organization(organization)
            .filter(cliente=cliente)
            .exclude(status=StatusCobranca.CANCELED)[:20]
        ):
            eventos.append(
                EventoTimeline(
                    timestamp=_aware(cob.data_vencimento) or cob.criado_em,
                    tipo="cobranca",
                    titulo="Cobrança",
                    resumo=cob.get_status_display(),
                    source="cobranca",
                    source_id=cob.pk,
                    capability="financeiro",
                )
            )

    if caps.recebimentos:
        from financeiro.models import CobrancaRecebimento

        recs = CobrancaRecebimento.objects.filter(
            organization=organization,
            cobranca__cliente=cliente,
            cancelado_em__isnull=True,
        ).order_by("-data_recebimento")[:20]
        for rec in recs:
            eventos.append(
                EventoTimeline(
                    timestamp=_aware(rec.data_recebimento) or rec.criado_em,
                    tipo="recebimento",
                    titulo="Recebimento",
                    resumo="Recebimento relacionado ao contrato/cobrança.",
                    source="recebimento",
                    source_id=rec.pk,
                    capability="recebimentos",
                )
            )

    if caps.documentos:
        for doc in Documentos.objects.filter(cliente=cliente).order_by("-data_upload")[:15]:
            eventos.append(
                EventoTimeline(
                    timestamp=doc.data_upload,
                    tipo="documento",
                    titulo=doc.get_tipo_display() if incluir_titulos else "Documento",
                    resumo="Documento vinculado ao cliente.",
                    source="documento",
                    source_id=doc.pk,
                    capability="documentos",
                )
            )

    if cliente.data_relatorio_prospeccao:
        eventos.append(
            EventoTimeline(
                timestamp=_aware(cliente.data_relatorio_prospeccao),
                tipo="prospeccao",
                titulo="Relatório de prospecção",
                resumo="Registro estruturado de prospecção.",
                source="cliente",
                source_id=cliente.pk,
                capability="cliente",
            )
        )

    eventos.sort(key=lambda e: e.timestamp or timezone.now(), reverse=True)
    return eventos[:TIMELINE_LIMIT]


def _ultima_interacao(eventos: list[EventoTimeline]) -> InteracaoResumo:
    humanos = [
        e
        for e in eventos
        if e.tipo
        in {
            "compromisso",
            "tarefa",
            "contrato",
            "recebimento",
            "documento",
            "prospeccao",
        }
    ]
    if not humanos:
        return InteracaoResumo(
            quando=None, tipo="", label="Sem interação registrada"
        )
    top = humanos[0]
    tipo_label = {
        "compromisso": "Compromisso",
        "tarefa": "Tarefa",
        "contrato": "Contrato",
        "recebimento": "Recebimento",
        "documento": "Documento",
        "prospeccao": "Prospecção",
    }.get(top.tipo, top.tipo)
    return InteracaoResumo(
        quando=top.timestamp,
        tipo=top.tipo,
        label=f"{_fmt_dt(top.timestamp)} — {tipo_label}",
    )


def _proxima_existente(organization, cliente, caps: CapsClientePro, agora) -> ProximaAcaoResumo:
    if not caps.agenda:
        return ProximaAcaoResumo(
            existe=False, titulo="", quando_label="", url="", ja_agendada=False
        )
    futuro = (
        _compromissos_qs(organization, cliente)
        .filter(data_hora__gte=agora)
        .order_by("data_hora")
        .first()
    )
    tarefa = (
        _tarefas_qs(organization, cliente)
        .filter(status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO))
        .exclude(prazo=None)
        .order_by("prazo")
        .first()
    )
    if futuro and tarefa and tarefa.prazo:
        tarefa_dt = _aware(tarefa.prazo)
        usa_tarefa = tarefa_dt and tarefa_dt < futuro.data_hora
    else:
        usa_tarefa = bool(tarefa) and not futuro

    if usa_tarefa and tarefa:
        return ProximaAcaoResumo(
            existe=True,
            titulo="Tarefa pendente",
            quando_label=tarefa.prazo.strftime("%d/%m/%Y") if tarefa.prazo else "",
            url=_url_agenda(cliente.pk, modal="tarefa"),
            ja_agendada=True,
        )
    if futuro:
        return ProximaAcaoResumo(
            existe=True,
            titulo=futuro.get_tipo_display() or "Compromisso",
            quando_label=_fmt_dt(futuro.data_hora),
            url=_url_agenda(cliente.pk, modal="compromisso"),
            ja_agendada=True,
        )
    return ProximaAcaoResumo(
        existe=False, titulo="", quando_label="", url="", ja_agendada=False
    )


def _contexto_financeiro(organization, cliente, caps: CapsClientePro) -> ContextoFinanceiroCliente | None:
    if not caps.financeiro:
        return None
    from financeiro.choices import StatusCobranca
    from financeiro.models import CobrancaRecebimento
    from financeiro.services.cliente_financeiro import resumo_financeiro_cliente_organization
    from financeiro.services.cobranca_listagem import queryset_anotado_organization

    resumo = resumo_financeiro_cliente_organization(organization, cliente)
    prox = (
        queryset_anotado_organization(organization)
        .filter(cliente=cliente)
        .exclude(status__in=(StatusCobranca.CANCELED, StatusCobranca.PAID))
        .order_by("data_vencimento")
        .values_list("data_vencimento", flat=True)
        .first()
    )
    ultimo = None
    if caps.recebimentos:
        ultimo = (
            CobrancaRecebimento.objects.filter(
                organization=organization,
                cobranca__cliente=cliente,
                cancelado_em__isnull=True,
            )
            .order_by("-data_recebimento")
            .values_list("data_recebimento", flat=True)
            .first()
        )
    return ContextoFinanceiroCliente(
        a_receber=resumo.a_receber,
        vencido=resumo.vencido,
        proximo_vencimento=prox.strftime("%d/%m/%Y") if prox else None,
        ultimo_recebimento=ultimo.strftime("%d/%m/%Y") if ultimo else None,
    )


def _health(
    *,
    cliente,
    caps: CapsClientePro,
    temp: str,
    proxima: ProximaAcaoResumo,
    ultima: InteracaoResumo,
    fin: ContextoFinanceiroCliente | None,
    organization,
    agora,
) -> HealthRelacionamento:
    motivos: list[tuple[int, str]] = []
    tem_registro = False

    if caps.financeiro and fin is not None and fin.vencido and fin.vencido > 0:
        tem_registro = True
        motivos.append((90, "Há cobrança com saldo vencido — atenção operacional, não juízo sobre o cliente."))
    if caps.agenda:
        atrasadas = (
            _tarefas_qs(organization, cliente)
            .filter(
                status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
                prazo__lt=agora.date(),
            )
            .exists()
        )
        if atrasadas:
            tem_registro = True
            motivos.append(
                (
                    82,
                    "Há tarefa com data registrada ultrapassada. Isso não significa prazo jurídico perdido.",
                )
            )
        if proxima.existe:
            tem_registro = True

    if cliente.fase_funil == "aguardando_decisao" and not proxima.existe:
        tem_registro = True
        motivos.append((76, "Proposta aguardando decisão sem próxima ação registrada."))
    if temp in {TEMP_QUENTE, TEMP_FERVENDO} and not proxima.existe:
        tem_registro = True
        motivos.append((74, "Oportunidade quente/fervendo sem próxima ação registrada."))
    if cliente.status == "em_prospeccao" and not proxima.existe:
        motivos.append((70, "Nenhuma próxima ação registrada no relacionamento."))
        tem_registro = True

    if ultima.quando:
        tem_registro = True
        dias = (agora - ultima.quando).days
        if dias >= DIAS_SEM_INTERACAO_ATENCAO:
            motivos.append(
                (65, f"Sem interação relevante registrada há {dias} dias. Sem interação ≠ cliente inativo.")
            )
    elif cliente.status == "em_prospeccao":
        motivos.append((50, "Nenhuma interação humana registrada além do cadastro."))

    if not tem_registro and not motivos:
        return HealthRelacionamento(
            estado="dados_insuficientes",
            label="Dados insuficientes",
            motivos=("Ausência de registros não significa relacionamento saudável.",),
            score=0,
        )

    if not motivos:
        return HealthRelacionamento(
            estado="saudavel",
            label="Saudável",
            motivos=("Nenhum sinal operacional de atenção no momento.",),
            score=20,
        )

    motivos.sort(key=lambda x: x[0], reverse=True)
    score = motivos[0][0]
    reasons = tuple(m[1] for m in motivos[:3])
    if score >= 80:
        estado, label = "critico", "Crítico"
    elif score >= 60:
        estado, label = "atencao", "Atenção"
    else:
        estado, label = "saudavel", "Saudável"
    return HealthRelacionamento(estado=estado, label=label, motivos=reasons, score=score)


def _nba(
    *,
    cliente,
    caps: CapsClientePro,
    health: HealthRelacionamento,
    proxima: ProximaAcaoResumo,
    temp: str,
    fin: ContextoFinanceiroCliente | None,
) -> NextBestAction:
    cid = cliente.pk
    if proxima.ja_agendada:
        return NextBestAction(
            key="acompanhar_existente",
            titulo="Acompanhar ação já agendada",
            motivo=f"Já existe próxima ação: {proxima.titulo} em {proxima.quando_label}.",
            acao="Executar ou acompanhar o compromisso/tarefa existente — não criar duplicata.",
            url=proxima.url or _url_agenda(cid),
            cta="Ver na agenda",
            ja_existe=True,
        )
    if caps.financeiro and fin is not None and fin.vencido and fin.vencido > 0:
        return NextBestAction(
            key="acompanhar_cobranca",
            titulo="Acompanhar cobrança vencida",
            motivo="Há valor vencido operacional neste relacionamento.",
            acao="Revisar as cobranças em aberto no Financeiro.",
            url=_url_cobrancas(cid),
            cta="Ver cobranças",
            ja_existe=False,
        )
    if cliente.fase_funil == "aguardando_decisao":
        return NextBestAction(
            key="revisar_proposta",
            titulo="Revisar proposta / follow-up de decisão",
            motivo="Fase aguardando decisão sem próxima ação registrada.",
            acao="Agendar follow-up comercial para retomar a decisão.",
            url=_url_agenda(cid, modal="compromisso"),
            cta="Agendar contato",
            ja_existe=False,
        )
    if temp in {TEMP_QUENTE, TEMP_FERVENDO}:
        return NextBestAction(
            key="followup_quente",
            titulo="Realizar follow-up",
            motivo="Temperatura elevada sem próxima ação registrada.",
            acao="Agendar o próximo contato comercial.",
            url=_url_agenda(cid, modal="compromisso"),
            cta="Agendar follow-up",
            ja_existe=False,
        )
    if health.estado == "dados_insuficientes":
        return NextBestAction(
            key="dados_insuficientes",
            titulo="Registrar a primeira interação",
            motivo="Não há eventos suficientes para priorizar o relacionamento.",
            acao="Registrar um contato, compromisso ou relatório de prospecção.",
            url=_url_cliente(cid),
            cta="Ver cliente",
            ja_existe=False,
        )
    if health.estado == "saudavel":
        return NextBestAction(
            key="nenhuma",
            titulo="Nenhuma ação prioritária identificada",
            motivo="O relacionamento não apresenta sinal operacional que exija ação agora.",
            acao="Manter o acompanhamento habitual. Não inventar tarefa.",
            url=_url_cliente(cid),
            cta="Ver cliente",
            ja_existe=False,
        )
    return NextBestAction(
        key="agendar_contato",
        titulo="Agendar contato",
        motivo="Não há próxima ação registrada.",
        acao="Definir o próximo passo na Agenda.",
        url=_url_agenda(cid, modal="compromisso"),
        cta="Agendar contato",
        ja_existe=False,
    )


def _advisor(nba: NextBestAction, health: HealthRelacionamento, fin, caps) -> AdvisorCliente:
    impacto = None
    if (
        caps.financeiro
        and fin is not None
        and nba.key == "acompanhar_cobranca"
        and fin.vencido
    ):
        impacto = f"R$ {fin.vencido} em valor vencido operacional — não é juízo sobre o cliente."
    status = health.estado
    if nba.key == "nenhuma":
        status = "saudavel"
    evidence = nba.motivo
    if health.motivos:
        evidence = health.motivos[0]
    return AdvisorCliente(
        principal=nba if nba.key != "nenhuma" else nba,
        status=status,
        evidence=evidence,
        impacto=impacto,
        criterios=CRITERIOS,
    )


def montar_clientes_pro(
    organization,
    cliente,
    *,
    caps: CapsClientePro,
    incluir_titulos: bool = False,
) -> PainelClientePro:
    if organization is None or cliente is None:
        return painel_vazio(cliente, caps=caps)
    if getattr(cliente, "organization_id", None) != organization.pk:
        return painel_vazio(cliente, caps=caps)

    agora = timezone.now()
    temp_key, _ = _temperatura_cliente(cliente, organization, agora)
    desfecho = _desfecho(cliente, organization)
    motivo = None
    if desfecho == "perdido":
        motivo = motivo_perda_label(parse_motivo_perda(cliente.relatorio_prospeccao))

    eventos = _coletar_eventos(
        organization, cliente, caps, incluir_titulos=incluir_titulos
    )
    ultima = _ultima_interacao(eventos)
    proxima = _proxima_existente(organization, cliente, caps, agora)
    fin = _contexto_financeiro(organization, cliente, caps)
    health = _health(
        cliente=cliente,
        caps=caps,
        temp=temp_key,
        proxima=proxima,
        ultima=ultima,
        fin=fin,
        organization=organization,
        agora=agora,
    )
    nba = _nba(
        cliente=cliente,
        caps=caps,
        health=health,
        proxima=proxima,
        temp=temp_key,
        fin=fin,
    )
    origem_label = cliente.get_origem_display() or "Origem não identificada"
    if not cliente.origem:
        origem_label = "Origem não identificada"

    return PainelClientePro(
        status=cliente.status,
        status_label=cliente.get_status_display(),
        fase=cliente.fase_funil or "",
        fase_label=cliente.get_fase_funil_display() if cliente.status == "em_prospeccao" else "Não aplicável",
        origem=cliente.origem or "",
        origem_label=origem_label,
        atribuicao_confiavel=bool(cliente.atribuicao_confiavel),
        temperatura=temp_key,
        temperatura_label=TEMP_LABELS.get(temp_key, "Não classificado"),
        ultima_interacao=ultima,
        proxima_acao=proxima,
        health=health,
        nba=nba,
        advisor=_advisor(nba, health, fin, caps),
        comercial=ContextoComercialCliente(
            fase=cliente.fase_funil or "",
            fase_label=cliente.get_fase_funil_display(),
            temperatura=temp_key,
            temperatura_label=TEMP_LABELS.get(temp_key, "Não classificado"),
            desfecho=desfecho,
            motivo_perda=motivo,
            valor_potencial=None,
        ),
        financeiro=fin,
        timeline=tuple(eventos),
        caps=caps,
        tenant_ok=True,
    )


def sinais_listagem_clientes(
    organization,
    cliente_ids: list[int],
    *,
    caps: CapsClientePro,
) -> dict[int, SinalListagem]:
    """Batch Organization-scoped. Sem N+1 por cliente."""
    vazio = SinalListagem(
        health="dados_insuficientes",
        health_label="Dados insuficientes",
        tem_proxima=False,
        atencao=False,
    )
    if organization is None or not cliente_ids:
        return {cid: vazio for cid in cliente_ids}

    agora = timezone.now()
    hoje = agora.date()
    from usuarios.models import Cliente

    clientes = {
        c.pk: c
        for c in Cliente.objects.filter(
            organization=organization, pk__in=cliente_ids
        ).only("id", "status", "fase_funil")
    }

    futuros: set[int] = set()
    atrasadas: set[int] = set()
    if caps.agenda:
        futuros = set(
            Compromisso.objects.filter(
                organization=organization,
                cliente_id__in=cliente_ids,
                data_hora__gte=agora,
            )
            .exclude(status=StatusCompromisso.CANCELADO)
            .values_list("cliente_id", flat=True)
        )
        atrasadas = set(
            Tarefa.objects.filter(
                organization=organization,
                cliente_id__in=cliente_ids,
                status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
                prazo__lt=hoje,
            ).values_list("cliente_id", flat=True)
        )
        pendentes = set(
            Tarefa.objects.filter(
                organization=organization,
                cliente_id__in=cliente_ids,
                status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
            )
            .exclude(prazo=None)
            .values_list("cliente_id", flat=True)
        )
        futuros |= pendentes

    vencidos: set[int] = set()
    if caps.financeiro:
        from financeiro.choices import StatusCobranca
        from financeiro.models import Cobranca

        vencidos = set(
            Cobranca.objects.filter(
                organization=organization,
                cliente_id__in=cliente_ids,
                status=StatusCobranca.OVERDUE,
            ).values_list("cliente_id", flat=True)
        )

    out: dict[int, SinalListagem] = {}
    for cid in cliente_ids:
        cli = clientes.get(cid)
        if cli is None:
            out[cid] = vazio
            continue
        tem_proxima = cid in futuros
        if caps.financeiro and cid in vencidos:
            health, label, atencao = "critico", "Crítico", True
        elif caps.agenda and cid in atrasadas:
            health, label, atencao = "critico", "Crítico", True
        elif cli.fase_funil == "aguardando_decisao" and not tem_proxima:
            health, label, atencao = "atencao", "Atenção", True
        elif cli.status == "em_prospeccao" and not tem_proxima:
            health, label, atencao = "atencao", "Atenção", True
        elif tem_proxima or cli.status == "ativo":
            health, label, atencao = "saudavel", "Saudável", False
        else:
            health, label, atencao = "dados_insuficientes", "Dados insuficientes", False
        out[cid] = SinalListagem(
            health=health,
            health_label=label,
            tem_proxima=tem_proxima,
            atencao=atencao,
        )
    return out
