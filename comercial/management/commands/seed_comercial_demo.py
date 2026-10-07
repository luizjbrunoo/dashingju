"""Popula o tenant DEMO com pipeline comercial fictício (Comercial PRO)."""

from django.core.management.base import BaseCommand, CommandError

from comercial.services.demo_seed import (
    ERR_NO_MEMBER,
    ERR_ORG_NOT_FOUND,
    popular_comercial_demo,
    resolver_organization_demo,
)


class Command(BaseCommand):
    help = (
        "Seed idempotente do Comercial PRO no tenant Almeida & Torres — DEMO. "
        "Não dispara e-mail, WhatsApp, Asaas, webhook ou LLM."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Valida o tenant DEMO sem gravar dados.",
        )

    def handle(self, *args, **options):
        org = resolver_organization_demo()
        if org is None:
            raise CommandError(
                f"{ERR_ORG_NOT_FOUND}: Organization DEMO não encontrada "
                "(slug almeida-torres-advocacia-demo)."
            )
        try:
            resumo = popular_comercial_demo(executar=not options["dry_run"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"ORG={resumo.organization_id} slug={org.slug}")
        self.stdout.write(f"CLIENTES={resumo.clientes}")
        self.stdout.write(f"COMPROMISSOS={resumo.compromissos}")
        self.stdout.write(f"CONTRATOS={resumo.ganhos}")
        self.stdout.write(f"PERDAS={resumo.perdas}")
        self.stdout.write(f"META_PRESERVADA={resumo.meta_preservada}")
        if options["dry_run"]:
            self.stdout.write("DRY_RUN=1")
        if ERR_NO_MEMBER in str(resumo):
            raise CommandError(ERR_NO_MEMBER)
