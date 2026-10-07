"""Provisiona o tenant DEMO (Organization + Membership) para o User 1.

Não migra ownership dos módulos. Não aceita user/nome arbitrários.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from organizacoes.models import Membership, Organization
from organizacoes.services import resolver_organization

User = get_user_model()

TARGET_USER_ID = 1
ORGANIZATION_NAME = "Almeida & Torres Advocacia — DEMO"

RESULT_READY = "READY_TO_APPLY"
RESULT_PROVISIONED = "PROVISIONED"
RESULT_ALREADY = "ALREADY_PROVISIONED"
RESULT_NOTHING_ROLLBACK = "NOTHING_TO_ROLLBACK"
RESULT_ROLLED_BACK = "ROLLED_BACK"

ERR_USAGE = "USAGE_ERROR"
ERR_USER_NOT_FOUND = "TARGET_USER_NOT_FOUND"
ERR_USER_INACTIVE = "TARGET_USER_INACTIVE"
ERR_EXISTING_MEMBERSHIP = "CONFLICT_EXISTING_MEMBERSHIP"
ERR_AMBIGUOUS = "CONFLICT_AMBIGUOUS_MEMBERSHIPS"
ERR_ORPHAN_DEMO = "CONFLICT_ORPHAN_DEMO_ORGANIZATION"
ERR_CREATED_BY = "CONFLICT_CREATED_BY_WITHOUT_MEMBERSHIP"
ERR_INACTIVE_MEMBERSHIP = "CONFLICT_INACTIVE_MEMBERSHIP"
ERR_INACTIVE_ORG = "CONFLICT_INACTIVE_ORGANIZATION"
ERR_ROLLBACK_REVIEW = "ROLLBACK_REQUIRES_HUMAN_REVIEW"


def collect_root_counts(user_id: int) -> dict[str, int]:
    from comercial.models import (
        AcaoComercialResolvida,
        AdvGrowthScoreSnapshot,
        ComercialAuditLog,
        InvestimentoMidia,
        MetaComercial,
    )
    from financeiro.models import Banco, Categoria, Cobranca, Contrato, Movimento
    from marketing.models import (
        ContentIdea,
        ContentItem,
        ContentProfile,
        EditorialCalendar,
        MarketingIntegracao,
    )
    from usuarios.models import Cliente, Compromisso, Tarefa

    specs = (
        ("Cliente", Cliente, "user_id"),
        ("Compromisso", Compromisso, "user_id"),
        ("Tarefa", Tarefa, "user_id"),
        ("Banco", Banco, "usuario_id"),
        ("Categoria", Categoria, "usuario_id"),
        ("Movimento", Movimento, "usuario_id"),
        ("Contrato", Contrato, "usuario_id"),
        ("Cobranca", Cobranca, "usuario_id"),
        ("MetaComercial", MetaComercial, "usuario_id"),
        ("ComercialAuditLog", ComercialAuditLog, "usuario_id"),
        ("AcaoComercialResolvida", AcaoComercialResolvida, "usuario_id"),
        ("InvestimentoMidia", InvestimentoMidia, "usuario_id"),
        ("AdvGrowthScoreSnapshot", AdvGrowthScoreSnapshot, "usuario_id"),
        ("ContentProfile", ContentProfile, "usuario_id"),
        ("EditorialCalendar", EditorialCalendar, "usuario_id"),
        ("ContentItem", ContentItem, "usuario_id"),
        ("ContentIdea", ContentIdea, "usuario_id"),
        ("MarketingIntegracao", MarketingIntegracao, "usuario_id"),
    )
    return {
        name: model.objects.filter(**{field: user_id}).count()
        for name, model, field in specs
    }


def _memberships_do_user(user_id: int):
    return list(
        Membership.objects.filter(user_id=user_id).select_related("organization")
    )


def _validas(memberships: list[Membership]) -> list[Membership]:
    return [
        m
        for m in memberships
        if m.status == Membership.Status.ACTIVE
        and m.organization.status == Organization.Status.ACTIVE
    ]


def _orgs_demo() -> list[Organization]:
    return list(Organization.objects.filter(name=ORGANIZATION_NAME))


def _orgs_created_by(user_id: int) -> list[Organization]:
    return list(Organization.objects.filter(created_by_id=user_id))


def _context_label(user) -> str:
    _org, status = resolver_organization(user)
    return status


def _demo_match(membership: Membership) -> bool:
    org = membership.organization
    return (
        org.name == ORGANIZATION_NAME
        and org.status == Organization.Status.ACTIVE
        and membership.role == Membership.Role.OWNER
        and membership.status == Membership.Status.ACTIVE
    )


@dataclass
class Diagnostico:
    result: str
    user: object | None = None
    memberships: list | None = None
    validas: list | None = None


def diagnosticar(user_id: int = TARGET_USER_ID) -> Diagnostico:
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return Diagnostico(result=ERR_USER_NOT_FOUND)

    if not user.is_active:
        return Diagnostico(result=ERR_USER_INACTIVE, user=user)

    memberships = _memberships_do_user(user.pk)
    validas = _validas(memberships)

    if len(validas) > 1:
        return Diagnostico(
            result=ERR_AMBIGUOUS, user=user, memberships=memberships, validas=validas
        )

    if len(validas) == 1:
        if _demo_match(validas[0]):
            return Diagnostico(
                result=RESULT_ALREADY,
                user=user,
                memberships=memberships,
                validas=validas,
            )
        return Diagnostico(
            result=ERR_EXISTING_MEMBERSHIP,
            user=user,
            memberships=memberships,
            validas=validas,
        )

    for m in memberships:
        if m.organization.status == Organization.Status.INACTIVE:
            return Diagnostico(
                result=ERR_INACTIVE_ORG,
                user=user,
                memberships=memberships,
                validas=validas,
            )

    if any(m.status == Membership.Status.INACTIVE for m in memberships):
        return Diagnostico(
            result=ERR_INACTIVE_MEMBERSHIP,
            user=user,
            memberships=memberships,
            validas=validas,
        )

    if _orgs_demo():
        return Diagnostico(
            result=ERR_ORPHAN_DEMO,
            user=user,
            memberships=memberships,
            validas=validas,
        )

    if _orgs_created_by(user.pk):
        return Diagnostico(
            result=ERR_CREATED_BY,
            user=user,
            memberships=memberships,
            validas=validas,
        )

    return Diagnostico(
        result=RESULT_READY, user=user, memberships=memberships, validas=validas
    )


class Command(BaseCommand):
    help = (
        "Provisiona Organization + Membership DEMO para o User 1. "
        "Exija exatamente um modo: --dry-run, --apply ou --rollback."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Consulta e simula; não escreve.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Cria Organization + Membership DEMO se as pré-condições passarem.",
        )
        parser.add_argument(
            "--rollback",
            action="store_true",
            help="Remove somente o provisionamento DEMO inequívoco do User 1.",
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

    def _emit_header(self, mode: str, diag: Diagnostico):
        user = diag.user
        counts = collect_root_counts(TARGET_USER_ID) if user else {}
        self.stdout.write(f"MODE: {mode}")
        self.stdout.write(f"USER_ID: {TARGET_USER_ID}")
        if user is None:
            self.stdout.write("USER_EXISTS: False")
            self.stdout.write(f"RESULT: {diag.result}")
            return counts

        self.stdout.write("USER_EXISTS: True")
        self.stdout.write(f"USER_ACTIVE: {user.is_active}")
        self.stdout.write(f"CURRENT_CONTEXT: {_context_label(user)}")
        self.stdout.write(f"ORGANIZATION_COUNT: {Organization.objects.count()}")
        self.stdout.write(f"MEMBERSHIP_COUNT: {Membership.objects.count()}")
        self.stdout.write(f"VALID_MEMBERSHIPS: {len(diag.validas or [])}")
        self.stdout.write(
            f"MODULE_ROOT_TOTAL: {sum(counts.values())}"
        )
        return counts

    def _dry_run(self):
        diag = diagnosticar()
        self._emit_header("DRY-RUN", diag)
        if diag.result == RESULT_READY:
            self.stdout.write(f"ORGANIZATION_TO_CREATE: {ORGANIZATION_NAME}")
            self.stdout.write(
                f"MEMBERSHIP_TO_CREATE: {Membership.Role.OWNER} / {Membership.Status.ACTIVE}"
            )
            self.stdout.write("MODULE_DATA_CHANGES: 0")
            self.stdout.write(f"RESULT: {RESULT_READY}")
            return
        if diag.result == RESULT_ALREADY:
            self.stdout.write("ORGANIZATION_TO_CREATE: (nenhuma)")
            self.stdout.write("MEMBERSHIP_TO_CREATE: (nenhuma)")
            self.stdout.write("MODULE_DATA_CHANGES: 0")
            self.stdout.write(f"RESULT: {RESULT_ALREADY}")
            return
        self.stdout.write("MODULE_DATA_CHANGES: 0")
        self.stdout.write(f"RESULT: {diag.result}")
        raise CommandError(diag.result)

    def _apply(self):
        diag = diagnosticar()
        before = self._emit_header("APPLY", diag)
        if diag.result == RESULT_ALREADY:
            from organizacoes.role_capabilities import grant_capabilities_for_membership

            granted = grant_capabilities_for_membership(diag.validas[0])
            self.stdout.write("MODULE_DATA_CHANGES: 0")
            self.stdout.write(f"OWNER_CAPABILITIES: {','.join(granted)}")
            self.stdout.write(f"RESULT: {RESULT_ALREADY}")
            return
        if diag.result != RESULT_READY:
            self.stdout.write("MODULE_DATA_CHANGES: 0")
            self.stdout.write(f"RESULT: {diag.result}")
            raise CommandError(diag.result)

        user = diag.user
        from organizacoes.role_capabilities import create_organization_with_owner

        org, _membership, granted = create_organization_with_owner(
            name=ORGANIZATION_NAME, user=user
        )
        after = collect_root_counts(TARGET_USER_ID)
        changed = sum(after[k] - before[k] for k in before)
        _org, ctx = resolver_organization(user)
        self.stdout.write(f"ORGANIZATION_ID: {org.pk}")
        self.stdout.write(f"MEMBERSHIP_ROLE: {Membership.Role.OWNER}")
        self.stdout.write(f"OWNER_CAPABILITIES: {','.join(granted)}")
        self.stdout.write(f"SLUG_GENERATED: {bool(org.slug)}")
        self.stdout.write(f"CURRENT_CONTEXT: {ctx}")
        self.stdout.write(f"MODULE_DATA_CHANGES: {changed}")
        self.stdout.write(f"RESULT: {RESULT_PROVISIONED}")
        if changed != 0:
            raise CommandError("MODULE_DATA_CHANGED")

    def _rollback(self):
        try:
            user = User.objects.get(pk=TARGET_USER_ID)
        except User.DoesNotExist:
            self.stdout.write("MODE: ROLLBACK")
            self.stdout.write(f"USER_ID: {TARGET_USER_ID}")
            self.stdout.write(f"RESULT: {RESULT_NOTHING_ROLLBACK}")
            return

        before = collect_root_counts(TARGET_USER_ID)
        self.stdout.write("MODE: ROLLBACK")
        self.stdout.write(f"USER_ID: {TARGET_USER_ID}")
        self.stdout.write(f"CURRENT_CONTEXT: {_context_label(user)}")

        memberships = _memberships_do_user(TARGET_USER_ID)
        if not memberships:
            if _orgs_demo():
                self.stdout.write(f"RESULT: {ERR_ROLLBACK_REVIEW}")
                raise CommandError(ERR_ROLLBACK_REVIEW)
            self.stdout.write("MODULE_DATA_CHANGES: 0")
            self.stdout.write(f"RESULT: {RESULT_NOTHING_ROLLBACK}")
            return

        if len(memberships) != 1:
            self.stdout.write(f"RESULT: {ERR_ROLLBACK_REVIEW}")
            raise CommandError(ERR_ROLLBACK_REVIEW)

        membership = memberships[0]
        org = membership.organization
        org_memberships = list(Membership.objects.filter(organization=org))

        inequívoco = (
            membership.user_id == TARGET_USER_ID
            and membership.role == Membership.Role.OWNER
            and membership.status == Membership.Status.ACTIVE
            and org.status == Organization.Status.ACTIVE
            and org.name == ORGANIZATION_NAME
            and org.created_by_id == TARGET_USER_ID
            and len(org_memberships) == 1
            and org_memberships[0].pk == membership.pk
        )
        if not inequívoco:
            self.stdout.write(f"RESULT: {ERR_ROLLBACK_REVIEW}")
            raise CommandError(ERR_ROLLBACK_REVIEW)

        with transaction.atomic():
            membership.delete()
            org.delete()

        after = collect_root_counts(TARGET_USER_ID)
        changed = sum(after[k] - before[k] for k in before)
        _org, ctx = resolver_organization(user)
        self.stdout.write(f"CURRENT_CONTEXT: {ctx}")
        self.stdout.write(f"MODULE_DATA_CHANGES: {changed}")
        self.stdout.write(f"RESULT: {RESULT_ROLLED_BACK}")
        if changed != 0:
            raise CommandError("MODULE_DATA_CHANGED")
