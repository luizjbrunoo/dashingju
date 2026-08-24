NICHOS_ATUACAO: list[tuple[str, str]] = [
    ("desbloqueio_conta_bancaria", "Desbloqueio de conta bancária"),
    ("busca_apreensao", "Busca e Apreensão"),
    ("indicacao_condutor", "Indicação de condutor"),
    ("revisao_contrato_conta_corrente", "Revisão de contrato conta corrente"),
    ("revisao_contrato_veiculo", "Revisão de contrato veículo"),
    ("tratamento_autismo", "Tratamento de autismo"),
    ("cirurgia_bariatrica", "Cirurgia bariátrica"),
    ("cirurgia_reparadora", "Cirurgia reparadora"),
    ("fraudes_financeiras", "Fraudes Financeiras"),
    ("anulacao_leilao", "Anulação de leilão"),
    ("registro_marcas", "Registro de Marcas"),
    ("indenizacao_overbooking", "Indenização por overbooking"),
    ("acao_trabalhista", "Ação trabalhista"),
]

NICHOS_POR_CHAVE = dict(NICHOS_ATUACAO)


def resolver_nicho(valor: str | None) -> tuple[str, str]:
    chave = (valor or "").strip()
    if chave in NICHOS_POR_CHAVE:
        return chave, NICHOS_POR_CHAVE[chave]
    padrao = NICHOS_ATUACAO[0][0]
    return padrao, NICHOS_POR_CHAVE[padrao]
