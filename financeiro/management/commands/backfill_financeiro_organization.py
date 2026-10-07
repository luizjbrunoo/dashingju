"""Backfill determinístico de organization nos models financeiros.

NÃO usa request/TenantContext. NÃO aceita organization_id forçado.
Dry-run resolve dependências em memória, sem escrever.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from financeiro.models import (
    Banco,
    Categoria,
    Cobranca,
    CobrancaRecebimento,
    Contrato,
    Movimento,
)
from organizacoes.models import Membership, Organization

ELIGIBLE = "ELIGIBLE"
ALREADY_ASSIGNED = "ALREADY_ASSIGNED"
NO_ORGANIZATION = "NO_ORGANIZATION"
AMBIGUOUS = "AMBIGUOUS"
PARENT_CONFLICT = "PARENT_CONFLICT"
USER_ORG_CONFLICT = "USER_ORG_CONFLICT"
ORPHAN = "ORPHAN"
UNKNOWN = "UNKNOWN"

CLASSIFICACOES = (
    ELIGIBLE,
    ALREADY_ASSIGNED,
    NO_ORGANIZATION,
    AMBIGUOUS,
    PARENT_CONFLICT,
    USER_ORG_CONFLICT,
    ORPHAN,
    UNKNOWN,
)

BLOCKERS = frozenset(
    {
        NO_ORGANIZATION,
        AMBIGUOUS,
        PARENT_CONFLICT,
        USER_ORG_CONFLICT,
        ORPHAN,
        UNKNOWN,
    }
)

SRC_MEMBERSHIP_UNIQUE = "MEMBERSHIP_UNIQUE"
SRC_CLIENT_ORGANIZATION = "CLIENT_ORGANIZATION"
SRC_CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP = (
    "CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP"
)
SRC_CONTRACT_ORGANIZATION = "CONTRACT_ORGANIZATION"
SRC_CLIENT_AND_CONTRACT_AGREE = "CLIENT_AND_CONTRACT_AGREE"
SRC_BANK_AND_CATEGORY_AGREE = "BANK_AND_CATEGORY_AGREE"
SRC_COBRANCA_ORGANIZATION = "COBRANCA_ORGANIZATION"
SRC_COBRANCA_AND_MOVIMENTO_AGREE = "COBRANCA_AND_MOVIMENTO_AGREE"
SRC_EXISTING_CONSISTENT = "EXISTING_CONSISTENT"
SRC_EXISTING_VS_PARENT = "EXISTING_VS_PARENT"
SRC_EXISTING_VS_MEMBERSHIP = "EXISTING_VS_MEMBERSHIP"
SRC_PARENT_CONFLICT = "PARENT_CONFLICT"
SRC_NO_EVIDENCE = "NO_EVIDENCE"
SRC_AMBIGUOUS_MEMBERSHIP = "AMBIGUOUS_MEMBERSHIP"
SRC_INACTIVE_OR_MISSING_MEMBERSHIP = "INACTIVE_OR_MISSING_MEMBERSHIP"
SRC_MISSING_PARENT = "MISSING_PARENT"
SRC_UNRESOLVED_PARENT = "UNRESOLVED_PARENT"

ORDEM = (
    "Banco",
    "Categoria",
    "Contrato",
    "Cobranca",
    "Movimento",
    "CobrancaRecebimento",
)

RESULT_READY = "READY_TO_APPLY"
RESULT_NOTHING = "NOTHING_TO_APPLY"
RESULT_APPLIED = "APPLIED"
RESULT_ABORTED = "APPLY_ABORTED"
ERR_USAGE = "USAGE_ERROR"

MODELS_POR_LABEL = {
    "Banco": Banco,
    "Categoria": Categoria,
    "Contrato": Contrato,
    "Cobranca": Cobranca,
    "Movimento": Movimento,
    "CobrancaRecebimento": CobrancaRecebimento,
}


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
        return counts


class MembershipCache:
    """Membership ativa + Organization ativa, por usuario legado."""

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
    user_id: int | None = None,
) -> LinhaPlano:
    return LinhaPlano(
        model=model,
        pk=obj.pk,
        user_id=user_id if user_id is not None else getattr(obj, "usuario_id", None),
        organization_id=getattr(obj, "organization_id", None),
        classification=classification,
        resolved_organization_id=resolved_organization_id,
        source=source,
        conflict_org_ids=tuple(sorted(set(i for i in conflict_org_ids if i is not None))),
    )


def _membership_conflict(target_id: int, mem_orgs: tuple[int, ...]) -> bool:
    return len(mem_orgs) == 1 and mem_orgs[0] != target_id


_PARENT_SOURCES = {
    SRC_CLIENT_ORGANIZATION,
    SRC_CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP,
    SRC_CONTRACT_ORGANIZATION,
    SRC_CLIENT_AND_CONTRACT_AGREE,
    SRC_BANK_AND_CATEGORY_AGREE,
    SRC_COBRANCA_ORGANIZATION,
    SRC_COBRANCA_AND_MOVIMENTO_AGREE,
}


def _finalizar_existente(
    model: str,
    obj,
    *,
    classification: str,
    target_id: int | None,
    source: str,
    conflict_org_ids: tuple[int, ...] = (),
    user_id: int | None = None,
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
            user_id=user_id,
        )
    if classification in (PARENT_CONFLICT, USER_ORG_CONFLICT, ORPHAN, UNKNOWN):
        return _linha(
            model,
            obj,
            classification=classification,
            resolved_organization_id=target_id,
            source=source,
            conflict_org_ids=conflict_org_ids + (existing,),
            user_id=user_id,
        )
    if target_id is not None and existing == target_id:
        return _linha(
            model,
            obj,
            classification=ALREADY_ASSIGNED,
            resolved_organization_id=existing,
            source=SRC_EXISTING_CONSISTENT,
            user_id=user_id,
        )
    if target_id is not None and existing != target_id:
        parent_like = source in _PARENT_SOURCES
        cls = PARENT_CONFLICT if parent_like else USER_ORG_CONFLICT
        src = SRC_EXISTING_VS_PARENT if parent_like else SRC_EXISTING_VS_MEMBERSHIP
        return _linha(
            model,
            obj,
            classification=cls,
            resolved_organization_id=target_id,
            source=src,
            conflict_org_ids=(existing, target_id),
            user_id=user_id,
        )
    if _membership_conflict(existing, mem_orgs):
        return _linha(
            model,
            obj,
            classification=USER_ORG_CONFLICT,
            resolved_organization_id=mem_orgs[0],
            source=SRC_EXISTING_VS_MEMBERSHIP,
            conflict_org_ids=(existing, mem_orgs[0]),
            user_id=user_id,
        )
    if classification == AMBIGUOUS and mem_orgs and existing not in mem_orgs:
        return _linha(
            model,
            obj,
            classification=USER_ORG_CONFLICT,
            resolved_organization_id=None,
            source=SRC_EXISTING_VS_MEMBERSHIP,
            conflict_org_ids=mem_orgs + (existing,),
            user_id=user_id,
        )
    return _linha(
        model,
        obj,
        classification=ALREADY_ASSIGNED,
        resolved_organization_id=existing,
        source=SRC_EXISTING_CONSISTENT,
        user_id=user_id,
    )


def org_do_parent(plan: dict, label: str, obj) -> int | None:
    """Organization persistida OU resolvida no plano (ELIGIBLE/ALREADY_ASSIGNED)."""
    if obj is None:
        return None
    current = getattr(obj, "organization_id", None)
    if current is not None:
        return current
    row = plan.get((label, obj.pk))
    if row is None:
        return None
    if row.classification in (ELIGIBLE, ALREADY_ASSIGNED):
        return row.resolved_organization_id
    return None


def classificacao_parent(plan: dict, label: str, obj) -> str | None:
    if obj is None:
        return None
    row = plan.get((label, obj.pk))
    return row.classification if row else None


def classificar_por_membership(model: str, obj, cache: MembershipCache) -> LinhaPlano:
    mem_orgs = cache.orgs_for(obj.usuario_id)
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


def classificar_contrato(obj: Contrato, cache: MembershipCache) -> LinhaPlano:
    if obj.cliente_id is None or getattr(obj, "cliente", None) is None:
        return _linha("Contrato", obj, classification=ORPHAN, source=SRC_MISSING_PARENT)
    mem_orgs = cache.orgs_for(obj.usuario_id)
    client_org = obj.cliente.organization_id
    if client_org is not None:
        if _membership_conflict(client_org, mem_orgs):
            return _finalizar_existente(
                "Contrato",
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
            "Contrato",
            obj,
            classification=ELIGIBLE,
            target_id=client_org,
            source=source,
            mem_orgs=mem_orgs,
        )
    return classificar_por_membership("Contrato", obj, cache)


def classificar_cobranca(
    obj: Cobranca, plan: dict, cache: MembershipCache
) -> LinhaPlano:
    if obj.cliente_id is None or getattr(obj, "cliente", None) is None:
        return _linha("Cobranca", obj, classification=ORPHAN, source=SRC_MISSING_PARENT)
    mem_orgs = cache.orgs_for(obj.usuario_id)
    client_org = obj.cliente.organization_id
    contrato_org = None
    if obj.contrato_id:
        contrato_org = org_do_parent(plan, "Contrato", obj.contrato)
    if client_org and contrato_org and client_org != contrato_org:
        return _finalizar_existente(
            "Cobranca",
            obj,
            classification=PARENT_CONFLICT,
            target_id=None,
            source=SRC_PARENT_CONFLICT,
            conflict_org_ids=(client_org, contrato_org),
            mem_orgs=mem_orgs,
        )
    target = client_org or contrato_org
    if target is not None:
        if _membership_conflict(target, mem_orgs):
            return _finalizar_existente(
                "Cobranca",
                obj,
                classification=USER_ORG_CONFLICT,
                target_id=target,
                source=SRC_EXISTING_VS_MEMBERSHIP
                if obj.organization_id
                else SRC_CLIENT_ORGANIZATION,
                conflict_org_ids=(target, mem_orgs[0]),
                mem_orgs=mem_orgs,
            )
        if client_org and contrato_org:
            source = SRC_CLIENT_AND_CONTRACT_AGREE
        elif client_org:
            source = (
                SRC_CLIENT_ORGANIZATION_CONFIRMED_BY_MEMBERSHIP
                if len(mem_orgs) == 1
                else SRC_CLIENT_ORGANIZATION
            )
        else:
            source = SRC_CONTRACT_ORGANIZATION
        return _finalizar_existente(
            "Cobranca",
            obj,
            classification=ELIGIBLE,
            target_id=target,
            source=source,
            mem_orgs=mem_orgs,
        )
    return classificar_por_membership("Cobranca", obj, cache)


def _herdar_parent_nao_resolvido(plan: dict, pares: list[tuple[str, object]]) -> str:
    classes = []
    for label, obj in pares:
        if obj is None:
            continue
        cls = classificacao_parent(plan, label, obj)
        if cls in BLOCKERS:
            classes.append(cls)
    if PARENT_CONFLICT in classes:
        return PARENT_CONFLICT
    if USER_ORG_CONFLICT in classes:
        return USER_ORG_CONFLICT
    if ORPHAN in classes:
        return ORPHAN
    if AMBIGUOUS in classes:
        return AMBIGUOUS
    if NO_ORGANIZATION in classes:
        return NO_ORGANIZATION
    if UNKNOWN in classes:
        return UNKNOWN
    return NO_ORGANIZATION


def classificar_movimento(
    obj: Movimento, plan: dict, cache: MembershipCache
) -> LinhaPlano:
    if obj.banco_id is None or obj.categoria_id is None:
        return _linha("Movimento", obj, classification=ORPHAN, source=SRC_MISSING_PARENT)
    banco = getattr(obj, "banco", None)
    categoria = getattr(obj, "categoria", None)
    if banco is None or categoria is None:
        return _linha("Movimento", obj, classification=ORPHAN, source=SRC_MISSING_PARENT)
    mem_orgs = cache.orgs_for(obj.usuario_id)
    bank_org = org_do_parent(plan, "Banco", banco)
    cat_org = org_do_parent(plan, "Categoria", categoria)
    if bank_org and cat_org:
        if bank_org != cat_org:
            return _finalizar_existente(
                "Movimento",
                obj,
                classification=PARENT_CONFLICT,
                target_id=None,
                source=SRC_PARENT_CONFLICT,
                conflict_org_ids=(bank_org, cat_org),
                mem_orgs=mem_orgs,
            )
        if _membership_conflict(bank_org, mem_orgs):
            return _finalizar_existente(
                "Movimento",
                obj,
                classification=USER_ORG_CONFLICT,
                target_id=bank_org,
                source=SRC_EXISTING_VS_MEMBERSHIP
                if obj.organization_id
                else SRC_BANK_AND_CATEGORY_AGREE,
                conflict_org_ids=(bank_org, mem_orgs[0]),
                mem_orgs=mem_orgs,
            )
        return _finalizar_existente(
            "Movimento",
            obj,
            classification=ELIGIBLE,
            target_id=bank_org,
            source=SRC_BANK_AND_CATEGORY_AGREE,
            mem_orgs=mem_orgs,
        )
    inherited = _herdar_parent_nao_resolvido(
        plan, [("Banco", banco), ("Categoria", categoria)]
    )
    return _finalizar_existente(
        "Movimento",
        obj,
        classification=inherited,
        target_id=bank_org or cat_org,
        source=SRC_UNRESOLVED_PARENT,
        conflict_org_ids=tuple(i for i in (bank_org, cat_org) if i),
        mem_orgs=mem_orgs,
    )


def classificar_recebimento(
    obj: CobrancaRecebimento, plan: dict, cache: MembershipCache
) -> LinhaPlano:
    cobranca = getattr(obj, "cobranca", None)
    if obj.cobranca_id is None or cobranca is None:
        return _linha(
            "CobrancaRecebimento",
            obj,
            classification=ORPHAN,
            source=SRC_MISSING_PARENT,
        )
    mem_orgs = cache.orgs_for(obj.usuario_id)
    cob_org = org_do_parent(plan, "Cobranca", cobranca)
    mov_org = None
    movimento = getattr(obj, "movimento", None) if obj.movimento_id else None
    if obj.movimento_id:
        if movimento is None:
            return _linha(
                "CobrancaRecebimento",
                obj,
                classification=ORPHAN,
                source=SRC_MISSING_PARENT,
            )
        mov_org = org_do_parent(plan, "Movimento", movimento)
        if cob_org and mov_org and cob_org != mov_org:
            return _finalizar_existente(
                "CobrancaRecebimento",
                obj,
                classification=PARENT_CONFLICT,
                target_id=None,
                source=SRC_PARENT_CONFLICT,
                conflict_org_ids=(cob_org, mov_org),
                mem_orgs=mem_orgs,
            )
        if obj.movimento_id and mov_org is None:
            inherited = _herdar_parent_nao_resolvido(plan, [("Movimento", movimento)])
            if inherited in BLOCKERS and cob_org is None:
                return _finalizar_existente(
                    "CobrancaRecebimento",
                    obj,
                    classification=inherited,
                    target_id=cob_org,
                    source=SRC_UNRESOLVED_PARENT,
                    mem_orgs=mem_orgs,
                )
            if inherited in BLOCKERS and cob_org is not None:
                # movimento preenchido mas sem org resolvida: fail-closed
                return _finalizar_existente(
                    "CobrancaRecebimento",
                    obj,
                    classification=inherited,
                    target_id=cob_org,
                    source=SRC_UNRESOLVED_PARENT,
                    mem_orgs=mem_orgs,
                )
    if cob_org is not None:
        if _membership_conflict(cob_org, mem_orgs):
            return _finalizar_existente(
                "CobrancaRecebimento",
                obj,
                classification=USER_ORG_CONFLICT,
                target_id=cob_org,
                source=SRC_EXISTING_VS_MEMBERSHIP
                if obj.organization_id
                else SRC_COBRANCA_ORGANIZATION,
                conflict_org_ids=(cob_org, mem_orgs[0]),
                mem_orgs=mem_orgs,
            )
        source = (
            SRC_COBRANCA_AND_MOVIMENTO_AGREE
            if mov_org and cob_org == mov_org
            else SRC_COBRANCA_ORGANIZATION
        )
        return _finalizar_existente(
            "CobrancaRecebimento",
            obj,
            classification=ELIGIBLE,
            target_id=cob_org,
            source=source,
            mem_orgs=mem_orgs,
        )
    inherited = _herdar_parent_nao_resolvido(plan, [("Cobranca", cobranca)])
    if inherited in BLOCKERS:
        return _finalizar_existente(
            "CobrancaRecebimento",
            obj,
            classification=inherited,
            target_id=None,
            source=SRC_UNRESOLVED_PARENT,
            mem_orgs=mem_orgs,
        )
    return classificar_por_membership("CobrancaRecebimento", obj, cache)


def carregar_objetos(*, for_update: bool = False) -> dict[str, list]:
    def _qs(model, *related):
        qs = model.objects.all().order_by("pk")
        if related:
            qs = qs.select_related(*related)
        if for_update:
            qs = qs.select_for_update()
        return list(qs)

    return {
        "Banco": _qs(Banco),
        "Categoria": _qs(Categoria),
        "Contrato": _qs(Contrato, "cliente"),
        "Cobranca": _qs(Cobranca, "cliente", "contrato"),
        "Movimento": _qs(Movimento, "banco", "categoria"),
        "CobrancaRecebimento": _qs(CobrancaRecebimento, "cobranca", "movimento"),
    }


def construir_plano(objetos: dict[str, list] | None = None) -> RelatorioBackfill:
    objetos = objetos if objetos is not None else carregar_objetos(for_update=False)
    cache = MembershipCache()
    plan: dict[tuple[str, int], LinhaPlano] = {}

    for obj in objetos["Banco"]:
        plan[("Banco", obj.pk)] = classificar_por_membership("Banco", obj, cache)
    for obj in objetos["Categoria"]:
        plan[("Categoria", obj.pk)] = classificar_por_membership("Categoria", obj, cache)
    for obj in objetos["Contrato"]:
        plan[("Contrato", obj.pk)] = classificar_contrato(obj, cache)
    for obj in objetos["Cobranca"]:
        plan[("Cobranca", obj.pk)] = classificar_cobranca(obj, plan, cache)
    for obj in objetos["Movimento"]:
        plan[("Movimento", obj.pk)] = classificar_movimento(obj, plan, cache)
    for obj in objetos["CobrancaRecebimento"]:
        plan[("CobrancaRecebimento", obj.pk)] = classificar_recebimento(
            obj, plan, cache
        )

    by_model: dict[str, list[LinhaPlano]] = {label: [] for label in ORDEM}
    rows: list[LinhaPlano] = []
    for label in ORDEM:
        for obj in objetos[label]:
            row = plan[(label, obj.pk)]
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
        "Materializa financeiro.organization somente com resolução determinística. "
        "Exija exatamente um modo: --dry-run ou --apply."
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
                f"USER_ORG_CONFLICT={c[USER_ORG_CONFLICT]} ORPHAN={c[ORPHAN]} "
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
        self.stdout.write(f"ORPHAN: {t[ORPHAN]}")
        self.stdout.write(f"UNKNOWN: {t[UNKNOWN]}")
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
