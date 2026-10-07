"""Backfill controlado de Cliente.organization a partir do resolver canônico.

Não altera leituras, criações, Membership ou Organization.
Não usa user_id/organization_id hardcoded como regra de negócio.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from organizacoes.models import Membership, Organization
from organizacoes.services import (
    CONTEXT_AMBIGUOUS,
    CONTEXT_NONE,
    CONTEXT_RESOLVED,
    resolver_organization,
)
from usuarios.models import Cliente

BACKFILL_ELIGIBLE = "BACKFILL_ELIGIBLE"
ALREADY_ASSIGNED = "ALREADY_ASSIGNED"
NO_ORGANIZATION = "NO_ORGANIZATION"
AMBIGUOUS_ORGANIZATION = "AMBIGUOUS_ORGANIZATION"
INACTIVE_ORGANIZATION = "INACTIVE_ORGANIZATION"
INACTIVE_MEMBERSHIP = "INACTIVE_MEMBERSHIP"
INVALID_USER = "INVALID_USER"
CONFLICT_EXISTING_ORGANIZATION = "CONFLICT_EXISTING_ORGANIZATION"
RESOLUTION_ERROR = "RESOLUTION_ERROR"

CLASSIFICACOES = (
    BACKFILL_ELIGIBLE,
    ALREADY_ASSIGNED,
    NO_ORGANIZATION,
    AMBIGUOUS_ORGANIZATION,
    INACTIVE_ORGANIZATION,
    INACTIVE_MEMBERSHIP,
    INVALID_USER,
    CONFLICT_EXISTING_ORGANIZATION,
    RESOLUTION_ERROR,
)

RESULT_READY = "READY_TO_APPLY"
RESULT_NOTHING = "NOTHING_TO_APPLY"
RESULT_APPLIED = "APPLIED"
RESULT_ROLLBACK_NOT_SAFE = "ROLLBACK_NOT_SAFE"
ERR_USAGE = "USAGE_ERROR"


@dataclass
class ClassificacaoCliente:
    cliente_id: int
    user_id: int | None
    organization_id: int | None
    classification: str
    resolved_organization_id: int | None = None
    resolver_context: str | None = None


@dataclass
class RelatorioBackfill:
    rows: list[ClassificacaoCliente] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    null_organization: int = 0
    already_assigned: int = 0
    rows_to_update: int = 0

    def eligible(self) -> list[ClassificacaoCliente]:
        return [r for r in self.rows if r.classification == BACKFILL_ELIGIBLE]


class ResolverCache:
    """Reutiliza o resolver canônico por user_id. Não reimplementa a regra."""

    def __init__(self):
        self._resolved: dict[int, tuple] = {}
        self._none_detail: dict[int, str] = {}

    def resolve(self, user):
        key = user.pk
        if key not in self._resolved:
            self._resolved[key] = resolver_organization(user)
        return self._resolved[key]

    def detalhe_none(self, user) -> str:
        key = user.pk
        if key not in self._none_detail:
            self._none_detail[key] = _classificar_none(user)
        return self._none_detail[key]


def _classificar_none(user) -> str:
    """Subdivide CONTEXT_NONE. Não autoriza escrita; só reporta.

    Prioridade alinhada ao diagnóstico de provisionamento:
    organization inativa > membership inativa > ausência de vínculo.
    """
    memberships = list(
        Membership.objects.filter(user=user).select_related("organization")
    )
    if not memberships:
        return NO_ORGANIZATION
    if any(m.organization.status == Organization.Status.INACTIVE for m in memberships):
        return INACTIVE_ORGANIZATION
    if any(m.status == Membership.Status.INACTIVE for m in memberships):
        return INACTIVE_MEMBERSHIP
    return NO_ORGANIZATION


def classificar_cliente(cliente: Cliente, cache: ResolverCache) -> ClassificacaoCliente:
    user = getattr(cliente, "user", None)
    if cliente.user_id is None or user is None:
        return ClassificacaoCliente(
            cliente_id=cliente.pk,
            user_id=cliente.user_id,
            organization_id=cliente.organization_id,
            classification=INVALID_USER,
        )

    try:
        org, ctx = cache.resolve(user)
    except Exception:
        return ClassificacaoCliente(
            cliente_id=cliente.pk,
            user_id=cliente.user_id,
            organization_id=cliente.organization_id,
            classification=RESOLUTION_ERROR,
        )

    resolved_id = org.pk if org is not None else None

    if cliente.organization_id is not None:
        if (
            ctx == CONTEXT_RESOLVED
            and resolved_id is not None
            and resolved_id == cliente.organization_id
        ):
            classification = ALREADY_ASSIGNED
        else:
            classification = CONFLICT_EXISTING_ORGANIZATION
        return ClassificacaoCliente(
            cliente_id=cliente.pk,
            user_id=cliente.user_id,
            organization_id=cliente.organization_id,
            classification=classification,
            resolved_organization_id=resolved_id,
            resolver_context=ctx,
        )

    if ctx == CONTEXT_RESOLVED and resolved_id is not None:
        return ClassificacaoCliente(
            cliente_id=cliente.pk,
            user_id=cliente.user_id,
            organization_id=None,
            classification=BACKFILL_ELIGIBLE,
            resolved_organization_id=resolved_id,
            resolver_context=ctx,
        )
    if ctx == CONTEXT_AMBIGUOUS:
        return ClassificacaoCliente(
            cliente_id=cliente.pk,
            user_id=cliente.user_id,
            organization_id=None,
            classification=AMBIGUOUS_ORGANIZATION,
            resolver_context=ctx,
        )
    if ctx == CONTEXT_NONE:
        return ClassificacaoCliente(
            cliente_id=cliente.pk,
            user_id=cliente.user_id,
            organization_id=None,
            classification=cache.detalhe_none(user),
            resolver_context=ctx,
        )
    return ClassificacaoCliente(
        cliente_id=cliente.pk,
        user_id=cliente.user_id,
        organization_id=cliente.organization_id,
        classification=RESOLUTION_ERROR,
        resolver_context=ctx,
    )


def carregar_clientes(*, for_update: bool = False):
    qs = Cliente.objects.select_related("user", "organization").order_by("pk")
    if for_update:
        qs = qs.select_for_update()
    return list(qs)


def construir_relatorio(clientes) -> RelatorioBackfill:
    cache = ResolverCache()
    rows = [classificar_cliente(c, cache) for c in clientes]
    counts = {nome: 0 for nome in CLASSIFICACOES}
    for row in rows:
        counts[row.classification] = counts.get(row.classification, 0) + 1
    eligible = [r for r in rows if r.classification == BACKFILL_ELIGIBLE]
    return RelatorioBackfill(
        rows=rows,
        counts=counts,
        total=len(rows),
        null_organization=sum(1 for r in rows if r.organization_id is None),
        already_assigned=counts.get(ALREADY_ASSIGNED, 0),
        rows_to_update=len(eligible),
    )


def snapshot_campos(cliente: Cliente) -> tuple:
    """Campos de negócio usados para provar que só organization_id muda."""
    return (
        cliente.pk,
        cliente.user_id,
        cliente.nome,
        cliente.email,
        cliente.telefone,
        cliente.status,
        cliente.fase_funil,
        cliente.origem,
        cliente.criado_em,
    )


class Command(BaseCommand):
    help = (
        "Preenche Cliente.organization somente quando o User resolve de forma "
        "inequívoca. Exija exatamente um modo: --dry-run, --apply ou --rollback."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Classifica e simula; não escreve.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Atualiza organization_id somente de BACKFILL_ELIGIBLE.",
        )
        parser.add_argument(
            "--rollback",
            action="store_true",
            help="Recusa limpar associações sem prova de origem (fail-closed).",
        )

    def handle(self, *args, **options):
        modos = [k for k in ("dry_run", "apply", "rollback") if options.get(k)]
        if len(modos) != 1:
            raise CommandError(
                f"{ERR_USAGE}: informe exatamente um modo "
                "(--dry-run, --apply ou --rollback)."
            )
        if options["rollback"]:
            return self._rollback()
        if options["dry_run"]:
            return self._dry_run()
        return self._apply()

    def _emit(self, mode: str, rel: RelatorioBackfill, *, updated: int | None = None):
        self.stdout.write(f"MODE: {mode}")
        self.stdout.write(f"TOTAL_CLIENTES: {rel.total}")
        self.stdout.write(f"NULL_ORGANIZATION: {rel.null_organization}")
        self.stdout.write(f"ALREADY_ASSIGNED: {rel.already_assigned}")
        for nome in CLASSIFICACOES:
            if nome == ALREADY_ASSIGNED:
                continue
            self.stdout.write(f"{nome}: {rel.counts.get(nome, 0)}")
        self.stdout.write(f"ROWS_TO_UPDATE: {rel.rows_to_update}")
        if updated is not None:
            self.stdout.write(f"UPDATED: {updated}")

        eligible = rel.eligible()
        eligible_users = sorted({r.user_id for r in eligible if r.user_id is not None})
        eligible_orgs = sorted(
            {
                r.resolved_organization_id
                for r in eligible
                if r.resolved_organization_id is not None
            }
        )
        self.stdout.write(
            "ELIGIBLE_USER_IDS: "
            + (",".join(str(i) for i in eligible_users) if eligible_users else "(none)")
        )
        self.stdout.write(
            "ELIGIBLE_ORGANIZATION_IDS: "
            + (",".join(str(i) for i in eligible_orgs) if eligible_orgs else "(none)")
        )

        for label in (
            NO_ORGANIZATION,
            AMBIGUOUS_ORGANIZATION,
            INACTIVE_ORGANIZATION,
            INACTIVE_MEMBERSHIP,
            INVALID_USER,
            CONFLICT_EXISTING_ORGANIZATION,
            RESOLUTION_ERROR,
        ):
            ids = [str(r.cliente_id) for r in rel.rows if r.classification == label]
            self.stdout.write(
                f"{label}_CLIENTE_IDS: "
                + (",".join(ids) if ids else "(none)")
            )

        try:
            from organizacoes.management.commands.provision_demo_tenant import (
                collect_root_counts,
            )

            for uid in eligible_users:
                total = sum(collect_root_counts(uid).values())
                self.stdout.write(f"MODULE_ROOT_TOTAL user_id={uid}: {total}")
        except Exception:
            self.stdout.write("MODULE_ROOT_TOTAL: unavailable")

    def _dry_run(self):
        rel = construir_relatorio(carregar_clientes(for_update=False))
        self._emit("DRY-RUN", rel)
        result = RESULT_READY if rel.rows_to_update else RESULT_NOTHING
        self.stdout.write(f"RESULT: {result}")

    def _apply(self):
        with transaction.atomic():
            rel = construir_relatorio(carregar_clientes(for_update=True))
            self._emit("APPLY", rel)
            eligible = rel.eligible()
            if not eligible:
                self.stdout.write("UPDATED: 0")
                self.stdout.write(f"RESULT: {RESULT_NOTHING}")
                return

            por_org: dict[int, list[int]] = defaultdict(list)
            for row in eligible:
                if row.resolved_organization_id is None:
                    continue
                por_org[row.resolved_organization_id].append(row.cliente_id)

            updated = 0
            for org_id, ids in por_org.items():
                updated += Cliente.objects.filter(
                    pk__in=ids,
                    organization_id__isnull=True,
                ).update(organization_id=org_id)

        self.stdout.write(f"UPDATED: {updated}")
        self.stdout.write(f"RESULT: {RESULT_APPLIED if updated else RESULT_NOTHING}")

    def _rollback(self):
        rel = construir_relatorio(carregar_clientes(for_update=False))
        self._emit("ROLLBACK", rel)
        self.stdout.write("UPDATED: 0")
        self.stdout.write(
            "ROLLBACK_LIMITATION: sem metadata de origem não é possível "
            "distinguir backfill de associação manual coincidente."
        )
        self.stdout.write(f"RESULT: {RESULT_ROLLBACK_NOT_SAFE}")
        raise CommandError(RESULT_ROLLBACK_NOT_SAFE)
