"""Captura, resolução e persistência de atribuição de leads (Google Ads e demais)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from marketing.definitions import ORIGEM_GOOGLE_ADS
from marketing.services.periodo import filtro_datetime_campo
from usuarios.choices import OrigemLead
from usuarios.models import Cliente
SESSION_KEY = "lead_atribuicao"

# Sinais objetivos de tráfego pago Google (não inventar além disso).
_GOOGLE_SOURCES = frozenset({"google", "google_ads", "adwords", "googleads"})
_PAID_MEDIUMS = frozenset({"cpc", "ppc", "paid", "paidsearch", "cpm", "display"})

_PARAM_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "gclid",
    "campaign_id",
)


@dataclass(frozen=True)
class AtribuicaoLead:
    """Payload normalizado de atribuição."""

    origem: str = OrigemLead.NAO_IDENTIFICADA
    atribuicao_confiavel: bool = False
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    utm_content: str = ""
    utm_term: str = ""
    gclid: str = ""
    campaign_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "origem": self.origem,
            "atribuicao_confiavel": self.atribuicao_confiavel,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "utm_content": self.utm_content,
            "utm_term": self.utm_term,
            "gclid": self.gclid,
            "campaign_id": self.campaign_id,
        }


def _limpar(valor: Any, *, max_len: int = 120) -> str:
    if valor is None:
        return ""
    return str(valor).strip()[:max_len]


def extrair_params_atribuicao(params) -> dict[str, str]:
    """Extrai parâmetros UTM/gclid de GET ou POST."""
    out: dict[str, str] = {}
    for chave in _PARAM_KEYS:
        bruto = params.get(chave)
        if bruto is None or str(bruto).strip() == "":
            continue
        max_len = 255 if chave == "gclid" else 64 if chave == "campaign_id" else 120
        out[chave] = _limpar(bruto, max_len=max_len)
    return out


def _detectar_google_ads(params: dict[str, str]) -> bool:
    gclid = params.get("gclid", "")
    if gclid:
        return True
    source = params.get("utm_source", "").lower()
    medium = params.get("utm_medium", "").lower()
    if source in _GOOGLE_SOURCES and medium in _PAID_MEDIUMS:
        return True
    if source == "google" and medium == "cpc":
        return True
    return False


def _detectar_google_organico(params: dict[str, str]) -> bool:
    source = params.get("utm_source", "").lower()
    medium = params.get("utm_medium", "").lower()
    return source in _GOOGLE_SOURCES and medium in {"organic", "orgânico", "organico"}


def _origem_manual_valida(valor: str) -> str | None:
    if not valor:
        return None
    validas = {choice.value for choice in OrigemLead if choice.value}
    return valor if valor in validas else None


def resolver_atribuicao(
    params,
    *,
    origem_manual: str = "",
) -> AtribuicaoLead:
    """
    Resolve origem sem inventar Google Ads.

    Google Ads confiável: gclid ou combinação utm_source+utm_medium compatível.
    Origem manual explícita (exceto google_ads): confiável.
    google_ads manual sem prova: armazena mas atribuicao_confiavel=False.
    """
    extraido = extrair_params_atribuicao(params)
    manual = _origem_manual_valida(origem_manual)

    base = AtribuicaoLead(
        utm_source=extraido.get("utm_source", ""),
        utm_medium=extraido.get("utm_medium", ""),
        utm_campaign=extraido.get("utm_campaign", ""),
        utm_content=extraido.get("utm_content", ""),
        utm_term=extraido.get("utm_term", ""),
        gclid=extraido.get("gclid", ""),
        campaign_id=extraido.get("campaign_id", ""),
    )

    if _detectar_google_ads(extraido):
        return AtribuicaoLead(
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            utm_source=base.utm_source,
            utm_medium=base.utm_medium,
            utm_campaign=base.utm_campaign,
            utm_content=base.utm_content,
            utm_term=base.utm_term,
            gclid=base.gclid,
            campaign_id=base.campaign_id,
        )

    if _detectar_google_organico(extraido):
        return AtribuicaoLead(
            origem=OrigemLead.GOOGLE_ORGANIC,
            atribuicao_confiavel=True,
            utm_source=base.utm_source,
            utm_medium=base.utm_medium,
            utm_campaign=base.utm_campaign,
            utm_content=base.utm_content,
            utm_term=base.utm_term,
            gclid=base.gclid,
            campaign_id=base.campaign_id,
        )

    if manual == OrigemLead.GOOGLE_ADS:
        return AtribuicaoLead(
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=False,
            utm_source=base.utm_source,
            utm_medium=base.utm_medium,
            utm_campaign=base.utm_campaign,
            utm_content=base.utm_content,
            utm_term=base.utm_term,
            gclid=base.gclid,
            campaign_id=base.campaign_id,
        )

    if manual and manual != OrigemLead.NAO_IDENTIFICADA:
        return AtribuicaoLead(
            origem=manual,
            atribuicao_confiavel=True,
            utm_source=base.utm_source,
            utm_medium=base.utm_medium,
            utm_campaign=base.utm_campaign,
            utm_content=base.utm_content,
            utm_term=base.utm_term,
            gclid=base.gclid,
            campaign_id=base.campaign_id,
        )

    if extraido:
        return AtribuicaoLead(
            origem=OrigemLead.NAO_IDENTIFICADA,
            atribuicao_confiavel=False,
            utm_source=base.utm_source,
            utm_medium=base.utm_medium,
            utm_campaign=base.utm_campaign,
            utm_content=base.utm_content,
            utm_term=base.utm_term,
            gclid=base.gclid,
            campaign_id=base.campaign_id,
        )

    return AtribuicaoLead()


def mesclar_atribuicao_sessao(request, params) -> dict[str, str]:
    """Combina parâmetros atuais com valores já capturados na sessão."""
    sessao = dict(request.session.get(SESSION_KEY) or {})
    extraido = extrair_params_atribuicao(params)
    if extraido:
        sessao.update(extraido)
        sessao["capturado_em"] = timezone.now().isoformat()
        request.session[SESSION_KEY] = sessao
        request.session.modified = True
    return {k: sessao.get(k, "") for k in _PARAM_KEYS}


def capturar_atribuicao_sessao(request) -> None:
    """Persiste UTM/gclid da query string na sessão (first-touch na sessão)."""
    mesclar_atribuicao_sessao(request, request.GET)


def aplicar_atribuicao_em_cliente(cliente: Cliente, atrib: AtribuicaoLead) -> None:
    cliente.origem = atrib.origem or OrigemLead.NAO_IDENTIFICADA
    cliente.atribuicao_confiavel = atrib.atribuicao_confiavel
    cliente.utm_source = atrib.utm_source
    cliente.utm_medium = atrib.utm_medium
    cliente.utm_campaign = atrib.utm_campaign
    cliente.utm_content = atrib.utm_content
    cliente.utm_term = atrib.utm_term
    cliente.gclid = atrib.gclid
    cliente.campaign_id = atrib.campaign_id


def clientes_google_ads(
    user,
    *,
    data_inicio=None,
    data_fim=None,
) -> QuerySet[Cliente]:
    """Leads atribuíveis ao Google Ads (origem confiável, tenant-scoped)."""
    qs = Cliente.objects.filter(
        user=user,
        origem=ORIGEM_GOOGLE_ADS,
        atribuicao_confiavel=True,
    )
    return filtro_datetime_campo(
        qs, "criado_em", data_inicio=data_inicio, data_fim=data_fim
    )

def ids_clientes_google_ads(user, *, data_inicio=None, data_fim=None) -> list[int]:
    return list(
        clientes_google_ads(user, data_inicio=data_inicio, data_fim=data_fim).values_list(
            "pk", flat=True
        )
    )


def resumo_qualidade_atribuicao(user, *, data_inicio=None, data_fim=None) -> dict:
    """Percentual de clientes no período com origem identificada vs. não."""
    qs = Cliente.objects.filter(user=user)
    qs = filtro_datetime_campo(
        qs, "criado_em", data_inicio=data_inicio, data_fim=data_fim
    )
    agg = qs.aggregate(
        total=Count("pk"),
        identificados=Count(
            "pk",
            filter=Q(atribuicao_confiavel=True)
            & ~Q(origem=OrigemLead.NAO_IDENTIFICADA),
        ),
    )
    total = agg["total"]
    if total == 0:
        return {
            "total": 0,
            "identificados": 0,
            "nao_identificados": 0,
            "pct_identificados": None,
            "pct_nao_identificados": None,
        }
    identificados = agg["identificados"]
    nao_id = total - identificados
    return {
        "total": total,
        "identificados": identificados,
        "nao_identificados": nao_id,
        "pct_identificados": round(100.0 * identificados / total, 1),
        "pct_nao_identificados": round(100.0 * nao_id / total, 1),
    }