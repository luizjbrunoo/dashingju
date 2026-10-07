"""FIN-BACKFILL-PREP: dry-run/apply de organization no Financeiro (test DB)."""

from decimal import Decimal
from io import StringIO
from inspect import getsource

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from financeiro.management.commands.backfill_financeiro_organization import (
    ALREADY_ASSIGNED,
    AMBIGUOUS,
    ELIGIBLE,
    ERR_USAGE,
    NO_ORGANIZATION,
    ORPHAN,
    PARENT_CONFLICT,
    RESULT_ABORTED,
    RESULT_APPLIED,
    RESULT_NOTHING,
    SRC_CLIENT_ORGANIZATION,
    SRC_MEMBERSHIP_UNIQUE,
    USER_ORG_CONFLICT,
    Command,
    classificar_movimento,
    classificar_recebimento,
    construir_plano,
)
from financeiro.models import (
    Banco,
    Categoria,
    Cobranca,
    CobrancaHistorico,
    CobrancaRecebimento,
    Contrato,
    Movimento,
)
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente


def _run(*flags):
    out = StringIO()
    err = StringIO()
    call_command("backfill_financeiro_organization", *flags, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class FinBackfillHelpers(TestCase):
    def _user(self, username):
        return User.objects.create_user(username=username, password="senha123")

    def _org(self, name, *, status=Organization.Status.ACTIVE):
        return Organization.objects.create(name=name, status=status)

    def _membership(
        self,
        user,
        org,
        *,
        role=Membership.Role.OWNER,
        status=Membership.Status.ACTIVE,
    ):
        return Membership.objects.create(
            user=user, organization=org, role=role, status=status
        )

    def _cliente(self, user, *, nome="Cli", organization=None):
        return Cliente.objects.create(
            user=user,
            nome=nome,
            email=f"{user.username}.{nome.replace(' ', '')}@ex.test",
            organization=organization,
        )

    def _banco(self, user, *, nome="Banco", organization=None):
        return Banco.objects.create(
            usuario=user, nome=nome, organization=organization
        )

    def _categoria(self, user, *, nome="Cat", organization=None, tipo=None):
        return Categoria.objects.create(
            usuario=user,
            nome=nome,
            tipo=tipo or Categoria.Tipo.RECEITA,
            organization=organization,
        )

    def _movimento(self, user, banco, categoria, *, organization=None, valor="10.00"):
        return Movimento.objects.create(
            usuario=user,
            banco=banco,
            categoria=categoria,
            valor=Decimal(valor),
            data=timezone.localdate(),
            organization=organization,
        )

    def _contrato(
        self,
        user,
        cliente,
        *,
        referencia="CTR-1",
        organization=None,
        criado_por=None,
        responsavel=None,
    ):
        return Contrato.objects.create(
            usuario=user,
            cliente=cliente,
            referencia=referencia,
            descricao="Contrato teste",
            valor_total=Decimal("100.00"),
            organization=organization,
            criado_por=criado_por,
            responsavel=responsavel,
        )

    def _cobranca(
        self,
        user,
        cliente,
        *,
        contrato=None,
        organization=None,
        criado_por=None,
        responsavel=None,
        descricao="Honorarios",
    ):
        return Cobranca.objects.create(
            usuario=user,
            cliente=cliente,
            contrato=contrato,
            descricao=descricao,
            valor_original=Decimal("50.00"),
            data_vencimento=timezone.localdate(),
            organization=organization,
            criado_por=criado_por,
            responsavel=responsavel,
        )

    def _recebimento(
        self,
        user,
        cobranca,
        *,
        movimento=None,
        organization=None,
        registrado_por=None,
    ):
        return CobrancaRecebimento.objects.create(
            cobranca=cobranca,
            usuario=user,
            valor=Decimal("10.00"),
            data_recebimento=timezone.localdate(),
            movimento=movimento,
            organization=organization,
            registrado_por=registrado_por,
        )

    def _row(self, model, pk):
        rel = construir_plano()
        for row in rel.rows:
            if row.model == model and row.pk == pk:
                return row
        self.fail(f"linha ausente: {model} {pk}")

    def _snapshot_invariants(self):
        return {
            "banco": list(Banco.objects.values_list("pk", "usuario_id", "organization_id")),
            "categoria": list(
                Categoria.objects.values_list("pk", "usuario_id", "organization_id")
            ),
            "movimento": list(
                Movimento.objects.values_list("pk", "usuario_id", "organization_id")
            ),
            "contrato": list(
                Contrato.objects.values_list(
                    "pk", "usuario_id", "organization_id", "criado_por_id", "responsavel_id"
                )
            ),
            "cobranca": list(
                Cobranca.objects.values_list(
                    "pk", "usuario_id", "organization_id", "criado_por_id", "responsavel_id"
                )
            ),
            "recebimento": list(
                CobrancaRecebimento.objects.values_list(
                    "pk",
                    "usuario_id",
                    "organization_id",
                    "registrado_por_id",
                )
            ),
            "historico": list(
                CobrancaHistorico.objects.values_list("pk", "usuario_id", "autor_id", "acao")
            ),
            "cliente_org": list(Cliente.objects.values_list("pk", "organization_id")),
        }


class FinBackfillFlagsTests(FinBackfillHelpers):
    def test_27_sem_flag_nao_escreve(self):
        user = self._user("noflag")
        org = self._org("Org NoFlag")
        self._membership(user, org)
        banco = self._banco(user)
        antes = self._snapshot_invariants()
        with self.assertRaises(CommandError) as ctx:
            _run()
        self.assertIn(ERR_USAGE, str(ctx.exception))
        self.assertEqual(antes, self._snapshot_invariants())
        banco.refresh_from_db()
        self.assertIsNone(banco.organization_id)

    def test_26_dry_run_e_apply_juntos_nao_escreve(self):
        user = self._user("bothflags")
        org = self._org("Org Both")
        self._membership(user, org)
        banco = self._banco(user)
        antes = self._snapshot_invariants()
        with self.assertRaises(CommandError) as ctx:
            _run("--dry-run", "--apply")
        self.assertIn(ERR_USAGE, str(ctx.exception))
        self.assertEqual(antes, self._snapshot_invariants())
        banco.refresh_from_db()
        self.assertIsNone(banco.organization_id)

    def test_comando_rejeita_organization_forcada(self):
        with self.assertRaises(CommandError):
            _run("--dry-run", "--organization", "1")


class FinBackfillClassificacaoTests(FinBackfillHelpers):
    def test_01_banco_membership_unica_eligible(self):
        user = self._user("b1")
        org = self._org("Org B1")
        self._membership(user, org)
        banco = self._banco(user)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)
        self.assertEqual(row.source, SRC_MEMBERSHIP_UNIQUE)

    def test_02_categoria_membership_unica_eligible(self):
        user = self._user("c1")
        org = self._org("Org C1")
        self._membership(user, org)
        cat = self._categoria(user)
        row = self._row("Categoria", cat.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)

    def test_03_contrato_cliente_organization_eligible(self):
        user = self._user("ct3")
        org = self._org("Org CT3")
        cliente = self._cliente(user, organization=org)
        contrato = self._contrato(user, cliente)
        row = self._row("Contrato", contrato.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)
        self.assertEqual(row.source, SRC_CLIENT_ORGANIZATION)

    def test_04_contrato_parent_e_membership_concordam(self):
        user = self._user("ct4")
        org = self._org("Org CT4")
        self._membership(user, org)
        cliente = self._cliente(user, organization=org)
        contrato = self._contrato(user, cliente)
        row = self._row("Contrato", contrato.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)
        self.assertIn("CLIENT_ORGANIZATION", row.source)

    def test_05_contrato_parent_a_membership_b_user_org_conflict(self):
        user = self._user("ct5")
        org_a = self._org("Org CT5A")
        org_b = self._org("Org CT5B")
        self._membership(user, org_b)
        cliente = self._cliente(user, organization=org_a)
        contrato = self._contrato(user, cliente)
        row = self._row("Contrato", contrato.pk)
        self.assertEqual(row.classification, USER_ORG_CONFLICT)
        self.assertIsNone(contrato.organization_id)
        self.assertCountEqual(row.conflict_org_ids, (org_a.pk, org_b.pk))

    def test_06_contrato_sem_client_org_membership_unica(self):
        user = self._user("ct6")
        org = self._org("Org CT6")
        self._membership(user, org)
        cliente = self._cliente(user, organization=None)
        contrato = self._contrato(user, cliente)
        row = self._row("Contrato", contrato.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)
        self.assertEqual(row.source, SRC_MEMBERSHIP_UNIQUE)

    def test_07_cobranca_client_e_contrato_org_a(self):
        user = self._user("cb7")
        org = self._org("Org CB7")
        self._membership(user, org)
        cliente = self._cliente(user, organization=org)
        contrato = self._contrato(user, cliente)
        cob = self._cobranca(user, cliente, contrato=contrato)
        row = self._row("Cobranca", cob.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)

    def test_08_cobranca_client_a_contrato_b_parent_conflict(self):
        user = self._user("cb8")
        org_a = self._org("Org CB8A")
        org_b = self._org("Org CB8B")
        cliente = self._cliente(user, organization=org_a)
        contrato = self._contrato(user, cliente, organization=org_b)
        cob = self._cobranca(user, cliente, contrato=contrato)
        row = self._row("Cobranca", cob.pk)
        self.assertEqual(row.classification, PARENT_CONFLICT)
        self.assertCountEqual(row.conflict_org_ids, (org_a.pk, org_b.pk))

    def test_09_movimento_banco_e_categoria_org_a(self):
        user = self._user("mv9")
        org = self._org("Org MV9")
        self._membership(user, org)
        banco = self._banco(user, organization=org)
        cat = self._categoria(user, organization=org)
        mov = self._movimento(user, banco, cat)
        row = self._row("Movimento", mov.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)

    def test_10_movimento_banco_a_categoria_b_parent_conflict(self):
        user = self._user("mv10")
        org_a = self._org("Org MV10A")
        org_b = self._org("Org MV10B")
        banco = self._banco(user, organization=org_a)
        cat = self._categoria(user, organization=org_b)
        mov = self._movimento(user, banco, cat)
        row = self._row("Movimento", mov.pk)
        self.assertEqual(row.classification, PARENT_CONFLICT)
        self.assertCountEqual(row.conflict_org_ids, (org_a.pk, org_b.pk))

    def test_11_recebimento_cobranca_org_a(self):
        user = self._user("rc11")
        org = self._org("Org RC11")
        self._membership(user, org)
        cliente = self._cliente(user, organization=org)
        cob = self._cobranca(user, cliente)
        rec = self._recebimento(user, cob)
        row = self._row("CobrancaRecebimento", rec.pk)
        self.assertEqual(row.classification, ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)

    def test_12_recebimento_cobranca_a_movimento_b_parent_conflict(self):
        user = self._user("rc12")
        org_a = self._org("Org RC12A")
        org_b = self._org("Org RC12B")
        cliente = self._cliente(user, organization=org_a)
        cob = self._cobranca(user, cliente, organization=org_a)
        banco = self._banco(user, organization=org_b)
        cat = self._categoria(user, organization=org_b)
        mov = self._movimento(user, banco, cat, organization=org_b)
        rec = self._recebimento(user, cob, movimento=mov)
        row = self._row("CobrancaRecebimento", rec.pk)
        self.assertEqual(row.classification, PARENT_CONFLICT)
        self.assertCountEqual(row.conflict_org_ids, (org_a.pk, org_b.pk))

    def test_13_zero_membership_sem_parent_no_organization(self):
        user = self._user("noorg")
        banco = self._banco(user)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, NO_ORGANIZATION)

    def test_14_multiplas_memberships_sem_parent_ambiguous(self):
        user = self._user("amb")
        org_a = self._org("Org Amb A")
        org_b = self._org("Org Amb B")
        self._membership(user, org_a)
        self._membership(user, org_b)
        banco = self._banco(user)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, AMBIGUOUS)
        self.assertCountEqual(row.conflict_org_ids, (org_a.pk, org_b.pk))

    def test_15_organization_inativa_nao_resolve(self):
        user = self._user("orginativa")
        org = self._org("Org Inativa", status=Organization.Status.INACTIVE)
        self._membership(user, org)
        banco = self._banco(user)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, NO_ORGANIZATION)

    def test_16_membership_inativa_nao_resolve(self):
        user = self._user("meminativa")
        org = self._org("Org Mem Inativa")
        self._membership(user, org, status=Membership.Status.INACTIVE)
        banco = self._banco(user)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, NO_ORGANIZATION)

    def test_17_ja_preenchido_consistente_already_assigned(self):
        user = self._user("already")
        org = self._org("Org Already")
        self._membership(user, org)
        banco = self._banco(user, organization=org)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, ALREADY_ASSIGNED)
        self.assertEqual(row.resolved_organization_id, org.pk)

    def test_18_ja_preenchido_inconsistente_nao_sobrescreve(self):
        user = self._user("incons")
        org_a = self._org("Org Inc A")
        org_b = self._org("Org Inc B")
        self._membership(user, org_a)
        banco = self._banco(user, organization=org_b)
        row = self._row("Banco", banco.pk)
        self.assertEqual(row.classification, USER_ORG_CONFLICT)
        with self.assertRaises(CommandError):
            _run("--apply")
        banco.refresh_from_db()
        self.assertEqual(banco.organization_id, org_b.pk)

    def test_orphan_movimento_sem_parent(self):
        user = self._user("orphan")
        mov = Movimento(
            usuario=user,
            valor=Decimal("1.00"),
            data=timezone.localdate(),
        )
        mov.pk = 999001
        from financeiro.management.commands.backfill_financeiro_organization import (
            MembershipCache,
        )

        row = classificar_movimento(mov, {}, MembershipCache())
        self.assertEqual(row.classification, ORPHAN)

    def test_orphan_recebimento_sem_cobranca(self):
        user = self._user("orphan2")
        rec = CobrancaRecebimento(
            usuario=user,
            valor=Decimal("1.00"),
            data_recebimento=timezone.localdate(),
        )
        rec.pk = 999002
        from financeiro.management.commands.backfill_financeiro_organization import (
            MembershipCache,
        )

        row = classificar_recebimento(rec, {}, MembershipCache())
        self.assertEqual(row.classification, ORPHAN)


