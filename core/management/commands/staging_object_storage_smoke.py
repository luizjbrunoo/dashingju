"""Smoke Web → Bucket → Worker Django-Q. Bloqueado em production e filesystem local."""

from django.core.management.base import BaseCommand, CommandError

from core.object_storage_smoke import (
    ObjectStorageSmokeError,
    run_staging_object_storage_smoke,
)
from core.worker_smoke import DEFAULT_TIMEOUT_SECONDS


class Command(BaseCommand):
    help = (
        "Smoke de object storage no staging: Web grava, Worker lê o mesmo objeto. "
        "Bloqueado em production e FileSystemStorage."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_TIMEOUT_SECONDS,
            help="Segundos máximos à espera do Worker (default 45).",
        )

    def handle(self, *args, **options):
        try:
            outcome = run_staging_object_storage_smoke(
                timeout_seconds=options["timeout"]
            )
        except ObjectStorageSmokeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"STORAGE_BACKEND={outcome.backend}")
        self.stdout.write("TENANT_KEY_SHAPE=OK")
        self.stdout.write("WEB_WRITE=OK")
        self.stdout.write("WEB_READ=OK")
        self.stdout.write(f"SMOKE_KEY={outcome.smoke_key}")
        self.stdout.write(f"TASK_ID={outcome.task_id}")
        self.stdout.write("WORKER_READ=OK")
        self.stdout.write(f"SHA256={outcome.sha256}")
        self.stdout.write("SHA256_MATCH=OK")
        self.stdout.write("CLEANUP=OK")
        self.stdout.write("OBJECT_STORAGE_SMOKE=OK")
