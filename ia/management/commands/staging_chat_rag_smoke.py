"""Smoke do retrieval tenant-aware do chat no staging. Bloqueado em production."""

from django.core.management.base import BaseCommand, CommandError

from ia.chat_rag_smoke import ChatRagSmokeError, run_staging_chat_rag_smoke


class Command(BaseCommand):
    help = (
        "Smoke do caminho real de retrieval do chat (retrieve_tenant_context) "
        "no staging. Bloqueado em production. Não chama OpenAI."
    )

    def handle(self, *args, **options):
        try:
            run_staging_chat_rag_smoke()
        except ChatRagSmokeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write("PGVECTOR_BACKEND=OK")
        self.stdout.write("CHAT_ORG_A_CONTEXT=OK")
        self.stdout.write("CHAT_ORG_A_NO_B=OK")
        self.stdout.write("CHAT_ORG_B_CONTEXT=OK")
        self.stdout.write("CHAT_ORG_B_NO_A=OK")
        self.stdout.write("CHAT_NO_ORG_FAIL_CLOSED=OK")
        self.stdout.write("NO_GLOBAL_RETRIEVAL=OK")
        self.stdout.write("NO_LANCEDB_RUNTIME=OK")
        self.stdout.write("CLEANUP=OK")
        self.stdout.write("CHAT_RAG_SMOKE=OK")
