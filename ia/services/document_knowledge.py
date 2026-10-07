"""Fronteira tenant-aware de indexação e retrieval de Documentos.

Isolamento pré-retrieval: namespace físico LanceDB
`documentos_org_{organization_id}`.

A tabela legado `documentos` NÃO é consultada em retrieval tenant-specific
(vectors sem provenance ficam invisíveis até reindex).

SQL continua a fonte de verdade: metadata do vetor é validada contra
Documento.cliente.organization depois da busca no namespace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

TABLE_PREFIX = "documentos_org_"
LEGACY_GLOBAL_TABLE = "documentos"

_store_factory: Callable[[str], Any] | None = None


@dataclass
class KnowledgeHit:
    text: str
    metadata: dict = field(default_factory=dict)
    table_name: str = ""


def table_name_for_organization(organization) -> str:
    if organization is None or getattr(organization, "pk", None) is None:
        raise ValueError("MISSING_ORGANIZATION")
    return f"{TABLE_PREFIX}{organization.pk}"


def set_store_factory(factory: Callable[[str], Any] | None) -> None:
    global _store_factory
    _store_factory = factory


def get_store(organization):
    name = table_name_for_organization(organization)
    factory = _store_factory or _lance_store
    return factory(name)


def mandatory_tenant_filters(organization, extra: dict | None = None) -> dict:
    """Organization é obrigatória. Caller não pode sobrescrever/remover."""
    filters = dict(extra or {})
    filters.pop("organization_id", None)
    filters["organization_id"] = organization.pk
    return filters


class InMemoryTenantStore:
    """Store de teste: cada table_name é um namespace isolado ANTES do ranking."""

    buckets: dict[str, list] = {}

    def __init__(self, table_name: str):
        self.table_name = table_name
        self.rows = self.buckets.setdefault(table_name, [])

    @classmethod
    def reset(cls) -> None:
        cls.buckets = {}

    def upsert_chunks(self, chunks: list[dict]) -> None:
        seen_docs = {
            (chunk.get("meta") or {}).get("documento_id")
            for chunk in chunks
            if (chunk.get("meta") or {}).get("documento_id") is not None
        }
        if seen_docs:
            self.rows[:] = [
                row
                for row in self.rows
                if row.get("meta", {}).get("documento_id") not in seen_docs
            ]
        self.rows.extend(chunks)

    def search(self, query: str, *, limit: int = 5, filters: dict | None = None) -> list[KnowledgeHit]:
        candidates = list(self.rows)
        for key, value in (filters or {}).items():
            candidates = [
                row
                for row in candidates
                if row.get("meta", {}).get(key) == value
            ]
        q = (query or "").lower()
        ranked = sorted(
            candidates,
            key=lambda row: 1 if q and q in (row.get("text") or "").lower() else 0,
            reverse=True,
        )
        return [
            KnowledgeHit(
                text=row.get("text") or "",
                metadata=dict(row.get("meta") or {}),
                table_name=self.table_name,
            )
            for row in ranked[:limit]
        ]

    def delete_documento(self, documento_id: int) -> int:
        before = len(self.rows)
        self.rows[:] = [
            row
            for row in self.rows
            if row.get("meta", {}).get("documento_id") != documento_id
        ]
        return before - len(self.rows)


def _lance_store(table_name: str):
    return LanceTenantStore(table_name)


class LanceTenantStore:
    """Namespace LanceDB por Organization. Não busca a tabela legado."""

    def __init__(self, table_name: str):
        from agno.knowledge.embedder.openai import OpenAIEmbedder
        from agno.knowledge.knowledge import Knowledge
        from agno.vectordb.lancedb import LanceDb

        self.table_name = table_name
        self.knowledge = Knowledge(
            vector_db=LanceDb(
                table_name=table_name,
                uri="lancedb",
                embedder=OpenAIEmbedder(),
            )
        )

    def upsert_chunks(self, chunks: list[dict]) -> None:
        for chunk in chunks:
            meta = dict(chunk.get("meta") or {})
            self.delete_documento(meta.get("documento_id"))
            self.knowledge.insert(
                name=meta.get("name") or f"doc-{meta.get('documento_id')}",
                text_content=chunk.get("text") or "",
                metadata=meta,
            )

    def search(self, query: str, *, limit: int = 5, filters: dict | None = None) -> list[KnowledgeHit]:
        docs = self.knowledge.search(query=query, max_results=limit, filters=None)
        tenant_filters = dict(filters or {})
        hits = []
        for doc in docs or []:
            meta = dict(getattr(doc, "meta_data", None) or {})
            if tenant_filters and any(meta.get(k) != v for k, v in tenant_filters.items()):
                continue
            hits.append(
                KnowledgeHit(
                    text=getattr(doc, "content", None) or "",
                    metadata=meta,
                    table_name=self.table_name,
                )
            )
        return hits[:limit]

    def delete_documento(self, documento_id) -> int:
        if documento_id is None:
            return 0
        try:
            self.knowledge.remove_vectors_by_metadata({"documento_id": documento_id})
            return 1
        except Exception:
            logger.info(
                "skip model=Documentos pk=%s reason=VECTOR_DELETE_FAILED table=%s",
                documento_id,
                self.table_name,
            )
            return 0


def index_document(organization, documento, *, text: str | None = None) -> str:
    from ia.services.docs_tenancy import (
        REASON_MISSING_ORGANIZATION,
        REASON_ORGANIZATION_CONFLICT,
        organization_of_documento,
    )

    sql_org = organization_of_documento(documento)
    if organization is None or sql_org is None:
        return REASON_MISSING_ORGANIZATION
    if sql_org.pk != organization.pk:
        return REASON_ORGANIZATION_CONFLICT
    body = text if text is not None else (documento.content or "")
    if not str(body).strip():
        return "EMPTY"
    store = get_store(organization)
    filename = ""
    if getattr(documento, "arquivo", None):
        filename = getattr(documento.arquivo, "name", "") or ""
    store.upsert_chunks(
        [
            {
                "id": f"org{organization.pk}-doc{documento.pk}-c0",
                "text": str(body),
                "meta": {
                    "organization_id": organization.pk,
                    "documento_id": documento.pk,
                    "cliente_id": documento.cliente_id,
                    "name": filename,
                },
            }
        ]
    )
    return "ok"


def _sql_hit_belongs(organization, metadata: dict) -> bool:
    from usuarios.models import Documentos

    doc_id = metadata.get("documento_id")
    if not doc_id or organization is None:
        return False
    if metadata.get("organization_id") != organization.pk:
        return False
    doc = (
        Documentos.objects.select_related("cliente", "cliente__organization")
        .filter(pk=doc_id)
        .first()
    )
    if doc is None or doc.cliente_id is None or doc.cliente.organization_id is None:
        return False
    if doc.cliente.organization_id != organization.pk:
        return False
    if metadata.get("cliente_id") not in (None, doc.cliente_id):
        return False
    return True


def search_knowledge(
    organization,
    query: str,
    *,
    cliente_id=None,
    documento_id=None,
    knowledge_filters: dict | None = None,
    limit: int = 5,
) -> list[KnowledgeHit]:
    from usuarios.models import Cliente, Documentos

    if organization is None:
        return []
    extra = dict(knowledge_filters or {})
    extra.pop("organization_id", None)
    if cliente_id is not None:
        ok = Cliente.objects.filter(
            pk=cliente_id, organization=organization
        ).exists()
        if not ok:
            return []
        extra["cliente_id"] = cliente_id
    if documento_id is not None:
        ok = Documentos.objects.filter(
            pk=documento_id, cliente__organization=organization
        ).exists()
        if not ok:
            return []
        extra["documento_id"] = documento_id
    filters = mandatory_tenant_filters(organization, extra)
    store = get_store(organization)
    hits = store.search(query, limit=limit, filters=filters)
    return [hit for hit in hits if _sql_hit_belongs(organization, hit.metadata)]


def purge_documento_vectors(organization, documento_id: int) -> int:
    if organization is None:
        return 0
    return get_store(organization).delete_documento(documento_id)


def knowledge_for_organization(organization):
    """Knowledge Agno no namespace da Organization (não usa tabela legado)."""
    store = get_store(organization)
    return getattr(store, "knowledge", None)
