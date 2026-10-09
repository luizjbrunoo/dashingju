"""Smoke staging do índice pgvector. Sem OpenAI, sem PII, reversível."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models.signals import post_save
from django.utils import timezone

from core.worker_smoke import is_production_environment
from ia.models import VectorChunk
from ia.services.document_knowledge import (
    PgVectorTenantStore,
    index_document,
    search_knowledge,
    set_store_factory,
)
from ia.services.embeddings import (
    RAG_EMBEDDING_DIM,
    RAG_EMBEDDING_MODEL,
    deterministic_embedding,
    reset_embedder,
    set_embedder,
)
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.signals import post_save_documentos

SMOKE_PREFIX = "_SMOKE_PGVECTOR_"


class PgVectorSmokeError(Exception):
    """Falha controlada do smoke pgvector."""


@dataclass
class PgVectorSmokeOutcome:
    extension_version: str


def _require_postgres() -> None:
    if connection.vendor != "postgresql":
        raise PgVectorSmokeError("PGVECTOR_SMOKE_BLOCKED: not postgresql")


def _require_backend() -> None:
    from django.conf import settings

    backend = (getattr(settings, "RAG_VECTOR_BACKEND", None) or "").strip().lower()
    if backend != "pgvector":
        raise PgVectorSmokeError("PGVECTOR_SMOKE_BLOCKED: backend")


def _extension_version() -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT extversion FROM pg_extension WHERE extname = %s",
            ["vector"],
        )
        row = cursor.fetchone()
    if not row or not row[0]:
        raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: extension missing")
    return str(row[0])


def _doc(cliente, name, content):
    return Documentos.objects.create(
        cliente=cliente,
        tipo="O",
        arquivo=SimpleUploadedFile(name, content.encode("utf-8")),
        data_upload=timezone.now(),
        content=content,
    )


def run_staging_pgvector_smoke(*, environ: dict | None = None) -> PgVectorSmokeOutcome:
    if is_production_environment(environ):
        raise PgVectorSmokeError("PGVECTOR_SMOKE_BLOCKED: production")
    _require_postgres()
    _require_backend()
    extension_version = _extension_version()

    token = secrets.token_hex(8)
    suffix = f"{SMOKE_PREFIX}{token}"
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
        a1 = _doc(cli_a, "a1.txt", f"{suffix} documento A1 contrato honorarios")
        a2 = _doc(cli_a, "a2.txt", f"{suffix} documento A2 peticao inicial")
        b1 = _doc(cli_b, "b1.txt", f"{suffix} documento B1 segredo org B")

        if index_document(org_a, a1) != "ok":
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: A1_INSERT")
        if index_document(org_a, a2) != "ok":
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: A2_INSERT")
        if index_document(org_b, b1) != "ok":
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: B1_INSERT")

        hits_a = search_knowledge(org_a, "documento A1")
        if not any(h.metadata.get("documento_id") == a1.pk for h in hits_a):
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: A_SEARCH")
        if any(h.metadata.get("documento_id") == b1.pk for h in hits_a):
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: A_SEARCH_NO_B1")

        hits_b = search_knowledge(org_b, "documento B1")
        if not any(h.metadata.get("documento_id") == b1.pk for h in hits_b):
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: B_SEARCH")
        if any(h.metadata.get("documento_id") in {a1.pk, a2.pk} for h in hits_b):
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: B_SEARCH_NO_A")

        store_a = PgVectorTenantStore(org_a)
        store_a.delete_documento(a1.pk)
        after_del_a = search_knowledge(org_a, "documento A1")
        if any(h.metadata.get("documento_id") == a1.pk for h in after_del_a):
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: DELETE_A1_ISOLATED")
        if not VectorChunk.objects.filter(organization=org_a, documento=a2).exists():
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: DELETE_A1_ISOLATED")
        if not VectorChunk.objects.filter(organization=org_b, documento=b1).exists():
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: DELETE_A1_ISOLATED")

        if index_document(org_a, a2) != "ok":
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: REINDEX_A2_IDEMPOTENT")
        n_a2 = VectorChunk.objects.filter(
            organization=org_a,
            documento=a2,
            embedding_model=RAG_EMBEDDING_MODEL,
        ).count()
        if n_a2 != 1:
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: REINDEX_A2_IDEMPOTENT")
        if VectorChunk.objects.filter(organization=org_b, documento=b1).count() != 1:
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: CROSS_TENANT_ISOLATION")

        dim_ok = all(
            chunk.embedding_dim == RAG_EMBEDDING_DIM
            and len(chunk.embedding or []) == RAG_EMBEDDING_DIM
            for chunk in VectorChunk.objects.filter(organization__in=[org_a, org_b])
        )
        if not dim_ok:
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: embedding dim")
    finally:
        reset_embedder()
        set_store_factory(None)
        try:
            VectorChunk.objects.filter(
                organization__name__startswith=f"{SMOKE_PREFIX}{token}"
            ).delete()
            Documentos.objects.filter(
                cliente__nome__startswith=f"{SMOKE_PREFIX}{token}"
            ).delete()
            Cliente.objects.filter(nome__startswith=f"{SMOKE_PREFIX}{token}").delete()
            Membership.objects.filter(
                organization__name__startswith=f"{SMOKE_PREFIX}{token}"
            ).delete()
            User.objects.filter(username__startswith=f"{SMOKE_PREFIX}{token}").delete()
            Organization.objects.filter(
                name__startswith=f"{SMOKE_PREFIX}{token}"
            ).delete()
        except Exception as exc:
            post_save.connect(post_save_documentos, sender=Documentos)
            raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: cleanup") from exc
        post_save.connect(post_save_documentos, sender=Documentos)

    leftover = Organization.objects.filter(name__startswith=f"{SMOKE_PREFIX}{token}")
    if leftover.exists():
        raise PgVectorSmokeError("PGVECTOR_SMOKE_FAILED: cleanup")

    return PgVectorSmokeOutcome(extension_version=extension_version)
