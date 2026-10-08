"""Smoke de object storage Web → Bucket → Worker. Sem tenant real, LanceDB ou Agno."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage

from core.runtime_env import env_bool
from core.worker_smoke import DEFAULT_TIMEOUT_SECONDS, is_production_environment
from usuarios.services.document_storage import documento_upload_to

SMOKE_MARKER = "object-storage-worker-smoke"
TASK_PATH = "core.object_storage_smoke.object_storage_worker_smoke_task"
SMOKE_KEY_PREFIX = "_smoke/object-storage/"
PAYLOAD_PREFIX = "adv-growth-object-storage-smoke:"
SYNTHETIC_ORG_ID = 424242


class ObjectStorageSmokeError(Exception):
    """Falha controlada do smoke de object storage."""


@dataclass(frozen=True)
class ObjectStorageSmokeOutcome:
    backend: str
    tenant_key: str
    smoke_key: str
    task_id: str
    token: str
    sha256: str


class _SyntheticCliente:
    organization_id = SYNTHETIC_ORG_ID


class _SyntheticDocumento:
    cliente = _SyntheticCliente()


def storage_backend_label(storage=None) -> str:
    backend = storage if storage is not None else default_storage
    cls = type(backend)
    return cls.__name__


def storage_is_filesystem(storage=None) -> bool:
    backend = storage if storage is not None else default_storage
    return isinstance(backend, FileSystemStorage)


def object_storage_required(environ: dict | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return env_bool(
        env.get("DJANGO_OBJECT_STORAGE_REQUIRED"),
        default=False,
        name="DJANGO_OBJECT_STORAGE_REQUIRED",
    )


def tenant_key_shape_sample(filename: str = "contrato.pdf") -> str:
    """Valida documento_upload_to sem gravar no Bucket nem no PostgreSQL."""
    return documento_upload_to(_SyntheticDocumento(), filename)


def assert_tenant_key_shape(key: str, *, org_id: int = SYNTHETIC_ORG_ID) -> None:
    prefix = f"documentos/org_{org_id}/"
    if not key.startswith(prefix):
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: tenant key")
    rest = key[len(prefix) :]
    if ".." in key or "\\" in key or rest.count("/") != 0:
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: tenant key")
    if "contrato" in key.lower() or "slug" in key.lower():
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: tenant key")
    name, ext = rest.rsplit(".", 1) if "." in rest else (rest, "")
    if ext != "pdf" or len(name) != 32 or any(ch not in "0123456789abcdef" for ch in name):
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: tenant key")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_storage_worker_smoke_task(
    storage_key: str, token: str, expected_sha256: str
) -> dict:
    with default_storage.open(storage_key, "rb") as fh:
        data = fh.read()
    digest = _sha256_bytes(data)
    ok = digest == expected_sha256 and PAYLOAD_PREFIX.encode("utf-8") + token.encode(
        "utf-8"
    ) == data
    return {
        "ok": ok,
        "marker": SMOKE_MARKER,
        "sha256": digest,
        "token": token,
    }


def _open_and_hash(storage, name: str) -> bytes:
    with storage.open(name, "rb") as fh:
        return fh.read()


def _validate_worker_payload(payload: Any, *, token: str, sha256: str) -> dict:
    if not isinstance(payload, dict):
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: resultado inválido")
    if payload.get("ok") is not True:
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: resultado inválido")
    if payload.get("marker") != SMOKE_MARKER:
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: marker inválido")
    if payload.get("token") != token:
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: token divergente")
    if payload.get("sha256") != sha256:
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: sha256 divergente")
    return payload


def _cleanup(storage, name: str | None) -> bool:
    if not name:
        return True
    try:
        storage.delete(name)
    except Exception:
        return False
    try:
        return not storage.exists(name)
    except Exception:
        return False


def run_staging_object_storage_smoke(
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    environ: dict | None = None,
    storage=None,
    async_task_func: Callable[..., str] | None = None,
    result_func: Callable[..., Any] | None = None,
    token: str | None = None,
    smoke_id: str | None = None,
) -> ObjectStorageSmokeOutcome:
    if is_production_environment(environ):
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_BLOCKED: production")
    if not object_storage_required(environ):
        raise ObjectStorageSmokeError(
            "OBJECT_STORAGE_SMOKE_BLOCKED: object storage required"
        )

    backend = storage if storage is not None else default_storage
    if storage_is_filesystem(backend):
        raise ObjectStorageSmokeError(
            "OBJECT_STORAGE_SMOKE_BLOCKED: FileSystemStorage"
        )
    if timeout_seconds <= 0:
        raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_BLOCKED: timeout inválido")

    tenant_key = tenant_key_shape_sample("contrato.pdf")
    assert_tenant_key_shape(tenant_key)

    smoke_token = token or secrets.token_hex(16)
    ident = smoke_id or uuid.uuid4().hex
    smoke_key = f"{SMOKE_KEY_PREFIX}{ident}.txt"
    payload = f"{PAYLOAD_PREFIX}{smoke_token}".encode("utf-8")
    digest = _sha256_bytes(payload)

    saved_name = None
    try:
        saved_name = backend.save(smoke_key, ContentFile(payload))
        read_back = _open_and_hash(backend, saved_name)
        if _sha256_bytes(read_back) != digest:
            raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: web read")

        enqueue = async_task_func
        if enqueue is None:
            from django_q.tasks import async_task as enqueue
        task_id = enqueue(TASK_PATH, saved_name, smoke_token, digest)
        if not task_id:
            raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: task id ausente")

        poll = result_func
        if poll is None:
            from django_q.tasks import result as poll
        wait_ms = int(timeout_seconds) * 1000
        worker_payload = poll(task_id, wait=wait_ms)
        if worker_payload is None:
            raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: timeout")
        _validate_worker_payload(
            worker_payload, token=smoke_token, sha256=digest
        )
    except ObjectStorageSmokeError:
        raise
    except Exception as exc:
        raise ObjectStorageSmokeError(
            "OBJECT_STORAGE_SMOKE_FAILED: runtime"
        ) from exc
    finally:
        cleaned = _cleanup(backend, saved_name)
        if saved_name and not cleaned:
            raise ObjectStorageSmokeError("OBJECT_STORAGE_SMOKE_FAILED: cleanup")

    return ObjectStorageSmokeOutcome(
        backend=storage_backend_label(backend),
        tenant_key=tenant_key,
        smoke_key=saved_name,
        task_id=str(task_id),
        token=smoke_token,
        sha256=digest,
    )
