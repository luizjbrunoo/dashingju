"""Serviço de plano editorial com IA."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel, Field

from marketing.choices import FREQUENCIA_PLANO, StatusConteudo
from marketing.models import ContentItem, ContentProfile, EditorialCalendar
from marketing.services.conteudo_ia import ContentGenerationError
from marketing.services.prompts import prompt_plano_editorial


class ItemPlanoEditorial(BaseModel):
    data: str = Field(..., description="Data no formato YYYY-MM-DD")
    canal: str = Field(..., description="Canal: instagram, linkedin, youtube, blog, email, facebook")
    formato: str = Field(..., description="Formato do conteúdo")
    tema: str = Field(..., description="Tema do conteúdo")
    objetivo: str = Field(default="", description="Objetivo do conteúdo")
    titulo: str = Field(..., description="Título curto sugerido")


class PlanoEditorialOutput(BaseModel):
    resumo: str = Field(..., description="Resumo do plano editorial")
    itens: list[ItemPlanoEditorial] = Field(default_factory=list)


class PlanoEditorialService:
    def __init__(self, profile: ContentProfile | None = None):
        self.profile = profile

    def gerar_plano(self, *, user, parametros: dict[str, Any], organization=None) -> EditorialCalendar:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ContentGenerationError(
                "OPENAI_API_KEY não configurada. Defina a chave no arquivo .env."
            )
        parametros = self._enriquecer(parametros)
        temas = self._temas_existentes(user, organization=organization)
        prompt = prompt_plano_editorial(parametros, self.profile, temas)
        agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini"),
            description="Estrategista de conteúdo jurídico.",
            instructions=[
                "Distribua conteúdos de forma equilibrada no período.",
                "Varie canais e formatos.",
                "Evite temas muito similares aos já existentes.",
            ],
            output_schema=PlanoEditorialOutput,
            structured_outputs=True,
        )
        try:
            output: PlanoEditorialOutput = agent.run(prompt).content
        except Exception as exc:
            raise ContentGenerationError(f"Falha ao gerar plano: {exc}") from exc

        data_inicio = parametros["data_inicio"]
        periodo = parametros["periodo_dias"]
        calendario = EditorialCalendar.objects.create(
            usuario=user,
            organization=organization,
            nome=f"Plano {parametros.get('area_juridica') or 'geral'} — {periodo} dias",
            area_juridica=parametros.get("area_juridica", ""),
            publico=parametros.get("publico", ""),
            objetivo=parametros.get("objetivo", ""),
            periodo_dias=periodo,
            data_inicio=data_inicio,
            data_fim=data_inicio + timedelta(days=periodo - 1),
            canais=parametros.get("canais", ""),
        )
        for item_plano in output.itens:
            data_planejada = self._parse_data(item_plano.data, data_inicio)
            objetivo = parametros.get("objetivo", "")
            ContentItem.objects.create(
                usuario=user,
                organization=organization,
                calendario=calendario,
                titulo=item_plano.titulo[:255],
                tema=item_plano.tema[:255],
                area_juridica=parametros.get("area_juridica", "")[:120],
                canal=self._normalizar_canal(item_plano.canal),
                formato=item_plano.formato[:40],
                objetivo=objetivo[:30],
                status=StatusConteudo.IDEIA,
                data_planejada=data_planejada,
                responsavel=user,
                metadados={"plano_resumo": output.resumo, "gerado_por_ia": True},
            )
        return calendario

    def _temas_existentes(self, user, organization=None) -> list[str]:
        del user
        if organization is None:
            return []
        qs = ContentItem.objects.filter(organization=organization).values_list(
            "titulo", "tema"
        )
        temas = []
        for titulo, tema in qs:
            if titulo:
                temas.append(titulo)
            if tema and tema != titulo:
                temas.append(tema)
        return temas

    def _enriquecer(self, parametros: dict[str, Any]) -> dict[str, Any]:
        from marketing.choices import ObjetivoConteudo

        params = dict(parametros)
        if not params.get("data_inicio"):
            params["data_inicio"] = date.today()
        freq = params.get("frequencia", "")
        labels = dict(FREQUENCIA_PLANO)
        params["frequencia_label"] = labels.get(freq, freq)
        objetivo = params.get("objetivo", "")
        try:
            params["objetivo_label"] = ObjetivoConteudo(objetivo).label if objetivo else ""
        except ValueError:
            params["objetivo_label"] = objetivo
        return params

    def _parse_data(self, data_str: str, fallback: date) -> date:
        try:
            parts = data_str.strip()[:10].split("-")
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return fallback

    def _normalizar_canal(self, canal: str) -> str:
        canal = canal.lower().strip()
        mapa = {
            "instagram": "instagram",
            "linkedin": "linkedin",
            "facebook": "facebook",
            "youtube": "youtube",
            "blog": "blog",
            "e-mail": "email",
            "email": "email",
        }
        for chave, valor in mapa.items():
            if chave in canal:
                return valor
        return canal[:20]
