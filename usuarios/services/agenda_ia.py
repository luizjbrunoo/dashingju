"""Assistente do dia — resumo da agenda (padrão e IA)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from django.utils import timezone
from pydantic import BaseModel, Field

from usuarios.choices import (
    StatusCompromisso,
    StatusConfirmacaoConsulta,
    StatusTarefa,
    TipoCompromisso,
)
from usuarios.services.agenda import (
    DIAS_PRAZOS_PROXIMOS,
    _base_compromissos,
    _base_tarefas,
    calcular_kpis,
    itens_atencao,
)


class AgendaIaError(Exception):
    """Erro ao gerar resumo com IA."""


@dataclass(frozen=True)
class ResumoDiaAgenda:
    texto: str
    origem: str  # "padrao" | "ia"
    ia_disponivel: bool
    destaques: list[str] = field(default_factory=list)


class ResumoDiaOutput(BaseModel):
    resumo: str = Field(
        ...,
        description="Resumo curto do dia em português, 3 a 6 linhas, tom profissional.",
    )
    destaques: list[str] = Field(
        default_factory=list,
        description="Até 4 bullets com prioridades do dia.",
    )


def ia_disponivel() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _session_key(user_id: int, ref: date) -> str:
    return f"agenda_resumo_ia_{user_id}_{ref.isoformat()}"


def ler_resumo_ia_sessao(request, user_id: int, ref: date) -> ResumoDiaAgenda | None:
    if not request:
        return None
    payload = request.session.get(_session_key(user_id, ref))
    if not payload or not isinstance(payload, dict):
        return None
    texto = (payload.get("texto") or "").strip()
    if not texto:
        return None
    destaques = payload.get("destaques") or []
    return ResumoDiaAgenda(
        texto=texto,
        origem="ia",
        ia_disponivel=ia_disponivel(),
        destaques=list(destaques),
    )


def salvar_resumo_ia_sessao(
    request,
    user_id: int,
    ref: date,
    resumo: ResumoDiaAgenda,
) -> None:
    request.session[_session_key(user_id, ref)] = {
        "texto": resumo.texto,
        "destaques": resumo.destaques,
    }
    request.session.modified = True


def limpar_resumo_ia_sessao(request, user_id: int, ref: date) -> None:
    chave = _session_key(user_id, ref)
    if chave in request.session:
        del request.session[chave]
        request.session.modified = True


def _aplicar_filtros_resumo(user, base_c, base_t, filtros):
    if not filtros:
        return base_c, base_t
    from usuarios.services.agenda import (
        _aplicar_filtros_compromissos,
        _aplicar_filtros_tarefas,
    )
    from usuarios.services.agenda_equipe import filtrar_compromissos_escopo

    base_c = filtrar_compromissos_escopo(
        _aplicar_filtros_compromissos(base_c, filtros),
        user,
        filtros.escopo,
    )
    base_t = _aplicar_filtros_tarefas(base_t, filtros)
    return base_c, base_t


def montar_contexto_resumo_dia(
    user,
    ref: date | None = None,
    filtros=None,
) -> dict:
    ref = ref or timezone.localdate()
    amanha = ref + timedelta(days=1)
    kpis = calcular_kpis(user, ref)
    base_c, base_t = _aplicar_filtros_resumo(
        user, _base_compromissos(user), _base_tarefas(user), filtros
    )

    compromissos_hoje = list(
        base_c.filter(data_hora__date=ref)
        .order_by("data_hora")
        .values_list("titulo", "data_hora", "tipo")
    )
    tarefas_hoje = list(
        base_t.filter(
            prazo=ref,
            status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
        )
        .order_by("prioridade", "titulo")
        .values_list("titulo", "prioridade")
    )
    tarefas_atrasadas = list(
        base_t.filter(
            prazo__lt=ref,
            status__in=(StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO),
        )
        .order_by("prazo", "titulo")[:8]
        .values_list("titulo", "prazo")
    )
    prazos_internos_amanha = base_c.filter(
        tipo=TipoCompromisso.PRAZO,
        prazo_interno=amanha,
        status__in=(StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO),
    ).count()
    consultas_sem_confirmacao = base_c.filter(
        tipo=TipoCompromisso.CONSULTA,
        data_hora__date__gte=ref,
        status__in=(StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO),
    ).filter(
        confirmacao_consulta__in=(
            "",
            StatusConfirmacaoConsulta.PENDENTE,
        )
    ).count()
    atencao = itens_atencao(user, ref, limit=6)
    if filtros and filtros.processo:
        proc = filtros.processo.lower()
        atencao = [item for item in atencao if proc in (item.titulo or "").lower()]

    return {
        "data_ref": ref,
        "kpis": kpis,
        "compromissos_hoje": compromissos_hoje,
        "tarefas_hoje": tarefas_hoje,
        "tarefas_atrasadas": tarefas_atrasadas,
        "prazos_internos_amanha": prazos_internos_amanha,
        "consultas_sem_confirmacao": consultas_sem_confirmacao,
        "itens_atencao": [item.titulo for item in atencao],
    }


def gerar_resumo_padrao(ctx: dict) -> ResumoDiaAgenda:
    ref: date = ctx["data_ref"]
    kpis = ctx["kpis"]
    linhas = [f"Hoje ({ref:%d/%m/%Y}) você possui:"]
    destaques: list[str] = []

    qtd_comp = len(ctx["compromissos_hoje"])
    if qtd_comp == 1:
        linhas.append("- 1 compromisso agendado")
    elif qtd_comp:
        linhas.append(f"- {qtd_comp} compromissos agendados")
    else:
        linhas.append("- Nenhum compromisso agendado")
    if qtd_comp:
        for titulo, dt, _tipo in ctx["compromissos_hoje"][:3]:
            hora = timezone.localtime(dt).strftime("%H:%M")
            destaques.append(f"{hora} — {titulo}")

    qtd_tar = len(ctx["tarefas_hoje"])
    if qtd_tar:
        linhas.append(f"- {qtd_tar} tarefa{'s' if qtd_tar != 1 else ''} com prazo hoje")

    if kpis.tarefas_atrasadas:
        linhas.append(
            f"- {kpis.tarefas_atrasadas} tarefa"
            f"{'s' if kpis.tarefas_atrasadas != 1 else ''} em atraso"
        )
        for titulo, prazo in ctx["tarefas_atrasadas"][:2]:
            destaques.append(f"Atrasada: {titulo} (prazo {prazo:%d/%m})")

    if ctx["prazos_internos_amanha"]:
        n = ctx["prazos_internos_amanha"]
        linhas.append(
            f"- {n} prazo interno{'s' if n != 1 else ''} amanhã"
        )

    if ctx["consultas_sem_confirmacao"]:
        n = ctx["consultas_sem_confirmacao"]
        linhas.append(
            f"- {n} consulta{'s' if n != 1 else ''} ainda sem confirmação"
        )

    if kpis.prazos_proximos:
        linhas.append(
            f"- {kpis.prazos_proximos} prazo"
            f"{'s' if kpis.prazos_proximos != 1 else ''} "
            f"nos próximos {DIAS_PRAZOS_PROXIMOS} dias"
        )

    if kpis.audiencias_semana:
        linhas.append(
            f"- {kpis.audiencias_semana} audiência"
            f"{'s' if kpis.audiencias_semana != 1 else ''} nesta semana"
        )

    if not any(
        [
            qtd_comp,
            qtd_tar,
            kpis.tarefas_atrasadas,
            ctx["prazos_internos_amanha"],
            ctx["consultas_sem_confirmacao"],
            kpis.prazos_proximos,
        ]
    ):
        linhas.append("- Agenda tranquila — nenhum item crítico identificado.")

    if ctx["itens_atencao"]:
        linhas.append("")
        linhas.append("Prioridades imediatas:")
        for titulo in ctx["itens_atencao"][:4]:
            linhas.append(f"• {titulo}")
            if len(destaques) < 4:
                destaques.append(titulo)

    return ResumoDiaAgenda(
        texto="\n".join(linhas),
        origem="padrao",
        ia_disponivel=ia_disponivel(),
        destaques=destaques[:4],
    )


def _montar_prompt_ia(ctx: dict) -> str:
    ref: date = ctx["data_ref"]
    kpis = ctx["kpis"]
    comps = []
    for titulo, dt, tipo in ctx["compromissos_hoje"]:
        hora = timezone.localtime(dt).strftime("%H:%M")
        comps.append(f"{hora} {titulo} ({tipo})")
    tarefas = [f"{t[0]} (prioridade {t[1]})" for t in ctx["tarefas_hoje"]]
    atrasadas = [f"{t[0]} (venc. {t[1]:%d/%m})" for t in ctx["tarefas_atrasadas"]]

    return f"""
