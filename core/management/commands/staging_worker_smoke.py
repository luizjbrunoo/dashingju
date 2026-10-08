"""Enfileira um diagnóstico Django-Q e espera o worker separado responder."""

from django.core.management.base import BaseCommand, CommandError

from core.worker_smoke import (
    DEFAULT_TIMEOUT_SECONDS,
    WorkerSmokeError,
    run_staging_worker_smoke,
)


class Command(BaseCommand):
    help = (
        "Smoke assíncrono do worker Django-Q no staging. "
        "Bloqueado em production e com Q_CLUSTER sync=True."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_TIMEOUT_SECONDS,
            help="Segundos máximos à espera do resultado (default 45).",
        )

    def handle(self, *args, **options):
        try:
            outcome = run_staging_worker_smoke(timeout_seconds=options["timeout"])
        except WorkerSmokeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"TASK_ID={outcome.task_id}")
        self.stdout.write(f"TOKEN={outcome.token}")
        self.stdout.write(f"MARKER={outcome.marker}")
        self.stdout.write("WORKER_SMOKE=OK")
