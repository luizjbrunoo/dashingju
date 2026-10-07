"""
AGENDA PRO — criticidade operacional, prevenção e Assistente/Advisor.

Derivado on-read. Sem LLM/ML. Sem persistência. Organization-scoped.
Não é motor de conclusão jurídica.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from usuarios.choices import (
    Prioridade,
    Recorrencia,
    StatusCompromisso,
    StatusConfirmacaoConsulta,
    StatusTarefa,
    TipoCompromisso,
)
from usuarios.services.compromisso_prazos import (
    prazo_interno_vencido,
    prazo_oficial_vencido,
)

NIVEL_CRITICO = "critico"
NIVEL_ATENCAO = "atencao"
NIVEL_NORMAL = "normal"
NIVEL_INSUFICIENTE = "dados_insuficientes"

LABEL_NIVEL = {
    NIVEL_CRITICO: "Crítico operacional",
    NIVEL_ATENCAO: "Atenção",
    NIVEL_NORMAL: "Normal",
    NIVEL_INSUFICIENTE: "Dados insuficientes",
}

_RANK = {
    NIVEL_CRITICO: 3,
    NIVEL_ATENCAO: 2,
    NIVEL_NORMAL: 1,
    NIVEL_INSUFICIENTE: 0,
}

STATUS_ABERTOS_C = frozenset(
    {StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO}
)
STATUS_ABERTOS_T = frozenset({StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO})
TIPOS_JANELA_ALTA = frozenset({TipoCompromisso.AUDIENCIA, TipoCompromisso.PRAZO})
TIPOS_CONFIRMACAO = frozenset(
    {TipoCompromisso.CONSULTA, TipoCompromisso.AUDIENCIA}
)

PROXIMIDADE_DIAS = 2
CONCENTRACAO_LIMITE = 4
SECUNDARIOS_MAX = 2

CRITERIOS = (
    "Criticidade operacional derivada: vencido, proximidade, prioridade explícita, "
    "tipo, status, responsável e confirmação estruturada. "
    "Tarefa vencida ≠ prazo jurídico perdido. "
    "Data ultrapassada ≠ intempestividade. "
    "Ausência de confirmação ≠ evento não realizado. "
    "Criticidade operacional ≠ conclusão jurídica. "
    "Concentração ≠ sobrecarga humana comprovada. "
    "Sem LLM/ML."
)

FRASES_JURIDICAS_PROIBIDAS = (
    "prazo processual perdido",
    "intempestividade",
    "preclusão",
    "perda de direito",
    "perda de audiência",
    "falha profissional",
    "advogado sobrecarregado",
)


@dataclass(frozen=True)
class CapsAgendaPro:
    ver_financeiro: bool = False
    ver_auditoria: bool = False
    ver_cliente: bool = True


@dataclass(frozen=True)
class CriticidadeItem:
    nivel: str
    label: str
    reasons: tuple[str, ...]
    origem: str
    vencido: bool
    sem_responsavel: bool
    confirmacao: str  # confirmado | pendente | realizado | na | encerrado
    elevado: bool


@dataclass(frozen=True)
class ConflitoHorario:
    responsavel_id: int
    titulo_a: str
    titulo_b: str
    quando: str


@dataclass(frozen=True)
class ConcentracaoAgenda:
    responsavel_id: int | None
    quantidade: int
    janela: str
    evidencias: tuple[str, ...]


@dataclass(frozen=True)
class PrioridadeAgenda:
    key: str
    titulo: str
    evidence: str
    contexto: str
    acao: str
    url: str
    cta: str
    nivel: str
    item_tipo: str
    item_id: int | None


@dataclass(frozen=True)
class AdvisorAgenda:
    principal: PrioridadeAgenda | None
    secundarios: tuple[PrioridadeAgenda, ...]
    status: str  # critico | atencao | saudavel | vazio | dados_insuficientes
    criterios: str


@dataclass(frozen=True)
class KpisAgendaPro:
    criticos: int = 0
    atencao: int = 0
    vencidos: int = 0
    proximos: int = 0
    concluidos_hoje: int = 0


@dataclass(frozen=True)
class AgendaProPainel:
    advisor: AdvisorAgenda
    kpis: KpisAgendaPro
    conflitos: tuple[ConflitoHorario, ...]
    concentracoes: tuple[ConcentracaoAgenda, ...]
    criterios: str = CRITERIOS

    @classmethod
    def vazio(cls, status: str = "vazio") -> AgendaProPainel:
        return cls(
            advisor=AdvisorAgenda(
                principal=None,
                secundarios=(),
                status=status,
                criterios=CRITERIOS,
            ),
            kpis=KpisAgendaPro(),
            conflitos=(),
            concentracoes=(),
        )


def _ref(ref=None):
    return ref or timezone.localdate()


def _aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _data_compromisso(compromisso) -> datetime | None:
    if not compromisso.data_hora:
        return None
    return timezone.localtime(_aware(compromisso.data_hora))


def origem_evento(obj) -> str:
    meta = getattr(obj, "metadados", None) or {}
    if meta.get("cobranca_id"):
        return "financeiro"
    if meta.get("serie_raiz_id"):
        return "recorrencia"
    recorrencia = getattr(obj, "recorrencia", "") or ""
    if recorrencia and recorrencia != Recorrencia.NAO_REPETIR:
        return "recorrencia"
    return "manual"


def _confirmacao_compromisso(compromisso) -> str:
    status = compromisso.status
    if status == StatusCompromisso.REALIZADO:
        return "realizado"
    if status in (
        StatusCompromisso.CANCELADO,
        StatusCompromisso.NAO_COMPARECEU,
    ):
        return "encerrado"
    if compromisso.tipo == TipoCompromisso.CONSULTA:
        conf = (compromisso.confirmacao_consulta or "").strip()
        if conf == StatusConfirmacaoConsulta.CONFIRMADA:
            return "confirmado"
        if conf == StatusConfirmacaoConsulta.CANCELADA:
            return "encerrado"
        if conf in ("", StatusConfirmacaoConsulta.PENDENTE):
            return "pendente"
    if status == StatusCompromisso.CONFIRMADO:
        return "confirmado"
    if status == StatusCompromisso.AGENDADO and compromisso.tipo in TIPOS_CONFIRMACAO:
        return "pendente"
    return "na"


def _max_nivel(atual: str, candidato: str) -> str:
    if _RANK.get(candidato, 0) > _RANK.get(atual, 0):
        return candidato
    return atual


def classificar_compromisso(compromisso, ref=None) -> CriticidadeItem:
    ref = _ref(ref)
    reasons: list[str] = []
    nivel = NIVEL_NORMAL
    origem = origem_evento(compromisso)
    confirmacao = _confirmacao_compromisso(compromisso)
    dt = _data_compromisso(compromisso)
    sem_responsavel = compromisso.responsavel_id is None
    vencido = False

    if dt is None:
        return CriticidadeItem(
            nivel=NIVEL_INSUFICIENTE,
            label=LABEL_NIVEL[NIVEL_INSUFICIENTE],
            reasons=("compromisso sem data/hora estruturada.",),
            origem=origem,
            vencido=False,
            sem_responsavel=sem_responsavel,
            confirmacao=confirmacao,
            elevado=False,
        )

    if compromisso.status not in STATUS_ABERTOS_C:
        return CriticidadeItem(
            nivel=NIVEL_NORMAL,
            label=LABEL_NIVEL[NIVEL_NORMAL],
            reasons=("item já encerrado operacionalmente.",),
            origem=origem,
            vencido=False,
            sem_responsavel=sem_responsavel,
            confirmacao=confirmacao,
            elevado=False,
        )

    dia = dt.date()
    delta = (dia - ref).days
    tipo = compromisso.tipo
    prio = compromisso.prioridade

    if delta < 0:
        vencido = True
        if tipo in TIPOS_JANELA_ALTA or prio == Prioridade.URGENTE:
            nivel = NIVEL_CRITICO
        else:
            nivel = NIVEL_ATENCAO
        reasons.append(
            "data ultrapassada e item ainda pendente (operacional; "
            "não é intempestividade nem prazo jurídico perdido)."
        )
    elif delta == 0:
        if tipo in TIPOS_JANELA_ALTA or prio == Prioridade.URGENTE:
            nivel = NIVEL_CRITICO
            reasons.append("ocorre hoje, ainda pendente e com alta relevância operacional.")
        else:
            nivel = NIVEL_ATENCAO
            reasons.append("compromisso ocorre hoje e ainda está pendente.")
    elif delta <= PROXIMIDADE_DIAS:
        if tipo in TIPOS_JANELA_ALTA or prio in (Prioridade.URGENTE, Prioridade.ALTA):
            nivel = _max_nivel(nivel, NIVEL_ATENCAO)
            reasons.append("proximidade temporal com item ainda pendente.")

    if tipo == TipoCompromisso.PRAZO and (
        prazo_interno_vencido(compromisso, ref) or prazo_oficial_vencido(compromisso, ref)
    ):
        nivel = NIVEL_CRITICO
        vencido = True
        reasons.append(
            "prazo interno ou oficial com data ultrapassada no cadastro "
            "(sinal operacional; não conclui perda de prazo jurídico)."
        )

    if prio == Prioridade.URGENTE and nivel != NIVEL_CRITICO:
        nivel = _max_nivel(nivel, NIVEL_ATENCAO)
        reasons.append("prioridade explícita urgente (não é gravidade jurídica).")

    if sem_responsavel:
        nivel = _max_nivel(nivel, NIVEL_ATENCAO)
        reasons.append("sem responsável atribuído.")

    if confirmacao == "pendente" and delta <= 1:
        nivel = _max_nivel(nivel, NIVEL_ATENCAO)
        reasons.append(
            "pendente de confirmação estruturada "
            "(ausência de confirmação ≠ evento não realizado)."
        )

    if origem == "financeiro":
        reasons.append(
            "origem financeira via lembrete de cobrança "
            "(evento de cobrança ≠ recebimento)."
        )

    if not reasons:
        reasons.append("sem sinais operacionais de atenção agora.")

    return CriticidadeItem(
        nivel=nivel,
        label=LABEL_NIVEL[nivel],
        reasons=tuple(reasons),
        origem=origem,
        vencido=vencido,
        sem_responsavel=sem_responsavel,
        confirmacao=confirmacao,
        elevado=nivel in (NIVEL_CRITICO, NIVEL_ATENCAO),
    )


def classificar_tarefa(tarefa, ref=None) -> CriticidadeItem:
    ref = _ref(ref)
    reasons: list[str] = []
    nivel = NIVEL_NORMAL
    origem = origem_evento(tarefa)
    sem_responsavel = tarefa.responsavel_id is None
    vencido = False
    confirmacao = "na"

    if tarefa.status not in STATUS_ABERTOS_T:
        return CriticidadeItem(
            nivel=NIVEL_NORMAL,
            label=LABEL_NIVEL[NIVEL_NORMAL],
            reasons=("tarefa já encerrada operacionalmente.",),
            origem=origem,
            vencido=False,
            sem_responsavel=sem_responsavel,
            confirmacao=confirmacao,
            elevado=False,
        )

    if not tarefa.prazo:
        return CriticidadeItem(
            nivel=NIVEL_INSUFICIENTE,
            label=LABEL_NIVEL[NIVEL_INSUFICIENTE],
            reasons=("tarefa pendente sem vencimento estruturado.",),
            origem=origem,
            vencido=False,
            sem_responsavel=sem_responsavel,
            confirmacao=confirmacao,
            elevado=False,
        )

    delta = (tarefa.prazo - ref).days
    prio = tarefa.prioridade

    if delta < 0:
        vencido = True
        nivel = NIVEL_CRITICO if prio in (Prioridade.URGENTE, Prioridade.ALTA) else NIVEL_ATENCAO
        reasons.append(
            "tarefa vencida (operacional; não significa prazo jurídico perdido)."
        )
    elif delta == 0:
        nivel = NIVEL_CRITICO if prio == Prioridade.URGENTE else NIVEL_ATENCAO
        reasons.append("vencimento hoje e ainda pendente.")
    elif delta <= PROXIMIDADE_DIAS:
        if prio in (Prioridade.URGENTE, Prioridade.ALTA):
            nivel = NIVEL_ATENCAO
            reasons.append("proximidade do vencimento com prioridade elevada.")

    if prio == Prioridade.URGENTE and nivel != NIVEL_CRITICO:
        nivel = _max_nivel(nivel, NIVEL_ATENCAO)
        reasons.append("prioridade explícita urgente (não é gravidade jurídica).")

    if sem_responsavel:
        nivel = _max_nivel(nivel, NIVEL_ATENCAO)
        reasons.append("sem responsável atribuído.")

    if not reasons:
        reasons.append("sem sinais operacionais de atenção agora.")

    return CriticidadeItem(
        nivel=nivel,
        label=LABEL_NIVEL[nivel],
        reasons=tuple(reasons),
        origem=origem,
        vencido=vencido,
        sem_responsavel=sem_responsavel,
        confirmacao=confirmacao,
        elevado=nivel in (NIVEL_CRITICO, NIVEL_ATENCAO),
    )


def anexar_criticidade_agenda(compromissos, tarefas, ref=None) -> None:
    ref = _ref(ref)
    for compromisso in compromissos:
        compromisso.agenda_pro = classificar_compromisso(compromisso, ref)
    for tarefa in tarefas:
        tarefa.agenda_pro = classificar_tarefa(tarefa, ref)


def _url_compromisso(pk: int) -> str:
    return f"{reverse('agenda')}?{urlencode({'modal': 'compromisso', 'compromisso_id': pk})}"


def _url_tarefa(pk: int) -> str:
    return f"{reverse('agenda')}?{urlencode({'modal': 'tarefa', 'tarefa_id': pk})}"


def _url_cliente(pk: int) -> str:
    return reverse("cliente", kwargs={"id": pk})


def _nome(usuario) -> str:
    if not usuario:
        return ""
    return usuario.get_full_name() or usuario.username


def _minuto(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def detectar_conflitos(compromissos) -> tuple[ConflitoHorario, ...]:
    abertos = [
        c
        for c in compromissos
        if c.status in STATUS_ABERTOS_C and c.responsavel_id and c.data_hora
    ]
    vistos: set[tuple[int, int]] = set()
    conflitos: list[ConflitoHorario] = []
    for i, a in enumerate(abertos):
        sa = timezone.localtime(_aware(a.data_hora))
        ea = timezone.localtime(_aware(a.data_hora_fim)) if a.data_hora_fim else None
        for b in abertos[i + 1 :]:
            if a.responsavel_id != b.responsavel_id:
                continue
            par = (min(a.pk, b.pk), max(a.pk, b.pk))
            if par in vistos:
                continue
            sb = timezone.localtime(_aware(b.data_hora))
            eb = timezone.localtime(_aware(b.data_hora_fim)) if b.data_hora_fim else None
            choca = False
            if ea and eb:
                choca = sa < eb and sb < ea
            else:
                choca = _minuto(sa) == _minuto(sb)
            if not choca:
                continue
            vistos.add(par)
            conflitos.append(
                ConflitoHorario(
                    responsavel_id=a.responsavel_id,
                    titulo_a=a.titulo,
                    titulo_b=b.titulo,
                    quando=sa.strftime("%d/%m %H:%M"),
                )
            )
    return tuple(conflitos)


def detectar_concentracoes(compromissos, ref=None) -> tuple[ConcentracaoAgenda, ...]:
    ref = _ref(ref)
    por_resp: dict[int | None, list] = {}
    for c in compromissos:
        if c.status not in STATUS_ABERTOS_C or not c.data_hora:
            continue
        dia = timezone.localtime(_aware(c.data_hora)).date()
        if dia != ref:
            continue
        por_resp.setdefault(c.responsavel_id, []).append(c)
    resultado: list[ConcentracaoAgenda] = []
    for rid, itens in por_resp.items():
        if len(itens) < CONCENTRACAO_LIMITE:
            continue
        resultado.append(
            ConcentracaoAgenda(
                responsavel_id=rid,
                quantidade=len(itens),
                janela=f"hoje ({ref.strftime('%d/%m/%Y')})",
                evidencias=tuple(c.titulo for c in itens[:6]),
            )
        )
    return tuple(resultado)


def _itens_janela(organization, ref):
    from usuarios.services.agenda import _base_compromissos, _base_tarefas

    if organization is None:
        return [], []
    ini = ref - timedelta(days=30)
    fim = ref + timedelta(days=PROXIMIDADE_DIAS)
    compromissos = list(
        _base_compromissos(organization)
        .filter(
            data_hora__date__gte=ini,
            data_hora__date__lte=fim,
            status__in=list(STATUS_ABERTOS_C),
        )
        .order_by("data_hora")[:250]
    )
    realizados_hoje = list(
        _base_compromissos(organization).filter(
            data_hora__date=ref,
            status=StatusCompromisso.REALIZADO,
        )[:50]
    )
    tarefas = list(
        _base_tarefas(organization)
        .filter(status__in=list(STATUS_ABERTOS_T))
        .order_by("prazo", "titulo")[:250]
    )
    tarefas_hoje_ok = list(
        _base_tarefas(organization).filter(
            prazo=ref,
            status=StatusTarefa.CONCLUIDA,
        )[:50]
    )
    return compromissos + realizados_hoje, tarefas + tarefas_hoje_ok


def _cta_item(item, item_tipo: str, caps: CapsAgendaPro) -> tuple[str, str]:
    if item_tipo == "compromisso":
        meta = item.metadados or {}
        cobranca_id = meta.get("cobranca_id")
        if caps.ver_financeiro and cobranca_id:
            return (
                reverse("financeiro_cobranca_detalhe", args=[cobranca_id]),
                "Abrir cobrança",
            )
        if caps.ver_cliente and item.cliente_id:
            return _url_cliente(item.cliente_id), "Abrir cliente"
        return _url_compromisso(item.pk), "Abrir compromisso"
    return _url_tarefa(item.pk), "Abrir tarefa"


def _prioridade_de(
    *,
    key: str,
    titulo: str,
    evidence: str,
    contexto: str,
    acao: str,
    item,
    item_tipo: str,
    caps: CapsAgendaPro,
    nivel: str,
) -> PrioridadeAgenda:
    url, cta = ("", "")
    item_id = None
    if item is not None:
        item_id = item.pk
        url, cta = _cta_item(item, item_tipo, caps)
    elif key == "concentracao":
        url, cta = reverse("agenda"), "Ver hoje"
        item_tipo = "agenda"
    elif key == "conflito":
        url, cta = reverse("agenda"), "Ver hoje"
        item_tipo = "agenda"
    return PrioridadeAgenda(
        key=key,
        titulo=titulo,
        evidence=evidence,
        contexto=contexto,
        acao=acao,
        url=url,
        cta=cta,
        nivel=nivel,
        item_tipo=item_tipo,
        item_id=item_id,
    )


def _escolher_advisor(
    compromissos,
    tarefas,
    conflitos: tuple[ConflitoHorario, ...],
    concentracoes: tuple[ConcentracaoAgenda, ...],
    caps: CapsAgendaPro,
    ref,
) -> AdvisorAgenda:
    candidatos: list[tuple[int, PrioridadeAgenda]] = []
    anexar_criticidade_agenda(compromissos, tarefas, ref)

    abertos_c = [c for c in compromissos if c.status in STATUS_ABERTOS_C]
    abertos_t = [t for t in tarefas if t.status in STATUS_ABERTOS_T]

    if conflitos:
        cf = conflitos[0]
        candidatos.append(
            (
                100,
                _prioridade_de(
                    key="conflito",
                    titulo="Conflito de horário observável",
                    evidence=(
                        f"Dois compromissos do mesmo responsável se interceptam "
                        f"({cf.titulo_a} e {cf.titulo_b} às {cf.quando})."
                    ),
                    contexto="Sobreposição objetiva de agenda; não avalia qualidade do trabalho.",
                    acao="Rever horários ou reatribuir um dos compromissos.",
                    item=None,
                    item_tipo="agenda",
                    caps=caps,
                    nivel=NIVEL_CRITICO,
                ),
            )
        )

    def _score_item(pro: CriticidadeItem, extra: int) -> int:
        return extra + _RANK.get(pro.nivel, 0)

    for t in abertos_t:
        pro = t.agenda_pro
        extra = 0
        if pro.vencido:
            extra += 40
        if pro.sem_responsavel:
            extra += 12
        if pro.nivel == NIVEL_CRITICO:
            extra += 20
        candidatos.append(
            (
                _score_item(pro, extra),
                _prioridade_de(
                    key=f"tarefa-{t.pk}",
                    titulo=t.titulo,
                    evidence=" ".join(pro.reasons),
                    contexto=(
                        f"Tarefa · {t.get_prioridade_display()}"
                        + (f" · {_nome(t.responsavel)}" if t.responsavel_id else " · sem responsável")
                    ),
                    acao="Concluir ou atualizar o vencimento operacional.",
                    item=t,
                    item_tipo="tarefa",
                    caps=caps,
                    nivel=pro.nivel,
                ),
            )
        )

    for c in abertos_c:
        pro = c.agenda_pro
        extra = 0
        if pro.vencido:
            extra += 38
        if c.tipo in TIPOS_JANELA_ALTA:
            extra += 10
        if pro.sem_responsavel:
            extra += 12
        if pro.nivel == NIVEL_CRITICO:
            extra += 18
        dt = _data_compromisso(c)
        quando = dt.strftime("%d/%m %H:%M") if dt else ""
        candidatos.append(
            (
                _score_item(pro, extra),
                _prioridade_de(
                    key=f"compromisso-{c.pk}",
                    titulo=c.titulo,
                    evidence=" ".join(pro.reasons),
                    contexto=(
                        f"{c.get_tipo_display()} · {quando}"
                        + (f" · {_nome(c.responsavel)}" if c.responsavel_id else " · sem responsável")
                    ),
                    acao=(
                        "Confirmar o compromisso."
                        if pro.confirmacao == "pendente"
                        else "Executar ou atualizar o status operacional."
                    ),
                    item=c,
                    item_tipo="compromisso",
                    caps=caps,
                    nivel=pro.nivel,
                ),
            )
        )

    if concentracoes:
        conc = concentracoes[0]
        candidatos.append(
            (
                55,
                _prioridade_de(
                    key="concentracao",
                    titulo="Concentração de compromissos nesta janela",
                    evidence=(
                        f"Há concentração de {conc.quantidade} compromissos {conc.janela}."
                    ),
                    contexto="Quantidade observada na janela; não comprova sobrecarga humana.",
                    acao="Redistribuir horários ou revisar o que ainda precisa acontecer hoje.",
                    item=None,
                    item_tipo="agenda",
                    caps=caps,
                    nivel=NIVEL_ATENCAO,
                ),
            )
        )

    relevantes = [
        (s, p)
        for s, p in candidatos
        if p.nivel in (NIVEL_CRITICO, NIVEL_ATENCAO) or p.key in ("conflito", "concentracao")
    ]
    if not abertos_c and not abertos_t:
        return AdvisorAgenda(
            principal=None,
            secundarios=(),
            status="vazio",
            criterios=CRITERIOS,
        )
    if not relevantes:
        return AdvisorAgenda(
            principal=None,
            secundarios=(),
            status="saudavel",
            criterios=CRITERIOS,
        )

    relevantes.sort(key=lambda par: (-par[0], par[1].titulo))
    principal = relevantes[0][1]
    secundarios = tuple(
        p for _, p in relevantes[1:] if p.key != principal.key
    )[:SECUNDARIOS_MAX]
    status = NIVEL_CRITICO if principal.nivel == NIVEL_CRITICO else NIVEL_ATENCAO
    return AdvisorAgenda(
        principal=principal,
        secundarios=secundarios,
        status=status,
        criterios=CRITERIOS,
    )


def _kpis_pro(compromissos, tarefas, ref) -> KpisAgendaPro:
    criticos = atencao = vencidos = proximos = concluidos = 0
    for c in compromissos:
        pro = getattr(c, "agenda_pro", None) or classificar_compromisso(c, ref)
        if c.status == StatusCompromisso.REALIZADO:
            dt = _data_compromisso(c)
            if dt and dt.date() == ref:
                concluidos += 1
            continue
        if pro.nivel == NIVEL_CRITICO:
            criticos += 1
        elif pro.nivel == NIVEL_ATENCAO:
            atencao += 1
        if pro.vencido:
            vencidos += 1
        dt = _data_compromisso(c)
        if dt and 0 < (dt.date() - ref).days <= PROXIMIDADE_DIAS and c.status in STATUS_ABERTOS_C:
            proximos += 1
    for t in tarefas:
        pro = getattr(t, "agenda_pro", None) or classificar_tarefa(t, ref)
        if t.status == StatusTarefa.CONCLUIDA:
            if t.prazo == ref:
                concluidos += 1
            continue
        if pro.nivel == NIVEL_CRITICO:
            criticos += 1
        elif pro.nivel == NIVEL_ATENCAO:
            atencao += 1
        if pro.vencido:
            vencidos += 1
        if t.prazo and 0 < (t.prazo - ref).days <= PROXIMIDADE_DIAS and t.status in STATUS_ABERTOS_T:
            proximos += 1
    return KpisAgendaPro(
        criticos=criticos,
        atencao=atencao,
        vencidos=vencidos,
        proximos=proximos,
        concluidos_hoje=concluidos,
    )


def montar_agenda_pro(
    organization,
    *,
    user=None,
    ref=None,
    caps: CapsAgendaPro | None = None,
    compromissos=None,
    tarefas=None,
) -> AgendaProPainel:
    """Painel PRO Organization-scoped. user é actor; não é tenant.

    Se compromissos/tarefas forem passados, respeitam o recorte da tela
    (escopo, filtros). Sempre Organization-scoped pelo caller.
    """
    del user  # actor reservado; tenancy é organization
    if organization is None:
        return AgendaProPainel.vazio("vazio")
    ref = _ref(ref)
    caps = caps or CapsAgendaPro()
    if compromissos is None or tarefas is None:
        compromissos, tarefas = _itens_janela(organization, ref)
    else:
        compromissos = list(compromissos)
        tarefas = list(tarefas)
    anexar_criticidade_agenda(compromissos, tarefas, ref)
    hoje_c = [
        c
        for c in compromissos
        if c.data_hora and timezone.localtime(_aware(c.data_hora)).date() == ref
    ]
    conflitos = detectar_conflitos(hoje_c)
    concentracoes = detectar_concentracoes(compromissos, ref)
    advisor = _escolher_advisor(
        compromissos, tarefas, conflitos, concentracoes, caps, ref
    )
    return AgendaProPainel(
        advisor=advisor,
        kpis=_kpis_pro(compromissos, tarefas, ref),
        conflitos=conflitos,
        concentracoes=concentracoes,
    )
