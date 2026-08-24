"""Serviços de geração de conteúdo com IA."""

from __future__ import annotations

import os
from typing import Any

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel, Field

from marketing.choices import CanalConteudo, ObjetivoConteudo, StatusConteudo, TomComunicacao
from marketing.models import ContentItem, ContentProfile, ContentVersion
from marketing.services.prompts import (
    prompt_newsletter_de_conteudo,
    prompt_por_canal,
    prompt_refinamento,
)


class SecaoConteudo(BaseModel):
    titulo: str = Field(..., description="Nome da seção (ex.: Gancho, Slide 1, FAQ)")
    conteudo: str = Field(..., description="Texto da seção")


class ContentGenerationOutput(BaseModel):
    titulo_sugerido: str = Field(..., description="Título principal sugerido para o conteúdo")
    corpo_texto: str = Field(..., description="Texto completo formatado para leitura/edição")
    secoes: list[SecaoConteudo] = Field(
        default_factory=list,
        description="Partes estruturadas do conteúdo (slides, cenas, capítulos, etc.)",
    )
    hashtags: list[str] = Field(default_factory=list)
    palavras_chave: list[str] = Field(default_factory=list)
    cta: str = Field(default="", description="Chamada para ação sugerida")
    meta_description: str = Field(default="", description="Meta description SEO, se aplicável")
    observacoes_revisao: list[str] = Field(
        default_factory=list,
        description="Pontos que merecem revisão humana antes de publicar",
    )


class ContentGenerationError(Exception):
    """Erro na geração de conteúdo."""


class MarketingContentAgent:
    def __init__(self):
        self.agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini"),
            description="Especialista em marketing de conteúdo jurídico para escritórios de advocacia.",
            instructions=[
                "Produza conteúdo informativo, ético e adaptado ao canal solicitado.",
                "Nunca prometa resultados garantidos nem use linguagem sensacionalista.",
                "Adapte tom, tamanho e estrutura ao canal e formato.",
                "Preencha todas as seções relevantes para o tipo de conteúdo.",
                "Inclua observacoes_revisao quando houver afirmações jurídicas ou dados que exijam validação.",
            ],
            output_schema=ContentGenerationOutput,
            structured_outputs=True,
            markdown=True,
        )

    def run(self, prompt: str) -> ContentGenerationOutput:
        response = self.agent.run(prompt)
        return response.content


