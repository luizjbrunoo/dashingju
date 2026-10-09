"""Identidade e geração de embeddings do RAG.

Modelo canónico: text-embedding-3-small, 1536 dimensões.
Testes/smoke injectam embedder determinístico — nunca OpenAI.
"""

from __future__ import annotations

import hashlib
import math
from typing import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

RAG_EMBEDDING_MODEL = "text-embedding-3-small"
RAG_EMBEDDING_DIM = 1536

_embedder: Callable[[str], list[float]] | None = None


class EmbeddingError(Exception):
    """Falha ao gerar embedding com dimensão/modelo esperados."""


def rag_embedding_model() -> str:
    return getattr(settings, "RAG_EMBEDDING_MODEL", None) or RAG_EMBEDDING_MODEL


def rag_embedding_dim() -> int:
    return int(getattr(settings, "RAG_EMBEDDING_DIM", None) or RAG_EMBEDDING_DIM)


def set_embedder(func: Callable[[str], list[float]] | None) -> None:
    global _embedder
    _embedder = func


def reset_embedder() -> None:
    set_embedder(None)


def deterministic_embedding(text: str, dim: int | None = None) -> list[float]:
    """Vetor 1536-d estável a partir de tokens. Sem rede."""
    size = dim or rag_embedding_dim()
    vec = [0.0] * size
    tokens = (text or "").lower().split() or [""]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % size
        vec[idx] += 1.0
        idx2 = int.from_bytes(digest[4:8], "big") % size
        vec[idx2] += 0.5
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine_distance(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 1.0
    dot = sum(a * b for a, b in zip(left, right))
    na = math.sqrt(sum(a * a for a in left)) or 1.0
    nb = math.sqrt(sum(b * b for b in right)) or 1.0
    similarity = max(-1.0, min(1.0, dot / (na * nb)))
    return 1.0 - similarity


def embed_text(text: str) -> list[float]:
    dim = rag_embedding_dim()
    if _embedder is not None:
        vector = _embedder(text)
    else:
        vector = _openai_embed(text)
    if not vector or len(vector) != dim:
        raise EmbeddingError("EMBEDDING_FAILED")
    return [float(v) for v in vector]


def _openai_embed(text: str) -> list[float]:
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    model = rag_embedding_model()
    dim = rag_embedding_dim()
    embedder = OpenAIEmbedder(id=model, dimensions=dim)
    return embedder.get_embedding(text or "")


def require_pgvector_backend() -> str:
    backend = (getattr(settings, "RAG_VECTOR_BACKEND", None) or "pgvector").strip().lower()
    if backend in {"lancedb", "lance"}:
        raise ImproperlyConfigured(
            "RAG_VECTOR_BACKEND lancedb não é permitido. Use pgvector."
        )
    if backend not in {"pgvector", "memory"}:
        raise ImproperlyConfigured(
            f"RAG_VECTOR_BACKEND desconhecido: {backend}. Use pgvector."
        )
    return backend
