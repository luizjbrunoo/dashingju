"""Utilitários da agenda jurídica."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional
from urllib.parse import urlencode
import calendar
from collections import defaultdict

from django.db.models import Q, QuerySet
from django.http import HttpRequest, QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_date

from usuarios.choices import (
    EscopoAgenda,
    Prioridade,
    StatusCompromisso,
    StatusConfirmacaoConsulta,
    StatusTarefa,
    TipoCompromisso,
)
from usuarios.models import AgendaAuditLog, Cliente, Compromisso, Tarefa
from usuarios.services.agenda_equipe import (
    filtrar_compromissos_escopo,
    filtrar_tarefas_escopo,
)

VIEW_HOJE = "hoje"
VIEW_LISTA = "lista"
VIEW_SEMANA = "semana"
VIEW_MES = "mes"
VIEWS_VALIDAS = frozenset({VIEW_HOJE, VIEW_LISTA, VIEW_SEMANA, VIEW_MES})

MESES_PT = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)

DIAS_SEMANA_PT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


@dataclass
class AgendaFiltros:
    view: str = VIEW_HOJE
    data: Optional[date] = None
    cliente_id: Optional[int] = None
    tipo: str = ""
    responsavel_id: Optional[int] = None
    status_compromisso: str = ""
    status_tarefa: str = ""
    prioridade: str = ""
    escopo: str = EscopoAgenda.TODOS
    processo: str = ""

    @classmethod
    def from_get(cls, get_params) -> AgendaFiltros:
        view = (get_params.get("view") or VIEW_HOJE).strip()
        if view not in VIEWS_VALIDAS:
            view = VIEW_HOJE

        data_raw = (get_params.get("data") or "").strip()
        data = parse_date(data_raw) if data_raw else None

        escopo = (get_params.get("escopo") or EscopoAgenda.TODOS).strip()
        if escopo not in EscopoAgenda.values:
            escopo = EscopoAgenda.TODOS

        return cls(
            view=view,
            data=data,
            cliente_id=_int_param(get_params, "cliente"),
            tipo=(get_params.get("tipo") or "").strip(),
            responsavel_id=_int_param(get_params, "responsavel"),
            status_compromisso=(get_params.get("status_compromisso") or "").strip(),
            status_tarefa=(get_params.get("status") or "").strip(),
            prioridade=(get_params.get("prioridade") or "").strip(),
            escopo=escopo,
            processo=(get_params.get("processo") or "").strip(),
        )

    @classmethod
    def from_request(cls, request: HttpRequest) -> AgendaFiltros:
        if request.method == "POST":
            q = (request.POST.get("_agenda_q") or "").strip()
            if q:
                return cls.from_get(QueryDict(q))
        return cls.from_get(request.GET)

    @property
    def data_referencia(self) -> date:
        return self.data or timezone.localdate()

    def to_query_dict(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.view != VIEW_HOJE:
            params["view"] = self.view
        if self.data:
            params["data"] = self.data.isoformat()
        if self.cliente_id:
            params["cliente"] = str(self.cliente_id)
        if self.tipo:
            params["tipo"] = self.tipo
        if self.responsavel_id:
            params["responsavel"] = str(self.responsavel_id)
        if self.status_compromisso:
            params["status_compromisso"] = self.status_compromisso
        if self.status_tarefa:
            params["status"] = self.status_tarefa
        if self.prioridade:
            params["prioridade"] = self.prioridade
        if self.escopo and self.escopo != EscopoAgenda.TODOS:
            params["escopo"] = self.escopo
        if self.processo:
            params["processo"] = self.processo
        return params

    def query_string(self) -> str:
        return urlencode(self.to_query_dict())

    def with_data(self, nova_data: date) -> AgendaFiltros:
        return AgendaFiltros(
            view=self.view,
            data=nova_data,
            cliente_id=self.cliente_id,
            tipo=self.tipo,
            responsavel_id=self.responsavel_id,
            status_compromisso=self.status_compromisso,
            status_tarefa=self.status_tarefa,
            prioridade=self.prioridade,
            escopo=self.escopo,
            processo=self.processo,
        )


def inicio_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def fim_semana(d: date) -> date:
    return inicio_semana(d) + timedelta(days=6)


def inicio_mes(d: date) -> date:
    return d.replace(day=1)


def fim_mes(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def deslocar_mes(d: date, meses: int) -> date:
    month = d.month + meses
    year = d.year
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    ultimo = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, ultimo))


def periodo_view(filtros: AgendaFiltros) -> tuple[date, date] | None:
    ref = filtros.data_referencia
    if filtros.view == VIEW_SEMANA:
        return inicio_semana(ref), fim_semana(ref)
    if filtros.view == VIEW_MES:
        return inicio_mes(ref), fim_mes(ref)
    return None


def rotulo_periodo(filtros: AgendaFiltros) -> str:
    ref = filtros.data_referencia
    if filtros.view == VIEW_SEMANA:
        ini, fim = inicio_semana(ref), fim_semana(ref)
        return f"{ini.strftime('%d/%m')} – {fim.strftime('%d/%m/%Y')}"
    if filtros.view == VIEW_MES:
        return f"{MESES_PT[ref.month]} {ref.year}"
    return ""


def navegacao_periodo(filtros: AgendaFiltros) -> dict[str, str]:
    if filtros.view not in (VIEW_SEMANA, VIEW_MES):
        return {}

    ref = filtros.data_referencia
    hoje = timezone.localdate()

    if filtros.view == VIEW_SEMANA:
        anterior = filtros.with_data(ref - timedelta(days=7))
        proximo = filtros.with_data(ref + timedelta(days=7))
        atual = filtros.with_data(hoje)
    else:
        anterior = filtros.with_data(deslocar_mes(ref, -1))
        proximo = filtros.with_data(deslocar_mes(ref, 1))
        atual = filtros.with_data(hoje)

    return {
        "anterior": anterior.query_string(),
        "proximo": proximo.query_string(),
        "atual": atual.query_string(),
    }


@dataclass
class DiaAgenda:
    data: date
    label_dia: str = ""
    compromissos: list = field(default_factory=list)
    tarefas: list = field(default_factory=list)
    eh_hoje: bool = False


@dataclass
class ResumoDiaMes:
    compromissos: int = 0
    tarefas: int = 0
    audiencias: int = 0
    prazos: int = 0

    @property
    def total(self) -> int:
        return self.compromissos + self.tarefas

    @property
    def tem_itens(self) -> bool:
        return self.total > 0


@dataclass
class ResumoSemana:
    compromissos: int = 0
    tarefas: int = 0
    audiencias: int = 0
    prazos: int = 0
    atrasadas: int = 0


@dataclass
class CelulaMes:
    data: date
    no_mes: bool
    eh_hoje: bool
    compromissos: list = field(default_factory=list)
    tarefas: list = field(default_factory=list)
    resumo: ResumoDiaMes | None = None


_ORDEM_PRIORIDADE = {
    Prioridade.URGENTE: 0,
    Prioridade.ALTA: 1,
    Prioridade.NORMAL: 2,
    Prioridade.BAIXA: 3,
}


def _calcular_resumo_dia_mes(compromissos, tarefas) -> ResumoDiaMes:
    audiencias = sum(
        1 for c in compromissos if c.tipo == TipoCompromisso.AUDIENCIA
    )
    prazos = sum(1 for c in compromissos if c.tipo == TipoCompromisso.PRAZO)
    return ResumoDiaMes(
        compromissos=len(compromissos),
        tarefas=len(tarefas),
        audiencias=audiencias,
        prazos=prazos,
    )


def calcular_resumo_semana(dias: list[DiaAgenda], tarefas_atrasadas) -> ResumoSemana:
    resumo = ResumoSemana(atrasadas=len(tarefas_atrasadas))
    for dia in dias:
        for compromisso in dia.compromissos:
            resumo.compromissos += 1
            if compromisso.tipo == TipoCompromisso.AUDIENCIA:
                resumo.audiencias += 1
            elif compromisso.tipo == TipoCompromisso.PRAZO:
                resumo.prazos += 1
        resumo.tarefas += len(dia.tarefas)
    return resumo


def _ordenar_compromissos_dia(compromissos) -> list:
    return sorted(compromissos, key=lambda c: c.data_hora)


def _ordenar_tarefas_dia(tarefas) -> list:
    return sorted(
        tarefas,
        key=lambda t: (_ORDEM_PRIORIDADE.get(t.prioridade, 9), t.titulo),
    )


@dataclass
class ItemTimelineHoje:
    item_tipo: str
    sort_key: datetime
    hora_label: str
    compromisso: Compromisso | None = None
    tarefa: Tarefa | None = None
    atrasada: bool = False


@dataclass
class TimelineHoje:
    atrasadas: list[ItemTimelineHoje] = field(default_factory=list)
    eventos: list[ItemTimelineHoje] = field(default_factory=list)

    @property
    def vazia(self) -> bool:
        return not self.atrasadas and not self.eventos


@dataclass
class ItemListaAgenda:
    item_tipo: str
    data: date | None
    hora: str
    tipo_label: str
    titulo: str
    cliente_nome: str
    processo: str
    responsavel_nome: str
    status: str
    prioridade: str
    compromisso: Compromisso | None = None
    tarefa: Tarefa | None = None
    atrasada: bool = False
    sort_key: tuple = field(default_factory=tuple)


def _nome_responsavel(usuario) -> str:
    if not usuario:
        return ""
    return usuario.get_full_name() or usuario.username


def montar_timeline_hoje(compromissos, tarefas, ref: date) -> TimelineHoje:
    atrasadas: list[ItemTimelineHoje] = []
    eventos: list[ItemTimelineHoje] = []

    for compromisso in compromissos:
        dt = timezone.localtime(compromisso.data_hora)
        eventos.append(
            ItemTimelineHoje(
                item_tipo="compromisso",
                sort_key=dt,
                hora_label=dt.strftime("%H:%M"),
                compromisso=compromisso,
            )
        )

    fim_do_dia = timezone.make_aware(datetime.combine(ref, time(23, 59)))

    for tarefa in tarefas:
        if not tarefa.prazo:
            continue
        if tarefa.prazo < ref and tarefa.status in (
            StatusTarefa.PENDENTE,
            StatusTarefa.EM_ANDAMENTO,
        ):
            sort = timezone.make_aware(datetime.combine(tarefa.prazo, time.min))
            atrasadas.append(
                ItemTimelineHoje(
                    item_tipo="tarefa",
                    sort_key=sort,
                    hora_label=tarefa.prazo.strftime("%d/%m"),
                    tarefa=tarefa,
                    atrasada=True,
                )
            )
        elif tarefa.prazo == ref:
            eventos.append(
                ItemTimelineHoje(
                    item_tipo="tarefa",
                    sort_key=fim_do_dia,
                    hora_label="Prazo",
                    tarefa=tarefa,
                )
            )

    atrasadas.sort(key=lambda item: item.sort_key)
    eventos.sort(key=lambda item: item.sort_key)
    return TimelineHoje(atrasadas=atrasadas, eventos=eventos)


def montar_lista_unificada(compromissos, tarefas) -> list[ItemListaAgenda]:
    itens: list[ItemListaAgenda] = []

    for compromisso in compromissos:
        dt = timezone.localtime(compromisso.data_hora)
        itens.append(
            ItemListaAgenda(
                item_tipo="compromisso",
                data=dt.date(),
                hora=dt.strftime("%H:%M"),
                tipo_label=compromisso.get_tipo_display(),
                titulo=compromisso.titulo,
                cliente_nome=compromisso.cliente.nome if compromisso.cliente else "",
                processo=compromisso.processo_referencia or "",
                responsavel_nome=_nome_responsavel(compromisso.responsavel),
                status=compromisso.status,
                prioridade=compromisso.prioridade,
                compromisso=compromisso,
                sort_key=(dt.date(), dt.time(), 0, compromisso.pk),
            )
        )

    for tarefa in tarefas:
        prazo = tarefa.prazo
        itens.append(
            ItemListaAgenda(
                item_tipo="tarefa",
                data=prazo,
                hora="—",
                tipo_label="Tarefa",
                titulo=tarefa.titulo,
                cliente_nome=tarefa.cliente.nome if tarefa.cliente else "",
                processo=tarefa.processo_referencia or "",
                responsavel_nome=_nome_responsavel(tarefa.responsavel),
                status=tarefa.status,
                prioridade=tarefa.prioridade,
                tarefa=tarefa,
                atrasada=tarefa.atrasada,
                sort_key=(
                    prazo or date.min,
                    time.min,
                    1,
                    tarefa.pk,
                ),
            )
        )

    itens.sort(key=lambda item: item.sort_key)
    return itens


def _agrupar_compromissos_por_dia(compromissos) -> dict[date, list]:
    por_dia: dict[date, list] = defaultdict(list)
    for c in compromissos:
        dia = timezone.localtime(c.data_hora).date()
        por_dia[dia].append(c)
    return por_dia


def _agrupar_tarefas_por_dia(tarefas) -> dict[date, list]:
    por_dia: dict[date, list] = defaultdict(list)
    for t in tarefas:
        if t.prazo:
            por_dia[t.prazo].append(t)
    return por_dia


def montar_semana(
    compromissos, tarefas, filtros: AgendaFiltros
) -> tuple[list[DiaAgenda], list]:
    ini = inicio_semana(filtros.data_referencia)
    fim = fim_semana(filtros.data_referencia)
    hoje = timezone.localdate()
    comp_map = _agrupar_compromissos_por_dia(compromissos)

    tarefas_atrasadas = []
    tar_map: dict[date, list] = defaultdict(list)
    for t in tarefas:
        if not t.prazo:
            continue
        if (
            t.prazo < ini
            and t.status in (StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO)
        ):
            tarefas_atrasadas.append(t)
        elif ini <= t.prazo <= fim:
            tar_map[t.prazo].append(t)

    dias = []
    for i in range(7):
        d = ini + timedelta(days=i)
        comps = _ordenar_compromissos_dia(comp_map.get(d, []))
        tars = _ordenar_tarefas_dia(tar_map.get(d, []))
        dias.append(
            DiaAgenda(
                data=d,
                label_dia=DIAS_SEMANA_PT[d.weekday()],
                compromissos=comps,
                tarefas=tars,
                eh_hoje=d == hoje,
            )
        )
    tarefas_atrasadas.sort(
        key=lambda t: (t.prazo or date.min, _ORDEM_PRIORIDADE.get(t.prioridade, 9))
    )
    return dias, tarefas_atrasadas


def montar_grade_mes(compromissos, tarefas, filtros: AgendaFiltros) -> list[list[CelulaMes]]:
    ref = filtros.data_referencia
    mes_ini = inicio_mes(ref)
    mes_fim = fim_mes(ref)
    grid_ini = inicio_semana(mes_ini)
    hoje = timezone.localdate()
    comp_map = _agrupar_compromissos_por_dia(compromissos)
    tar_map = _agrupar_tarefas_por_dia(tarefas)

    semanas: list[list[CelulaMes]] = []
    cur = grid_ini
    for _ in range(6):
        semana: list[CelulaMes] = []
        for _ in range(7):
            comps = comp_map.get(cur, [])
            tars = tar_map.get(cur, [])
            semana.append(
                CelulaMes(
                    data=cur,
                    no_mes=mes_ini <= cur <= mes_fim,
                    eh_hoje=cur == hoje,
                    compromissos=comps,
                    tarefas=tars,
                    resumo=_calcular_resumo_dia_mes(comps, tars),
                )
            )
            cur += timedelta(days=1)
        semanas.append(semana)
    return semanas


@dataclass
class ItemAtencao:
    item_tipo: str
    item_id: int
    titulo: str
    motivo: str
    prioridade: str
    quando: str
    url: str = ""
    acao: str = ""


@dataclass
class AgendaKpis:
    compromissos_hoje: int = 0
    prazos_proximos: int = 0
    tarefas_atrasadas: int = 0
    audiencias_semana: int = 0


DIAS_PRAZOS_PROXIMOS = 7


def _url_agenda_compromisso(pk: int) -> str:
    from django.urls import reverse

    return f"{reverse('agenda')}?modal=compromisso&compromisso_id={pk}"


def _url_agenda_tarefa(pk: int) -> str:
    from django.urls import reverse

    return f"{reverse('agenda')}?modal=tarefa&tarefa_id={pk}"


def calcular_kpis(user, ref: date | None = None) -> AgendaKpis:
    ref = ref or timezone.localdate()
    ini_sem = inicio_semana(ref)
    fim_sem = fim_semana(ref)
    fim_proximos = ref + timedelta(days=DIAS_PRAZOS_PROXIMOS)

    base_c = _base_compromissos(user)
    base_t = _base_tarefas(user)

    tarefas_atrasadas = base_t.filter(
        prazo__lt=ref,
        status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
    ).count()

    prazos_proximos = (
        base_c.filter(
            tipo__in=[TipoCompromisso.PRAZO, TipoCompromisso.AUDIENCIA],
            data_hora__date__gt=ref,
            data_hora__date__lte=fim_proximos,
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        ).count()
        + base_t.filter(
            prazo__gt=ref,
            prazo__lte=fim_proximos,
            status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
        ).count()
    )

    return AgendaKpis(
        compromissos_hoje=base_c.filter(data_hora__date=ref).count(),
        prazos_proximos=prazos_proximos,
        tarefas_atrasadas=tarefas_atrasadas,
        audiencias_semana=base_c.filter(
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora__date__gte=ini_sem,
            data_hora__date__lte=fim_sem,
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        ).count(),
    )


def itens_atencao(user, ref: date | None = None, limit: int = 8) -> list[ItemAtencao]:
    ref = ref or timezone.localdate()
    fim_proximos = ref + timedelta(days=2)
    resultado: list[ItemAtencao] = []
    vistos: set[tuple[str, int]] = set()

    def adicionar(item: ItemAtencao) -> None:
        chave = (item.item_tipo, item.item_id)
        if chave in vistos or len(resultado) >= limit:
            return
        vistos.add(chave)
        resultado.append(item)

    try:
        from financeiro.services.cobranca_agenda import cobrancas_itens_atencao

        for item in cobrancas_itens_atencao(user, ref=ref, limit=limit):
            adicionar(item)
    except ImportError:
        pass

    for t in (
        _base_tarefas(user)
        .filter(
            prazo__lt=ref,
            status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
        )
        .order_by("prazo")
    ):
        dias = (ref - t.prazo).days
        adicionar(
            ItemAtencao(
                item_tipo="tarefa",
                item_id=t.pk,
                titulo=t.titulo,
                motivo=f"Atrasada há {dias} dia{'s' if dias != 1 else ''}",
                prioridade=t.prioridade,
                quando=t.prazo.strftime("%d/%m/%Y") if t.prazo else "",
                url=_url_agenda_tarefa(t.pk),
                acao="Concluir",
            )
        )

    for t in (
        _base_tarefas(user)
        .filter(
            prazo=ref,
            status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
        )
        .order_by("prioridade", "titulo")
    ):
        adicionar(
            ItemAtencao(
                item_tipo="tarefa",
                item_id=t.pk,
                titulo=t.titulo,
                motivo="Prazo hoje",
                prioridade=t.prioridade,
                quando=ref.strftime("%d/%m/%Y"),
                url=_url_agenda_tarefa(t.pk),
                acao="Concluir",
            )
        )

    for c in (
        _base_compromissos(user)
        .filter(
            tipo=TipoCompromisso.PRAZO,
            prazo_interno__isnull=False,
            prazo_interno__lt=ref,
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        )
        .order_by("prazo_interno", "titulo")
    ):
        dias = (ref - c.prazo_interno).days
        adicionar(
            ItemAtencao(
                item_tipo="compromisso",
                item_id=c.pk,
                titulo=c.titulo,
                motivo=f"Prazo interno vencido há {dias} dia{'s' if dias != 1 else ''}",
                prioridade=Prioridade.URGENTE,
                quando=c.prazo_interno.strftime("%d/%m/%Y"),
                url=_url_agenda_compromisso(c.pk),
            )
        )

    for c in (
        _base_compromissos(user)
        .filter(
            tipo=TipoCompromisso.PRAZO,
            prazo_interno__isnull=False,
            prazo_interno__gte=ref,
            prazo_interno__lte=fim_proximos,
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        )
        .order_by("prazo_interno", "titulo")
    ):
        adicionar(
            ItemAtencao(
                item_tipo="compromisso",
                item_id=c.pk,
                titulo=c.titulo,
                motivo="Prazo interno próximo",
                prioridade=c.prioridade or Prioridade.ALTA,
                quando=c.prazo_interno.strftime("%d/%m/%Y"),
                url=_url_agenda_compromisso(c.pk),
            )
        )

    for c in (
        _base_compromissos(user)
        .filter(
            tipo=TipoCompromisso.PRAZO,
            prazo_oficial__isnull=False,
            prazo_oficial__gte=ref,
            prazo_oficial__lte=ref + timedelta(days=3),
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        )
        .order_by("prazo_oficial", "titulo")
    ):
        adicionar(
            ItemAtencao(
                item_tipo="compromisso",
                item_id=c.pk,
                titulo=c.titulo,
                motivo="Prazo oficial próximo",
                prioridade=c.prioridade or Prioridade.ALTA,
                quando=c.prazo_oficial.strftime("%d/%m/%Y"),
                url=_url_agenda_compromisso(c.pk),
            )
        )

    for c in (
        _base_compromissos(user)
        .filter(
            tipo=TipoCompromisso.CONSULTA,
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        )
        .filter(
            Q(confirmacao_consulta="")
            | Q(confirmacao_consulta=StatusConfirmacaoConsulta.PENDENTE)
        )
        .filter(data_hora__date__gte=ref, data_hora__date__lte=ref + timedelta(days=3))
        .order_by("data_hora")
    ):
        adicionar(
            ItemAtencao(
                item_tipo="compromisso",
                item_id=c.pk,
                titulo=c.titulo,
                motivo="Consulta sem confirmação",
                prioridade=c.prioridade or Prioridade.NORMAL,
                quando=timezone.localtime(c.data_hora).strftime("%d/%m %H:%M"),
                url=_url_agenda_compromisso(c.pk),
                acao="Confirmar",
            )
        )

    for c in (
        _base_compromissos(user)
        .filter(
            data_hora__date=ref,
            prioridade__in=[Prioridade.URGENTE, Prioridade.ALTA],
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        )
        .order_by("data_hora")
    ):
        adicionar(
            ItemAtencao(
                item_tipo="compromisso",
                item_id=c.pk,
                titulo=c.titulo,
                motivo=f"Hoje · {c.get_tipo_display()}",
                prioridade=c.prioridade,
                quando=timezone.localtime(c.data_hora).strftime("%d/%m %H:%M"),
                url=_url_agenda_compromisso(c.pk),
            )
        )

    for c in (
        _base_compromissos(user)
        .filter(
            tipo__in=[TipoCompromisso.PRAZO, TipoCompromisso.AUDIENCIA],
            data_hora__date__gt=ref,
            data_hora__date__lte=fim_proximos,
            status__in=[StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO],
        )
        .order_by("data_hora")
    ):
        adicionar(
            ItemAtencao(
                item_tipo="compromisso",
                item_id=c.pk,
                titulo=c.titulo,
                motivo=f"Próximo · {c.get_tipo_display()}",
                prioridade=c.prioridade,
                quando=timezone.localtime(c.data_hora).strftime("%d/%m %H:%M"),
                url=_url_agenda_compromisso(c.pk),
            )
        )

    for t in (
        _base_tarefas(user)
        .filter(
            prioridade=Prioridade.URGENTE,
            status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
        )
        .exclude(prazo__lt=ref)
        .exclude(prazo=ref)
        .order_by("prazo", "titulo")[:3]
    ):
        adicionar(
            ItemAtencao(
                item_tipo="tarefa",
                item_id=t.pk,
                titulo=t.titulo,
                motivo="Prioridade urgente",
                prioridade=t.prioridade,
                quando=t.prazo.strftime("%d/%m/%Y") if t.prazo else "Sem prazo",
                url=_url_agenda_tarefa(t.pk),
                acao="Concluir",
            )
        )

    return resultado[:limit]


def contexto_visualizacao(
    filtros: AgendaFiltros, compromissos, tarefas
) -> dict:
    ctx: dict = {
        "nav_periodo": navegacao_periodo(filtros),
        "periodo_label": rotulo_periodo(filtros),
    }
    if filtros.view == VIEW_HOJE:
        ctx["timeline_hoje"] = montar_timeline_hoje(
            compromissos, tarefas, filtros.data_referencia
        )
    elif filtros.view == VIEW_LISTA:
        ctx["lista_unificada"] = montar_lista_unificada(compromissos, tarefas)
    elif filtros.view == VIEW_SEMANA:
        dias, atrasadas = montar_semana(compromissos, tarefas, filtros)
        ctx["semana_dias"] = dias
        ctx["tarefas_atrasadas"] = atrasadas
        ctx["resumo_semana"] = calcular_resumo_semana(dias, atrasadas)
        ctx["dias_semana_pt"] = DIAS_SEMANA_PT
    elif filtros.view == VIEW_MES:
        ctx["mes_semanas"] = montar_grade_mes(compromissos, tarefas, filtros)
        ctx["dias_semana_pt"] = DIAS_SEMANA_PT
    return ctx


def _int_param(get_params, name: str) -> Optional[int]:
    raw = (get_params.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _base_compromissos(user) -> QuerySet[Compromisso]:
    return (
        Compromisso.objects.filter(user=user)
        .exclude(status=StatusCompromisso.CANCELADO)
        .select_related("cliente", "responsavel")
        .prefetch_related("participantes__usuario")
    )


def _base_tarefas(user) -> QuerySet[Tarefa]:
    return (
        Tarefa.objects.filter(user=user)
        .exclude(status=StatusTarefa.CANCELADA)
        .select_related("cliente", "responsavel")
    )


def _aplicar_filtros_compromissos(qs: QuerySet[Compromisso], filtros: AgendaFiltros) -> QuerySet[Compromisso]:
    if filtros.cliente_id:
        qs = qs.filter(cliente_id=filtros.cliente_id)
    if filtros.tipo and filtros.tipo in TipoCompromisso.values:
        qs = qs.filter(tipo=filtros.tipo)
    if filtros.responsavel_id:
        qs = qs.filter(responsavel_id=filtros.responsavel_id)
    if filtros.status_compromisso and filtros.status_compromisso in StatusCompromisso.values:
        qs = qs.filter(status=filtros.status_compromisso)
    if filtros.prioridade and filtros.prioridade in Prioridade.values:
        qs = qs.filter(prioridade=filtros.prioridade)
    if filtros.processo:
        qs = qs.filter(processo_referencia__icontains=filtros.processo)
    return qs


def _aplicar_filtros_tarefas(qs: QuerySet[Tarefa], filtros: AgendaFiltros) -> QuerySet[Tarefa]:
    if filtros.cliente_id:
        qs = qs.filter(cliente_id=filtros.cliente_id)
    if filtros.responsavel_id:
        qs = qs.filter(responsavel_id=filtros.responsavel_id)
    if filtros.prioridade and filtros.prioridade in Prioridade.values:
        qs = qs.filter(prioridade=filtros.prioridade)
    if filtros.processo:
        qs = qs.filter(processo_referencia__icontains=filtros.processo)
    if filtros.status_tarefa == "pendente":
        qs = qs.exclude(status=StatusTarefa.CONCLUIDA)
    elif filtros.status_tarefa == "concluida":
        qs = qs.filter(status=StatusTarefa.CONCLUIDA)
    elif filtros.status_tarefa and filtros.status_tarefa in StatusTarefa.values:
        qs = qs.filter(status=filtros.status_tarefa)
    return qs


def compromissos_para_agenda(user, filtros: AgendaFiltros) -> QuerySet[Compromisso]:
    qs = filtrar_compromissos_escopo(
        _aplicar_filtros_compromissos(_base_compromissos(user), filtros),
        user,
        filtros.escopo,
    )

    if filtros.view == VIEW_HOJE:
        qs = qs.filter(data_hora__date=filtros.data_referencia)
    elif filtros.view in (VIEW_SEMANA, VIEW_MES):
        periodo = periodo_view(filtros)
        if periodo:
            ini, fim = periodo
            qs = qs.filter(data_hora__date__gte=ini, data_hora__date__lte=fim)
    elif filtros.data:
        qs = qs.filter(data_hora__date=filtros.data)

    return qs.order_by("data_hora")


def tarefas_para_agenda(user, filtros: AgendaFiltros) -> QuerySet[Tarefa]:
    if filtros.status_tarefa == StatusTarefa.CANCELADA:
        qs = Tarefa.objects.filter(
            user=user, status=StatusTarefa.CANCELADA
        ).select_related("cliente", "responsavel")
        qs = _aplicar_filtros_tarefas(qs, filtros)
        qs = filtrar_tarefas_escopo(qs, user, filtros.escopo)
        return qs.order_by("prazo", "criado_em")

    qs = _aplicar_filtros_tarefas(_base_tarefas(user), filtros)
    qs = filtrar_tarefas_escopo(qs, user, filtros.escopo)

    if filtros.view == VIEW_HOJE:
        hoje = filtros.data_referencia
        qs = qs.filter(
            Q(prazo=hoje)
            | Q(
                prazo__lt=hoje,
                status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
            )
        )
    elif filtros.view == VIEW_SEMANA:
        periodo = periodo_view(filtros)
        if periodo:
            ini, fim = periodo
            qs = qs.filter(
                Q(prazo__gte=ini, prazo__lte=fim)
                | Q(
                    prazo__lt=ini,
                    status__in=[StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO],
                )
            )
    elif filtros.view == VIEW_MES:
        periodo = periodo_view(filtros)
        if periodo:
            ini, fim = periodo
            qs = qs.filter(prazo__gte=ini, prazo__lte=fim)

    return qs.order_by("prazo", "criado_em")


def clientes_para_filtro(user) -> QuerySet[Cliente]:
    return Cliente.objects.filter(user=user).order_by("nome")


def registrar_audit(
    *,
    usuario,
    item_tipo: str,
    item_id: int,
    acao: str,
    campo: str = "",
    valor_anterior: str = "",
    valor_novo: str = "",
) -> AgendaAuditLog:
    return AgendaAuditLog.objects.create(
        usuario=usuario,
        item_tipo=item_tipo,
        item_id=item_id,
        acao=acao,
        campo=campo,
        valor_anterior=str(valor_anterior) if valor_anterior is not None else "",
        valor_novo=str(valor_novo) if valor_novo is not None else "",
    )


def registrar_edicao_compromisso(
    usuario,
    compromisso: Compromisso,
    *,
    prazo_oficial_antes,
    prazo_interno_antes,
    responsavel_antes_id,
) -> None:
    registrar_audit(
        usuario=usuario,
        item_tipo="compromisso",
        item_id=compromisso.pk,
        acao="alterado",
    )
    if prazo_oficial_antes != compromisso.prazo_oficial:
        registrar_audit(
            usuario=usuario,
            item_tipo="compromisso",
            item_id=compromisso.pk,
            acao="prazo_alterado",
            campo="prazo_oficial",
            valor_anterior=prazo_oficial_antes or "",
            valor_novo=compromisso.prazo_oficial or "",
        )
    if prazo_interno_antes != compromisso.prazo_interno:
        registrar_audit(
            usuario=usuario,
            item_tipo="compromisso",
            item_id=compromisso.pk,
            acao="prazo_alterado",
            campo="prazo_interno",
            valor_anterior=prazo_interno_antes or "",
            valor_novo=compromisso.prazo_interno or "",
        )
    if responsavel_antes_id != compromisso.responsavel_id:
        registrar_audit(
            usuario=usuario,
            item_tipo="compromisso",
            item_id=compromisso.pk,
            acao="responsavel_alterado",
            campo="responsavel",
            valor_anterior=responsavel_antes_id or "",
            valor_novo=compromisso.responsavel_id or "",
        )


def aplicar_status_tarefa(tarefa: Tarefa, novo_status: str, *, usuario) -> bool:
    if novo_status not in StatusTarefa.values:
        return False
    if tarefa.status == StatusTarefa.CANCELADA:
        return False
    if tarefa.status == novo_status:
        return False
    anterior = tarefa.status
    tarefa.status = novo_status
    tarefa.save()
    acao = "alterado"
    if novo_status == StatusTarefa.CONCLUIDA:
        acao = "concluido"
    elif novo_status == StatusTarefa.CANCELADA:
        acao = "cancelado"
    registrar_audit(
        usuario=usuario,
        item_tipo="tarefa",
        item_id=tarefa.pk,
        acao=acao,
        campo="status",
        valor_anterior=anterior,
        valor_novo=novo_status,
    )
    return True


def aplicar_status_compromisso(compromisso: Compromisso, novo_status: str, *, usuario) -> bool:
    if novo_status not in StatusCompromisso.values:
        return False
    if compromisso.status == StatusCompromisso.CANCELADO:
        return False
    if novo_status == StatusCompromisso.CANCELADO:
        return False
    if compromisso.status == novo_status:
        return False
    anterior = compromisso.status
    compromisso.status = novo_status
    compromisso.save(update_fields=["status", "atualizado_em"])
    registrar_audit(
        usuario=usuario,
        item_tipo="compromisso",
        item_id=compromisso.pk,
        acao="alterado",
        campo="status",
        valor_anterior=anterior,
        valor_novo=novo_status,
    )
    return True


def confirmar_compromisso(compromisso: Compromisso, *, usuario) -> bool:
    if not aplicar_status_compromisso(
        compromisso, StatusCompromisso.CONFIRMADO, usuario=usuario
    ):
        return False
    if compromisso.tipo == TipoCompromisso.CONSULTA:
        compromisso.confirmacao_consulta = StatusConfirmacaoConsulta.CONFIRMADA
        compromisso.save(update_fields=["confirmacao_consulta", "atualizado_em"])
    return True


def nao_compareceu_compromisso(compromisso: Compromisso, *, usuario) -> bool:
    if not aplicar_status_compromisso(
        compromisso, StatusCompromisso.NAO_COMPARECEU, usuario=usuario
    ):
        return False
    if compromisso.tipo == TipoCompromisso.CONSULTA:
        compromisso.confirmacao_consulta = StatusConfirmacaoConsulta.CANCELADA
        compromisso.save(update_fields=["confirmacao_consulta", "atualizado_em"])
    return True
