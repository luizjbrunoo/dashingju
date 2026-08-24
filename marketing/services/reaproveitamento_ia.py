"""Reaproveitamento de conteúdo com IA."""

from __future__ import annotations

from typing import Any

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel, Field

from marketing.choices import MAPA_REAPROVEITAMENTO, StatusConteudo
from marketing.models import ContentItem, ContentProfile
from marketing.services.conteudo_ia import (
    ContentGenerationError,
    ContentGenerationService,
)
from marketing.services.prompts import prompt_posts_curtos, prompt_reaproveitamento


class PostCurto(BaseModel):
    titulo: str
    corpo_texto: str


class PostsCurtosOutput(BaseModel):
    posts: list[PostCurto] = Field(..., min_length=3, max_length=3)


class ReaproveitamentoService:
    """Transforma um conteúdo em múltiplos formatos adaptados."""

    def __init__(self, profile: ContentProfile | None = None):
        self.profile = profile
        self._gen = ContentGenerationService(profile=profile)

    def reaproveitar(
        self,
        origem: ContentItem,
        formatos: list[str],
        *,
        user,
    ) -> list[ContentItem]:
        texto = self._texto_origem(origem)
        if not texto.strip():
            raise ContentGenerationError(
                "O conteúdo de origem não possui texto. Gere ou edite o conteúdo antes de reaproveitar."
            )
        if not formatos:
            raise ContentGenerationError("Selecione ao menos um formato de destino.")

        criados: list[ContentItem] = []
        params_base = {
            "area_juridica": origem.area_juridica,
            "publico": origem.publico,
            "objetivo": origem.objetivo,
            "tom": origem.tom,
            "tema": origem.tema or origem.titulo,
        }

        for chave in formatos:
            if chave == "posts_curtos":
                criados.extend(self._gerar_posts_curtos(origem, texto, params_base, user))
                continue
            if chave not in MAPA_REAPROVEITAMENTO:
                continue
            canal, formato = MAPA_REAPROVEITAMENTO[chave]
            item = self._criar_item_derivado(
                origem, user, canal=canal, formato=formato, sufixo=chave
            )
            prompt = prompt_reaproveitamento(
                texto,
                origem.titulo,
                canal,
                formato,
                params_base,
                self.profile,
            )
            self._gen.gerar_e_salvar(
                item,
                user=user,
                acao="reaproveitar",
                parametros_extra=params_base,
                prompt_custom=prompt,
            )
            criados.append(item)

        if not criados:
            raise ContentGenerationError("Nenhum formato válido selecionado.")
        return criados

    def _gerar_posts_curtos(
        self,
        origem: ContentItem,
        texto: str,
        params: dict[str, Any],
        user,
    ) -> list[ContentItem]:
        self._gen._verificar_api_key()
        prompt = prompt_posts_curtos(texto, origem.titulo, params, self.profile)
        agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini"),
            description="Especialista em posts curtos para advocacia.",
            instructions=[
                "Crie 3 posts distintos, éticos e informativos.",
                "Não repita o mesmo texto entre os posts.",
            ],
            output_schema=PostsCurtosOutput,
            structured_outputs=True,
        )
        try:
            output: PostsCurtosOutput = agent.run(prompt).content
        except Exception as exc:
            raise ContentGenerationError(f"Falha ao gerar posts curtos: {exc}") from exc

        criados = []
        for i, post in enumerate(output.posts, start=1):
            item = self._criar_item_derivado(
                origem,
                user,
                canal="instagram",
                formato="post",
                sufixo=f"post_curto_{i}",
                titulo=f"{post.titulo[:200]} (post {i})",
            )
            resultado = {
                "titulo_sugerido": post.titulo,
                "corpo_texto": post.corpo_texto,
                "corpo_estruturado": {
                    "titulo_sugerido": post.titulo,
                    "secoes": [{"titulo": "Post", "conteudo": post.corpo_texto}],
                    "hashtags": [],
                    "palavras_chave": [],
                    "cta": "",
                    "meta_description": "",
                    "observacoes_revisao": [],
                },
                "metadados": {"modelo": "gpt-4o-mini", "gerado_por_ia": True, "acao": "reaproveitar"},
            }
            self._gen._persistir_versao(item, resultado, user=user, acao="reaproveitar")
            criados.append(item)
        return criados

    def _criar_item_derivado(
        self,
        origem: ContentItem,
        user,
        *,
        canal: str,
        formato: str,
        sufixo: str,
        titulo: str | None = None,
    ) -> ContentItem:
        return ContentItem.objects.create(
            usuario=user,
            origem=origem,
            titulo=titulo or f"{origem.titulo[:200]} ({sufixo})",
            tema=origem.tema,
            area_juridica=origem.area_juridica,
            canal=canal,
            formato=formato,
            objetivo=origem.objetivo,
            publico=origem.publico,
            palavra_chave=origem.palavra_chave,
            tom=origem.tom,
            cta=origem.cta,
            status=StatusConteudo.RASCUNHO,
            responsavel=user,
            metadados={"reaproveitado_de": origem.pk, "formato_reaproveitamento": sufixo},
        )

    def _texto_origem(self, origem: ContentItem) -> str:
        if origem.corpo:
            return origem.corpo
        ultima = origem.versoes.order_by("-numero").first()
        return ultima.corpo_texto if ultima else ""
