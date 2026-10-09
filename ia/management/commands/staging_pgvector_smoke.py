"""Smoke pgvector no staging. Bloqueado em production."""

from django.core.management.base import BaseCommand, CommandError

from ia.pgvector_smoke import PgVectorSmokeError, run_staging_pgvector_smoke


class Command(BaseCommand):
    help = (
        "Smoke tenant A1/A2/B1 do índice pgvector no staging. "
        "Bloqueado em production. Não chama OpenAI."
    )

    def handle(self, *args, **options):
        try:
            outcome = run_staging_pgvector_smoke()
        except PgVectorSmokeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write("PGVECTOR_EXTENSION=OK")
        self.stdout.write(f"PGVECTOR_EXTENSION_VERSION={outcome.extension_version}")
        self.stdout.write("PGVECTOR_BACKEND=OK")
        self.stdout.write("A1_INSERT=OK")
        self.stdout.write("A2_INSERT=OK")
        self.stdout.write("B1_INSERT=OK")
        self.stdout.write("A_SEARCH_NO_B1=OK")
        self.stdout.write("B_SEARCH_NO_A=OK")
        self.stdout.write("DELETE_A1_ISOLATED=OK")
        self.stdout.write("REINDEX_A2_IDEMPOTENT=OK")
        self.stdout.write("CROSS_TENANT_ISOLATION=OK")
        self.stdout.write("CLEANUP=OK")
        self.stdout.write("PGVECTOR_SMOKE=OK")
