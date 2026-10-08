"""Smoke test mínimo do worker Django-Q (staging). Sem tenant, I/O local ou PII."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from django.conf import settings

SMOKE_MARKER = "worker-smoke"
TASK_PATH = "core.worker_smoke.worker_smoke_task"
DEFAULT_TIMEOUT_SECONDS = 45
PRODUCTION_ENV_NAMES = frozenset({"production", "prod"})
PRODUCTION_ENV_KEYS = ("APP_ENV", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_ENVIRONMENT")


class WorkerSmokeError(Exception):
    """Falha controlada do smoke (production, sync, timeout ou resultado inválido)."""


@dataclass(frozen=True)
class WorkerSmokeOutcome:
    task_id: str
    token: str
    marker: str
    payload: dict


def is_production_environment(environ: dict | None = None) -> bool:
    env = environ if environ is not None else os.environ
    for key in PRODUCTION_ENV_KEYS:
        value = str(env.get(key) or "").strip().lower()
        if value in PRODUCTION_ENV_NAMES:
            return True
    return False


def q_cluster_is_sync(q_cluster: dict | None = None) -> bool:
    conf = q_cluster if q_cluster is not None else getattr(settings, "Q_CLUSTER", {})
    return bool(conf.get("sync", False))


def worker_smoke_task(token: str) -> dict:
    return {
        "ok": True,
        "token": token,
        "marker": SMOKE_MARKER,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def enqueue_worker_smoke(
    token: str,
    *,
    async_task_func: Callable[..., str] | None = None,
) -> str:
    func = async_task_func
    if func is None:
        from django_q.tasks import async_task as func
    return func(TASK_PATH, token)


def poll_worker_smoke_result(
    task_id: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    result_func: Callable[..., Any] | None = None,
):
    func = result_func
    if func is None:
        from django_q.tasks import result as func
    wait_ms = max(0, int(timeout_seconds) * 1000)
    return func(task_id, wait=wait_ms)


def _validate_payload(payload: Any, token: str) -> dict:
    if not isinstance(payload, dict):
        raise WorkerSmokeError("WORKER_SMOKE_FAILED: resultado inválido")
    if payload.get("ok") is not True:
        raise WorkerSmokeError("WORKER_SMOKE_FAILED: resultado inválido")
    if payload.get("marker") != SMOKE_MARKER:
        raise WorkerSmokeError("WORKER_SMOKE_FAILED: marker inválido")
    if payload.get("token") != token:
        raise WorkerSmokeError("WORKER_SMOKE_FAILED: token divergente")
    return payload


def run_staging_worker_smoke(
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    environ: dict | None = None,
    q_cluster: dict | None = None,
    async_task_func: Callable[..., str] | None = None,
    result_func: Callable[..., Any] | None = None,
    token: str | None = None,
) -> WorkerSmokeOutcome:
    if is_production_environment(environ):
        raise WorkerSmokeError("WORKER_SMOKE_BLOCKED: production")
    if q_cluster_is_sync(q_cluster):
        raise WorkerSmokeError("WORKER_SMOKE_BLOCKED: django-q sync mode")
    if timeout_seconds <= 0:
        raise WorkerSmokeError("WORKER_SMOKE_BLOCKED: timeout inválido")

    smoke_token = token or secrets.token_hex(16)
    task_id = enqueue_worker_smoke(smoke_token, async_task_func=async_task_func)
    if not task_id:
        raise WorkerSmokeError("WORKER_SMOKE_FAILED: task id ausente")

    payload = poll_worker_smoke_result(
        task_id,
        timeout_seconds=timeout_seconds,
        result_func=result_func,
    )
    if payload is None:
        raise WorkerSmokeError("WORKER_SMOKE_FAILED: timeout")

    validated = _validate_payload(payload, smoke_token)
    return WorkerSmokeOutcome(
        task_id=str(task_id),
        token=smoke_token,
        marker=SMOKE_MARKER,
        payload=validated,
    )
