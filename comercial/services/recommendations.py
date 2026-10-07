"""Próximas melhores ações + score de prioridade explicável (0–100)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from comercial.models import AcaoComercialResolvida, ComercialAuditLog
from comercial.services.audit import registrar_auditoria
from marketing.definitions import FASES_PROPOSTA
from usuarios.choices import Prioridade, StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa
from usuarios.services.org_scope import (
    clientes_da_organizacao,
    compromissos_da_organizacao,
)


@dataclass(frozen=True)
class FatorScore:
    codigo: str
    label: str
    pontos: int


@dataclass(frozen=True)
class RecomendacaoAcao:
    chave: str
    prioridade: str  # urgente | alta | media | baixa
    score: int
    titulo: str
    motivo: str
    acao_recomendada: str
    cliente_id: int | None
    cliente_nome: str
    responsavel: str
    prazo_label: str
    url_registro: str
    url_agendar: str
    fatores: tuple[FatorScore, ...]


@dataclass(frozen=True)
class PainelRecomendacoes:
    itens: tuple[RecomendacaoAcao, ...]
    mensagem: str


def _prioridade_label(score: int) -> str:
    if score >= 80:
        return "urgente"
    if score >= 60:
        return "alta"
    if score >= 40:
        return "media"
    return "baixa"


def _url_cliente(cliente_id: int | None) -> str:
    if cliente_id:
        return reverse("cliente", args=[cliente_id])
    return reverse("clientes")


def _url_agendar(cliente_id: int | None) -> str:
    params = {"view": "lista", "modal": "compromisso"}
    if cliente_id:
        params["cliente"] = str(cliente_id)
    return reverse("agenda") + "?" + urlencode(params)


def _score_base(
    *,
    dias_sem_acao: int,
    em_proposta: bool,
    tem_consulta_recente: bool,
    cobranca_vencida: bool,
    lead_recente_24h: bool,
) -> tuple[int, list[FatorScore]]:
    fatores: list[FatorScore] = []
    score = 10
    fatores.append(FatorScore("base", "Base comercial", 10))

    if lead_recente_24h:
        score += 35
        fatores.append(FatorScore("lead_24h", "Lead recebido nas últimas 24h", 35))
    if em_proposta:
        score += 25
        fatores.append(FatorScore("proposta", "Etapa de proposta", 25))
    if tem_consulta_recente:
        score += 15
        fatores.append(FatorScore("consulta", "Consulta realizada sem próximo passo", 15))
    if cobranca_vencida:
        score += 30
        fatores.append(FatorScore("vencido", "Cobrança vencida vinculada", 30))

    if dias_sem_acao >= 14:
        score += 20
        fatores.append(FatorScore("parada_14", "Sem atividade há 14+ dias", 20))
    elif dias_sem_acao >= 7:
        score += 12
        fatores.append(FatorScore("parada_7", "Sem atividade há 7+ dias", 12))
    elif dias_sem_acao >= 3:
        score += 6
        fatores.append(FatorScore("parada_3", "Sem atividade há 3+ dias", 6))

    score = max(0, min(100, score))
    return score, fatores


def _acoes_resolvidas(organization):
    if organization is None:
        return set()
    return set(
        AcaoComercialResolvida.objects.filter(organization=organization).values_list(
            "chave", flat=True
        )
    )


def listar_recomendacoes(user, *, limite: int = 12, organization=None) -> PainelRecomendacoes:
    agora = timezone.now()
    resolvidas = _acoes_resolvidas(organization)
    cli_qs = clientes_da_organizacao(organization)
    comp_qs = compromissos_da_organizacao(organization)
    itens: list[RecomendacaoAcao] = []

    # 1) Leads criados nas últimas 24h sem consulta
    desde_24h = agora - timedelta(hours=24)
    leads_novos = cli_qs.filter(
        criado_em__gte=desde_24h,
        status="em_prospeccao",
    ).exclude(
        id__in=comp_qs.filter(tipo=TipoCompromisso.CONSULTA)
        .exclude(status=StatusCompromisso.CANCELADO)
        .values("cliente_id")
    )[:20]
    for cli in leads_novos:
        chave = f"lead_24h:{cli.pk}"
        if chave in resolvidas:
            continue
        score, fatores = _score_base(
            dias_sem_acao=0,
            em_proposta=False,
            tem_consulta_recente=False,
            cobranca_vencida=False,
            lead_recente_24h=True,
        )
        itens.append(
            RecomendacaoAcao(
                chave=chave,
                prioridade=_prioridade_label(score),
                score=score,
                titulo=f"Retornar lead recebido há menos de 24 horas",
                motivo=f"{cli.nome} ainda sem consulta agendada",
                acao_recomendada="Entrar em contato e agendar consulta",
                cliente_id=cli.pk,
                cliente_nome=cli.nome,
                responsavel=user.get_username(),
                prazo_label="Hoje",
                url_registro=_url_cliente(cli.pk),
                url_agendar=_url_agendar(cli.pk),
                fatores=tuple(fatores),
            )
        )

    # 2) Propostas sem follow-up
    propostas = cli_qs.filter(fase_funil__in=FASES_PROPOSTA)[:40]
    com_fu = set(
        comp_qs.filter(
            cliente_id__in=propostas.values("pk"),
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
    )
    for cli in propostas:
        if cli.pk in com_fu:
            continue
        chave = f"proposta_sem_followup:{cli.pk}"
        if chave in resolvidas:
            continue
        dias = (agora - cli.criado_em).days if cli.criado_em else 7
        score, fatores = _score_base(
            dias_sem_acao=dias,
            em_proposta=True,
            tem_consulta_recente=False,
            cobranca_vencida=False,
            lead_recente_24h=False,
        )
        itens.append(
            RecomendacaoAcao(
                chave=chave,
                prioridade=_prioridade_label(score),
                score=score,
                titulo=f"Follow-up da proposta de {cli.nome}",
                motivo="Proposta sem follow-up comercial futuro",
                acao_recomendada="Agendar follow-up ou registrar retorno",
                cliente_id=cli.pk,
                cliente_nome=cli.nome,
                responsavel=user.get_username(),
                prazo_label="48h",
                url_registro=_url_cliente(cli.pk),
                url_agendar=_url_agendar(cli.pk),
                fatores=tuple(fatores),
            )
        )

    # 3) Consultas futuras não confirmadas (próximos 3 dias)
    limite_c = agora + timedelta(days=3)
    consultas = (
        comp_qs.filter(
            tipo=TipoCompromisso.CONSULTA,
            data_hora__gte=agora,
            data_hora__lte=limite_c,
            cliente__isnull=False,
        )
        .exclude(status__in=(StatusCompromisso.CANCELADO, StatusCompromisso.REALIZADO))
        .select_related("cliente", "responsavel")[:20]
    )
    for c in consultas:
        chave = f"confirmar_consulta:{c.pk}"
        if chave in resolvidas:
            continue
        score, fatores = _score_base(
            dias_sem_acao=0,
            em_proposta=False,
            tem_consulta_recente=False,
            cobranca_vencida=False,
            lead_recente_24h=False,
        )
        score = min(100, score + 20)
        fatores = list(fatores) + [
            FatorScore("consulta_proxima", "Consulta nos próximos 3 dias", 20)
        ]
        resp = (
            c.responsavel.get_username()
            if c.responsavel_id
            else user.get_username()
        )
        itens.append(
            RecomendacaoAcao(
                chave=chave,
                prioridade=_prioridade_label(score),
                score=score,
                titulo=f"Confirmar consulta de {c.cliente.nome}",
                motivo=f"Consulta em {timezone.localtime(c.data_hora).strftime('%d/%m %H:%M')}",
                acao_recomendada="Confirmar presença com o cliente",
                cliente_id=c.cliente_id,
                cliente_nome=c.cliente.nome,
                responsavel=resp,
                prazo_label="Antes da consulta",
                url_registro=_url_cliente(c.cliente_id),
                url_agendar=_url_agendar(c.cliente_id),
                fatores=tuple(fatores),
            )
        )

    # 4) Leads parados
    limite_parado = agora - timedelta(days=7)
    parados = cli_qs.filter(
        status="em_prospeccao",
        criado_em__lt=limite_parado,
    ).exclude(
        id__in=comp_qs.filter(data_hora__gte=limite_parado).values(
            "cliente_id"
        )
    )[:25]
    for cli in parados:
        chave = f"lead_parado:{cli.pk}"
        if chave in resolvidas:
            continue
        dias = (agora - cli.criado_em).days if cli.criado_em else 7
        score, fatores = _score_base(
            dias_sem_acao=dias,
            em_proposta=cli.fase_funil in FASES_PROPOSTA,
            tem_consulta_recente=False,
            cobranca_vencida=False,
            lead_recente_24h=False,
        )
        itens.append(
            RecomendacaoAcao(
                chave=chave,
                prioridade=_prioridade_label(score),
                score=score,
                titulo=f"Contatar {cli.nome} sem movimentação",
                motivo=f"Sem atividade há cerca de {dias} dia(s)",
                acao_recomendada="Retomar contato e registrar próximo passo",
                cliente_id=cli.pk,
                cliente_nome=cli.nome,
                responsavel=user.get_username(),
                prazo_label="Esta semana",
                url_registro=_url_cliente(cli.pk),
                url_agendar=_url_agendar(cli.pk),
                fatores=tuple(fatores),
            )
        )

    itens.sort(key=lambda x: x.score, reverse=True)
    itens = itens[:limite]
    mensagem = ""
    if not itens:
        mensagem = "Nenhuma ação prioritária no momento — ou todas foram marcadas como resolvidas."

    return PainelRecomendacoes(itens=tuple(itens), mensagem=mensagem)


def marcar_resolvida(
    user, *, ator, chave: str, cliente_id: int | None = None, organization=None
) -> bool:
    chave = (chave or "").strip()[:120]
    if not chave or organization is None:
        return False
    cliente = None
    if cliente_id:
        cliente = clientes_da_organizacao(organization).filter(pk=cliente_id).first()
        if cliente is None:
            return False
    obj, created = AcaoComercialResolvida.objects.get_or_create(
        organization=organization,
        chave=chave,
        defaults={
            "usuario": user,
            "cliente": cliente,
            "resolvido_por": ator,
            "resolvido_em": timezone.now(),
        },
    )
    if created:
        registrar_auditoria(
            user,
            ator=ator,
            acao=ComercialAuditLog.ACAO_RESOLVIDA,
            detalhe=chave,
        )
    return True


def criar_tarefa_da_recomendacao(
    user,
    *,
    ator,
    chave: str,
    cliente_id: int | None,
    titulo: str,
    motivo: str,
    organization=None,
) -> Tarefa | None:
    if organization is None:
        return None
    cliente = None
    if cliente_id:
        cliente = clientes_da_organizacao(organization).filter(pk=cliente_id).first()
        if cliente is None:
            return None
    tarefa = Tarefa.objects.create(
        user=user,
        organization=organization,
        titulo=(titulo or "Ação comercial")[:255],
        descricao=f"{motivo}\n\nOrigem: Comercial · chave={chave}"[:2000],
        prazo=timezone.localdate() + timedelta(days=2),
        status=StatusTarefa.PENDENTE,
        prioridade=Prioridade.ALTA,
        cliente=cliente,
        responsavel=user,
        metadados={"comercial_chave": chave, "origem": "comercial"},
    )
    registrar_auditoria(
        user,
        ator=ator,
        acao=ComercialAuditLog.ACAO_TAREFA_CRIADA,
        detalhe=f"tarefa={tarefa.pk} chave={chave}",
    )
    return tarefa