class ContentGenerationService:
    """Orquestra geração, versionamento e metadados."""

    ACOES_IA = frozenset({"gerar_ia", "regenerar", "melhorar", "encurtar", "expandir", "alterar_tom"})

    def __init__(self, profile: ContentProfile | None = None):
        self.profile = profile

    def gerar(self, *, parametros: dict[str, Any], prompt: str | None = None) -> dict[str, Any]:
        self._verificar_api_key()
        parametros = self._enriquecer_parametros(parametros)
        if prompt is None:
            canal = parametros.get("canal", "")
            formato = parametros.get("formato", "")
            prompt = prompt_por_canal(canal, formato, parametros, self.profile)
        agent = MarketingContentAgent()
        try:
            output = agent.run(prompt)
        except Exception as exc:
            raise ContentGenerationError(f"Falha na geração: {exc}") from exc
        return self._output_para_dict(output, acao=parametros.get("acao"))

    def gerar_e_salvar(
        self,
        item: ContentItem,
        *,
        user,
        acao: str = "gerar_ia",
        parametros_extra: dict | None = None,
        conteudo_origem: ContentItem | None = None,
        prompt_custom: str | None = None,
    ) -> ContentVersion:
        params = self._parametros_de_item(item)
        params["acao"] = acao
        if parametros_extra:
            params.update(parametros_extra)
        prompt = prompt_custom
        if prompt is None and conteudo_origem and item.canal == CanalConteudo.EMAIL:
            texto_origem = conteudo_origem.corpo or ""
            if not texto_origem:
                ultima = conteudo_origem.versoes.order_by("-numero").first()
                texto_origem = ultima.corpo_texto if ultima else ""
            prompt = prompt_newsletter_de_conteudo(
                texto_origem,
                conteudo_origem.titulo,
                params,
                self.profile,
            )
        elif prompt is None and acao in {"regenerar", "melhorar", "encurtar", "expandir", "alterar_tom"}:
            texto_atual = item.corpo or ""
            if not texto_atual:
                ultima = item.versoes.order_by("-numero").first()
                texto_atual = ultima.corpo_texto if ultima else ""
            if not texto_atual:
                raise ContentGenerationError("Salve ou gere um conteúdo antes de usar esta ação.")
            prompt = prompt_refinamento(acao, texto_atual, params, self.profile)
        resultado = self.gerar(parametros=params, prompt=prompt)
        return self._persistir_versao(item, resultado, user=user, acao=acao)

    def _persistir_versao(
        self,
        item: ContentItem,
        resultado: dict[str, Any],
        *,
        user,
        acao: str,
    ) -> ContentVersion:
        ultima = item.versoes.order_by("-numero").first()
        numero = (ultima.numero + 1) if ultima else 1
        versao = ContentVersion.objects.create(
            content_item=item,
            numero=numero,
            corpo_texto=resultado["corpo_texto"],
            corpo_estruturado=resultado["corpo_estruturado"],
            gerado_por_ia=True,
            criado_por=user,
        )
        item.corpo = resultado["corpo_texto"]
        item.metadados = {
            **item.metadados,
            "ultima_geracao": resultado.get("metadados", {}),
            "ultima_acao_ia": acao,
        }
        item.status = StatusConteudo.GERADO_IA
        titulo_sugerido = resultado.get("titulo_sugerido", "")
        if titulo_sugerido and (not item.titulo.strip() or acao == "regenerar"):
            item.titulo = titulo_sugerido[:255]
        item.save(update_fields=["corpo", "metadados", "status", "titulo", "atualizado_em"])
        return versao

    def _parametros_de_item(self, item: ContentItem) -> dict[str, Any]:
        meta = item.metadados or {}
        return {
            "titulo": item.titulo,
            "tema": item.tema,
            "area_juridica": item.area_juridica,
            "canal": item.canal,
            "formato": item.formato,
            "objetivo": item.objetivo,
            "publico": item.publico,
            "palavra_chave": item.palavra_chave,
            "tom": item.tom,
            "cta": item.cta,
            "duracao_estimada": meta.get("duracao_estimada"),
            "tamanho_aproximado": meta.get("tamanho_aproximado"),
            "num_slides": meta.get("num_slides"),
        }

    def _verificar_api_key(self) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ContentGenerationError(
                "OPENAI_API_KEY não configurada. Defina a chave no arquivo .env para usar a geração com IA."
            )

    def _enriquecer_parametros(self, parametros: dict[str, Any]) -> dict[str, Any]:
        params = dict(parametros)
        canal = params.get("canal", "")
        try:
            params["canal_label"] = CanalConteudo(canal).label if canal else ""
        except ValueError:
            params["canal_label"] = canal
        objetivo = params.get("objetivo", "")
        try:
            params["objetivo_label"] = ObjetivoConteudo(objetivo).label if objetivo else ""
        except ValueError:
            params["objetivo_label"] = objetivo
        tom = params.get("tom", "")
        try:
            params["tom_label"] = TomComunicacao(tom).label if tom else ""
        except ValueError:
            params["tom_label"] = tom
        formato = params.get("formato", "")
        if formato and canal:
            from marketing.choices import FORMATOS_POR_CANAL

            labels = dict(FORMATOS_POR_CANAL.get(canal, []))
            params["formato_label"] = labels.get(formato, formato)
        return params

    def _output_para_dict(self, output: ContentGenerationOutput, acao: str | None = None) -> dict[str, Any]:
        corpo_estruturado = {
            "titulo_sugerido": output.titulo_sugerido,
            "secoes": [s.model_dump() for s in output.secoes],
            "hashtags": output.hashtags,
            "palavras_chave": output.palavras_chave,
            "cta": output.cta,
            "meta_description": output.meta_description,
            "observacoes_revisao": output.observacoes_revisao,
        }
        return {
            "titulo_sugerido": output.titulo_sugerido,
            "corpo_texto": output.corpo_texto,
            "corpo_estruturado": corpo_estruturado,
            "metadados": {"modelo": "gpt-4o-mini", "gerado_por_ia": True, "acao": acao},
        }