Data de referência: {ref:%d/%m/%Y}

KPIs:
- Compromissos hoje: {kpis.compromissos_hoje}
- Prazos próximos ({DIAS_PRAZOS_PROXIMOS}d): {kpis.prazos_proximos}
- Tarefas atrasadas: {kpis.tarefas_atrasadas}
- Audiências na semana: {kpis.audiencias_semana}
- Prazos internos amanhã: {ctx["prazos_internos_amanha"]}
- Consultas sem confirmação: {ctx["consultas_sem_confirmacao"]}

Compromissos de hoje: {", ".join(comps) or "nenhum"}
Tarefas com prazo hoje: {", ".join(tarefas) or "nenhuma"}
Tarefas atrasadas: {", ".join(atrasadas) or "nenhuma"}
Itens de atenção: {", ".join(ctx["itens_atencao"]) or "nenhum"}

Redija um resumo executivo curto para o advogado iniciar o dia.
""".strip()


def gerar_resumo_ia(ctx: dict) -> ResumoDiaAgenda:
    if not ia_disponivel():
        raise AgendaIaError(
            "OPENAI_API_KEY não configurada. Defina a chave no .env para usar o assistente com IA."
        )

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        description="Assistente de agenda jurídica para escritórios de advocacia.",
        instructions=[
            "Resuma a agenda do dia de forma clara, objetiva e profissional.",
            "Priorize prazos, audiências, tarefas atrasadas e consultas pendentes.",
            "Use português brasileiro. Tom de secretaria sênior, sem alarmismo.",
            "Não invente compromissos ou tarefas que não estejam no contexto.",
            "O resumo deve caber em poucas linhas; destaques são bullets curtos.",
        ],
        output_schema=ResumoDiaOutput,
        structured_outputs=True,
    )
    try:
        output: ResumoDiaOutput = agent.run(_montar_prompt_ia(ctx)).content
    except Exception as exc:
        raise AgendaIaError(f"Falha ao gerar resumo com IA: {exc}") from exc

    texto = (output.resumo or "").strip()
    if not texto:
        raise AgendaIaError("A IA não retornou um resumo válido.")

    destaques = [d.strip() for d in (output.destaques or []) if d and d.strip()][:4]
    return ResumoDiaAgenda(
        texto=texto,
        origem="ia",
        ia_disponivel=True,
        destaques=destaques,
    )


def obter_resumo_dia(
    user,
    ref: date | None = None,
    *,
    request=None,
    filtros=None,
    forcar_padrao: bool = False,
) -> ResumoDiaAgenda:
    ref = ref or timezone.localdate()
    if not forcar_padrao and request:
        cached = ler_resumo_ia_sessao(request, user.pk, ref)
        if cached:
            return cached
    ctx = montar_contexto_resumo_dia(user, ref, filtros=filtros)
    return gerar_resumo_padrao(ctx)
