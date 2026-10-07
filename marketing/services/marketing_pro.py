"""
MARKETING PRO — interpretação da cadeia origem → comercial → financeiro.

Reutiliza Cliente.origem, contratos/recebimentos Organization-scoped e o
funil Google Ads já existente. Não calcula métricas via LLM. Não inventa
atribuição, causalidade, lucro ou investimento real.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse

from comercial.services.finance_org import contratos_organization, recebimentos_organization
from marketing.choices import StatusConteudo, StatusIdeia
from marketing.definitions import FASES_PROPOSTA
from marketing.services.periodo import PeriodoMarketing, filtro_datetime_campo
from usuarios.choices import OrigemLead
from usuarios.models import Cliente

_QTD = Decimal("0.01")
_ZERO = Decimal("0")
MIN_AMOSTRA = 5
MAX_ALERTAS = 2

# TENANT: agregações novas usam Organization.
# ACTOR: usuario em ideias/itens/integrações permanece autoria, não ownership.
# AUTHORIZATION: valores financeiros só entram no painel se ocultar_financeiro=False.


@dataclass(frozen=True)
class LinhaOrigemMarketing:
    origem: str
    label: str
    leads: int
    oportunidades: int
    contratos: int
    taxa_avanco_pct: Decimal | None
    valor_comercial: Decimal | None
    recebido: Decimal | None


@dataclass(frozen=True)
class OrigensMarketing:
    linhas: tuple[LinhaOrigemMarketing, ...]
    disponivel: bool
    estado: str  # ok | sem_dados | sem_atribuicao | dados_insuficientes | tenant_invalido
    mensagem: str


@dataclass(frozen=True)
class CadeiaMarketing:
    """Totais da cadeia no período. Money = None quando indisponível ou sem capability."""

    leads: int
    oportunidades: int
    contratos: int
    valor_comercial: Decimal | None
    recebido: Decimal | None
    investimento: Decimal | None
    investimento_demo: bool
    cpl: Decimal | None
    cac_midia: Decimal | None
    roas_comercial: Decimal | None
    roas_definicao: str


@dataclass(frozen=True)
class DiagnosticoMarketing:
    chave: str
    titulo: str
    detalhe: str
    disponivel: bool


@dataclass(frozen=True)
class PrioridadeMarketing:
    key: str
    score: int
    prioridade: str
    titulo: str
    motivo: str
    evidence: str
    impacto: str | None
    acao: str
    url: str
    cta: str


@dataclass(frozen=True)
class AlertaMarketing:
    categoria: str
    severity: str
    titulo: str
    detalhe: str
    url: str | None


@dataclass(frozen=True)
class AdvisorMarketing:
    principal: PrioridadeMarketing | None
    status: str
    alertas: tuple[AlertaMarketing, ...]
    criterios: str


@dataclass(frozen=True)
class PainelMarketingPro:
    cadeia: CadeiaMarketing
    origens: OrigensMarketing
    diagnostico: DiagnosticoMarketing
    advisor: AdvisorMarketing
    ocultar_financeiro: bool
    tenant_ok: bool


CRITERIOS_ADVISOR = (
    "Uma prioridade determinística (sem LLM). Scores: follow-up atrasado 82, "
    "leads sem próxima ação 80, rastreabilidade insuficiente 78, origem com "
    "baixa progressão 76, investimento sem captação atribuível 72, ideias "
    "pendentes 58. Ausência de integração ≠ performance ruim. Valores são "
    "atribuídos à origem registrada — não provam causalidade nem lucro."
)

_LABELS_ORIGEM = dict(OrigemLead.choices)


def _dec(valor) -> Decimal | None:
    if valor is None:
        return None
    return Decimal(str(valor)).quantize(_QTD, rounding=ROUND_HALF_UP)


def _safe_div(numerador, denominador) -> Decimal | None:
    if not denominador:
        return None
    return _dec(Decimal(str(numerador)) / Decimal(str(denominador)))


def _safe_pct(parte: int, total: int) -> Decimal | None:
    if not total:
        return None
    return _dec(100 * Decimal(parte) / Decimal(total))


def _prioridade_de_score(score: int) -> str:
    if score >= 80:
        return "urgente"
    if score >= 70:
        return "alta"
    return "atencao"


def _cadeia_vazia(*, investimento=None, investimento_demo=False) -> CadeiaMarketing:
    return CadeiaMarketing(
        leads=0,
        oportunidades=0,
        contratos=0,
        valor_comercial=None,
        recebido=None,
        investimento=investimento,
        investimento_demo=investimento_demo,
        cpl=None,
        cac_midia=None,
        roas_comercial=None,
        roas_definicao="ROAS sobre valor comercial atribuído (não é lucro nem causalidade).",
    )


def painel_vazio(*, ocultar_financeiro: bool = True, tenant_ok: bool = False) -> PainelMarketingPro:
    estado = "tenant_invalido" if not tenant_ok else "dados_insuficientes"
    mensagem = (
        "Contexto de organização inválido — métricas Organization-scoped indisponíveis."
        if not tenant_ok
        else "Não há dados suficientes para avaliar a cadeia de marketing no período."
    )
    return PainelMarketingPro(
        cadeia=_cadeia_vazia(),
        origens=OrigensMarketing(
            linhas=(),
            disponivel=False,
            estado=estado,
            mensagem=mensagem,
        ),
        diagnostico=DiagnosticoMarketing(
            chave="dados_insuficientes",
            titulo="Não há dados suficientes para avaliar",
            detalhe=mensagem,
            disponivel=False,
        ),
        advisor=AdvisorMarketing(
            principal=None,
            status="dados_insuficientes",
            alertas=(),
            criterios=CRITERIOS_ADVISOR,
        ),
        ocultar_financeiro=ocultar_financeiro,
        tenant_ok=tenant_ok,
    )


def _leads_org(organization, periodo: PeriodoMarketing):
    qs = Cliente.objects.filter(organization=organization)
    return filtro_datetime_campo(
        qs, "criado_em", data_inicio=periodo.data_inicio, data_fim=periodo.data_fim
    )


def performance_origens(
    organization,
    periodo: PeriodoMarketing,
    *,
    ocultar_financeiro: bool,
) -> OrigensMarketing:
    """Origem registrada → leads / oportunidades / contratos / valores. Sem heurística."""
    if organization is None:
        return OrigensMarketing(
            linhas=(),
            disponivel=False,
            estado="tenant_invalido",
            mensagem="Contexto de organização inválido — sem atribuição Organization-scoped.",
        )

    leads_qs = _leads_org(organization, periodo)
    if not leads_qs.exists():
        return OrigensMarketing(
            linhas=(),
            disponivel=False,
            estado="sem_dados",
            mensagem="Sem leads no período. Cadastre a origem nos novos clientes para atribuir resultados.",
        )

    origens = [
        row["origem"]
        for row in leads_qs.values("origem").annotate(n=Count("pk")).order_by("-n")
    ]
    identificados = leads_qs.exclude(origem="").exclude(origem__isnull=True).count()
    linhas: list[LinhaOrigemMarketing] = []

    for origem in origens:
        qs = leads_qs.filter(origem=origem)
        leads = qs.count()
        ids = list(qs.values_list("pk", flat=True))
        oportunidades = qs.filter(fase_funil__in=FASES_PROPOSTA).count()
        contratos_qs = contratos_organization(organization).filter(cliente_id__in=ids)
        contratos_qs = filtro_datetime_campo(
            contratos_qs,
            "criado_em",
            data_inicio=periodo.data_inicio,
            data_fim=periodo.data_fim,
        )
        contratos = contratos_qs.count()
        valor = contratos_qs.aggregate(t=Coalesce(Sum("valor_total"), _ZERO))["t"]
        rec_qs = recebimentos_organization(organization).filter(
            cobranca__cliente_id__in=ids,
            cancelado_em__isnull=True,
            data_recebimento__gte=periodo.data_inicio,
            data_recebimento__lte=periodo.data_fim,
        )
        recebido = rec_qs.aggregate(t=Coalesce(Sum("valor"), _ZERO))["t"]
        linhas.append(
            LinhaOrigemMarketing(
                origem=origem or "",
                label=_LABELS_ORIGEM.get(origem or "", "Origem não identificada"),
                leads=leads,
                oportunidades=oportunidades,
                contratos=contratos,
                taxa_avanco_pct=_safe_pct(contratos, leads),
                valor_comercial=None if ocultar_financeiro else _dec(valor) or _ZERO,
                recebido=None if ocultar_financeiro else _dec(recebido) or _ZERO,
            )
        )

    if not identificados:
        estado = "sem_atribuicao"
        mensagem = (
            "Leads no período sem origem registrada. Não há atribuição para conectar "
            "marketing a contratos — isso não é desempenho zero."
        )
    else:
        estado = "ok"
        mensagem = (
            "Valores associados à origem registrada no cadastro. Atribuição ≠ causalidade. "
            "Mais leads não significa melhor origem."
        )

    return OrigensMarketing(
        linhas=tuple(linhas),
        disponivel=True,
        estado=estado,
        mensagem=mensagem,
    )


def _totais_cadeia(
    origens: OrigensMarketing,
    *,
    investimento: Decimal | None,
    investimento_demo: bool,
    ocultar_financeiro: bool,
    cpl: Decimal | None = None,
    cac_midia: Decimal | None = None,
    roas_comercial: Decimal | None = None,
) -> CadeiaMarketing:
    leads = sum(l.leads for l in origens.linhas)
    oportunidades = sum(l.oportunidades for l in origens.linhas)
    contratos = sum(l.contratos for l in origens.linhas)
    if ocultar_financeiro or not origens.disponivel:
        valor = None
        recebido = None
        roas_comercial = None
    else:
        valor = _dec(sum((l.valor_comercial or _ZERO) for l in origens.linhas)) or _ZERO
        recebido = _dec(sum((l.recebido or _ZERO) for l in origens.linhas)) or _ZERO

    return CadeiaMarketing(
        leads=leads,
        oportunidades=oportunidades,
        contratos=contratos,
        valor_comercial=valor,
        recebido=recebido,
        investimento=investimento,
        investimento_demo=investimento_demo,
        cpl=cpl,
        cac_midia=cac_midia,
        roas_comercial=roas_comercial,
        roas_definicao="ROAS sobre valor comercial atribuído ao Google Ads (não é lucro nem causalidade).",
    )


def diagnosticar(origens: OrigensMarketing, cadeia: CadeiaMarketing) -> DiagnosticoMarketing:
    if origens.estado == "tenant_invalido":
        return DiagnosticoMarketing(
            chave="tenant_invalido",
            titulo="Não há dados suficientes para avaliar",
            detalhe=origens.mensagem,
            disponivel=False,
        )
    if origens.estado == "sem_dados":
        return DiagnosticoMarketing(
            chave="sem_dados",
            titulo="Sem dados no período",
            detalhe=origens.mensagem,
            disponivel=False,
        )
    if origens.estado == "sem_atribuicao":
        return DiagnosticoMarketing(
            chave="sem_atribuicao",
            titulo="Sem atribuição de origem",
            detalhe=origens.mensagem,
            disponivel=False,
        )

    candidatas = [l for l in origens.linhas if l.leads >= MIN_AMOSTRA]
    if not candidatas:
        return DiagnosticoMarketing(
            chave="amostra_insuficiente",
            titulo="Não há dados suficientes para avaliar a maior queda",
            detalhe=(
                f"É preciso ao menos {MIN_AMOSTRA} leads em uma origem para comparar "
                "progressão. Volume isolado não indica qualidade."
            ),
            disponivel=False,
        )

    pior = min(
        candidatas,
        key=lambda l: (l.taxa_avanco_pct if l.taxa_avanco_pct is not None else Decimal("100")),
    )
    return DiagnosticoMarketing(
        chave="baixa_progressao",
        titulo="Origem com menor avanço no período",
        detalhe=(
            f"{pior.label}: {pior.leads} leads e {pior.contratos} contratos atribuídos "
            f"({pior.taxa_avanco_pct}% de avanço). Maior volume de leads não significa "
            "melhor origem."
        ),
        disponivel=True,
    )


def _url_origens() -> str:
    return f"{reverse('comercial_dashboard')}?{urlencode({'aba': 'origens'})}"


def _url_clientes_filtro(links, attr: str, fallback: str) -> str:
    if links is not None:
        valor = getattr(links, attr, None)
        if valor:
            return valor
    return fallback


def _ideias_pendentes(organization) -> int:
    from marketing.models import ContentIdea

    if organization is None:
        return 0
    return ContentIdea.objects.filter(
        organization=organization, status=StatusIdeia.PENDENTE
    ).count()


def _conteudos_planejados_nao_executados(organization) -> int:
    from marketing.models import ContentItem

    if organization is None:
        return 0
    return ContentItem.objects.filter(
        organization=organization,
        status__in=(StatusConteudo.AGENDADO, StatusConteudo.APROVADO),
    ).count()


def _montar_candidatos(
    *,
    user,
    organization,
    origens: OrigensMarketing,
    cadeia: CadeiaMarketing,
    resultados,
    links,
    ocultar_financeiro: bool,
) -> list[PrioridadeMarketing]:
    candidatos: list[PrioridadeMarketing] = []
    op = getattr(resultados, "operacional", None) if resultados is not None else None
    qualidade = getattr(resultados, "qualidade", None) if resultados is not None else None
    clientes_url = reverse("clientes")

    followups = int(getattr(op, "followups_atrasados", 0) or 0) if op else 0
    if followups > 0:
        candidatos.append(
            PrioridadeMarketing(
                key="followups_atrasados",
                score=82,
                prioridade=_prioridade_de_score(82),
                titulo="Follow-ups atrasados nos leads atribuídos",
                motivo="Há compromissos de follow-up vencidos em leads com origem de mídia.",
                evidence=f"{followups} follow-up(s) atrasado(s) no período do funil de mídia.",
                impacto=None,
                acao="Atualizar os follow-ups atrasados para retomar o avanço comercial.",
                url=_url_clientes_filtro(links, "followups_atrasados", clientes_url),
                cta="Ver follow-ups atrasados",
            )
        )

    sem_acao = int(getattr(op, "leads_sem_proxima_acao", 0) or 0) if op else 0
    if sem_acao > 0:
        candidatos.append(
            PrioridadeMarketing(
                key="leads_sem_acao",
                score=80,
                prioridade=_prioridade_de_score(80),
                titulo="Leads atribuídos sem próxima ação",
                motivo="Captação sem passo comercial agendado reduz a chance de avanço observado.",
                evidence=f"{sem_acao} lead(s) Google Ads do período sem compromisso futuro nem tarefa pendente.",
                impacto=None,
                acao="Definir a próxima ação desses leads na lista de clientes.",
                url=_url_clientes_filtro(links, "leads_sem_acao", clientes_url),
                cta="Ver leads sem ação",
            )
        )

    total_q = int(getattr(qualidade, "total_clientes_periodo", 0) or 0) if qualidade else 0
    pct_id = getattr(qualidade, "pct_identificados", None) if qualidade else None
    nao_id = int(getattr(qualidade, "nao_identificados", 0) or 0) if qualidade else 0
    if total_q >= MIN_AMOSTRA and pct_id is not None and pct_id < 50:
        candidatos.append(
            PrioridadeMarketing(
                key="tracking_insuficiente",
                score=78,
                prioridade=_prioridade_de_score(78),
                titulo="Rastreabilidade de origem insuficiente",
                motivo="Sem origem registrada não é possível atribuir avanço comercial ao marketing.",
                evidence=(
                    f"{nao_id} de {total_q} clientes do período estão sem origem identificada "
                    f"({pct_id:.1f}% identificados). Ausência de tracking ≠ baixa performance."
                ),
                impacto=None,
                acao="Registrar origem (UTM/gclid ou origem manual) nos novos leads.",
                url=reverse("marketing_conteudo_integracoes"),
                cta="Revisar integrações e tracking",
            )
        )

    candidatas_origem = [l for l in origens.linhas if l.leads >= MIN_AMOSTRA]
    if candidatas_origem:
        pior = min(
            candidatas_origem,
            key=lambda l: (l.taxa_avanco_pct if l.taxa_avanco_pct is not None else Decimal("100")),
        )
        taxa = pior.taxa_avanco_pct if pior.taxa_avanco_pct is not None else _ZERO
        if taxa <= Decimal("20"):
            impacto = None
            if (
                not ocultar_financeiro
                and pior.valor_comercial is not None
                and pior.valor_comercial > 0
            ):
                impacto = (
                    f"R$ {pior.valor_comercial} em valor comercial está associado à origem "
                    f"{pior.label} — não prova causalidade."
                )
            candidatos.append(
                PrioridadeMarketing(
                    key="baixa_progressao_origem",
                    score=76,
                    prioridade=_prioridade_de_score(76),
                    titulo="Origem com baixa progressão observada",
                    motivo="Volume de leads alto com pouco avanço até contrato no período.",
                    evidence=(
                        f"{pior.label} concentrou {pior.leads} leads e {pior.contratos} contratos "
                        f"atribuídos ({taxa}% de avanço). Atribuição segundo origem registrada."
                    ),
                    impacto=impacto,
                    acao="Revisar a origem no Comercial e o follow-up dos leads atribuídos.",
                    url=_url_origens(),
                    cta="Ver origens no Comercial",
                )
            )

    inv = cadeia.investimento
    funil_leads = 0
    if resultados is not None:
        funil_leads = int(getattr(getattr(resultados, "funil", None), "leads", 0) or 0)
    if inv is not None and inv > 0 and funil_leads == 0:
        candidatos.append(
            PrioridadeMarketing(
                key="investimento_sem_captacao",
                score=72,
                prioridade=_prioridade_de_score(72),
                titulo="Investimento sem captação atribuível",
                motivo="Há investimento no período, mas nenhum lead Google Ads atribuído com rastreio confiável.",
                evidence=(
                    f"Investimento {'de demonstração ' if cadeia.investimento_demo else ''}"
                    f"R$ {inv} no período e 0 leads atribuíveis ao Google Ads. "
                    "Não afirma desperdício — pode ser lacuna de tracking."
                ),
                impacto=None,
                acao="Conferir UTM/gclid e a origem dos novos leads antes de reinterpretar o investimento.",
                url=reverse("marketing_dashboard"),
                cta="Revisar Resultados do negócio",
            )
        )

    if origens.estado in {"sem_dados", "sem_atribuicao"} and not candidatos:
        candidatos.append(
            PrioridadeMarketing(
                key="dados_insuficientes",
                score=50,
                prioridade="atencao",
                titulo="Dados insuficientes para priorizar marketing",
                motivo="Sem origem registrada ou sem leads no período não há cadeia atribuível.",
                evidence=origens.mensagem,
                impacto=None,
                acao="Registrar a origem nos novos clientes para conectar marketing ao Comercial.",
                url=_url_origens(),
                cta="Ver origens no Comercial",
            )
        )

    ideias = _ideias_pendentes(organization)
    if ideias > 0:
        candidatos.append(
            PrioridadeMarketing(
                key="ideias_pendentes",
                score=58,
                prioridade=_prioridade_de_score(58),
                titulo="Ideias ainda não convertidas em conteúdo",
                motivo="Há ideias no banco que ainda não foram planejadas ou produzidas.",
                evidence=f"{ideias} ideia(s) com status pendente no banco de ideias.",
                impacto=None,
                acao="Escolher uma ideia e convertê-la em conteúdo ou item do plano editorial.",
                url=reverse("marketing_conteudo_ideias"),
                cta="Abrir banco de ideias",
            )
        )

    planejados = _conteudos_planejados_nao_executados(organization)
    if planejados > 0 and not any(c.key == "ideias_pendentes" for c in candidatos):
        candidatos.append(
            PrioridadeMarketing(
                key="editorial_pendente",
                score=56,
                prioridade=_prioridade_de_score(56),
                titulo="Conteúdo planejado ainda não executado",
                motivo="Itens aprovados ou agendados ainda não foram publicados.",
                evidence=f"{planejados} conteúdo(s) em status aprovado ou agendado.",
                impacto=None,
                acao="Executar ou reagendar os itens do plano editorial.",
                url=reverse("marketing_conteudo_calendario_plano"),
                cta="Abrir plano editorial",
            )
        )

    candidatos.sort(key=lambda c: c.score, reverse=True)
    return candidatos


def _alertas_de_candidatos(candidatos: list[PrioridadeMarketing]) -> tuple[AlertaMarketing, ...]:
    mapa = {
        "followups_atrasados": ("CONVERSAO", "ATENCAO"),
        "leads_sem_acao": ("CAPTACAO", "ATENCAO"),
        "tracking_insuficiente": ("TRACKING", "ATENCAO"),
        "baixa_progressao_origem": ("CONVERSAO", "ATENCAO"),
        "investimento_sem_captacao": ("INVESTIMENTO", "ATENCAO"),
        "dados_insuficientes": ("TRACKING", "DADOS INSUFICIENTES"),
        "ideias_pendentes": ("CONTEUDO", "ATENCAO"),
        "editorial_pendente": ("PLANO EDITORIAL", "ATENCAO"),
    }
    out: list[AlertaMarketing] = []
    for c in candidatos[1 : 1 + MAX_ALERTAS]:
        cat, sev = mapa.get(c.key, ("CAPTACAO", "ATENCAO"))
        out.append(
            AlertaMarketing(
                categoria=cat,
                severity=sev,
                titulo=c.titulo,
                detalhe=c.evidence,
                url=c.url,
            )
        )
    return tuple(out)


def montar_advisor(
    *,
    user,
    organization,
    origens: OrigensMarketing,
    cadeia: CadeiaMarketing,
    resultados=None,
    links=None,
    ocultar_financeiro: bool,
) -> AdvisorMarketing:
    candidatos = _montar_candidatos(
        user=user,
        organization=organization,
        origens=origens,
        cadeia=cadeia,
        resultados=resultados,
        links=links,
        ocultar_financeiro=ocultar_financeiro,
    )
    if not candidatos:
        return AdvisorMarketing(
            principal=None,
            status="saudavel",
            alertas=(),
            criterios=CRITERIOS_ADVISOR,
        )
    principal = candidatos[0]
    status = principal.prioridade
    if principal.key in {"dados_insuficientes", "tracking_insuficiente"}:
        status = "dados_insuficientes" if principal.key == "dados_insuficientes" else principal.prioridade
    return AdvisorMarketing(
        principal=principal,
        status=status,
        alertas=_alertas_de_candidatos(candidatos),
        criterios=CRITERIOS_ADVISOR,
    )


def montar_marketing_pro(
    user,
    periodo: PeriodoMarketing,
    *,
    organization,
    ocultar_financeiro: bool,
    resultados=None,
    links=None,
) -> PainelMarketingPro:
    """Camada PRO Organization-scoped. Fail-closed se organization is None."""
    if resultados is None:
        from marketing.services.google_ads_resultados import calcular_resultados_negocio

        resultados = calcular_resultados_negocio(
            user, periodo, organization=organization, modo_demo=False
        )

    funil = getattr(resultados, "funil", None)
    eficiencia = getattr(resultados, "eficiencia", None)
    investimento = getattr(funil, "investimento", None) if funil is not None else None
    investimento_demo = bool(getattr(funil, "investimento_demo", False)) if funil is not None else False
    cpl = getattr(eficiencia, "cpl", None) if eficiencia is not None else None
    cac = getattr(eficiencia, "cac_midia", None) if eficiencia is not None else None
    roas = getattr(eficiencia, "receita_midia_ratio", None) if eficiencia is not None else None
    if ocultar_financeiro:
        roas = None

    origens = performance_origens(
        organization, periodo, ocultar_financeiro=ocultar_financeiro
    )
    cadeia = _totais_cadeia(
        origens,
        investimento=investimento,
        investimento_demo=investimento_demo,
        ocultar_financeiro=ocultar_financeiro,
        cpl=cpl,
        cac_midia=cac,
        roas_comercial=roas,
    )
    diagnostico = diagnosticar(origens, cadeia)
    advisor = montar_advisor(
        user=user,
        organization=organization,
        origens=origens,
        cadeia=cadeia,
        resultados=resultados,
        links=links,
        ocultar_financeiro=ocultar_financeiro,
    )
    return PainelMarketingPro(
        cadeia=cadeia,
        origens=origens,
        diagnostico=diagnostico,
        advisor=advisor,
        ocultar_financeiro=ocultar_financeiro,
        tenant_ok=organization is not None,
    )
