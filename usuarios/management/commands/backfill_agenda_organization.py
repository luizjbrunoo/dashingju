"""Backfill determinístico de organization em Compromisso e Tarefa.

Ordem:
1. Cliente.organization, quando o evento pertence a Cliente;
2. Membership ativa única do owner legado (user);
3. nunca escolher arbitrariamente.

NO_ORGANIZATION não é blocker (permanece NULL).
AMBIGUOUS / PARENT_CONFLICT / USER_ORG_CONFLICT / UNKNOWN abortam --apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from organizacoes.models import Membership, Organization
from usuarios.models import Compromisso, Tarefa

ELIGIBLE = "ELIGIBLE"
ALREADY_ASSIGNED = "ALREADY_ASSIGNED"
NO_ORGANIZATION = "NO_ORGANIZATION"
AMBIGUOUS = "AMBIGUOUS"
PARENT_CONFLICT = "PARENT_CONFLICT"
USER_ORG_CONFLICT = "USER_ORG_CONFLICT"
UNKNOWN = "UNKNOWN"

CLASSIFICACOES = (
    ELIGIBLE,
    ALREADY_ASSIGNED,
    NO_ORGANIZATION,
    AMBIGUOUS,
    PARENT_CONFLICT,
    USER_ORG_CONFLICT,
    UNKNOWN,
)

BLOCKERS = frozenset({AMBIGUOUS, PARENT_CONFLICT, USER_ORG_CONFLICT, UNKNOWN})

SRC_CLIENT_ORGANIZATION = "CLIENT_ORGANIZATION"
SRC_CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP = (
    "CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP"
)
SRC_MEMBERSHIP_UNIQUE = "MEMBERSHIP_UNIQUE"
SRC_EXISTING_CONSISTENT = "EXISTING_CONSISTENT"
SRC_EXISTING_VS_CLIENT = "EXISTING_VS_CLIENT"
SRC_EXISTING_VS_MEMBERSHIP = "EXISTING_VS_MEMBERSHIP"
SRC_AMBIGUOUS_MEMBERSHIP = "AMBIGUOUS_MEMBERSHIP"
SRC_INACTIVE_OR_MISSING_MEMBERSHIP = "INACTIVE_OR_MISSING_MEMBERSHIP"

ORDEM = ("Compromisso", "Tarefa")
MODELS_POR_LABEL = {"Compromisso": Compromisso, "Tarefa": Tarefa}

RESULT_READY = "READY_TO_APPLY"
RESULT_NOTHING = "NOTHING_TO_APPLY"
RESULT_APPLIED = "APPLIED"
RESULT_ABORTED = "APPLY_ABORTED"
ERR_USAGE = "USAGE_ERROR"


@dataclass
class LinhaPlano:
    model: str
    pk: int
    user_id: int | None
    organization_id: int | None
    classification: str
    resolved_organization_id: int | None = None
    source: str = ""
    conflict_org_ids: tuple[int, ...] = ()


@dataclass
class RelatorioBackfill:
    rows: list[LinhaPlano] = field(default_factory=list)
    by_model: dict[str, list[LinhaPlano]] = field(default_factory=dict)

    def eligible(self) -> list[LinhaPlano]:
        return [r for r in self.rows if r.classification == ELIGIBLE]

    def blockers(self) -> list[LinhaPlano]:
        return [r for r in self.rows if r.classification in BLOCKERS]

    def counts_for(self, model: str) -> dict[str, int]:
        rows = self.by_model.get(model, [])
        counts = {nome: 0 for nome in CLASSIFICACOES}
        for row in rows:
            counts[row.classification] = counts.get(row.classification, 0) + 1
        counts["TOTAL"] = len(rows)
        counts["PLANNED_UPDATES"] = counts[ELIGIBLE]
        return counts

    def totals(self) -> dict[str, int]:
        counts = {nome: 0 for nome in CLASSIFICACOES}
        for row in self.rows:
            counts[row.classification] = counts.get(row.classification, 0) + 1
        counts["TOTAL"] = len(self.rows)
        counts["PLANNED_UPDATES"] = counts[ELIGIBLE]
        counts["NULL"] = sum(1 for r in self.rows if r.organization_id is None)
        return counts


class MembershipCache:
    def __init__(self):
        self._orgs: dict[int, tuple[int, ...]] = {}

    def orgs_for(self, user_id: int | None) -> tuple[int, ...]:
        if user_id is None:
            return ()
        if user_id not in self._orgs:
            ids = Membership.objects.filter(
                user_id=user_id,
                status=Membership.Status.ACTIVE,
                organization__status=Organization.Status.ACTIVE,
            ).values_list("organization_id", flat=True)
            self._orgs[user_id] = tuple(sorted(set(ids)))
        return self._orgs[user_id]


def _linha(
    model: str,
    obj,
    *,
    classification: str,
    resolved_organization_id: int | None = None,
    source: str = "",
    conflict_org_ids: tuple[int, ...] = (),
) -> LinhaPlano:
    return LinhaPlano(
        model=model,
        pk=obj.pk,
        user_id=getattr(obj, "user_id", None),
        organization_id=getattr(obj, "organization_id", None),
        classification=classification,
        resolved_organization_id=resolved_organization_id,
        source=source,
        conflict_org_ids=tuple(sorted(set(i for i in conflict_org_ids if i is not None))),
    )


def _membership_conflict(target_id: int, mem_orgs: tuple[int, ...]) -> bool:
    return len(mem_orgs) == 1 and mem_orgs[0] != target_id


def _finalizar_existente(
    model: str,
    obj,
    *,
    classification: str,
    target_id: int | None,
    source: str,
    conflict_org_ids: tuple[int, ...] = (),
    mem_orgs: tuple[int, ...] = (),
) -> LinhaPlano:
    existing = getattr(obj, "organization_id", None)
    if existing is None:
        return _linha(
            model,
            obj,
            classification=classification,
            resolved_organization_id=target_id,
            source=source,
            conflict_org_ids=conflict_org_ids,
        )
    if classification in (PARENT_CONFLICT, USER_ORG_CONFLICT, UNKNOWN):
        return _linha(
            model,
            obj,
            classification=classification,
            resolved_organization_id=target_id,
            source=source,
            conflict_org_ids=conflict_org_ids + (existing,),
        )
    if target_id is not None and existing == target_id:
        return _linha(
            model,
            obj,
            classification=ALREADY_ASSIGNED,
            resolved_organization_id=existing,
            source=SRC_EXISTING_CONSISTENT,
        )
    if target_id is not None and existing != target_id:
        parent_like = source in (
            SRC_CLIENT_ORGANIZATION,
            SRC_CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP,
        )
        cls = PARENT_CONFLICT if parent_like else USER_ORG_CONFLICT
        src = SRC_EXISTING_VS_CLIENT if parent_like else SRC_EXISTING_VS_MEMBERSHIP
        return _linha(
            model,
            obj,
            classification=cls,
            resolved_organization_id=target_id,
            source=src,
            conflict_org_ids=(existing, target_id),
        )
    if _membership_conflict(existing, mem_orgs):
        return _linha(
            model,
            obj,
            classification=USER_ORG_CONFLICT,
            resolved_organization_id=mem_orgs[0],
            source=SRC_EXISTING_VS_MEMBERSHIP,
            conflict_org_ids=(existing, mem_orgs[0]),
        )
    return _linha(
        model,
        obj,
        classification=ALREADY_ASSIGNED,
        resolved_organization_id=existing,
        source=SRC_EXISTING_CONSISTENT,
    )


def classificar_item(model: str, obj, cache: MembershipCache) -> LinhaPlano:
    mem_orgs = cache.orgs_for(obj.user_id)
    cliente = getattr(obj, "cliente", None) if obj.cliente_id else None
    client_org = getattr(cliente, "organization_id", None) if cliente is not None else None

    if client_org is not None:
        if _membership_conflict(client_org, mem_orgs):
            return _finalizar_existente(
                model,
                obj,
                classification=USER_ORG_CONFLICT,
                target_id=client_org,
                source=SRC_EXISTING_VS_MEMBERSHIP
                if obj.organization_id
                else SRC_CLIENT_ORGANIZATION,
                conflict_org_ids=(client_org, mem_orgs[0]),
                mem_orgs=mem_orgs,
            )
        source = (
            SRC_CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP
            if len(mem_orgs) == 1
            else SRC_CLIENT_ORGANIZATION
        )
        return _finalizar_existente(
            model,
            obj,
            classification=ELIGIBLE,
            target_id=client_org,
            source=source,
            mem_orgs=mem_orgs,
        )

    if len(mem_orgs) == 1:
        return _finalizar_existente(
            model,
            obj,
            classification=ELIGIBLE,
            target_id=mem_orgs[0],
            source=SRC_MEMBERSHIP_UNIQUE,
            mem_orgs=mem_orgs,
        )
    if len(mem_orgs) == 0:
        return _finalizar_existente(
            model,
            obj,
            classification=NO_ORGANIZATION,
            target_id=None,
            source=SRC_INACTIVE_OR_MISSING_MEMBERSHIP,
            mem_orgs=mem_orgs,
        )
    return _finalizar_existente(
        model,
        obj,
        classification=AMBIGUOUS,
        target_id=None,
        source=SRC_AMBIGUOUS_MEMBERSHIP,
        conflict_org_ids=mem_orgs,
        mem_orgs=mem_orgs,
    )


def carregar_objetos(*, for_update: bool = False) -> dict[str, list]:
    def _qs(model, *related):
        qs = model.objects.all().order_by("pk")
        if related:
            qs = qs.select_related(*related)
        if for_update:
            qs = qs.select_for_update()
        return list(qs)

    return {
        "Compromisso": _qs(Compromisso, "cliente"),
        "Tarefa": _qs(Tarefa, "cliente"),
    }


def construir_plano(objetos: dict[str, list] | None = None) -> RelatorioBackfill:
    objetos = objetos if objetos is not None else carregar_objetos(for_update=False)
    cache = MembershipCache()
    by_model: dict[str, list[LinhaPlano]] = {label: [] for label in ORDEM}
    rows: list[LinhaPlano] = []
    for label in ORDEM:
        for obj in objetos[label]:
            row = classificar_item(label, obj, cache)
            by_model[label].append(row)
            rows.append(row)
    return RelatorioBackfill(rows=rows, by_model=by_model)


def aplicar_eligible(rel: RelatorioBackfill) -> int:
    updated = 0
    por_model_org: dict[str, dict[int, list[int]]] = {label: {} for label in ORDEM}
    for row in rel.eligible():
        org_id = row.resolved_organization_id
        if org_id is None:
            continue
        por_model_org[row.model].setdefault(org_id, []).append(row.pk)
    for label, por_org in por_model_org.items():
        model = MODELS_POR_LABEL[label]
        for org_id, ids in por_org.items():
            updated += model.objects.filter(
                pk__in=ids, organization_id__isnull=True
            ).update(organization_id=org_id)
    return updated


class Command(BaseCommand):
    help = (
        "Materializa Compromisso/Tarefa.organization somente com resolução "
        "determinística. Exija exatamente um modo: --dry-run ou --apply."
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
            help="Atualiza organization_id somente de ELIGIBLE, se não houver blockers.",
        )

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        apply = bool(options.get("apply"))
        if dry and apply:
            raise CommandError(
                f"{ERR_USAGE}: --dry-run e --apply são mutuamente exclusivos."
            )
        if not dry and not apply:
            raise CommandError(
                f"{ERR_USAGE}: informe exatamente um modo (--dry-run ou --apply)."
            )
        if dry:
            return self._dry_run()
        return self._apply()

    def _emit(self, mode: str, rel: RelatorioBackfill, *, updated: int | None = None):
        self.stdout.write(f"MODE: {mode}")
        for label in ORDEM:
            c = rel.counts_for(label)
            self.stdout.write(
                f"MODEL {label} TOTAL={c['TOTAL']} ELIGIBLE={c[ELIGIBLE]} "
                f"ALREADY_ASSIGNED={c[ALREADY_ASSIGNED]} "
                f"NO_ORGANIZATION={c[NO_ORGANIZATION]} AMBIGUOUS={c[AMBIGUOUS]} "
                f"PARENT_CONFLICT={c[PARENT_CONFLICT]} "
                f"USER_ORG_CONFLICT={c[USER_ORG_CONFLICT]} "
                f"UNKNOWN={c[UNKNOWN]} PLANNED_UPDATES={c['PLANNED_UPDATES']}"
            )
        t = rel.totals()
        self.stdout.write(f"TOTAL_RECORDS: {t['TOTAL']}")
        self.stdout.write(f"ELIGIBLE: {t[ELIGIBLE]}")
        self.stdout.write(f"ALREADY_ASSIGNED: {t[ALREADY_ASSIGNED]}")
        self.stdout.write(f"NO_ORGANIZATION: {t[NO_ORGANIZATION]}")
        self.stdout.write(f"AMBIGUOUS: {t[AMBIGUOUS]}")
        self.stdout.write(f"PARENT_CONFLICT: {t[PARENT_CONFLICT]}")
        self.stdout.write(f"USER_ORG_CONFLICT: {t[USER_ORG_CONFLICT]}")
        self.stdout.write(f"UNKNOWN: {t[UNKNOWN]}")
        self.stdout.write(f"NULL: {t['NULL']}")
        self.stdout.write(f"PLANNED_UPDATES: {t['PLANNED_UPDATES']}")
        if updated is not None:
            self.stdout.write(f"UPDATED: {updated}")
        for row in rel.blockers():
            orgs = ",".join(str(i) for i in row.conflict_org_ids) or "-"
            self.stdout.write(
                f"CONFLICT model={row.model} pk={row.pk} "
                f"classification={row.classification} source={row.source} "
                f"org_ids={orgs} user_id={row.user_id if row.user_id is not None else '-'}"
            )

    def _dry_run(self):
        rel = construir_plano()
        self._emit("DRY-RUN", rel)
        if rel.blockers():
            result = "DRY-RUN_HAS_BLOCKERS"
        elif rel.eligible():
            result = RESULT_READY
        else:
            result = RESULT_NOTHING
        self.stdout.write(f"RESULT: {result}")

    def _apply(self):
        with transaction.atomic():
            rel = construir_plano(carregar_objetos(for_update=True))
            self._emit("APPLY", rel)
            if rel.blockers():
                self.stdout.write("UPDATED: 0")
                self.stdout.write(f"RESULT: {RESULT_ABORTED}")
                raise CommandError(RESULT_ABORTED)
            updated = aplicar_eligible(rel)
            self.stdout.write(f"UPDATED: {updated}")
            result = RESULT_APPLIED if updated else RESULT_NOTHING
            self.stdout.write(f"RESULT: {result}")
