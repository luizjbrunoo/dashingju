"""Agregação de métricas de conteúdo (dados reais do banco)."""

from __future__ import annotations

from django.db.models import Q, Sum

from marketing.choices import PlataformaMarketing, StatusConteudo, StatusIntegracao
from marketing.models import ContentItem, ContentPerformance, MarketingIntegracao


def _integracoes_usuario(user):
    return MarketingIntegracao.objects.filter(usuario=user)


def plataformas_com_status(user) -> list[dict]:
    """Lista plataformas com status de conexão do usuário."""
    existentes = {i.plataforma: i for i in _integracoes_usuario(user)}
    resultado = []
    for valor, label in PlataformaMarketing.choices:
        integracao = existentes.get(valor)
        resultado.append({
            "plataforma": valor,
            "label": label,
            "status": integracao.status if integracao else StatusIntegracao.NAO_CONECTADO,
            "obj": integracao,
        })
    return resultado


def tem_integracao_ativa(user) -> bool:
    return _integracoes_usuario(user).filter(status=StatusIntegracao.CONECTADO).exists()


def performances_usuario(user):
    return ContentPerformance.objects.filter(
        content_item__usuario=user,
        content_item__status=StatusConteudo.PUBLICADO,
    ).select_related("content_item")


def tem_dados_reais(user) -> bool:
    return performances_usuario(user).filter(
        Q(visualizacoes__isnull=False)
        | Q(alcance__isnull=False)
        | Q(engajamento__isnull=False)
        | Q(cliques__isnull=False)
        | Q(leads_atribuidos__isnull=False)
    ).exists()


def metricas_agregadas(user) -> dict:
    qs = performances_usuario(user)
    agg = qs.aggregate(
        visualizacoes=Sum("visualizacoes"),
        alcance=Sum("alcance"),
        engajamento=Sum("engajamento"),
        cliques=Sum("cliques"),
        leads_atribuidos=Sum("leads_atribuidos"),
    )
    publicados = ContentItem.objects.filter(
        usuario=user, status=StatusConteudo.PUBLICADO
    ).count()
    return {
        "publicados": publicados,
        "visualizacoes": agg["visualizacoes"] or 0,
        "alcance": agg["alcance"] or 0,
        "engajamento": agg["engajamento"] or 0,
        "cliques": agg["cliques"] or 0,
        "leads_atribuidos": agg["leads_atribuidos"] or 0,
    }


def top_conteudos(user, limite: int = 10) -> list[dict]:
    qs = (
        performances_usuario(user)
        .filter(visualizacoes__isnull=False)
        .order_by("-visualizacoes")[:limite]
    )
    return [
        {
            "item": p.content_item,
            "visualizacoes": p.visualizacoes or 0,
            "engajamento": p.engajamento or 0,
            "cliques": p.cliques or 0,
            "leads": p.leads_atribuidos or 0,
        }
        for p in qs
    ]


def solicitar_integracao(user, plataforma: str) -> MarketingIntegracao:
    obj, _ = MarketingIntegracao.objects.get_or_create(
        usuario=user,
        plataforma=plataforma,
        defaults={"status": StatusIntegracao.PENDENTE},
    )
    if obj.status == StatusIntegracao.NAO_CONECTADO:
        obj.status = StatusIntegracao.PENDENTE
        obj.save(update_fields=["status", "atualizado_em"])
    return obj
