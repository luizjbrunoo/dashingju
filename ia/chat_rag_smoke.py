"""Smoke staging do retrieval real do chat. Sem OpenAI, sem PII, reversível."""

from __future__ import annotations

import inspect
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models.signals import post_save
from django.utils import timezone

from core.worker_smoke import is_production_environment
from ia.models import VectorChunk
from ia.services.document_knowledge import (
    PgVectorTenantStore,
    get_store,
    index_document,
    set_store_factory,
)
from ia.services.embeddings import (
    deterministic_embedding,
    reset_embedder,
    set_embedder,
)
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.signals import post_save_documentos

SMOKE_PREFIX = "_SMOKE_CHAT_RAG_"
MARKER_A = "CHAT-RAG-A"
MARKER_B = "CHAT-RAG-B"
LANCEDB_MODULE_NAMES = frozenset({"lancedb", "agno.vectordb.lancedb"})


class ChatRagSmokeError(Exception):
    """Falha controlada do smoke de chat RAG."""


@dataclass(frozen=True)
class ChatRagSmokeOutcome:
    backend: str


def _require_postgres() -> None:
    if connection.vendor != "postgresql":
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_BLOCKED: not postgresql")


def _require_backend() -> str:
    from django.conf import settings

    backend = (getattr(settings, "RAG_VECTOR_BACKEND", None) or "").strip().lower()
    if backend != "pgvector":
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_BLOCKED: backend")
    return backend


def _require_extension() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT extversion FROM pg_extension WHERE extname = %s",
            ["vector"],
        )
        row = cursor.fetchone()
    if not row or not row[0]:
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: extension missing")


def _doc(cliente, name, content):
    return Documentos.objects.create(
        cliente=cliente,
        tipo="O",
        arquivo=SimpleUploadedFile(name, content.encode("utf-8")),
        data_upload=timezone.now(),
        content=content,
    )


def _lancedb_paths() -> list[Path]:
    return [
        Path("lancedb") / "empresa.lance",
        Path("/app/lancedb/empresa.lance"),
    ]


def _assert_no_lancedb_runtime(*, files_before: set[str]) -> None:
    from ia.agents import SecretariaAI

    loaded = LANCEDB_MODULE_NAMES.intersection(sys.modules)
    if loaded:
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: NO_LANCEDB_RUNTIME")
    knowledge = getattr(SecretariaAI, "knowledge", None)
    if knowledge is not None:
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: NO_LANCEDB_RUNTIME")
    source = inspect.getsource(SecretariaAI)
    if "LanceDb" in source or 'table_name="empresa"' in source:
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: NO_LANCEDB_RUNTIME")
    created = {
        str(path) for path in _lancedb_paths() if path.exists()
    } - files_before
    if created:
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: NO_LANCEDB_RUNTIME")


def _cleanup(token: str) -> None:
    prefix = f"{SMOKE_PREFIX}{token}"
    VectorChunk.objects.filter(organization__name__startswith=prefix).delete()
    Documentos.objects.filter(cliente__nome__startswith=prefix).delete()
    Cliente.objects.filter(nome__startswith=prefix).delete()
    Membership.objects.filter(organization__name__startswith=prefix).delete()
    User.objects.filter(username__startswith=prefix).delete()
    Organization.objects.filter(name__startswith=prefix).delete()
    if Organization.objects.filter(name__startswith=prefix).exists():
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: cleanup")


def assert_chat_rag_tenant_retrieval() -> str:
    """Usa o mesmo helper do chat: ia.views.retrieve_tenant_context."""
    from ia.views import retrieve_tenant_context

    token = secrets.token_hex(8)
    suffix = f"{SMOKE_PREFIX}{token}"
    files_before = {str(path) for path in _lancedb_paths() if path.exists()}
    post_save.disconnect(post_save_documentos, sender=Documentos)
    set_store_factory(None)
    set_embedder(deterministic_embedding)
    try:
        org_a = Organization.objects.create(name=f"{suffix}_A")
        org_b = Organization.objects.create(name=f"{suffix}_B")
        user_a = User.objects.create_user(username=f"{suffix}_ua", password="smoke")
        user_b = User.objects.create_user(username=f"{suffix}_ub", password="smoke")
        Membership.objects.create(
            user=user_a,
            organization=org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=user_b,
            organization=org_b,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        cli_a = Cliente.objects.create(
            user=user_a,
            organization=org_a,
            nome=f"{suffix}_CA",
            email=f"{token}.a@smoke.test",
        )
        cli_b = Cliente.objects.create(
            user=user_b,
            organization=org_b,
            nome=f"{suffix}_CB",
            email=f"{token}.b@smoke.test",
        )
        doc_a = _doc(cli_a, "chat-a.txt", f"contexto interno {MARKER_A}")
        doc_b = _doc(cli_b, "chat-b.txt", f"contexto interno {MARKER_B}")
        if index_document(org_a, doc_a) != "ok":
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: index A")
        if index_document(org_b, doc_b) != "ok":
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: index B")

        store = get_store(org_a)
        if not isinstance(store, PgVectorTenantStore):
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: backend")
        backend = type(store).__name__

        ctx_a = retrieve_tenant_context(org_a, MARKER_A)
        if MARKER_A not in ctx_a:
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: CHAT_ORG_A_CONTEXT")
        if MARKER_B in ctx_a:
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: CHAT_ORG_A_NO_B")

        ctx_b = retrieve_tenant_context(org_b, MARKER_B)
        if MARKER_B not in ctx_b:
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: CHAT_ORG_B_CONTEXT")
        if MARKER_A in ctx_b:
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: CHAT_ORG_B_NO_A")

        ctx_none = retrieve_tenant_context(None, MARKER_A)
        if ctx_none:
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: CHAT_NO_ORG_FAIL_CLOSED")
        ctx_none_b = retrieve_tenant_context(None, MARKER_B)
        if ctx_none_b:
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: CHAT_NO_ORG_FAIL_CLOSED")
        if retrieve_tenant_context(None, f"{MARKER_A} {MARKER_B}"):
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: NO_GLOBAL_RETRIEVAL")

        _assert_no_lancedb_runtime(files_before=files_before)
        return backend
    finally:
        reset_embedder()
        set_store_factory(None)
        try:
            _cleanup(token)
        except ChatRagSmokeError:
            post_save.connect(post_save_documentos, sender=Documentos)
            raise
        except Exception as exc:
            post_save.connect(post_save_documentos, sender=Documentos)
            raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: cleanup") from exc
        post_save.connect(post_save_documentos, sender=Documentos)


def run_staging_chat_rag_smoke(*, environ: dict | None = None) -> ChatRagSmokeOutcome:
    if is_production_environment(environ):
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_BLOCKED: production")
    _require_postgres()
    backend_setting = _require_backend()
    _require_extension()
    runtime_backend = assert_chat_rag_tenant_retrieval()
    if runtime_backend != "PgVectorTenantStore" or backend_setting != "pgvector":
        raise ChatRagSmokeError("CHAT_RAG_SMOKE_FAILED: backend")
    return ChatRagSmokeOutcome(backend=runtime_backend)
