"""Growth Advisor — prioridade determinística (sem LLM)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from comercial.models import AcaoComercialResolvida, ComercialAuditLog
from comercial.services.audit import registrar_auditoria
from comercial.services.money import ZERO, money
from comercial.services.opportunities import OportunidadeReceita, listar_oportunidades
from comercial.services.pipeline import url_oportunidades
from comercial.services.recommendations import (
    RecomendacaoAcao,
    criar_tarefa_da_recomendacao,
    listar_recomendacoes,
    marcar_resolvida,
)
from comercial.services.revenue_risk import ReceitaRisco
from marketing.definitions import FASES_PROPOSTA
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso
from usuarios.services.org_scope import (
    clientes_da_organizacao,
    compromissos_da_organizacao,
)

# Critérios documentados (score 0–100 do tema agregado):
#   cobrança vencida (receita confirmada)     90
#   proposta sem follow-up (etapa avançada)   85
#   consulta sem próximo passo                70
#   lead sem agendamento                      55
#   relacionamento / base                     35
#   volume: +1 por item, teto +10
CRITERIOS_DOC = (
    "Prioridade agregada 0–100: cobrança vencida 90, proposta sem follow-up 85, "
    "oportunidades avançadas sem próxima ação 82, perdas observadas por etapa 74, "
    "consulta sem próximo passo 70, lead sem agendamento 55, relacionamento 35. "
    "Volume: +1 por item (máx. +10). Temperatura é evidência, não prioridade sozinha. "
    "Sem IA generativa."
)

_TEMA_BASE = {
    "cobrancas_vencidas": 90,
    "propostas_aguardando": 85,
    "avancados_sem_acao": 82,
    "perdas_etapa": 74,
    "consultas_sem_followup": 70,
    "leads_sem_agendamento": 55,
    "relacionamento": 35,
}

_RISCO_POR_TEMA = {
    "cobrancas_vencidas": "cobrancas_vencidas",
    "propostas_aguardando": "propostas_sem_followup",
    "consultas_sem_followup": "consultas_sem_proximo_passo",
    "leads_sem_agendamento": "leads_parados",
}

_ETAPA_POR_TEMA = {
    "cobrancas_vencidas": "Cobrança",
    "propostas_aguardando": "Proposta",
    "avancados_sem_acao": "Proposta",
    "perdas_etapa": "Funil",
    "consultas_sem_followup": "Consulta",
    "leads_sem_agendamento": "Lead",
    "relacionamento": "Carteira",
}


@dataclass(frozen=True)
class PrioridadeAdvisor:
    key: str
    score: int
    prioridade: str
    titulo: str
    quantidade: int
    valor_potencial: Decimal | None
    motivo: str
    acao: str
    url: str
    etapa: str
    fatores: tuple[tuple[str, int], ...]
    cta_ver: str
    cta_agir: str
    pode_criar_followups: bool


@dataclass(frozen=True)
class InsightHome:
    texto: str
    url_analise: str


@dataclass(frozen=True)
class PainelAdvisor:
    principal: PrioridadeAdvisor | None
    secundarias: tuple[PrioridadeAdvisor, ...]
    acoes_hoje: tuple[RecomendacaoAcao, ...]
    insight: InsightHome | None
    criterios_doc: str
    mensagem: str


def _prioridade_label(score: int) -> str:
    if score >= 80:
        return "urgente"
    if score >= 60:
        return "alta"
    if score >= 40:
        return "media"
    return "baixa"


def _score_tema(item: OportunidadeReceita) -> tuple[int, tuple[tuple[str, int], ...]]:
    base = _TEMA_BASE.get(item.key, 40)
    volume = min(10, max(0, item.quantidade))
    fatores = (
        ("tema", base),
        ("volume", volume),
    )
    return max(0, min(100, base + volume)), fatores


def _valor_tema(
    item: OportunidadeReceita,
    risco: ReceitaRisco | None,
    ticket: Decimal | None,
    ocultar_financeiro: bool,
) -> Decimal | None:
    if ocultar_financeiro:
        return None
    if risco:
        alias = _RISCO_POR_TEMA.get(item.key)
        if alias:
            for r in risco.itens:
                if r.key == alias and r.valor is not None:
                    return money(r.valor)
    if ticket and item.quantidade and item.key != "cobrancas_vencidas":
        return money(Decimal(item.quantidade) * Decimal(ticket))
    return None


def _copy_tema(item: OportunidadeReceita) -> tuple[str, str]:
    if item.key == "propostas_aguardando":
        return (
            "Existem propostas em estágio avançado sem próxima atividade registrada.",
            "Realizar follow-up das propostas mais antigas hoje.",
        )
    if item.key == "consultas_sem_followup":
        return (
            "Consultas realizadas sem próximo passo no CRM/Agenda.",
            "Definir a próxima ação de cada consulta ainda hoje.",
        )
    if item.key == "leads_sem_agendamento":
        return (
            "Leads em primeiro contato ainda sem consulta agendada.",
            "Agendar as consultas pendentes.",
        )
    if item.key == "avancados_sem_acao":
        return (
            "Há oportunidades Quentes/Fervendo sem próxima ação agendada.",
            "Priorizar follow-up destas oportunidades hoje.",
        )
    if item.key == "perdas_etapa":
        return (
            "Há volume observado de perdas em uma etapa do funil.",
            "Revisar o processo nessa transição — sem atribuir causa além do dado.",
        )
    if item.key == "cobrancas_vencidas":
        return (
            "Há cobranças vencidas com saldo em aberto — receita confirmada em risco.",
            "Acionar o acompanhamento no Financeiro hoje.",
        )
    return (
        item.detalhe,
        "Registrar o próximo passo destas oportunidades.",
    )


def _montar_prioridade(
    item: OportunidadeReceita,
    *,
    risco: ReceitaRisco | None,
    ticket: Decimal | None,
    ocultar_financeiro: bool,
) -> PrioridadeAdvisor:
    score, fatores = _score_tema(item)
    motivo, acao = _copy_tema(item)
    return PrioridadeAdvisor(
        key=item.key,
        score=score,
        prioridade=_prioridade_label(score),
        titulo=item.titulo,
        quantidade=item.quantidade,
        valor_potencial=_valor_tema(item, risco, ticket, ocultar_financeiro),
        motivo=motivo,
        acao=acao,
        url=item.url,
        etapa=_ETAPA_POR_TEMA.get(item.key, "Comercial"),
        fatores=fatores,
        cta_ver="Ver detalhes",
        cta_agir="Criar follow-ups" if item.key == "propostas_aguardando" else "Ver lista",
        pode_criar_followups=item.key == "propostas_aguardando",
    )


def _insight_home(meta_vs, funil, risco, ocultar_financeiro: bool, pipeline=None) -> InsightHome | None:
    url = reverse("comercial_dashboard") + "?aba=score"
    if risco and risco.total_confirmado_risco > 0 and not ocultar_financeiro:
        return InsightHome(
            texto=(
                f"Há receita confirmada em atraso. "
                f"Priorize as cobranças vencidas no Financeiro."
            ),
            url_analise=url,
        )
    if pipeline and pipeline.avancados_sem_acao:
        return InsightHome(
            texto=(
                f"{pipeline.avancados_sem_acao} oportunidade(s) Quente/Fervendo "
                "estão sem próxima ação agendada."
            ),
            url_analise=reverse("comercial_dashboard") + "?aba=oportunidades",
        )
    if pipeline and pipeline.amostra_perdas_suficiente and pipeline.perda_etapa_principal:
        etapa = pipeline.perda_etapa_principal
        motivo = (
            f" Motivo mais registrado: {pipeline.perda_motivo_principal.label}."
            if pipeline.perda_motivo_principal
            else ""
        )
        return InsightHome(
            texto=(
                f"Maior perda observada em {etapa.label} "
                f"({etapa.quantidade} oportunidades).{motivo} "
                "Não atribui causa além do registro."
            ),
            url_analise=url,
        )
    if meta_vs and meta_vs.pct_atingido is not None and meta_vs.pct_atingido < 70:
        return InsightHome(
            texto="O realizado comercial do mês ainda está abaixo de 70% da meta.",
            url_analise=url,
        )
    if funil and funil.gargalo:
        return InsightHome(
            texto=(
                f"Principal gargalo do funil: {funil.gargalo.label}. "
                "Maior queda observada nesta transição — não atribui causa."
            ),
            url_analise=url,
        )
    return None


def _itens_pipeline(pipeline) -> tuple[OportunidadeReceita, ...]:
    if pipeline is None:
        return ()
    itens: list[OportunidadeReceita] = []
    if pipeline.avancados_sem_acao:
        itens.append(
            OportunidadeReceita(
                key="avancados_sem_acao",
                titulo="Oportunidades avançadas sem próxima ação",
                quantidade=pipeline.avancados_sem_acao,
                detalhe="Quente/Fervendo sem follow-up agendado — temperatura é evidência, não garantia.",
                url=url_oportunidades(),
                tom="atencao",
            )
        )
    if pipeline.amostra_perdas_suficiente and pipeline.perda_etapa_principal:
        etapa = pipeline.perda_etapa_principal
        itens.append(
            OportunidadeReceita(
                key="perdas_etapa",
                titulo=f"Maior perda observada: {etapa.label}",
                quantidade=etapa.quantidade,
                detalhe=(
                    "Oportunidades encerradas como perdidas nesta etapa observada. "
                    "Não atribui causa além do registro."
                ),
                url=url_oportunidades(),
                tom="atencao",
            )
        )
    return tuple(itens)


def montar_advisor(
    user,
    *,
    organization=None,
    risco: ReceitaRisco | None = None,
    ticket: Decimal | None = None,
    ocultar_financeiro: bool = False,
    meta_vs=None,
    funil=None,
    pipeline=None,
) -> PainelAdvisor:
    oportunidades = listar_oportunidades(user, organization=organization)
    itens = tuple(oportunidades.itens) + _itens_pipeline(pipeline)
    prioridades = [
        _montar_prioridade(
            item,
            risco=risco,
            ticket=ticket,
            ocultar_financeiro=ocultar_financeiro,
        )
        for item in itens
    ]
    prioridades.sort(key=lambda p: p.score, reverse=True)
    principal = prioridades[0] if prioridades else None
    secundarias = tuple(prioridades[1:4])
    acoes = listar_recomendacoes(user, limite=5, organization=organization)
    insight = _insight_home(meta_vs, funil, risco, ocultar_financeiro, pipeline=pipeline)
    mensagem = ""
    if not principal:
        mensagem = oportunidades.mensagem or "Nenhuma prioridade comercial no momento."
    return PainelAdvisor(
        principal=principal,
        secundarias=secundarias,
        acoes_hoje=acoes.itens,
        insight=insight,
        criterios_doc=CRITERIOS_DOC,
        mensagem=mensagem,
    )


def criar_followups_propostas(user, *, ator, limite: int = 5, organization=None) -> int:
    """Cria tarefas de follow-up para propostas sem próximo passo (máx. `limite`)."""
    agora = timezone.now()
    if organization is None:
        return 0
    resolvidas = set(
        AcaoComercialResolvida.objects.filter(organization=organization).values_list(
            "chave", flat=True
        )
    )
    propostas = clientes_da_organizacao(organization).filter(
        fase_funil__in=FASES_PROPOSTA
    ).order_by("criado_em")
    com_fu = set(
        compromissos_da_organizacao(organization).filter(
            cliente_id__in=propostas.values("pk"),
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora__gte=agora,
        )
        .exclude(status=StatusCompromisso.CANCELADO)
        .values_list("cliente_id", flat=True)
    )
    criadas = 0
    for cli in propostas[:40]:
        if criadas >= limite:
            break
        if cli.pk in com_fu:
            continue
        chave = f"proposta_sem_followup:{cli.pk}"
        if chave in resolvidas:
            continue
        tarefa = criar_tarefa_da_recomendacao(
            user,
            ator=ator,
            chave=chave,
            cliente_id=cli.pk,
            titulo=f"Follow-up da proposta de {cli.nome}",
            motivo="Proposta sem follow-up comercial futuro",
            organization=organization,
        )
        if tarefa is None:
            continue
        marcar_resolvida(
            user, ator=ator, chave=chave, cliente_id=cli.pk, organization=organization
        )
        criadas += 1
    if criadas:
        registrar_auditoria(
            user,
            ator=ator,
            acao=ComercialAuditLog.ACAO_TAREFA_CRIADA,
            detalhe=f"advisor_followups={criadas}",
        )
    return criadas
