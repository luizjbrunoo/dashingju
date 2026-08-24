"""Planejamento Google Ads por nicho (demonstração, sem API)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdGroup:
    name: str
    keywords: list[str]
    intent: str


@dataclass
class CampaignPlan:
    risk_level: str
    risk_score: int
    risk_summary: str
    risk_factors: list[str]
    policy_notes: list[str]
    ad_groups: list[AdGroup]
    negative_keywords: list[str]
    headlines: list[str]
    descriptions: list[str]
    cpc_min: float
    cpc_max: float
    daily_budget: float
    monthly_min: float
    monthly_max: float
    clicks_month_min: int
    clicks_month_max: int


NEGATIVAS_COMUNS = [
    "grátis",
    "gratuito",
    "emprego",
    "vaga",
    "curso",
    "faculdade",
    "estágio",
    "salário",
    "download",
    "pdf",
    "modelo",
    "planilha",
    "como fazer sozinho",
    "diy",
    "concurso",
]


def _seed(nicho: str) -> int:
    return sum(ord(c) for c in nicho)


def _estimates(nicho: str) -> tuple[float, float, float, float, float, int, int]:
    s = _seed(nicho)
    cpc_min = 4.5 + (s % 17) * 0.35
    cpc_max = cpc_min + 6.0 + (s % 11) * 0.4
    daily = 80 + (s % 9) * 25
    monthly_min = daily * 22
    monthly_max = daily * 30 * 1.15
    clicks_min = int(monthly_min / cpc_max)
    clicks_max = int(monthly_max / cpc_min)
    return cpc_min, cpc_max, daily, monthly_min, monthly_max, clicks_min, clicks_max


def _plan(
    *,
    risk_level: str,
    risk_score: int,
    risk_summary: str,
    risk_factors: list[str],
    policy_notes: list[str],
    ad_groups: list[AdGroup],
    negative_keywords: list[str],
    headlines: list[str],
    descriptions: list[str],
    nicho: str,
) -> CampaignPlan:
    cpc_min, cpc_max, daily, monthly_min, monthly_max, clicks_min, clicks_max = _estimates(nicho)
    return CampaignPlan(
        risk_level=risk_level,
        risk_score=risk_score,
        risk_summary=risk_summary,
        risk_factors=risk_factors,
        policy_notes=policy_notes,
        ad_groups=ad_groups,
        negative_keywords=NEGATIVAS_COMUNS + negative_keywords,
        headlines=headlines[:6],
        descriptions=descriptions[:2],
        cpc_min=round(cpc_min, 2),
        cpc_max=round(cpc_max, 2),
        daily_budget=round(daily, 2),
        monthly_min=round(monthly_min, 2),
        monthly_max=round(monthly_max, 2),
        clicks_month_min=clicks_min,
        clicks_month_max=clicks_max,
    )


PLANOS: dict[str, dict] = {
    "desbloqueio_conta_bancaria": {
        "risk_level": "Médio",
        "risk_score": 58,
        "risk_summary": (
            "Demanda recorrente com CPC moderado. Exige clareza sobre atuação "
            "bancária e cuidado com promessas de desbloqueio imediato."
        ),
        "risk_factors": [
            "Concorrência de escritórios e correspondentes bancários",
            "Termos genéricos elevam custo por clique",
            "Usuários buscam solução rápida; copy deve ser realista",
        ],
        "policy_notes": [
            "Evitar garantia de desbloqueio ou prazo certo de resultado",
            "Informar que a análise depende do caso e do banco",
            "Não usar linguagem de recuperação milagrosa de valores",
        ],
        "ad_groups": [
            AdGroup(
                "Conta bloqueada",
                [
                    "conta bancária bloqueada advogado",
                    "desbloqueio judicial conta",
                    "conta bloqueada judicialmente",
                ],
                "Transacional",
            ),
            AdGroup(
                "Valores retidos",
                [
                    "valores retidos em conta",
                    "liberação de saldo bloqueado",
                    "ordem judicial desbloqueio",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["pix", "abrir conta", "cartão", "limite", "investimento"],
        "headlines": [
            "Conta bloqueada? Orientação",
            "Análise jurídica bancária",
            "Desbloqueio: avalie o caso",
            "Escritório jurídico",
            "Agende consulta jurídica",
            "Atuação em direito bancário",
        ],
        "descriptions": [
            "Consultoria para conta bloqueada. Avaliamos documentos e orientamos próximos passos.",
            "Sem promessa de resultado. Atendimento ético conforme normas da OAB e Google Ads.",
        ],
    },
    "busca_apreensao": {
        "risk_level": "Alto",
        "risk_score": 72,
        "risk_summary": (
            "Nicho sensível e competitivo, com urgência do usuário. "
            "Alto risco de cliques irrelevantes e exigência rigorosa de políticas financeiras."
        ),
        "risk_factors": [
            "CPC elevado em termos de urgência",
            "Risco de anúncios fora do escopo (financiamento genérico)",
            "Necessidade de landing page clara sobre serviço jurídico",
        ],
        "policy_notes": [
            "Não prometer suspensão imediata da apreensão",
            "Evitar sensacionalismo ou medo excessivo",
            "Deixar explícito que se trata de assessoria jurídica",
        ],
        "ad_groups": [
            AdGroup(
                "Defesa urgente",
                [
                    "busca e apreensão advogado",
                    "defesa busca apreensão veículo",
                    "mandado busca apreensão",
                ],
                "Urgência",
            ),
            AdGroup(
                "Revisão contrato",
                [
                    "revisão contrato financiamento",
                    "cláusulas abusivas veículo",
                    "ação revisional veículo",
                ],
                "Preventiva",
            ),
        ],
        "negative_keywords": ["leilão carro", "comprar carro", "financiamento barato", "consorcio"],
        "headlines": [
            "Busca e apreensão: apoio",
            "Defesa em ações veiculares",
            "Análise de mandado judicial",
            "Consultoria jurídica veicular",
            "Agende orientação hoje",
            "Escritório de advocacia",
        ],
        "descriptions": [
            "Orientação jurídica em busca e apreensão de veículos. Análise individual do processo.",
            "Resultados variam conforme o caso. Informações claras, sem garantia de êxito.",
        ],
    },
    "indicacao_condutor": {
        "risk_level": "Médio",
        "risk_score": 52,
        "risk_summary": (
            "Volume estável com sazonalidade. Baixo ticket relativo, "
            "mas exige filtro forte contra buscas de multas DIY."
        ),
        "risk_factors": [
            "Muitas buscas informacionais de baixa intenção",
            "Concorrência local por geolocalização",
            "Termos ambíguos atraem tráfego de infrações simples",
        ],
        "policy_notes": [
            "Não garantir anulação de multa ou pontos",
            "Evitar prometer indicação aceita pelo DETRAN",
            "Informar necessidade de provas e prazos legais",
        ],
        "ad_groups": [
            AdGroup(
                "Indicação de condutor",
                [
                    "indicação de condutor advogado",
                    "recurso indicação condutor",
                    "multa indicação condutor prazo",
                ],
                "Transacional",
            ),
            AdGroup(
                "Defesa de multa",
                [
                    "defesa multa de trânsito",
                    "recurso multa cnh",
                    "assessoria infração trânsito",
                ],
                "Relacionada",
            ),
        ],
        "negative_keywords": ["simulador", "tabela detran", "prova teórica", "auto escola"],
        "headlines": [
            "Indicação de condutor",
            "Assessoria em multas",
            "Análise de infrações",
            "Consulta jurídica trânsito",
            "Escritório especializado",
            "Agende orientação legal",
        ],
        "descriptions": [
            "Apoio jurídico para indicação de condutor e recursos de trânsito. Avaliamos seu caso.",
            "Sem promessa de resultado. Atendimento conforme legislação e normas publicitárias.",
        ],
    },
    "revisao_contrato_conta_corrente": {
        "risk_level": "Médio",
        "risk_score": 55,
        "risk_summary": (
            "Boa intenção de busca, porém termos financeiros amplos "
            "aumentam custo e cliques de usuários buscando banco, não advogado."
        ),
        "risk_factors": [
            "Sobreposição com buscas de tarifas bancárias genéricas",
            "Necessidade de prova social ética, sem promessas",
            "CPC moderado em capitais",
        ],
        "policy_notes": [
            "Evitar prometer devolução em dobro ou valores certos",
            "Não usar 'elimine tarifas' como garantia",
            "Deixar claro escopo de revisão contratual",
        ],
        "ad_groups": [
            AdGroup(
                "Tarifas abusivas",
                [
                    "revisão contrato conta corrente",
                    "tarifas abusivas banco advogado",
                    "cobrança indevida conta corrente",
                ],
                "Transacional",
            ),
            AdGroup(
                "Restituição",
                [
                    "ação revisional bancária",
                    "repetição de indébito banco",
                    "revisão encargos bancários",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["abrir conta", "cartão sem anuidade", "investir", "crédito pessoal"],
        "headlines": [
            "Revisão de conta corrente",
            "Tarifas bancárias: análise",
            "Direito bancário aplicado",
            "Consultoria contratual",
            "Agende avaliação jurídica",
            "Escritório jurídico",
        ],
        "descriptions": [
            "Estudo jurídico de contratos de conta corrente e cobranças. Orientação personalizada.",
            "Cada caso é único. Sem garantia de valores ou prazos de restituição.",
        ],
    },
    "revisao_contrato_veiculo": {
        "risk_level": "Médio",
        "risk_score": 60,
        "risk_summary": (
            "Mercado competitivo ligado a financiamento veicular. "
            "Risco médio de atrair leads buscando refinanciamento, não advogado."
        ),
        "risk_factors": [
            "Palavras amplas como 'financiamento' elevam CPC",
            "Concorrência de correspondentes bancários",
            "Lead qualificado exige landing objetiva",
        ],
        "policy_notes": [
            "Não prometer redução certa de parcela",
            "Evitar comparação com concorrentes",
            "Informar atuação jurídica, não crédito",
        ],
        "ad_groups": [
            AdGroup(
                "Revisional veículo",
                [
                    "revisão contrato financiamento veículo",
                    "ação revisional automóvel",
                    "juros abusivos financiamento carro",
                ],
                "Transacional",
            ),
            AdGroup(
                "Cláusulas abusivas",
                [
                    "cláusula abusiva contrato veículo",
                    "advogado financiamento veicular",
                    "revisão contrato CDC veículo",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["simular financiamento", "comprar carro", "tabela fipe", "consorcio auto"],
        "headlines": [
            "Revisão contrato veículo",
            "Financiamento: análise legal",
            "Juros abusivos veículo",
            "Consultoria jurídica auto",
            "Agende orientação",
            "Escritório de advocacia",
        ],
        "descriptions": [
            "Análise jurídica de contratos de financiamento veicular. Verificamos cláusulas e encargos.",
            "Sem promessa de redução de parcela. Avaliação técnica conforme o contrato apresentado.",
        ],
    },
    "tratamento_autismo": {
        "risk_level": "Alto",
        "risk_score": 78,
        "risk_summary": (
            "Segmento de saúde suplementar com políticas sensíveis. "
            "Requer linguagem cautelosa, sem prometer tratamento ou cobertura."
        ),
        "risk_factors": [
            "Políticas de saúde e personalização médica",
            "CPC alto por termos clínicos",
            "Risco de reprovação por linguagem terapêutica",
        ],
        "policy_notes": [
            "Focar em cobertura/plano de saúde, não cura ou resultado clínico",
            "Evitar diagnóstico ou promessa terapêutica no anúncio",
            "Usar termos de assessoria jurídica em planos de saúde",
        ],
        "ad_groups": [
            AdGroup(
                "Plano de saúde TEA",
                [
                    "plano saúde autismo negativa",
                    "cobertura tratamento autismo plano",
                    "advogado plano saúde autismo",
                ],
                "Transacional",
            ),
            AdGroup(
                "Negativa operadora",
                [
                    "negativa tratamento autismo",
                    "liminar tratamento autismo plano",
                    "direito saúde autismo",
                ],
                "Urgência",
            ),
        ],
        "negative_keywords": ["clinica", "terapia preço", "medicamento", "sintomas", "diagnóstico online"],
        "headlines": [
            "Negativa plano: orientação",
            "Direito à saúde TEA",
            "Cobertura plano de saúde",
            "Assessoria jurídica saúde",
            "Análise de negativa",
            "Agende consulta jurídica",
        ],
        "descriptions": [
            "Assessoria jurídica em negativas de plano para TEA. Avaliamos documentos e contrato.",
            "Não garantimos cobertura. Cada decisão depende do caso e da operadora.",
        ],
    },
    "cirurgia_bariatrica": {
        "risk_level": "Alto",
        "risk_score": 75,
        "risk_summary": (
            "Anúncios ligados a procedimento médico exigem foco jurídico da negativa de cobertura, "
            "nunca promessa de emagrecimento ou resultado cirúrgico."
        ),
        "risk_factors": [
            "Restrições de conteúdo médico sensível",
            "Alta concorrência de clínicas (tráfego irrelevante)",
            "Termos clínicos aumentam CPC",
        ],
        "policy_notes": [
            "Não prometer cirurgia ou perda de peso",
            "Evitar imagens ou claims médicos no texto",
            "Posicionar como direito à saúde suplementar",
        ],
        "ad_groups": [
            AdGroup(
                "Cobertura bariátrica",
                [
                    "plano saúde negou bariátrica",
                    "cobertura cirurgia bariátrica plano",
                    "advogado bariátrica plano saúde",
                ],
                "Transacional",
            ),
            AdGroup(
                "Liminar saúde",
                [
                    "liminar cirurgia bariátrica",
                    "negativa procedimento bariátrica",
                    "direito cirurgia plano saúde",
                ],
                "Urgência",
            ),
        ],
        "negative_keywords": ["preço cirurgia", "clinica bariatrica", "emagrecer rapido", "dieta"],
        "headlines": [
            "Negativa bariátrica: apoio",
            "Plano negou procedimento",
            "Direito à saúde",
            "Assessoria plano de saúde",
            "Análise de negativa",
            "Consulta jurídica saúde",
        ],
        "descriptions": [
            "Orientação jurídica quando o plano nega cirurgia bariátrica. Análise documental do caso.",
            "Sem promessa de autorização. Resultados dependem do contrato e laudos apresentados.",
        ],
    },
    "cirurgia_reparadora": {
        "risk_level": "Alto",
        "risk_score": 76,
        "risk_summary": (
            "Procedimentos reparadores pós-bariátrica exigem cuidado com políticas de saúde "
            "e proibição de conteúdo estético enganoso."
        ),
        "risk_factors": [
            "Confusão com cirurgia estética pura",
            "Documentação médica complexa para conversão",
            "CPC elevado em termos clínicos",
        ],
        "policy_notes": [
            "Enfatizar caráter reparador e negativa do plano",
            "Evitar promessa de autorização ou resultado estético",
            "Não usar before/after ou linguagem sensacional",
        ],
        "ad_groups": [
            AdGroup(
                "Reparadora pós-bariátrica",
                [
                    "cirurgia reparadora plano saúde",
                    "negativa cirurgia reparadora",
                    "advogado cirurgia reparadora",
                ],
                "Transacional",
            ),
            AdGroup(
                "Continuidade tratamento",
                [
                    "cobertura pós bariátrica plano",
                    "liminar cirurgia reparadora",
                    "direito saúde reparadora",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["estetica", "lipoaspiração preço", "clinica estetica", "botox"],
        "headlines": [
            "Cirurgia reparadora: apoio",
            "Negativa do plano saúde",
            "Direito suplementar saúde",
            "Assessoria jurídica médica",
            "Análise de cobertura",
            "Agende orientação",
        ],
        "descriptions": [
            "Assessoria quando o plano nega cirurgia reparadora pós-bariátrica. Avaliamos o caso.",
            "Sem garantia de cobertura. Decisão depende de laudos e contrato do plano.",
        ],
    },
    "fraudes_financeiras": {
        "risk_level": "Médio",
        "risk_score": 62,
        "risk_summary": (
            "Alta intenção em vítimas de golpe, mas termos amplos atraem "
            "buscas sobre cripto, investimentos e recuperação duvidosa."
        ),
        "risk_factors": [
            "Risco de associar a serviços de recuperação não regulados",
            "Concorrência de empresas não jurídicas",
            "Necessidade de filtro geográfico e de intenção",
        ],
        "policy_notes": [
            "Não prometer recuperação garantida de valores",
            "Evitar 'recuperamos seu dinheiro'",
            "Posicionar como ação judicial ou extrajudicial lícita",
        ],
        "ad_groups": [
            AdGroup(
                "Golpe financeiro",
                [
                    "advogado golpe financeiro",
                    "fraude bancária advogado",
                    "golpe pix advogado",
                ],
                "Transacional",
            ),
            AdGroup(
                "Investimento fraudulento",
                [
                    "fraude investimento advogado",
                    "golpe corretora advogado",
                    "ação fraude financeira",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["recuperar bitcoin", "hack", "trader", "renda extra", "esquema"],
        "headlines": [
            "Vítima de golpe financeiro?",
            "Assessoria em fraudes",
            "Análise jurídica do caso",
            "Ação contra fraudadores",
            "Consulta jurídica",
            "Escritório especializado",
        ],
        "descriptions": [
            "Orientação para vítimas de golpes financeiros. Avaliamos as provas do seu caso.",
            "Recuperação de valores não é garantida. Estratégia definida após análise do caso.",
        ],
    },
    "anulacao_leilao": {
        "risk_level": "Alto",
        "risk_score": 70,
        "risk_summary": (
            "Público em situação de stress patrimonial. CPC alto e risco de copy agressiva "
            "ou promessas de cancelamento de leilão."
        ),
        "risk_factors": [
            "Urgência eleva CPC e cliques curiosos",
            "Termos de leilão atraem investidores, não clientes",
            "Exige prova de atuação em direito imobiliário",
        ],
        "policy_notes": [
            "Não garantir suspensão ou anulação do leilão",
            "Evitar contagem regressiva alarmista",
            "Informar análise processual individual",
        ],
        "ad_groups": [
            AdGroup(
                "Anulação leilão",
                [
                    "anulação leilão advogado",
                    "suspender leilão imóvel advogado",
                    "mandado leilão orientação jurídica",
                ],
                "Urgência",
            ),
            AdGroup(
                "Defesa imóvel",
                [
                    "defesa leilão extrajudicial",
                    "irregularidade leilão advogado",
                    "ação anulatória leilão",
                ],
                "Transacional",
            ),
        ],
        "negative_keywords": ["arrematar imovel", "lance leilão", "leilão barato", "investir imóvel"],
        "headlines": [
            "Leilão de imóvel: apoio",
            "Análise de procedimento",
            "Defesa patrimonial legal",
            "Consultoria em leilões",
            "Agende orientação",
            "Escritório jurídico",
        ],
        "descriptions": [
            "Assessoria em leilões extrajudiciais e judiciais. Verificamos irregularidades do caso.",
            "Anulação não é garantida. Estratégia depende de documentos e fase do procedimento.",
        ],
    },
    "registro_marcas": {
        "risk_level": "Baixo",
        "risk_score": 42,
        "risk_summary": (
            "Nicho mais previsível, com intenção clara e menor sensibilidade regulatória. "
            "Concorrência de marcas e INPI mantém CPC moderado."
        ),
        "risk_factors": [
            "Concorrência de despachantes e softwares",
            "Buscas informacionais sobre INPI",
            "Necessidade de diferenciar serviço jurídico",
        ],
        "policy_notes": [
            "Não garantir deferimento do pedido",
            "Evitar 'registro em 24h'",
            "Informar análise de viabilidade e anterioridade",
        ],
        "ad_groups": [
            AdGroup(
                "Registro INPI",
                [
                    "registro de marca advogado",
                    "depósito marca INPI advogado",
                    "consultoria registro marca",
                ],
                "Transacional",
            ),
            AdGroup(
                "Defesa marca",
                [
                    "oposição registro marca",
                    "infraction marca INPI",
                    "defesa propriedade industrial",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["logotipo grátis", "criar logo", "design", "patente grátis"],
        "headlines": [
            "Registro de marcas INPI",
            "Proteja sua marca",
            "Consultoria marcária",
            "Análise de viabilidade",
            "Escritório especializado",
            "Agende orientação",
        ],
        "descriptions": [
            "Assessoria jurídica para registro e defesa de marcas no INPI. Pesquisa de anterioridade.",
            "Deferimento não garantido. Orientação conforme normas do INPI e do cliente.",
        ],
    },
    "indenizacao_overbooking": {
        "risk_level": "Médio",
        "risk_score": 50,
        "risk_summary": (
            "Demanda sazonal ligada a viagens. CPC moderado; "
            "filtros evitam buscas de passagens e milhas."
        ),
        "risk_factors": [
            "Sazonalidade em feriados e férias",
            "Usuários buscam compensação rápida sem processo",
            "Concorrência de apps de reclamação",
        ],
        "policy_notes": [
            "Não prometer valor fixo de indenização",
            "Evitar 'ganhe até X mil'",
            "Citar base legal de forma informativa, sem garantia",
        ],
        "ad_groups": [
            AdGroup(
                "Overbooking",
                [
                    "overbooking advogado",
                    "indenização overbooking voo",
                    "negado embarque compensação",
                ],
                "Transacional",
            ),
            AdGroup(
                "Atraso e cancelamento",
                [
                    "atraso voo indenização advogado",
                    "cancelamento voo direito passageiro",
                    "ANAC indenização advogado",
                ],
                "Relacionada",
            ),
        ],
        "negative_keywords": ["passagem barata", "milhas", "check in", "bagagem despachar"],
        "headlines": [
            "Overbooking em voo?",
            "Direitos do passageiro",
            "Indenização aérea: análise",
            "Consultoria jurídica",
            "Agende orientação",
            "Escritório de advocacia",
        ],
        "descriptions": [
            "Orientação jurídica em overbooking e problemas com voos. Avaliamos bilhete e ocorrência.",
            "Valores de indenização variam. Sem promessa de quantia ou prazo de pagamento.",
        ],
    },
    "acao_trabalhista": {
        "risk_level": "Médio",
        "risk_score": 57,
        "risk_summary": (
            "Volume consistente o ano todo. CPC estável, mas termos amplos "
            "atraem empregadores e estudantes de direito."
        ),
        "risk_factors": [
            "Alta concorrência local",
            "Palavras genéricas como 'trabalhista' elevam custo",
            "Lead qualificado depende de filtro empregado vs empregador",
        ],
        "policy_notes": [
            "Não prometer valor de condenação ou verbas",
            "Evitar 'ganhe sua rescisão'",
            "Focar consulta trabalhista para empregados",
        ],
        "ad_groups": [
            AdGroup(
                "Rescisão e verbas",
                [
                    "advogado trabalhista rescisão",
                    "verbas rescisórias advogado",
                    "consulta direito trabalho",
                ],
                "Transacional",
            ),
            AdGroup(
                "Assédio e demissão",
                [
                    "assédio moral trabalho advogado",
                    "demissão irregular advogado",
                    "ação trabalhista orientação",
                ],
                "Investigativa",
            ),
        ],
        "negative_keywords": ["vaga emprego", "curriculum", "clt pdf", "empregador", "folha pagamento"],
        "headlines": [
            "Consulta trabalhista",
            "Direitos do trabalhador",
            "Análise de rescisão",
            "Assessoria em verbas",
            "Agende orientação",
            "Escritório trabalhista",
        ],
        "descriptions": [
            "Consultoria jurídica trabalhista para empregados. Avaliamos contrato e situação funcional.",
            "Sem garantia de valores ou êxito. Atendimento ético conforme normas da OAB.",
        ],
    },
}


def _validate_assets(plan: CampaignPlan) -> None:
    for h in plan.headlines:
        if len(h) > 30:
            raise ValueError(f"Título excede 30 caracteres: {h!r} ({len(h)})")
    for d in plan.descriptions:
        if len(d) > 90:
            raise ValueError(f"Descrição excede 90 caracteres: {d!r} ({len(d)})")


def demo_campaign_plan(nicho: str) -> CampaignPlan:
    data = PLANOS.get(nicho)
    if data is None:
        data = PLANOS["desbloqueio_conta_bancaria"]
    plan = _plan(nicho=nicho, **data)
    _validate_assets(plan)
    return plan
