
import json
import os
import requests
from datetime import datetime, timedelta
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.tools import tool
from .literals import TribunalLiteral
from dotenv import load_dotenv
from django.conf import settings
from django.utils import timezone
from agno.models.openai import OpenAIChat
from usuarios.models import Compromisso


def get_localzone_name():
    tz_name = datetime.now().astimezone().tzname()
    return tz_name or "America/Sao_Paulo"

load_dotenv()


def _parse_local_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@tool
def listar_compromissos_do_dia(data: str, user_id: int):
    """
    Lista compromissos da agenda interna para um dia específico.

    Args:
        data: Data no formato YYYY-MM-DD.
        user_id: ID do usuário dono da agenda.
    """
    inicio_dia = _parse_local_datetime(f"{data}T00:00:00")
    fim_dia = inicio_dia + timedelta(days=1)
    compromissos = Compromisso.objects.filter(
        user_id=user_id,
        data_hora__gte=inicio_dia,
        data_hora__lt=fim_dia,
    ).order_by("data_hora")

    return {
        "data": data,
        "compromissos": [
            {
                "id": c.id,
                "titulo": c.titulo,
                "descricao": c.descricao,
                "inicio": c.data_hora.isoformat(),
                "fim": (c.data_hora_fim or (c.data_hora + timedelta(hours=1))).isoformat(),
            }
            for c in compromissos
        ],
    }


@tool
def criar_compromisso_sistema(
    user_id: int,
    titulo: str,
    inicio_iso: str,
    fim_iso: str,
    descricao: str = "",
):
    """
    Cria um compromisso na agenda interna do sistema.

    Args:
        user_id: ID do usuário dono da agenda.
        titulo: Título da reunião.
        inicio_iso: Data/hora inicial no formato ISO (YYYY-MM-DDTHH:MM:SS).
        fim_iso: Data/hora final no formato ISO (YYYY-MM-DDTHH:MM:SS).
        descricao: Descrição opcional.
    """
    inicio = _parse_local_datetime(inicio_iso)
    fim = _parse_local_datetime(fim_iso)

    if fim <= inicio:
        return {"ok": False, "erro": "Horário final deve ser maior que o inicial."}
    if inicio.hour < 13 or fim.hour > 18:
        return {"ok": False, "erro": "Reuniões só podem ser agendadas entre 13h e 18h."}

    conflito = Compromisso.objects.filter(
        user_id=user_id,
        data_hora__lt=fim,
        data_hora_fim__gt=inicio,
    ).exists()
    if conflito:
        return {"ok": False, "erro": "Já existe compromisso nesse horário."}

    compromisso = Compromisso.objects.create(
        user_id=user_id,
        titulo=titulo,
        descricao=descricao,
        data_hora=inicio,
        data_hora_fim=fim,
    )
    return {
        "ok": True,
        "id": compromisso.id,
        "titulo": compromisso.titulo,
        "inicio": compromisso.data_hora.isoformat(),
        "fim": compromisso.data_hora_fim.isoformat(),
    }


def _normalizar_numero_cnj(process_number: str) -> str:
    """Remove pontuação e mantém apenas dígitos (20 caracteres no padrão CNJ)."""
    digits = "".join(c for c in str(process_number) if c.isdigit())
    return digits


