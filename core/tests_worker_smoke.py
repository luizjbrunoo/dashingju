"""Smoke do worker Django-Q: gates de production/sync e enqueue assíncrono."""

from io import StringIO
from unittest.mock import Mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.worker_smoke import (
    SMOKE_MARKER,
    TASK_PATH,
    WorkerSmokeError,
    is_production_environment,
    q_cluster_is_sync,
    run_staging_worker_smoke,
    worker_smoke_task,
)


def _payload(token: str) -> dict:
    return {"ok": True, "token": token, "marker": SMOKE_MARKER, "ts": "2026-01-01T00:00:00+00:00"}


class WorkerSmokeTaskTests(SimpleTestCase):
    def test_task_retorna_ok_token_e_marker_sem_tenant(self):
        payload = worker_smoke_task("abc123")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["token"], "abc123")
        self.assertEqual(payload["marker"], SMOKE_MARKER)
        self.assertIn("ts", payload)
        self.assertNotIn("organization", payload)
        self.assertNotIn("membership", payload)


class ProductionGateTests(SimpleTestCase):
    def test_app_env_production_bloqueia(self):
        self.assertTrue(is_production_environment({"APP_ENV": "production"}))
        self.assertTrue(is_production_environment({"APP_ENV": "PROD"}))

    def test_railway_environment_name_production_bloqueia(self):
        self.assertTrue(
            is_production_environment({"RAILWAY_ENVIRONMENT_NAME": "production"})
        )
        self.assertTrue(is_production_environment({"RAILWAY_ENVIRONMENT": "production"}))

    def test_staging_e_ausente_nao_bloqueiam(self):
        self.assertFalse(is_production_environment({}))
        self.assertFalse(
            is_production_environment({"RAILWAY_ENVIRONMENT_NAME": "staging"})
        )
        self.assertFalse(is_production_environment({"APP_ENV": "staging"}))

    def test_comando_production_exit_nao_zero(self):
        with self.assertRaises(WorkerSmokeError) as ctx:
            run_staging_worker_smoke(environ={"APP_ENV": "production"})
        self.assertIn("production", str(ctx.exception))
        self.assertNotIn("enqueue", str(ctx.exception).lower())


class SyncModeGateTests(SimpleTestCase):
    def test_sync_true_e_falso_positivo(self):
        self.assertTrue(q_cluster_is_sync({"orm": "default", "sync": True}))
        self.assertFalse(q_cluster_is_sync({"orm": "default"}))
        self.assertFalse(q_cluster_is_sync({"orm": "default", "sync": False}))

    def test_run_rejeita_sync_sem_enfileirar(self):
        enqueue = Mock(name="async_task")
        with self.assertRaises(WorkerSmokeError) as ctx:
            run_staging_worker_smoke(
                q_cluster={**settings.Q_CLUSTER, "sync": True},
                async_task_func=enqueue,
            )
        self.assertIn("sync", str(ctx.exception))
        enqueue.assert_not_called()

    def test_comando_sync_true_exit_nao_zero(self):
        cluster = {**settings.Q_CLUSTER, "sync": True}
        with override_settings(Q_CLUSTER=cluster):
            with self.assertRaises(CommandError) as ctx:
                call_command("staging_worker_smoke", stdout=StringIO())
        self.assertIn("sync", str(ctx.exception))


class EnqueueAndResultTests(SimpleTestCase):
    def test_enqueue_usa_async_task_real_sem_sync(self):
        enqueue = Mock(return_value="taskid0123456789abcdef012345678")
        result = Mock(return_value=_payload("tok-1"))
        run_staging_worker_smoke(
            async_task_func=enqueue,
            result_func=result,
            token="tok-1",
        )
        enqueue.assert_called_once_with(TASK_PATH, "tok-1")
        kwargs = enqueue.call_args.kwargs
        self.assertNotIn("sync", kwargs)
        q_options = kwargs.get("q_options") or {}
        self.assertNotEqual(q_options.get("sync"), True)

    def test_sucesso_valida_task_id_resultado_e_token(self):
        enqueue = Mock(return_value="abc-task-id")
        result = Mock(return_value=_payload("match-me"))
        outcome = run_staging_worker_smoke(
            async_task_func=enqueue,
            result_func=result,
            token="match-me",
            timeout_seconds=12,
        )
        self.assertEqual(outcome.task_id, "abc-task-id")
        self.assertEqual(outcome.token, "match-me")
        self.assertEqual(outcome.marker, SMOKE_MARKER)
        result.assert_called_once_with("abc-task-id", wait=12000)

    def test_timeout_falha(self):
        enqueue = Mock(return_value="pending-id")
        result = Mock(return_value=None)
        with self.assertRaises(WorkerSmokeError) as ctx:
            run_staging_worker_smoke(
                async_task_func=enqueue,
                result_func=result,
                token="tok",
            )
        self.assertIn("timeout", str(ctx.exception))

    def test_token_divergente_falha(self):
        enqueue = Mock(return_value="id-1")
        result = Mock(return_value=_payload("outro"))
        with self.assertRaises(WorkerSmokeError):
            run_staging_worker_smoke(
                async_task_func=enqueue,
                result_func=result,
                token="esperado",
            )

    def test_task_id_ausente_falha(self):
        enqueue = Mock(return_value="")
        result = Mock()
        with self.assertRaises(WorkerSmokeError) as ctx:
            run_staging_worker_smoke(
                async_task_func=enqueue,
                result_func=result,
                token="tok",
            )
        self.assertIn("task id", str(ctx.exception))
        result.assert_not_called()

    def test_comando_sucesso_imprime_ok(self):
        enqueue = Mock(return_value="cmd-task")
        result = Mock(side_effect=lambda task_id, wait: _payload("cmd-token"))
        from unittest.mock import patch

        out = StringIO()
        with (
            patch("core.worker_smoke.enqueue_worker_smoke", side_effect=lambda token, async_task_func=None: enqueue(TASK_PATH, token)),
            patch("core.worker_smoke.poll_worker_smoke_result", side_effect=lambda task_id, timeout_seconds=45, result_func=None: result(task_id, wait=timeout_seconds * 1000)),
            patch("core.worker_smoke.secrets.token_hex", return_value="cmd-token"),
        ):
            call_command("staging_worker_smoke", stdout=out)
        text = out.getvalue()
        self.assertIn("TASK_ID=cmd-task", text)
        self.assertIn("TOKEN=cmd-token", text)
        self.assertIn("WORKER_SMOKE=OK", text)

    def test_comando_timeout_exit_nao_zero(self):
        from unittest.mock import patch

        with (
            patch("core.worker_smoke.enqueue_worker_smoke", return_value="pending"),
            patch("core.worker_smoke.poll_worker_smoke_result", return_value=None),
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("staging_worker_smoke", stdout=StringIO())
        self.assertIn("timeout", str(ctx.exception))


class IsolationTests(SimpleTestCase):
    def test_modulo_nao_depende_de_tenant_nem_filesystem(self):
        import ast
        import inspect

        import core.worker_smoke as mod

        tree = ast.parse(inspect.getsource(mod))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".")[0])
        banned = {
            "organizacoes",
            "usuarios",
            "financeiro",
            "marketing",
            "comercial",
            "ia",
            "lancedb",
            "pathlib",
        }
        self.assertEqual(banned.intersection(imported), set())