class FinBackfillPlanoMemoriaTests(FinBackfillHelpers):
    def test_plano_memoria_movimento_com_parents_null(self):
        user = self._user("plan_mov")
        org = self._org("Org Plan Mov")
        self._membership(user, org)
        banco = self._banco(user)
        cat = self._categoria(user)
        mov = self._movimento(user, banco, cat)
        self.assertIsNone(banco.organization_id)
        self.assertIsNone(cat.organization_id)
        self.assertIsNone(mov.organization_id)
        rel = construir_plano()
        by_pk = {(r.model, r.pk): r for r in rel.rows}
        self.assertEqual(by_pk[("Banco", banco.pk)].classification, ELIGIBLE)
        self.assertEqual(by_pk[("Categoria", cat.pk)].classification, ELIGIBLE)
        self.assertEqual(by_pk[("Movimento", mov.pk)].classification, ELIGIBLE)
        self.assertEqual(by_pk[("Movimento", mov.pk)].resolved_organization_id, org.pk)
        banco.refresh_from_db()
        cat.refresh_from_db()
        mov.refresh_from_db()
        self.assertIsNone(banco.organization_id)
        self.assertIsNone(cat.organization_id)
        self.assertIsNone(mov.organization_id)

    def test_plano_memoria_cobranca_e_recebimento_com_contrato_null(self):
        user = self._user("plan_cob")
        org = self._org("Org Plan Cob")
        self._membership(user, org)
        cliente = self._cliente(user, organization=org)
        contrato = self._contrato(user, cliente)
        cob = self._cobranca(user, cliente, contrato=contrato)
        rec = self._recebimento(user, cob)
        self.assertIsNone(contrato.organization_id)
        self.assertIsNone(cob.organization_id)
        self.assertIsNone(rec.organization_id)
        rel = construir_plano()
        by_pk = {(r.model, r.pk): r for r in rel.rows}
        self.assertEqual(by_pk[("Contrato", contrato.pk)].classification, ELIGIBLE)
        self.assertEqual(by_pk[("Cobranca", cob.pk)].classification, ELIGIBLE)
        self.assertEqual(by_pk[("CobrancaRecebimento", rec.pk)].classification, ELIGIBLE)
        self.assertEqual(by_pk[("Cobranca", cob.pk)].resolved_organization_id, org.pk)
        self.assertEqual(
            by_pk[("CobrancaRecebimento", rec.pk)].resolved_organization_id, org.pk
        )
        contrato.refresh_from_db()
        cob.refresh_from_db()
        rec.refresh_from_db()
        self.assertIsNone(contrato.organization_id)
        self.assertIsNone(cob.organization_id)
        self.assertIsNone(rec.organization_id)


