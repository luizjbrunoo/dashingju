"""Mensagens de cobrança (template e IA)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from django.utils import timezone
from pydantic import BaseModel, Field

from financeiro.choices import AcaoCobrancaHistorico, TomMensagemCobranca
from financeiro.services.cobrancas import saldo_cobranca
from financeiro.services.historico_cobranca import registrar_historico_cobranca


class CobrancaMensagemError(Exception):
    """Erro ao gerar mensagem de cobrança."""


@dataclass(frozen=True)
class ContextoCobrancaMensagem:
    cliente_nome: str
    cliente_telefone: str
    cliente_email: str
    descricao: str
    saldo: Decimal
    valor_original: Decimal
    data_vencimento: date
    dias_atraso: int | None
    parcela_rotulo: str
    vencimento_fmt: str
    saldo_fmt: str


class MensagemCobrancaOutput(BaseModel):
    mensagem: str = Field(..., description="Texto pronto para envio ao cliente")


_TOM_INSTRUCOES = {
    TomMensagemCobranca.CORDIAL: "Tom cordial, acolhedor e respeitoso.",
    TomMensagemCobranca.OBJETIVO: "Tom objetivo e direto, sem rodeios.",
    TomMensagemCobranca.FORMAL: "Tom formal e institucional, adequado a comunicação escrita.",
    TomMensagemCobranca.RELACIONAMENTO: "Tom de relacionamento, valorizando a parceria com o cliente.",
}


def montar_contexto_mensagem(cobranca, *, hoje: date | None = None) -> ContextoCobrancaMensagem:
    hoje = hoje or timezone.localdate()
    cliente = cobranca.cliente
    saldo = saldo_cobranca(cobranca)
    dias_atraso = (hoje - cobranca.data_vencimento).days if cobranca.data_vencimento < hoje else None
    return ContextoCobrancaMensagem(
        cliente_nome=cliente.nome,
        cliente_telefone=cliente.telefone or "",
        cliente_email=cliente.email or "",
        descricao=cobranca.descricao,
        saldo=saldo,
        valor_original=cobranca.valor_original,
        data_vencimento=cobranca.data_vencimento,
        dias_atraso=dias_atraso if dias_atraso and dias_atraso > 0 else None,
        parcela_rotulo=cobranca.parcela_rotulo or "",
        vencimento_fmt=cobranca.data_vencimento.strftime("%d/%m/%Y"),
        saldo_fmt=f"R$ {saldo:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    )


def gerar_mensagem_padrao(
    contexto: ContextoCobrancaMensagem,
    *,
    tom: str = TomMensagemCobranca.CORDIAL,
) -> str:
    """Mensagem sugerida sem IA (sempre disponível)."""
    if tom not in TomMensagemCobranca.values:
        tom = TomMensagemCobranca.CORDIAL

    referencia = contexto.descricao
    if contexto.parcela_rotulo:
        referencia = f"{contexto.descricao} ({contexto.parcela_rotulo})"

    if tom == TomMensagemCobranca.FORMAL:
        saudacao = f"Prezado(a) {contexto.cliente_nome},"
        corpo = (
            f"Informamos que permanece pendente em nosso controle o valor de {contexto.saldo_fmt}, "
            f"referente a {referencia}, com vencimento em {contexto.vencimento_fmt}."
        )
        if contexto.dias_atraso:
            corpo += f" O débito encontra-se em atraso há {contexto.dias_atraso} dia(s)."
        fechamento = (
            "Caso o pagamento já tenha sido efetuado, solicitamos a gentileza de nos informar "
            "para atualização de nossos registros."
        )
        return f"{saudacao}\n\n{corpo}\n\n{fechamento}\n\nAtenciosamente."

    if tom == TomMensagemCobranca.OBJETIVO:
        linhas = [
            f"Olá, {contexto.cliente_nome}.",
            "",
            f"Pendência: {contexto.saldo_fmt} — {referencia}.",
            f"Vencimento: {contexto.vencimento_fmt}.",
        ]
        if contexto.dias_atraso:
            linhas.append(f"Atraso: {contexto.dias_atraso} dia(s).")
        linhas.extend(
            [
                "",
                "Se o pagamento já foi realizado, avise-nos para baixa.",
            ]
        )
        return "\n".join(linhas)

    if tom == TomMensagemCobranca.RELACIONAMENTO:
        saudacao = f"Olá, {contexto.cliente_nome}!"
        corpo = (
            f"Passando para alinhar sobre {referencia}, com vencimento em {contexto.vencimento_fmt}. "
            f"Consta saldo de {contexto.saldo_fmt} em aberto em nosso controle."
        )
        if contexto.dias_atraso:
            corpo += f" Sabemos que imprevistos acontecem — estamos à disposição para conversar."
        fechamento = (
            "Se já realizou o pagamento, nos avise para atualizarmos. "
            "Conte conosco para o que precisar."
        )
        return f"{saudacao}\n\n{corpo}\n\n{fechamento}"

    # Cordial (padrão)
    corpo = (
        f"Identificamos que {referencia}, com vencimento em {contexto.vencimento_fmt}, "
        f"permanece pendente em nosso controle ({contexto.saldo_fmt})."
    )
    if contexto.dias_atraso:
        corpo += f" O vencimento ocorreu há {contexto.dias_atraso} dia(s)."
    fechamento = (
        "Caso o pagamento já tenha sido realizado, desconsidere esta mensagem "
        "e nos informe para atualização."
    )
    return f"Olá, {contexto.cliente_nome}.\n\n{corpo}\n\n{fechamento}"


def ia_disponivel() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def gerar_mensagem_ia(cobranca, *, tom: str, autor) -> str:
    """Gera mensagem com IA; registra histórico."""
    if not ia_disponivel():
        raise CobrancaMensagemError(
            "OPENAI_API_KEY não configurada. Defina a chave no arquivo .env para usar a geração com IA."
        )
    if tom not in TomMensagemCobranca.values:
        tom = TomMensagemCobranca.CORDIAL

    contexto = montar_contexto_mensagem(cobranca)
    prompt = _montar_prompt_ia(contexto, tom)
    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        description="Redator de mensagens de cobrança para escritórios de advocacia.",
        instructions=[
            "Redija mensagens de cobrança éticas para escritórios de advocacia brasileiros.",
            _TOM_INSTRUCOES.get(tom, _TOM_INSTRUCOES[TomMensagemCobranca.CORDIAL]),
            "Nunca use linguagem ameaçadora, constrangedora, agressiva ou enganosa.",
            "Não prometa consequências jurídicas nem use tom coercitivo.",
            "Mencione valor em aberto e vencimento de forma clara.",
            "Inclua convite para o cliente informar se o pagamento já foi feito.",
            "Texto pronto para WhatsApp ou e-mail, sem markdown nem assinatura longa.",
            "Máximo de 120 palavras.",
        ],
        output_schema=MensagemCobrancaOutput,
        structured_outputs=True,
    )
    try:
        output: MensagemCobrancaOutput = agent.run(prompt).content
        mensagem = (output.mensagem or "").strip()
    except Exception as exc:
        raise CobrancaMensagemError(f"Falha ao gerar mensagem com IA: {exc}") from exc

    if not mensagem:
        raise CobrancaMensagemError("A IA não retornou uma mensagem válida.")

    registrar_historico_cobranca(
        cobranca,
        AcaoCobrancaHistorico.MENSAGEM_COBRANCA,
        descricao=f"Mensagem de cobrança gerada (tom: {TomMensagemCobranca(tom).label}).",
        autor=autor,
        metadados={"tom": tom, "origem": "ia"},
    )
    return mensagem


def _montar_prompt_ia(contexto: ContextoCobrancaMensagem, tom: str) -> str:
    atraso = (
        f"Dias de atraso: {contexto.dias_atraso}."
        if contexto.dias_atraso
        else "A cobrança ainda não está vencida ou vence hoje."
    )
    return (
        f"Cliente: {contexto.cliente_nome}\n"
        f"Descrição: {contexto.descricao}\n"
        f"Parcela: {contexto.parcela_rotulo or 'única'}\n"
        f"Saldo em aberto: {contexto.saldo_fmt}\n"
        f"Vencimento: {contexto.vencimento_fmt}\n"
        f"{atraso}\n"
        f"Tom desejado: {TomMensagemCobranca(tom).label}\n"
        "Gere uma única mensagem para o advogado revisar antes de enviar."
    )
