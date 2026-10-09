"""Gates do smoke pgvector: production e backend."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ia.pgvector_smoke import PgVectorSmokeError, run_staging_pgvector_smoke


class PgVectorSmokeGateTests(SimpleTestCase):
    def test_production_bloqueia(self):
        with self.assertRaises(PgVectorSmokeError) as ctx:
            run_staging_pgvector_smoke(environ={"APP_ENV": "production"})
        self.assertIn("production", str(ctx.exception))

    def test_comando_production_exit_nao_zero(self):
        with patch(
            "ia.pgvector_smoke.is_production_environment", return_value=True
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("staging_pgvector_smoke", stdout=StringIO())
        self.assertIn("production", str(ctx.exception))

    def test_sqlite_bloqueia_antes_de_escrever(self):
        with patch(
            "ia.pgvector_smoke.is_production_environment", return_value=False
        ):
            with self.assertRaises(PgVectorSmokeError) as ctx:
                run_staging_pgvector_smoke(environ={"APP_ENV": "staging"})
        self.assertIn("not postgresql", str(ctx.exception))