class FinBackfillDryRunApplyTests(FinBackfillHelpers):
    def test_19_dry_run_nao_altera_linhas(self):
        user = self._user("dry")
        org = self._org("Org Dry")
        self._membership(user, org)
        actor = self._user("dry_actor")
        cliente = self._cliente(user, organization=org)
        banco = self._banco(user)
        cat = self._categoria(user)
        mov = self._movimento(user, banco, cat)
        contrato = self._contrato(
            user, cliente, criado_por=actor, responsavel=actor
        )
        cob = self._cobranca(
            user, cliente, contrato=contrato, criado_por=actor, responsavel=actor
        )
        rec = self._recebimento(user, cob, registrado_por=actor)
        hist = CobrancaHistorico.objects.create(
            cobranca=cob,
            usuario=user,
            acao="criada",
            autor=actor,
        )
        antes = self._snapshot_invariants()
        out, _ = _run("--dry-run")
        self.assertIn("MODE: DRY-RUN", out)
        self.assertNotIn(cliente.nome, out)
        self.assertNotIn(cliente.email, out)
        self.assertEqual(antes, self._snapshot_invariants())
        hist.refresh_from_db()
        self.assertEqual(hist.usuario_id, user.pk)
        self.assertEqual(hist.autor_id, actor.pk)

    def test_20_apply_atualiza_somente_eligible(self):
        user = self._user("onlyelig")
        org = self._org("Org OnlyElig")
        self._membership(user, org)
        banco_null = self._banco(user, nome="Null")
        banco_ok = self._banco(user, nome="Ok", organization=org)
        out, _ = _run("--apply")
        self.assertIn(RESULT_APPLIED, out)
        banco_null.refresh_from_db()
        banco_ok.refresh_from_db()
        self.assertEqual(banco_null.organization_id, org.pk)
        self.assertEqual(banco_ok.organization_id, org.pk)

    def test_21_apply_aborta_com_conflito(self):
        user = self._user("abortc")
        org_a = self._org("Org Abort A")
        org_b = self._org("Org Abort B")
        self._membership(user, org_a)
        banco_elig = self._banco(user, nome="Elig")
        cliente = self._cliente(user, organization=org_a)
        self._contrato(user, cliente, organization=org_b)
        antes = banco_elig.organization_id
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(RESULT_ABORTED, str(ctx.exception))
        banco_elig.refresh_from_db()
        self.assertIsNone(antes)
        self.assertIsNone(banco_elig.organization_id)

    def test_22_apply_aborta_com_no_organization(self):
        user_ok = self._user("abortok")
        user_no = self._user("abortno")
        org = self._org("Org Abort No")
        self._membership(user_ok, org)
        banco_ok = self._banco(user_ok)
        banco_no = self._banco(user_no)
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(RESULT_ABORTED, str(ctx.exception))
        banco_ok.refresh_from_db()
        banco_no.refresh_from_db()
        self.assertIsNone(banco_ok.organization_id)
        self.assertIsNone(banco_no.organization_id)

    def test_23_apply_usa_transaction_atomic(self):
        from financeiro.management.commands import backfill_financeiro_organization as cmd

        self.assertIn("transaction.atomic()", getsource(Command._apply))
        self.assertIn("select_for_update", getsource(cmd.carregar_objetos))

    def test_28_cobranca_historico_fora_do_comando(self):
        from financeiro.management.commands import (
            backfill_financeiro_organization as cmd,
        )

        self.assertNotIn("CobrancaHistorico", getsource(cmd))
        user = self._user("hist")
        org = self._org("Org Hist")
        self._membership(user, org)
        cliente = self._cliente(user, organization=org)
        cob = self._cobranca(user, cliente)
        actor = self._user("hist_actor")
        hist = CobrancaHistorico.objects.create(
            cobranca=cob,
            usuario=user,
            acao="criada",
            autor=actor,
            descricao="nao deve mudar",
        )
        _run("--apply")
        hist.refresh_from_db()
        self.assertEqual(hist.usuario_id, user.pk)
        self.assertEqual(hist.autor_id, actor.pk)
        self.assertEqual(hist.descricao, "nao deve mudar")
        self.assertFalse(hasattr(hist, "organization_id"))