@tool
def search_datajud_api(tribunal: TribunalLiteral, process_number: str):
    """
    Busca informações de um processo judicial na API pública do DataJud (CNJ).

    Realiza uma consulta na API pública do Conselho Nacional de Justiça
    para obter dados de um processo judicial específico em um determinado tribunal.

    Args:
        tribunal: Código do tribunal onde o processo está tramitando.
            Valores aceitos: "tst", "tse", "stj", "stm", "trf1"-"trf6",
            "tjsp", "tjmg", etc. (ver TribunalLiteral para lista completa).
        process_number: Número do processo judicial no formato CNJ
            (ex: "00008323520184013202" ou com pontuação "0000832-35.2018.4.01.3202").

    Returns:
        Resposta da API em formato JSON como string contendo os dados do processo,
        incluindo informações como número, partes, movimentações, decisões, etc.
        Retorna JSON com campo "error" em caso de falha na requisição.
    """

    numero = _normalizar_numero_cnj(process_number)
    if len(numero) != 20:
        return json.dumps(
            {
                "error": "Número CNJ inválido: são necessários 20 dígitos "
                "(após remover pontos e traços). Peça o número completo ao usuário."
            }
        )

    url = f"https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal}/_search"
    payload = {
        "query": {
            "match": {
                "numeroProcesso": numero
            }
        }
    }
    api_key = (os.environ.get("DATAJUD_API_KEY") or "").strip()
    if not api_key:
        return json.dumps({"error": "consulta_indisponivel"})

    headers = {
        "Authorization": f"APIKey {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return json.dumps({"error": "consulta_indisponivel"})

class JuriAI:

    DATAJUD_BASE_URL = "https://api-publica.datajud.cnj.jus.br"
    # P2C-LEGACY: índice Lance por tabela global — não instanciar.
    MEMORY_DB_FILE = "db.sqlite3"
    MEMORY_TABLE = "my_memory_table"
    AGENT_NAME = "Assistente Jurídico Virtual"
    AGENT_DESCRIPTION = (
        "Assistente virtual especializado em questões jurídicas com acesso "
        "a base de conhecimento e consulta de processos judiciais."
    )

    INSTRUCTIONS = """
    SUAS CAPACIDADES:
    1. Acesso a Base de Conhecimento (RAG): Você possui acesso a uma base de dados
    e deve usá-la para responder as perguntas do usuário de forma precisa e fundamentada.
    2. Consulta de Processos: Você pode buscar informações sobre processos judiciais
    através da API do DataJud (CNJ).

    DIRETRIZES:
    - Sempre priorize informações da base de conhecimento quando disponíveis.
    - Ao consultar processos, forneça informações claras e organizadas.
    - Se não tiver certeza sobre alguma informação, indique isso ao usuário.
    - Mantenha um tom profissional e objetivo em todas as respostas.
    """

    @classmethod
    def build_agent(cls, organization, knowledge_filters: dict | None = None) -> Agent:
        from ia.services.document_knowledge import retrieve_tenant_context

        if organization is None:
            raise ValueError("MISSING_ORGANIZATION")
        extra = dict(knowledge_filters or {})
        extra.pop("organization_id", None)
        db = SqliteDb(
            db_file=cls.MEMORY_DB_FILE,
            memory_table=f"juri_memory_org_{organization.pk}",
        )
        context = retrieve_tenant_context(organization, "")
        instructions = cls.INSTRUCTIONS
        if context:
            instructions = (
                f"{cls.INSTRUCTIONS}\n\nCONTEXTO TENANT-SCOPED:\n{context}"
            )

        return Agent(
            name=cls.AGENT_NAME,
            description=cls.AGENT_DESCRIPTION,
            tools=[search_datajud_api],
            instructions=instructions,
            db=db,
            update_memory_on_run=True,
            knowledge=None,
            knowledge_filters=extra,
            search_knowledge=False,
        )

class SecretariaAI:
    MEMORY_DB_FILE = "db.sqlite3"
    MEMORY_TABLE = "secretaria_memory_table"

    INSTRUCTIONS = f"""
    Você é um assistente virtual de secretaria especializado em atendimento ao cliente e agendamento de reuniões.
    Atue como vendedor da empresa, você deve vender os produtos e serviços da empresa para o cliente.
    Sempre que vir alguma dúvida sobre a empresa, consulte a base de conhecimento e responda as perguntas do cliente direcionando para algum produto e com foco em agendar uma reuniao com o advogado, deixe a pessoa escolher entre os possiveis dias e horarios disponiveis.
    SUAS CAPACIDADES:

    1. BASE DE CONHECIMENTO (RAG):
       - Você possui acesso a uma base de conhecimento com informações da empresa, incluindo:
         * Informações sobre produtos e serviços
         * Preços e tabelas de valores
         * Políticas e procedimentos da empresa
         * Informações de contato e localização
         * Documentos e materiais institucionais
       - SEMPRE consulte a base de conhecimento antes de responder perguntas sobre a empresa.
       - Use as informações encontradas para fornecer respostas precisas e atualizadas.
       - Se não encontrar informações na base de conhecimento, seja honesto e informe ao cliente.

    2. ATENDIMENTO AO CLIENTE:
       - Seja cordial, profissional e prestativo em todas as interações.
       - Responda perguntas sobre produtos, serviços, preços e políticas da empresa.
       - Forneça informações claras e objetivas.
       - Se não souber algo, ofereça-se para buscar mais informações ou conectar o cliente com o setor adequado.

    3. AGENDAMENTO DE REUNIÕES:
       - Você tem acesso a agenda do sistema para agendar reuniões.
       - IMPORTANTE: Reuniões devem ser agendadas APENAS entre 13h e 18h (horário local).
       - Antes de agendar, SEMPRE verifique os horários disponíveis no calendário.
       - Procure por espaços livres no calendário entre 13h e 18h.
       - Se o cliente solicitar um horário fora desse intervalo, explique que os agendamentos são apenas entre 13h e 18h e ofereça alternativas dentro desse horário.
       - Ao criar um evento, inclua:
         * Título descritivo da reunião
         * Data e horário (entre 13h e 18h)
         * Duração sugerida (padrão: 1 hora, a menos que o cliente especifique)
         * Descrição com informações relevantes se fornecidas pelo cliente

    DIRETRIZES DE AGENDAMENTO:
    - Horário permitido: 13:00 às 18:00 (horário local)
    - Sempre verifique disponibilidade antes de confirmar
    - Se não houver horário disponível no dia solicitado, ofereça alternativas nos próximos dias
    - Confirme o agendamento com o cliente antes de criar o evento

    FLUXO DE ATENDIMENTO:
    1. Cumprimente o cliente de forma cordial
    2. Identifique a necessidade (informação ou agendamento)
    3. Para informações: consulte a base de conhecimento e responda
    4. Para agendamento: verifique disponibilidade e agende entre 13h-18h
    5. Confirme todas as informações antes de finalizar

    REGRAS OBRIGATÓRIAS DE FERRAMENTAS:
    - Quando o usuário pedir agendamento ou disponibilidade, SEMPRE chame a ferramenta de listagem antes de responder.
    - Nunca diga que "não conseguiu acessar a agenda" sem ter tentado a ferramenta.
    - Se o cliente disser "amanhã", converta para data relativa ao dia atual e consulte a agenda.
    - Depois que o cliente confirmar dia e horário, SEMPRE chame a ferramenta de criação do compromisso.
    - Após criar, responda com confirmação objetiva contendo data e horário final.
    - Se houver conflito, ofereça pelo menos 2 alternativas de horário livre entre 13h e 18h.

    FORMATO DE RESPOSTA (OBRIGATÓRIO):
    - Seja breve e direto. No máximo 3 linhas.
    - Não use listas com marcadores, não use markdown e não repita contexto.
    - Confirmação de agendamento deve seguir este padrão:
      "Agendado com sucesso: DD/MM/YYYY das HH:MM às HH:MM."
    - Quando houver conflito, responda neste padrão:
      "Horario indisponivel. Sugestoes: HH:MM-HH:MM, HH:MM-HH:MM."
    - Quando faltar confirmação do cliente, responda neste padrão:
      "Posso confirmar este horario? Responda: SIM para confirmar."

    4. CONSULTA DE PROCESSO JUDICIAL (DataJud):
       - Se o cliente perguntar sobre andamento, status ou dados de um processo e informar
         número CNJ (com ou sem pontuação), use a ferramenta search_datajud_api.
       - É obrigatório o código do tribunal correto (ex: tjsp, tjmg, trf1). Se o usuário não
         souber, explique que precisa do tribunal de tramitação e ofereça ajuda para identificar
         pelos dígitos do CNJ (segmentos do número indicam tribunal/segmento de justiça).
       - Após a consulta, resuma de forma objetiva: número, assunto/classe se vier na resposta,
         e principais movimentações ou situação. Pode usar até 8 linhas nesse caso.

    Data e hora atual: {datetime.now()}
    Fuso horário: {get_localzone_name()}
    """

    @classmethod
    def build_agent(
        cls,
        knowledge_filters: dict | None = None,
        session_id: int = 1,
        *,
        user_id: int,
        organization=None,
        knowledge_context: str = "",
    ) -> Agent:
        @tool
        def listar_compromissos_do_usuario(data: str):
            """
            Lista compromissos do dia para o usuário atual da sessão.
            Args:
                data: Data no formato YYYY-MM-DD.
            """
            return listar_compromissos_do_dia.entrypoint(data=data, user_id=user_id)

        @tool
        def criar_compromisso_do_usuario(
            titulo: str,
            inicio_iso: str,
            fim_iso: str,
            descricao: str = "",
        ):
            """
            Cria compromisso para o usuário atual da sessão.
            Args:
                titulo: Título da reunião.
                inicio_iso: Data/hora inicial no formato ISO (YYYY-MM-DDTHH:MM:SS).
                fim_iso: Data/hora final no formato ISO (YYYY-MM-DDTHH:MM:SS).
                descricao: Descrição opcional.
            """
            return criar_compromisso_sistema.entrypoint(
                user_id=user_id,
                titulo=titulo,
                inicio_iso=inicio_iso,
                fim_iso=fim_iso,
                descricao=descricao,
            )

        db = SqliteDb(
            db_file=cls.MEMORY_DB_FILE,
            memory_table=cls.MEMORY_TABLE
        )
        _ = knowledge_filters
        if organization is None:
            rag_block = (
                "Não há Organization resolvida. Não consulte índice global. "
                "Não invente documentos da empresa."
            )
        elif knowledge_context:
            rag_block = (
                "CONTEXTO INTERNO DA ORGANIZAÇÃO (já filtrado pelo tenant; use só isto):\n"
                f"{knowledge_context}\n"
                "Não use documentos de outras organizações. "
                "Não invente conteúdo ausente deste contexto."
            )
        else:
            rag_block = (
                "Não há contexto RAG tenant-scoped disponível. "
                "Não consulte índice global. Não invente documentos da empresa."
            )
        instructions = f"{cls.INSTRUCTIONS}\n\n{rag_block}"

        return Agent(
            name="Assistente de Secretaria Virtual",
            description="Assistente virtual para atendimento ao cliente e agendamento de reuniões",
            model=OpenAIChat(id="gpt-4o-mini"),
            tools=[
                listar_compromissos_do_usuario,
                criar_compromisso_do_usuario,
                search_datajud_api,
            ],
            instructions=instructions,
            db=db,
            update_memory_on_run=True,
            knowledge=None,
            knowledge_filters={},
            search_knowledge=False,
            session_id=f"secretaria-{session_id}-user-{user_id}",
            add_history_to_context=True,
            num_history_runs=5,
            add_datetime_to_context=True,
        )

