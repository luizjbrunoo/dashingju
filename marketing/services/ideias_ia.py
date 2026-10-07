"""Serviço de banco de ideias com IA."""

from __future__ import annotations

import os
from typing import Any

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel, Field

from marketing.choices import StatusIdeia
from marketing.models import ContentIdea, ContentItem, ContentProfile
from marketing.services.conteudo_ia import ContentGenerationError
from marketing.services.prompts import prompt_banco_ideias


class IdeiaSugerida(BaseModel):
    titulo: str
    descricao: str = ""
    canal_sugerido: str = ""
    area_juridica: str = ""


class BancoIdeiasOutput(BaseModel):
    ideias: list[IdeiaSugerida] = Field(default_factory=list)


class BancoIdeiasService:
    def __init__(self, profile: ContentProfile | None = None):
        self.profile = profile

    def sugerir(self, *, user, parametros: dict[str, Any], organization=None) -> list[ContentIdea]:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ContentGenerationError(
                "OPENAI_API_KEY não configurada. Defina a chave no arquivo .env."
            )
        parametros = self._enriquecer(parametros)
        temas = self._temas_existentes(user, organization=organization)
        prompt = prompt_banco_ideias(parametros, self.profile, temas)
        agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini"),
            description="Especialista em ideias de conteúdo jurídico.",
            instructions=[
                "Sugira temas originais e práticos para escritórios de advocacia.",
                "Evite repetir assuntos muito simelhantes aos listados.",
                "Indique canal sugerido para cada ideia.",
            ],
            output_schema=BancoIdeiasOutput,
            structured_outputs=True,
        )
        try:
            output: BancoIdeiasOutput = agent.run(prompt).content
        except Exception as exc:
            raise ContentGenerationError(f"Falha ao sugerir ideias: {exc}") from exc

        criadas = []
        for ideia in output.ideias:
            obj = ContentIdea.objects.create(
                usuario=user,
                organization=organization,
                titulo=ideia.titulo[:255],
                descricao=ideia.descricao,
                area_juridica=(ideia.area_juridica or parametros.get("area_juridica", ""))[:120],
                canal_sugerido=self._normalizar_canal(ideia.canal_sugerido),
                objetivo=parametros.get("objetivo", ""),
                status=StatusIdeia.PENDENTE,
            )
            criadas.append(obj)
        return criadas

    def _temas_existentes(self, user, organization=None) -> list[str]:
        del user
        if organization is None:
            return []
        itens = ContentItem.objects.filter(organization=organization)
        ideias = ContentIdea.objects.filter(organization=organization)
        temas = list(itens.values_list("titulo", flat=True))
        temas += list(
            ideias.exclude(status=StatusIdeia.DESCARTADA).values_list("titulo", flat=True)
        )
        return [t for t in temas if t]

    def _enriquecer(self, parametros: dict[str, Any]) -> dict[str, Any]:
        from marketing.choices import ObjetivoConteudo

        params = dict(parametros)
        params.setdefault("quantidade", 6)
        objetivo = params.get("objetivo", "")
        try:
            params["objetivo_label"] = ObjetivoConteudo(objetivo).label if objetivo else ""
        except ValueError:
            params["objetivo_label"] = objetivo
        return params

    def _normalizar_canal(self, canal: str) -> str:
        if not canal:
            return ""
        canal = canal.lower().strip()
        for chave in ("instagram", "linkedin", "facebook", "youtube", "blog", "email"):
            if chave in canal:
                return chave
        return canal[:20]
