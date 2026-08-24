"""Metadados tipados de compromissos (audiência, follow-up comercial, etc.)."""

from __future__ import annotations

MODALIDADE = "modalidade"
LOCAL = "local"
LINK = "link"
VARA = "vara"
TRIBUNAL = "tribunal"
OBSERVACOES = "observacoes"

FOLLOWUP_OBSERVACAO = "followup_observacao"
FOLLOWUP_OPORTUNIDADE = "followup_oportunidade"

CHAVES_AUDIENCIA = (MODALIDADE, LOCAL, LINK, VARA, TRIBUNAL, OBSERVACOES)
CHAVES_FOLLOWUP = (FOLLOWUP_OBSERVACAO, FOLLOWUP_OPORTUNIDADE)


def _limpar(dados: dict) -> dict:
    return {k: v for k, v in dados.items() if v}


def montar_metadados_audiencia(
    *,
    modalidade: str = "",
    local: str = "",
    link: str = "",
    vara: str = "",
    tribunal: str = "",
    observacoes: str = "",
) -> dict:
    return _limpar(
        {
            MODALIDADE: (modalidade or "").strip(),
            LOCAL: (local or "").strip(),
            LINK: (link or "").strip(),
            VARA: (vara or "").strip(),
            TRIBUNAL: (tribunal or "").strip(),
            OBSERVACOES: (observacoes or "").strip(),
        }
    )


def montar_metadados_followup(
    *,
    observacao: str = "",
    oportunidade_referencia: str = "",
) -> dict:
    return _limpar(
        {
            FOLLOWUP_OBSERVACAO: (observacao or "").strip(),
            FOLLOWUP_OPORTUNIDADE: (oportunidade_referencia or "").strip(),
        }
    )


def extrair_audiencia(metadados: dict | None) -> dict[str, str]:
    meta = metadados or {}
    return {
        "modalidade": meta.get(MODALIDADE, ""),
        "local": meta.get(LOCAL, ""),
        "link": meta.get(LINK, ""),
        "vara": meta.get(VARA, ""),
        "tribunal": meta.get(TRIBUNAL, ""),
        "observacoes": meta.get(OBSERVACOES, ""),
    }


def extrair_followup(metadados: dict | None) -> dict[str, str]:
    meta = metadados or {}
    return {
        "observacao": meta.get(FOLLOWUP_OBSERVACAO, ""),
        "oportunidade_referencia": meta.get(FOLLOWUP_OPORTUNIDADE, ""),
    }


def aplicar_metadados_por_tipo(
    metadados_existentes: dict | None,
    *,
    tipo: str,
    audiencia: dict | None = None,
    followup: dict | None = None,
) -> dict:
    """Mescla metadados preservando chaves de integrações (cobrança, série recorrente)."""
    from usuarios.choices import TipoCompromisso

    meta = dict(metadados_existentes or {})

    for chave in CHAVES_AUDIENCIA:
        meta.pop(chave, None)
    for chave in CHAVES_FOLLOWUP:
        meta.pop(chave, None)

    if tipo == TipoCompromisso.AUDIENCIA and audiencia:
        meta.update(montar_metadados_audiencia(**audiencia))
    elif tipo == TipoCompromisso.FOLLOWUP_COMERCIAL and followup:
        meta.update(montar_metadados_followup(**followup))

    return meta
