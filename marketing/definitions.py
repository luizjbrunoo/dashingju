"""
Definições centralizadas para métricas Marketing → Resultados do Negócio.

Lead (Google Ads):
    Cliente com origem=google_ads e atribuicao_confiavel=True no período (criado_em).

Consulta agendada:
    Compromisso tipo=consulta vinculado a cliente Google Ads, data_hora no período.

Consulta realizada:
    Consulta com status=realizado (exclui cancelado e não compareceu).

Proposta:
    Cliente Google Ads com fase_funil em (proposta_enviada, aguardando_decisao).

Contrato:
    Contrato active/closed do cliente Google Ads, criado_em no período.

Receita contratada:
    Soma de Contrato.valor_total dos contratos atribuíveis (não é caixa).

Receita recebida:
    Soma de CobrancaRecebimento.valor (cancelado_em IS NULL) dos clientes atribuíveis.

CAC de mídia:
    Investimento Google Ads / novos clientes com origem Google Ads confiável.
    Não inclui custos operacionais — rotular sempre como CAC DE MÍDIA.
"""

from usuarios.choices import OrigemLead

# Origens contabilizáveis como Google Ads nos relatórios de mídia.
ORIGEM_GOOGLE_ADS = OrigemLead.GOOGLE_ADS

# Fases do funil CRM consideradas "proposta" (fases reais do model Cliente).
FASES_PROPOSTA = frozenset({"proposta_enviada", "aguardando_decisao"})
