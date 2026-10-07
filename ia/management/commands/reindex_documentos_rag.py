"""Auditoria/reindex seguro de Documentos no RAG.

SQL é a fonte. Vectors sem provenance não recebem Organization por inferência.

--dry-run: apenas conta.
--apply: reindexa via store configurado (testes/local).
Embeddings externos: somente com --execute-embeddings (não usar no DEMO).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ia.services.docs_tenancy import documento_tenant_safe, organization_of_documento
from ia.services.document_knowledge import index_document
from usuarios.models import Documentos


class Command(BaseCommand):
    help = "Audita e opcionalmente reindexa Documentos tenant-safe no RAG."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", default=False)
        parser.add_argument("--apply", action="store_true", default=False)
        parser.add_argument(
            "--execute-embeddings",
            action="store_true",
            default=False,
            help="Permite chamar embedder externo. Não usar no DEMO.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"] or not options["apply"]
        execute = bool(options["execute_embeddings"])
        qs = Documentos.objects.select_related("cliente", "cliente__organization")
        total = qs.count()
        tenant_safe = 0
        null_org = 0
        reindexed = 0
        skipped = 0
        failed = 0
        for doc in qs.iterator():
            if not documento_tenant_safe(doc):
                null_org += 1
                skipped += 1
                continue
            tenant_safe += 1
            if dry or not execute:
                continue
            result = index_document(organization_of_documento(doc), doc)
            if result == "ok":
                reindexed += 1
            else:
                failed += 1
        if dry or not execute:
            prepared = tenant_safe
        else:
            prepared = 0
        self.stdout.write(
            "DOCUMENTS_TOTAL={total} TENANT_SAFE={safe} NULL_ORGANIZATION={null} "
            "AMBIGUOUS=0 LEGACY_VECTORS=UNSCOPED_GLOBAL_TABLE "
            "REINDEX_ELIGIBLE={safe} REINDEXED={reindexed} SKIPPED={skipped} "
            "FAILED={failed} PREPARED={prepared}".format(
                total=total,
                safe=tenant_safe,
                null=null_org,
                reindexed=reindexed,
                skipped=skipped,
                failed=failed,
                prepared=prepared,
            )
        )
        if dry:
            self.stdout.write("RESULT=DRY-RUN")
        elif not execute:
            self.stdout.write("RESULT=NOT_EXECUTED_EXTERNAL_COST")
        else:
            self.stdout.write("RESULT=APPLIED")
