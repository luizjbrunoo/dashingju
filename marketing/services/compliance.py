"""Verificação automatizada de compliance para conteúdo jurídico."""

from __future__ import annotations

import os
import re
from typing import Any

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel, Field

from marketing.models import ContentApproval, ContentItem, ContentVersion


class ItemChecklist(BaseModel):
    rotulo: str
    status: str = Field(..., description="ok, alerta ou revisar")
    detalhe: str = ""


class ComplianceOutput(BaseModel):
    itens: list[ItemChecklist] = Field(default_factory=list)
    observacoes: str = ""


AVISO_LEGAL = (
    "Verificação automatizada concluída. Recomenda-se revisão profissional "
    "antes da publicação."
)

# Padrões heurísticos (marketing jurídico / OAB)
_PADROES_PROMESSA = re.compile(
    r"garantimos|garantia de|resultado garantido|vit[oó]ria garantida|"
    r"100\s*%\s*de sucesso|sempre ganha|nunca perde|processo ganho|"
    r"causa ganha|dinheiro garantido",
    re.IGNORECASE,
)
_PADROES_SENSACIONALISMO = re.compile(
    r"urgente!|última chance|não perca|imperdível|chocante|escândalo|"
    r"revolucionário|milagre",
    re.IGNORECASE,
)
_PADROES_ABSOLUTO = re.compile(
    r"\b(sempre|nunca|todo mundo|todos os casos|com certeza|infallível)\b",
    re.IGNORECASE,
)
_PADROES_COMERCIAL = re.compile(
    r"melhor escritório|#1|mais barato|promoção|desconto exclusivo|"
    r"preço imperdível|contrate já",
    re.IGNORECASE,
)
_PADROES_JURIDICO = re.compile(
    r"você tem direito|a lei garante|art\.\s*\d+|§\s*\d+|"
    r"prazo de \d+ dias|multa de \d+|indenização de r\$",
    re.IGNORECASE,
)
_PADROES_CTA_INADEQUADA = re.compile(
    r"ganhe sua causa|processo ganho|contrate agora e ganhe|"
    r"resultado assegurado|vitória certa",
    re.IGNORECASE,
)
_PADROES_DADO_SEM_FONTE = re.compile(
    r"\d+\s*%|estudos mostram|pesquisa comprova|segundo dados|"
    r"estatística[s]? (indica|mostra|revela)",
    re.IGNORECASE,
)


class ComplianceService:
    """Checklist automatizado — não substitui revisão profissional."""

    def verificar(
        self,
        item: ContentItem,
        *,
        user,
        versao: ContentVersion | None = None,
    ) -> ContentApproval:
        texto = self._texto(item, versao)
        if not texto.strip():
            raise ValueError("Não há texto para verificar. Salve ou gere o conteúdo primeiro.")

        checklist = self._checklist_regras(texto, item)
        checklist = self._complementar_com_ia(texto, item, checklist)
        observacoes = AVISO_LEGAL
        if any(c["status"] == "revisar" for c in checklist):
            observacoes += " Foram identificados pontos que exigem atenção."

        return ContentApproval.objects.create(
            content_item=item,
            content_version=versao,
            checklist=checklist,
            observacoes=observacoes,
            revisado_por=user,
        )

    def _texto(self, item: ContentItem, versao: ContentVersion | None) -> str:
        if versao and versao.corpo_texto:
            return versao.corpo_texto
        if item.corpo:
            return item.corpo
        ultima = item.versoes.order_by("-numero").first()
        return ultima.corpo_texto if ultima else ""

    def _checklist_regras(self, texto: str, item: ContentItem) -> list[dict[str, Any]]:
        itens: list[dict[str, Any]] = []

        promessa = _PADROES_PROMESSA.search(texto)
        itens.append({
            "rotulo": "Sem promessa explícita de resultado",
            "status": "revisar" if promessa else "ok",
            "detalhe": f"Trecho detectado: «{promessa.group()}»" if promessa else "",
        })

        sensacional = _PADROES_SENSACIONALISMO.search(texto)
        itens.append({
            "rotulo": "Sem linguagem sensacionalista",
            "status": "revisar" if sensacional else "ok",
            "detalhe": f"Trecho: «{sensacional.group()}»" if sensacional else "",
        })

        absoluto = _PADROES_ABSOLUTO.search(texto)
        itens.append({
            "rotulo": "Sem afirmações absolutas",
            "status": "alerta" if absoluto else "ok",
            "detalhe": f"Trecho: «{absoluto.group()}»" if absoluto else "",
        })

        comercial = _PADROES_COMERCIAL.search(texto)
        itens.append({
            "rotulo": "Linguagem informativa (não excessivamente comercial)",
            "status": "alerta" if comercial else "ok",
            "detalhe": f"Trecho: «{comercial.group()}»" if comercial else "",
        })

        juridico = _PADROES_JURIDICO.search(texto)
        itens.append({
            "rotulo": "Revisar afirmação jurídica",
            "status": "revisar" if juridico else "ok",
            "detalhe": f"Trecho: «{juridico.group()}»" if juridico else "",
        })

        cta_texto = item.cta or ""
        cta_corpo = _PADROES_CTA_INADEQUADA.search(texto + " " + cta_texto)
        itens.append({
            "rotulo": "CTA informativa",
            "status": "revisar" if cta_corpo else "ok",
            "detalhe": f"Trecho: «{cta_corpo.group()}»" if cta_corpo else "",
        })

        dado = _PADROES_DADO_SEM_FONTE.search(texto)
        itens.append({
            "rotulo": "Validar dado citado",
            "status": "revisar" if dado else "ok",
            "detalhe": f"Trecho: «{dado.group()}»" if dado else "",
        })

        return itens

    def _complementar_com_ia(
        self,
        texto: str,
        item: ContentItem,
        checklist: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not os.environ.get("OPENAI_API_KEY"):
            return checklist
        try:
            agent = Agent(
                model=OpenAIChat(id="gpt-4o-mini"),
                description="Revisor de compliance para marketing jurídico.",
                instructions=[
                    "Analise o conteúdo buscando riscos éticos e de OAB.",
                    "Não afirme que o conteúdo está aprovado.",
                    "Retorne apenas itens adicionais relevantes com status alerta ou revisar.",
                ],
                output_schema=ComplianceOutput,
                structured_outputs=True,
            )
            prompt = f"""
Analise este conteúdo de marketing jurídico (canal: {item.canal}, área: {item.area_juridica}):

{texto[:6000]}

Identifique riscos adicionais não óbvios. Máximo 3 itens extras.
"""
            output: ComplianceOutput = agent.run(prompt).content
            rotulos_existentes = {c["rotulo"] for c in checklist}
            for extra in output.itens:
                if extra.rotulo not in rotulos_existentes and extra.status in ("alerta", "revisar"):
                    checklist.append(extra.model_dump())
        except Exception:
            pass
        return checklist
