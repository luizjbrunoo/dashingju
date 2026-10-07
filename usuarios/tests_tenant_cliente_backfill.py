from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from organizacoes.models import Membership, Organization
from organizacoes.services import (
    CONTEXT_AMBIGUOUS,
    CONTEXT_NONE,
    CONTEXT_RESOLVED,
    resolver_organization,
)
from usuarios.management.commands.backfill_cliente_organization import (
    ALREADY_ASSIGNED,
    AMBIGUOUS_ORGANIZATION,
    BACKFILL_ELIGIBLE,
    CONFLICT_EXISTING_ORGANIZATION,
    ERR_USAGE,
    INACTIVE_MEMBERSHIP,
    INACTIVE_ORGANIZATION,
    NO_ORGANIZATION,
    RESULT_APPLIED,
    RESULT_NOTHING,
    RESULT_READY,
    RESULT_ROLLBACK_NOT_SAFE,
    ResolverCache,
    classificar_cliente,
    construir_relatorio,
    snapshot_campos,
)
from usuarios.models import Cliente


def _run(*flags):
    out = StringIO()
    err = StringIO()
    call_command("backfill_cliente_organization", *flags, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _snapshot_all():
    return {
        c.pk: (snapshot_campos(c), c.organization_id)
        for c in Cliente.objects.order_by("pk")
    }


class BackfillClienteOrganizationCommandTests(TestCase):
    def _user(self, username, **kwargs):
        return User.objects.create_user(username=username, password="senha123", **kwargs)

    def _org(self, name, *, created_by=None, status=Organization.Status.ACTIVE):
        return Organization.objects.create(
            name=name, created_by=created_by, status=status
        )

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

    def _cliente(self, user, *, nome="Cli", email=None, organization=None, **kwargs):
        email = email or f"{username_safe(user.username)}.{nome.replace(' ', '')}@ex.test"
        return Cliente.objects.create(
            user=user,
            nome=nome,
            email=email,
            organization=organization,
            **kwargs,
        )

    def test_sem_modo_falha(self):
        with self.assertRaises(CommandError) as ctx:
            _run()
        self.assertIn(ERR_USAGE, str(ctx.exception))

    def test_multiplos_modos_falha(self):
        with self.assertRaises(CommandError) as ctx:
            _run("--dry-run", "--apply")
        self.assertIn(ERR_USAGE, str(ctx.exception))

    def test_dry_run_zero_writes(self):
        user = self._user("dry_user")
        org = self._org("Org Dry")
        self._membership(user, org)
        cli = self._cliente(user, nome="SECRET_PII_NOME_XYZ", email="pii.secret@ex.test")
        antes = _snapshot_all()
        out, _ = _run("--dry-run")
        self.assertIn("MODE: DRY-RUN", out)
        self.assertIn(RESULT_READY, out)
        self.assertIn(BACKFILL_ELIGIBLE, out)
        self.assertNotIn("SECRET_PII_NOME_XYZ", out)
        self.assertNotIn("pii.secret@ex.test", out)
        depois = _snapshot_all()
        self.assertEqual(antes, depois)
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)

    def test_eligible_uma_membership_ativa(self):
        user = self._user("elig")
        org = self._org("Org Elig")
        self._membership(user, org)
        cli = self._cliente(user)
        org_res, ctx = resolver_organization(user)
        self.assertEqual(ctx, CONTEXT_RESOLVED)
        self.assertEqual(org_res.pk, org.pk)
        row = classificar_cliente(cli, ResolverCache())
        self.assertEqual(row.classification, BACKFILL_ELIGIBLE)
        self.assertEqual(row.resolved_organization_id, org.pk)

    def test_apply_preenche_somente_organization(self):
        user = self._user("apply_ok")
        org = self._org("Org Apply")
        self._membership(user, org)
        cli = self._cliente(
            user, nome="Apply Nome", status="ativo", origem="indicacao"
        )
        user_id = cli.user_id
        criado = cli.criado_em
        out, _ = _run("--apply")
        self.assertIn(RESULT_APPLIED, out)
        self.assertIn("UPDATED: 1", out)
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)
        self.assertEqual(cli.user_id, user_id)
        self.assertEqual(cli.nome, "Apply Nome")
        self.assertEqual(cli.status, "ativo")
        self.assertEqual(cli.origem, "indicacao")
        self.assertEqual(cli.criado_em, criado)

    def test_user_sem_membership_permanece_null(self):
        user = self._user("sem_mem")
        cli = self._cliente(user)
        self.assertEqual(resolver_organization(user)[1], CONTEXT_NONE)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertEqual(
            classificar_cliente(cli, ResolverCache()).classification,
            NO_ORGANIZATION,
        )

    def test_ambiguous_permanece_null(self):
        user = self._user("amb")
        org_a = self._org("Org Amb A")
        org_b = self._org("Org Amb B")
        self._membership(user, org_a)
        self._membership(user, org_b)
        cli = self._cliente(user)
        self.assertEqual(resolver_organization(user)[1], CONTEXT_AMBIGUOUS)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertEqual(
            classificar_cliente(cli, ResolverCache()).classification,
            AMBIGUOUS_ORGANIZATION,
        )

    def test_membership_inativa_permanece_null(self):
        user = self._user("mem_inativa")
        org = self._org("Org Mem Inativa")
        self._membership(user, org, status=Membership.Status.INACTIVE)
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertEqual(
            classificar_cliente(cli, ResolverCache()).classification,
            INACTIVE_MEMBERSHIP,
        )

    def test_organization_inativa_permanece_null(self):
        user = self._user("org_inativa")
        org = self._org("Org Inativa", status=Organization.Status.INACTIVE)
        self._membership(user, org)
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertEqual(
            classificar_cliente(cli, ResolverCache()).classification,
            INACTIVE_ORGANIZATION,
        )

    def test_already_assigned_matching_nao_sobrescreve(self):
        user = self._user("already")
        org = self._org("Org Already")
        self._membership(user, org)
        cli = self._cliente(user, organization=org)
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)
        self.assertEqual(
            classificar_cliente(cli, ResolverCache()).classification,
            ALREADY_ASSIGNED,
        )

    def test_conflito_nao_sobrescreve(self):
        user = self._user("conflict")
        org_a = self._org("Org A conf")
        org_b = self._org("Org B conf")
        self._membership(user, org_a)
        cli = self._cliente(user, organization=org_b)
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org_b.pk)
        self.assertEqual(
            classificar_cliente(cli, ResolverCache()).classification,
            CONFLICT_EXISTING_ORGANIZATION,
        )

    def test_segundo_apply_zero_alteracoes(self):
        user = self._user("idemp")
        org = self._org("Org Idemp")
        self._membership(user, org)
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)
        out, _ = _run("--apply")
        self.assertIn("UPDATED: 0", out)
        self.assertIn(RESULT_NOTHING, out)
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)

    def test_dry_run_apos_apply_zero_eligible(self):
        user = self._user("dry_after")
        org = self._org("Org Dry After")
        self._membership(user, org)
        self._cliente(user)
        _run("--apply")
        out, _ = _run("--dry-run")
        self.assertIn(f"{BACKFILL_ELIGIBLE}: 0", out)
        self.assertIn(RESULT_NOTHING, out)
        self.assertIn(f"{ALREADY_ASSIGNED}: 1", out)

    def test_nao_usa_primeira_organization_global(self):
        primeira = self._org("Primeira Global")
        user = self._user("sem_vinculo")
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertTrue(Organization.objects.filter(pk=primeira.pk).exists())

    def test_superuser_sem_membership_nao_recebe_tenant(self):
        user = self._user("super_sem", is_superuser=True, is_staff=True)
        org = self._org("Org Super Ignorada")
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertTrue(Organization.objects.filter(pk=org.pk).exists())

    def test_group_nao_influencia(self):
        dono = self._user("dono_grupo")
        outro = self._user("membro_grupo")
        org = self._org("Org Dono")
        self._membership(dono, org)
        grupo, _ = Group.objects.get_or_create(name="Agenda — acesso completo")
        outro.groups.add(grupo)
        cli = self._cliente(outro)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)

    def test_created_by_nao_influencia(self):
        user = self._user("criador")
        org = self._org("Org Created By", created_by=user)
        cli = self._cliente(user)
        self.assertEqual(resolver_organization(user)[1], CONTEXT_NONE)
        _run("--apply")
        cli.refresh_from_db()
        self.assertIsNone(cli.organization_id)
        self.assertEqual(org.created_by_id, user.pk)

    def test_cliente_legado_sem_org_continua_valido(self):
        user = self._user("legado")
        cli = self._cliente(user, nome="Legado Ok")
        self.assertIsNone(cli.organization_id)
        self.client.force_login(user)
        lista = self.client.get(reverse("clientes"))
        self.assertEqual(lista.status_code, 200)
        self.assertNotContains(lista, "Legado Ok")
        detail = self.client.get(reverse("cliente", kwargs={"id": cli.pk}))
        self.assertEqual(detail.status_code, 404)

    def test_rollback_matching_recusa_sem_prova_de_origem(self):
        user = self._user("rb_match")
        org = self._org("Org Rb Match")
        self._membership(user, org)
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)
        with self.assertRaises(CommandError) as ctx:
            _run("--rollback")
        self.assertIn(RESULT_ROLLBACK_NOT_SAFE, str(ctx.exception))
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)

    def test_rollback_recusa_caso_inseguro(self):
        user = self._user("rb_unsafe")
        org = self._org("Org Rb Unsafe")
        self._membership(user, org)
        cli = self._cliente(user, organization=org)
        with self.assertRaises(CommandError) as ctx:
            _run("--rollback")
        self.assertIn(RESULT_ROLLBACK_NOT_SAFE, str(ctx.exception))
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)

    def test_rollback_nao_limpa_associacao_conflitante(self):
        user = self._user("rb_conf")
        org_a = self._org("Org Rb A")
        org_b = self._org("Org Rb B")
        self._membership(user, org_a)
        cli = self._cliente(user, organization=org_b)
        with self.assertRaises(CommandError):
            _run("--rollback")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org_b.pk)

    def test_apply_nao_altera_outros_campos(self):
        user = self._user("campos")
        org = self._org("Org Campos")
        self._membership(user, org)
        cli = self._cliente(user, nome="Nome Fixo", telefone="11999999999")
        antes = snapshot_campos(cli)
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(snapshot_campos(cli), antes)
        self.assertEqual(cli.organization_id, org.pk)

    def test_output_sem_pii(self):
        user = self._user("pii_user")
        org = self._org("Org PII")
        self._membership(user, org)
        self._cliente(
            user,
            nome="Maria Silva PII",
            email="maria.silva.pii@escritorio.test",
            telefone="11987654321",
        )
        out, _ = _run("--dry-run")
        self.assertNotIn("Maria Silva PII", out)
        self.assertNotIn("maria.silva.pii@escritorio.test", out)
        self.assertNotIn("11987654321", out)

    def test_resolver_permanece_canonico(self):
        user = self._user("canon")
        org_a = self._org("Org Canon A")
        org_b = self._org("Org Canon B")
        self._membership(user, org_a)
        self._membership(user, org_b, status=Membership.Status.INACTIVE)
        org_res, ctx = resolver_organization(user)
        self.assertEqual(ctx, CONTEXT_RESOLVED)
        self.assertEqual(org_res.pk, org_a.pk)
        cli = self._cliente(user)
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org_a.pk)
        self.assertNotEqual(cli.organization_id, org_b.pk)

    def test_member_mesma_org_nao_ganha_acesso_apos_apply(self):
        org = self._org("Org Shared")
        owner = self._user("owner_cut")
        member = self._user("member_cut")
        self._membership(owner, org, role=Membership.Role.OWNER)
        self._membership(member, org, role=Membership.Role.MEMBER)
        cli = self._cliente(owner, nome="Do Owner Cut")
        _run("--apply")
        cli.refresh_from_db()
        self.assertEqual(cli.organization_id, org.pk)
        self.client.force_login(member)
        lista = self.client.get(reverse("clientes"))
        self.assertContains(lista, "Do Owner Cut")
        detail = self.client.get(reverse("cliente", kwargs={"id": cli.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["detail_access_mode"], "full")

    def test_http_create_apos_apply_nao_altera_legado(self):
        user = self._user("http_after")
        org = self._org("Org Http After")
        self._membership(user, org)
        legado = self._cliente(user, nome="Ja Existente")
        _run("--apply")
        legado.refresh_from_db()
        self.assertEqual(legado.organization_id, org.pk)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("clientes"),
            {
                "nome": "Novo HTTP",
                "email": "novo.http@ex.test",
                "tipo": "PF",
                "status": "em_prospeccao",
            },
        )
        self.assertEqual(resp.status_code, 302)
        novo = Cliente.objects.get(email="novo.http@ex.test")
        self.assertEqual(novo.user_id, user.pk)
        self.assertEqual(novo.organization_id, org.pk)
        legado.refresh_from_db()
        self.assertEqual(legado.organization_id, org.pk)

    def test_relatorio_contagens(self):
        elig = self._user("rel_elig")
        none_u = self._user("rel_none")
        org = self._org("Org Rel")
        self._membership(elig, org)
        self._cliente(elig)
        self._cliente(none_u)
        rel = construir_relatorio(list(Cliente.objects.select_related("user")))
        self.assertEqual(rel.counts[BACKFILL_ELIGIBLE], 1)
        self.assertEqual(rel.counts[NO_ORGANIZATION], 1)
        self.assertEqual(rel.rows_to_update, 1)


def username_safe(username: str) -> str:
    return username.replace(" ", "_").lower()
