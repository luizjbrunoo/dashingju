"""Smoke de object storage: gates, SHA, Worker via default_storage.open, cleanup."""

import ast
import hashlib
import inspect
from io import BytesIO, StringIO
from unittest.mock import Mock, patch

from django.core.files.base import File
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.object_storage_smoke import (
    PAYLOAD_PREFIX,
    SMOKE_MARKER,
    SYNTHETIC_ORG_ID,
    TASK_PATH,
    ObjectStorageSmokeError,
    ObjectStorageSmokeOutcome,
    assert_tenant_key_shape,
    object_storage_worker_smoke_task,
    run_staging_object_storage_smoke,
    tenant_key_shape_sample,
)


class FakeRemoteStorage:
    def __init__(self):
        self.blobs = {}
        self.deleted = []

    def save(self, name, content):
        self.blobs[name] = content.read()
        return name

    def open(self, name, mode="rb"):
        if name not in self.blobs:
            raise FileNotFoundError(name)
        return File(BytesIO(self.blobs[name]), name)

    def delete(self, name):
        self.deleted.append(name)
        self.blobs.pop(name, None)

    def exists(self, name):
        return name in self.blobs


def _env():
    return {"DJANGO_OBJECT_STORAGE_REQUIRED": "true"}


def _payload(token: str) -> bytes:
    return f"{PAYLOAD_PREFIX}{token}".encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _worker_ok(token: str, sha256: str) -> dict:
    return {"ok": True, "marker": SMOKE_MARKER, "sha256": sha256, "token": token}


class TenantKeyShapeTests(SimpleTestCase):
    def test_documento_upload_to_org_id_sem_pii(self):
        key = tenant_key_shape_sample("contrato.pdf")
        assert_tenant_key_shape(key)
        self.assertIn(f"org_{SYNTHETIC_ORG_ID}", key)
        self.assertTrue(key.startswith(f"documentos/org_{SYNTHETIC_ORG_ID}/"))
        self.assertTrue(key.endswith(".pdf"))
        self.assertNotIn("contrato", key)
        self.assertNotIn("slug", key)
        self.assertNotIn("..", key)
        self.assertNotIn(" ", key)

    def test_traversal_filename_nao_entra_no_key(self):
        key = tenant_key_shape_sample("../../etc/passwd")
        self.assertIn(f"org_{SYNTHETIC_ORG_ID}", key)
        self.assertNotIn("..", key)
        self.assertNotIn("etc", key)
        self.assertNotIn("passwd", key)


class ProductionAndBackendGateTests(SimpleTestCase):
    def test_production_bloqueado(self):
        with self.assertRaises(ObjectStorageSmokeError) as ctx:
            run_staging_object_storage_smoke(
                environ={"APP_ENV": "production", "DJANGO_OBJECT_STORAGE_REQUIRED": "true"},
                storage=FakeRemoteStorage(),
            )
        self.assertIn("production", str(ctx.exception))

    def test_required_ausente_rejeitado(self):
        with self.assertRaises(ObjectStorageSmokeError) as ctx:
            run_staging_object_storage_smoke(
                environ={},
                storage=FakeRemoteStorage(),
            )
        self.assertIn("required", str(ctx.exception))

    def test_required_false_rejeitado(self):
        with self.assertRaises(ObjectStorageSmokeError) as ctx:
            run_staging_object_storage_smoke(
                environ={"DJANGO_OBJECT_STORAGE_REQUIRED": "false"},
                storage=FakeRemoteStorage(),
            )
        self.assertIn("required", str(ctx.exception))

    def test_filesystem_rejeitado(self):
        with self.assertRaises(ObjectStorageSmokeError) as ctx:
            run_staging_object_storage_smoke(
                environ=_env(),
                storage=FileSystemStorage(),
            )
        self.assertIn("FileSystemStorage", str(ctx.exception))


