"""Fronteira tenant-aware de indexação e retrieval de Documentos.

Índice operacional: PostgreSQL/pgvector (VectorChunk).
SQL (Documentos.content) continua a fonte de verdade.

Isolamento: WHERE organization_id = organization.pk na query.
Pós-filtro SQL (_sql_hit_belongs) é defesa adicional, não a barreira primária.

P2C-LEGACY: LanceTenantStore não é o default e não deve ser instanciado
em staging/production.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction

from ia.services.embeddings import (
    EmbeddingError,
    cosine_distance,
    embed_text,
    rag_embedding_dim,
    rag_embedding_model,
    require_pgvector_backend,
)

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
    if _store_factory is not None:
        return _store_factory(name)
    backend = require_pgvector_backend()
    if backend == "memory":
        return InMemoryTenantStore(name)
    if backend == "pgvector":
        return PgVectorTenantStore(organization, table_name=name)
    raise ImproperlyConfigured(f"RAG_VECTOR_BACKEND desconhecido: {backend}")


def mandatory_tenant_filters(organization, extra: dict | None = None) -> dict:
    """Organization é obrigatória. Caller não pode sobrescrever/remover."""
    filters = dict(extra or {})
    filters.pop("organization_id", None)
    filters["organization_id"] = organization.pk
    return filters


def format_rag_context(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return ""
    return "\n\n".join(f"[{i}] {hit.text}" for i, hit in enumerate(hits, 1))


def retrieve_tenant_context(organization, query: str, **kwargs) -> str:
    if organization is None or not str(query or "").strip():
        return ""
    return format_rag_context(search_knowledge(organization, query, **kwargs))


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


class PgVectorTenantStore:
    """Índice PostgreSQL/pgvector isolado por Organization."""

    def __init__(self, organization, table_name: str | None = None):
        if organization is None or getattr(organization, "pk", None) is None:
            raise ValueError("MISSING_ORGANIZATION")
        self.organization = organization
        self.table_name = table_name or table_name_for_organization(organization)

    def _documento_belongs(self, documento_id) -> bool:
        from ia.services.docs_tenancy import organization_of_documento
        from usuarios.models import Documentos

        if documento_id is None:
            return False
        doc = (
            Documentos.objects.select_related("cliente", "cliente__organization")
            .filter(pk=documento_id)
            .first()
        )
        if doc is None:
            return False
        sql_org = organization_of_documento(doc)
        return sql_org is not None and sql_org.pk == self.organization.pk

    def upsert_chunks(self, chunks: list[dict]) -> None:
        from ia.models import VectorChunk
        from usuarios.models import Documentos

        model = rag_embedding_model()
        dim = rag_embedding_dim()
        org_id = self.organization.pk
        with transaction.atomic():
            for chunk in chunks:
                meta = dict(chunk.get("meta") or {})
                documento_id = meta.get("documento_id")
                if not self._documento_belongs(documento_id):
                    logger.info(
                        "skip model=VectorChunk doc=%s reason=ORGANIZATION_CONFLICT org=%s",
                        documento_id,
                        org_id,
                    )
                    continue
                doc = Documentos.objects.select_related("cliente").get(pk=documento_id)
                cliente_id = doc.cliente_id
                if cliente_id is None:
                    continue
                chunk_id = chunk.get("id") or f"org{org_id}-doc{documento_id}-c0"
                text = str(chunk.get("text") or "")
                vector = chunk.get("embedding")
                if vector is None:
                    vector = embed_text(text)
                if not vector or len(vector) != dim:
                    raise EmbeddingError("EMBEDDING_FAILED")
                VectorChunk.objects.update_or_create(
                    organization_id=org_id,
                    documento_id=documento_id,
                    chunk_id=chunk_id,
                    embedding_model=model,
                    defaults={
                        "cliente_id": cliente_id,
                        "text": text,
                        "embedding": [float(v) for v in vector],
                        "embedding_dim": dim,
                        "metadata": {"name": meta.get("name") or ""},
                    },
                )

    def search(self, query: str, *, limit: int = 5, filters: dict | None = None) -> list[KnowledgeHit]:
        from ia.models import VectorChunk
        from pgvector.django import CosineDistance

        org_id = self.organization.pk
        tenant_filters = dict(filters or {})
        if tenant_filters.get("organization_id") not in (None, org_id):
            return []
        qs = VectorChunk.objects.filter(
            organization_id=org_id,
            embedding_model=rag_embedding_model(),
            embedding_dim=rag_embedding_dim(),
        )
        cliente_id = tenant_filters.get("cliente_id")
        if cliente_id is not None:
            qs = qs.filter(cliente_id=cliente_id)
        documento_id = tenant_filters.get("documento_id")
        if documento_id is not None:
            qs = qs.filter(documento_id=documento_id)
        if not qs.exists():
            return []
        query_vec = embed_text(query)
        if connection.vendor == "postgresql":
            rows = list(
                qs.annotate(distance=CosineDistance("embedding", query_vec)).order_by(
                    "distance"
                )[:limit]
            )
        else:
            rows = sorted(
                qs,
                key=lambda row: cosine_distance(list(row.embedding or []), query_vec),
            )[:limit]
        hits = []
        for row in rows:
            hits.append(
                KnowledgeHit(
                    text=row.text or "",
                    metadata={
                        "organization_id": row.organization_id,
                        "documento_id": row.documento_id,
                        "cliente_id": row.cliente_id,
                        "name": (row.metadata or {}).get("name") or "",
                        "chunk_id": row.chunk_id,
                        "embedding_model": row.embedding_model,
                    },
                    table_name=self.table_name,
                )
            )
        return hits

    def delete_documento(self, documento_id) -> int:
        from ia.models import VectorChunk

        if documento_id is None:
            return 0
        deleted, _ = VectorChunk.objects.filter(
            organization_id=self.organization.pk,
            documento_id=documento_id,
        ).delete()
        return deleted


class LanceTenantStore:
    """P2C-LEGACY: não usar em runtime. Não é o store default."""

    def __init__(self, table_name: str):
        raise ImproperlyConfigured(
            "LanceTenantStore foi removido do runtime. Use PgVectorTenantStore."
        )


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
    try:
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
    except EmbeddingError:
        logger.info(
            "skip model=Documentos pk=%s reason=EMBEDDING_FAILED",
            documento.pk,
        )
        return "EMBEDDING_FAILED"
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
    """P2C-LEGACY: Agno Knowledge. PgVectorTenantStore não expõe knowledge."""
    store = get_store(organization)
    return getattr(store, "knowledge", None)