class FinBackfillApplyCenarioCompletoTests(FinBackfillHelpers):
    def test_24_25_29_30_31_32_apply_completo_e_idempotente(self):
        user = self._user("full")
        actor = self._user("full_actor")
        org = self._org("Org Full")
        self._membership(user, org)
        cliente = self._cliente(user, organization=org)
        cliente_org = cliente.organization_id
        banco = self._banco(user)
        cat = self._categoria(user)
        mov = self._movimento(user, banco, cat)
        contrato = self._contrato(
            user, cliente, criado_por=actor, responsavel=actor
        )
        cob = self._cobranca(
            user, cliente, contrato=contrato, criado_por=actor, responsavel=actor
        )
        rec = self._recebimento(user, cob, movimento=mov, registrado_por=actor)

        self.assertIsNone(banco.organization_id)
        self.assertIsNone(cat.organization_id)
        self.assertIsNone(mov.organization_id)
        self.assertIsNone(contrato.organization_id)
        self.assertIsNone(cob.organization_id)
        self.assertIsNone(rec.organization_id)

        out, _ = _run("--apply")
        self.assertIn(RESULT_APPLIED, out)
        self.assertIn("UPDATED: 6", out)

        for obj in (banco, cat, mov, contrato, cob, rec):
            obj.refresh_from_db()
            self.assertEqual(obj.organization_id, org.pk)
            self.assertEqual(obj.usuario_id, user.pk)

        contrato.refresh_from_db()
        cob.refresh_from_db()
        rec.refresh_from_db()
        cliente.refresh_from_db()
        self.assertEqual(contrato.criado_por_id, actor.pk)
        self.assertEqual(contrato.responsavel_id, actor.pk)
        self.assertEqual(cob.criado_por_id, actor.pk)
        self.assertEqual(cob.responsavel_id, actor.pk)
        self.assertEqual(rec.registrado_por_id, actor.pk)
        self.assertEqual(cliente.organization_id, cliente_org)

        out2, _ = _run("--apply")
        self.assertIn(RESULT_NOTHING, out2)
        self.assertIn("UPDATED: 0", out2)
        rel = construir_plano()
        self.assertTrue(all(r.classification == ALREADY_ASSIGNED for r in rel.rows))
        self.assertEqual(rel.totals()["PLANNED_UPDATES"], 0)

        for obj in (banco, cat, mov, contrato, cob, rec):
            obj.refresh_from_db()
            self.assertEqual(obj.organization_id, org.pk)
            self.assertEqual(obj.usuario_id, user.pk)

    def test_apply_atomic_nao_escreve_parcial_ao_abortar(self):
        user = self._user("atomic")
        org = self._org("Org Atomic")
        self._membership(user, org)
        banco = self._banco(user)
        self._banco(self._user("atomic_no"))
        sid = transaction.savepoint()
        with self.assertRaises(CommandError):
            _run("--apply")
        transaction.savepoint_rollback(sid)
        banco.refresh_from_db()
        self.assertIsNone(banco.organization_id)