class WriteReadWorkerCleanupTests(SimpleTestCase):
    def test_web_write_read_sha_worker_e_cleanup(self):
        storage = FakeRemoteStorage()
        token = "aa" * 16
        data = _payload(token)
        digest = _sha(data)
        enqueue = Mock(return_value="task-os-1")
        result = Mock(return_value=_worker_ok(token, digest))
        outcome = run_staging_object_storage_smoke(
            environ=_env(),
            storage=storage,
            async_task_func=enqueue,
            result_func=result,
            token=token,
            smoke_id="deadbeef" * 4,
        )
        self.assertIsInstance(outcome, ObjectStorageSmokeOutcome)
        self.assertEqual(outcome.task_id, "task-os-1")
        self.assertEqual(outcome.sha256, digest)
        expected_key = "_smoke/object-storage/" + ("deadbeef" * 4) + ".txt"
        enqueue.assert_called_once_with(TASK_PATH, expected_key, token, digest)
        self.assertIn(expected_key, storage.deleted)
        self.assertFalse(storage.exists(expected_key))
        self.assertEqual(storage.blobs, {})

    def test_timeout_falha_e_tenta_cleanup(self):
        storage = FakeRemoteStorage()
        enqueue = Mock(return_value="pending")
        result = Mock(return_value=None)
        with self.assertRaises(ObjectStorageSmokeError) as ctx:
            run_staging_object_storage_smoke(
                environ=_env(),
                storage=storage,
                async_task_func=enqueue,
                result_func=result,
                token="bb" * 16,
                smoke_id="cafebabecafebabecafebabecafebabe",
            )
        self.assertIn("timeout", str(ctx.exception))
        self.assertTrue(storage.deleted)
        self.assertEqual(storage.blobs, {})

    def test_resultado_incorreto_falha(self):
        storage = FakeRemoteStorage()
        token = "cc" * 16
        enqueue = Mock(return_value="id-bad")
        result = Mock(
            return_value={
                "ok": True,
                "marker": SMOKE_MARKER,
                "sha256": "0" * 64,
                "token": token,
            }
        )
        with self.assertRaises(ObjectStorageSmokeError) as ctx:
            run_staging_object_storage_smoke(
                environ=_env(),
                storage=storage,
                async_task_func=enqueue,
                result_func=result,
                token=token,
            )
        self.assertIn("sha256", str(ctx.exception))
        self.assertEqual(storage.blobs, {})

    def test_worker_task_usa_open_e_valida_sha(self):
        storage = FakeRemoteStorage()
        token = "dd" * 16
        data = _payload(token)
        key = "_smoke/object-storage/unit.txt"
        storage.blobs[key] = data
        with patch("core.object_storage_smoke.default_storage", storage):
            payload = object_storage_worker_smoke_task(key, token, _sha(data))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["marker"], SMOKE_MARKER)
        self.assertEqual(payload["token"], token)
        self.assertEqual(payload["sha256"], _sha(data))

    def test_worker_task_sha_errado_ok_false(self):
        storage = FakeRemoteStorage()
        token = "ee" * 16
        data = _payload(token)
        key = "_smoke/object-storage/bad.txt"
        storage.blobs[key] = data
        with patch("core.object_storage_smoke.default_storage", storage):
            payload = object_storage_worker_smoke_task(key, token, "ff" * 32)
        self.assertFalse(payload["ok"])


class CommandTests(SimpleTestCase):
    def test_comando_sucesso_imprime_status(self):
        outcome = ObjectStorageSmokeOutcome(
            backend="S3Storage",
            tenant_key="documentos/org_424242/ab.pdf",
            smoke_key="_smoke/object-storage/x.txt",
            task_id="qid-1",
            token="tok",
            sha256="ab" * 32,
        )
        out = StringIO()
        with patch(
            "core.management.commands.staging_object_storage_smoke.run_staging_object_storage_smoke",
            return_value=outcome,
        ):
            call_command("staging_object_storage_smoke", stdout=out)
        text = out.getvalue()
        self.assertIn("STORAGE_BACKEND=S3Storage", text)
        self.assertIn("TENANT_KEY_SHAPE=OK", text)
        self.assertIn("WEB_WRITE=OK", text)
        self.assertIn("WEB_READ=OK", text)
        self.assertIn("TASK_ID=qid-1", text)
        self.assertIn("WORKER_READ=OK", text)
        self.assertIn("SHA256_MATCH=OK", text)
        self.assertIn("CLEANUP=OK", text)
        self.assertIn("OBJECT_STORAGE_SMOKE=OK", text)
        self.assertNotIn("AWS_SECRET", text)
        self.assertNotIn("DATABASE_URL", text)

    def test_comando_production_exit_nao_zero(self):
        with patch(
            "core.object_storage_smoke.is_production_environment",
            return_value=True,
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("staging_object_storage_smoke", stdout=StringIO())
        self.assertIn("production", str(ctx.exception))


class IsolationTests(SimpleTestCase):
    def test_modulo_nao_usa_path_lancedb_agno(self):
        import core.object_storage_smoke as mod

        source = inspect.getsource(mod)
        self.assertNotIn(".path", source)
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".")[0])
        self.assertEqual({"lancedb", "agno", "pathlib"} & set(imported), set())
