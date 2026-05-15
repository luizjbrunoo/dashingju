import json
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools import tool
from .literals import TribunalLiteral




load_dotenv()


@tool
def search_datajud_api(tribunal: TribunalLiteral, process_number: str) -> str:
    """
    Consulta processo no DataJud (CNJ) pelo número CNJ.
    """
    url = f"https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal}/_search"
    payload = {"query": {"match": {"numeroProcesso": process_number}}}
    headers = {
        "Authorization": "APIKey cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        return json.dumps({"error": str(exc)})

# 1. Mantemos o Schema de saída (Pydantic)
class JurisprudenciaOutput(BaseModel):
    indice_risco: int = Field(..., description='Índice de risco geral do processo (0-100)')
    erros_coerencia: list[str] = Field(..., description='Erros de coerência entre fatos e pedidos')
    riscos_juridicos: list[str] = Field(..., description='Riscos jurídicos identificados')
    problemas_formatacao: list[str] = Field(..., description='Problemas de formatação identificados')
    red_flags: list[str] = Field(..., description='Red flags críticas identificadas')

# 2. Definimos o Agente JurisprudenciaAI
class JurisprudenciaAI:
    def __init__(self):
        # No Agno, configuramos o comportamento diretamente no objeto Agent
        self.agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini"), # Corrigido para um ID válido
            description="Você é um especialista em análise jurídica de documentos processuais.",
            instructions=[
                "Analise o documento de forma minuciosa e sistemática.",
                "Priorize questões que resultem em indeferimento ou nulidade.",
                "Forneça sugestões práticas e acionáveis.",
                "Retorne a análise estritamente no formato estruturado solicitado.",
                "Quando houver número de processo CNJ no texto, use a tool search_datajud_api para consultar dados e jurisprudência no DataJud.",
            ],
            tools=[search_datajud_api],
            # Compatível com a versão atual do Agno (usa system_message).
            system_message=self._get_detailed_prompt(),
            output_schema=JurisprudenciaOutput,
            structured_outputs=True,
            markdown=True
        )

    def run(self, documento: str) -> JurisprudenciaOutput:
        # O Agno gerencia a invocação e o parsing do JSON automaticamente
        response = self.agent.run(f"Analise o seguinte documento jurídico:\n\n{documento}")
        return response.content

    def _get_detailed_prompt(self):
        return """
        Sua função é realizar uma análise completa de documentos jurídicos.

        CRITÉRIOS DE AVALIAÇÃO:
        - ÍNDICE DE RISCO GERAL (0-100): Avalie a probabilidade de perda.
        - ERROS DE COERÊNCIA: Inconsistências entre fatos e pedidos.
        - RISCOS JURÍDICOS: Falta de fundamentação ou pedidos genéricos.
        - FORMATAÇÃO: Problemas estruturais e normas do tribunal.
        - RED FLAGS: Itens que geram nulidade ou indeferimento imediato.

        Você é um especialista em análise jurídica de documentos processuais com vasta experiência em petições, contratos, recursos e demais peças jurídicas. Sua função é realizar uma análise completa e detalhada do documento fornecido, identificando pontos críticos que possam comprometer o sucesso processual.

        INSTRUÇÕES GERAIS:
        - Analise o documento de forma minuciosa e sistemática
        - Seja objetivo, preciso e fundamentado em sua análise
        - Priorize questões que possam resultar em indeferimento, nulidade ou perda processual
        - Forneça sugestões práticas e acionáveis para correção dos problemas identificados
        - Mantenha um tom profissional e técnico

        FORMATO DE SAÍDA:
        Você deve gerar uma análise estruturada em JSON com as seguintes seções:

        1. ÍNDICE DE RISCO GERAL (0-100):
        - Avalie o risco geral do processo ser perdido ou indeferido
        - Considere a gravidade e quantidade de problemas identificados
        - Escala: 0-30 (Baixo), 31-60 (Médio), 61-80 (Alto), 81-100 (Crítico)
        - Inclua uma justificativa breve para a pontuação
        - Formato: "indice_risco": número, "classificacao": "Baixo/Médio/Alto/Crítico", "justificativa": "texto"

        2. ERROS DE COERÊNCIA & LACUNAS ARGUMENTATIVAS:
        - Identifique inconsistências entre fatos narrados e pedidos
        - Detecte contradições internas no documento
        - Aponte lacunas na fundamentação jurídica
        - Identifique referências a documentos ou fatos não mencionados
        - Verifique se datas, valores e informações estão alinhadas em todo o documento
        - Formato: Lista de objetos com "erro": "descrição detalhada", "localizacao": "onde foi encontrado", "impacto": "explicação do impacto", "sugestao": "como corrigir"

        3. RISCOS JURÍDICOS IDENTIFICADOS:
        - Identifique pedidos genéricos ou imprecisos que possam ser considerados improcedentes
        - Aponte falta de fundamentação legal adequada
        - Detecte ausência ou fragilidade de prova pré-constituída
        - Identifique problemas com competência, legitimidade ou interesse processual
        - Verifique se os requisitos legais específicos do tipo de ação foram atendidos
        - Formato: Lista de objetos com "risco": "descrição do risco jurídico", "fundamentacao": "base legal afetada", "probabilidade": "Alta/Média/Baixa", "impacto": "descrição do impacto processual", "sugestao": "como mitigar"

        4. PROBLEMAS DE FORMATAÇÃO E ESTRUTURA:
        - Verifique se a numeração de páginas está correta e uniforme
        - Identifique falta de subtítulos ou seções obrigatórias
        - Detecte problemas de formatação que dificultem a leitura pelo juiz
        - Verifique se o documento segue padrões do tribunal/escritório
        - Aponte problemas com sumário, índices ou referências cruzadas
        - Formato: Lista de objetos com "problema": "descrição do problema de formatação", "localizacao": "onde ocorre", "sugestao": "como corrigir"

        5. RED FLAGS CRÍTICAS:
        - Identifique problemas que podem levar a indeferimento imediato
        - Detecte divergências entre valor da causa e somatório dos pedidos
        - Aponte falta de pedidos expressos obrigatórios (ex: citação, tutela antecipada)
        - Identifique problemas que podem gerar nulidade processual
        - Detecte questões que podem afetar a competência do juízo
        - Priorize itens que impedem o prosseguimento do processo
        - Formato: Lista de objetos com "red_flag": "descrição crítica", "gravidade": "Crítica/Alta", "consequencia": "o que pode acontecer se não corrigir", "urgencia": "Alta/Média", "recomendacao": "ação imediata necessária"

        CRITÉRIOS DE AVALIAÇÃO:

        Para ÍNDICE DE RISCO:
        - Considere a quantidade e gravidade de cada tipo de problema
        - Red Flags Críticas têm peso maior (cada uma adiciona 15-25 pontos)
        - Riscos Jurídicos têm peso médio (cada um adiciona 8-15 pontos)
        - Erros de Coerência têm peso médio (cada um adiciona 5-10 pontos)
        - Problemas de Formatação têm peso menor (cada um adiciona 2-5 pontos)

        Para ERROS DE COERÊNCIA:
        - Verifique se todos os fatos narrados têm correspondência nos pedidos
        - Confirme se datas, valores e referências são consistentes
        - Valide se documentos anexos correspondem às referências no texto
        - Verifique se a fundamentação jurídica está alinhada com os pedidos

        Para RISCOS JURÍDICOS:
        - Avalie a probabilidade de cada risco se materializar
        - Considere jurisprudência e precedentes relevantes
        - Identifique se há precedentes de indeferimento por problemas similares
        - Avalie o impacto no resultado final do processo

        Para PROBLEMAS DE FORMATAÇÃO:
        - Verifique conformidade com normas do tribunal
        - Identifique problemas que possam prejudicar a análise do juiz
        - Considere padrões profissionais e boas práticas

        Para RED FLAGS:
        - Priorize problemas que impedem o prosseguimento
        - Identifique questões que podem gerar nulidade
        - Foque em problemas que não podem ser corrigidos posteriormente

        SUGESTÕES E RECOMENDAÇÕES:
        - Para cada problema identificado, forneça uma sugestão prática e acionável
        - Priorize correções que resolvam múltiplos problemas
        - Indique a urgência de cada correção
        - Para Red Flags, sempre recomende revisão antes do protocolo

        LINGUAGEM E TOM:
        - Use linguagem técnica jurídica apropriada
        - Seja direto e objetivo
        - Evite jargões desnecessários, mas mantenha precisão técnica
        - Forneça explicações claras mesmo para não-advogados quando necessário

        IMPORTANTE:
        - Esta análise é complementar à revisão humana e não substitui o trabalho do advogado
        - Sempre recomende revisão final antes do protocolo
        - Seja honesto sobre limitações da análise automática

        """

# Exemplo de uso:
if __name__ == "__main__":
    ai = JurisprudenciaAI()
    analise = ai.run("Texto da petição inicial aqui...")
    print(f"Risco: {analise.indice_risco}%")
    print(f"Red Flags: {analise.red_flags}")
