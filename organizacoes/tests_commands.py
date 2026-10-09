from datetime import date, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from comercial.models import AdvGrowthScoreSnapshot, ComercialAuditLog, MetaComercial
from financeiro.models import Banco, Categoria, Cobranca, Contrato, Movimento
from marketing.models import ContentIdea, ContentProfile, MarketingIntegracao
from marketing.choices import PlataformaMarketing
from organizacoes.management.commands.provision_demo_tenant import (
    ERR_AMBIGUOUS,
    ERR_CREATED_BY,
    ERR_EXISTING_MEMBERSHIP,
    ERR_INACTIVE_MEMBERSHIP,
    ERR_INACTIVE_ORG,
    ERR_ORPHAN_DEMO,
    ERR_ROLLBACK_REVIEW,
    ERR_USAGE,
    ERR_USER_INACTIVE,
    ERR_USER_NOT_FOUND,
    ORGANIZATION_NAME,
    RESULT_ALREADY,
    RESULT_NOTHING_ROLLBACK,
    RESULT_PROVISIONED,
    RESULT_READY,
    RESULT_ROLLED_BACK,
    TARGET_USER_ID,
    collect_root_counts,
)
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_NONE, CONTEXT_RESOLVED, resolver_organization
from usuarios.models import Cliente, Compromisso, Tarefa


def _run(*flags):
    out = StringIO()
    err = StringIO()
    call_command("provision_demo_tenant", *flags, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class ProvisionDemoTenantCommandTests(TestCase):
    def _user1(self, **kwargs):
        opts = {
            "username": "demo_user_1",
            "password": "senha123",
            "id": TARGET_USER_ID,
            "is_active": True,
        }
        opts.update(kwargs)
        return User.objects.create_user(**opts)

    def _user(self, pk, username):
        return User.objects.create_user(
            username=username, password="senha123", id=pk, is_active=True
        )

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

    def _seed_roots(self, user):
        cliente = Cliente.objects.create(
            nome="Cliente teste roots",
            email="roots@example.com",
            user=user,
        )
        agora = timezone.now()
        Compromisso.objects.create(
            user=user, titulo="Compromisso teste", data_hora=agora
        )
        Tarefa.objects.create(user=user, titulo="Tarefa teste")
        banco = Banco.objects.create(usuario=user, nome="Banco teste")
        categoria = Categoria.objects.create(
            usuario=user, nome="Honorarios", tipo=Categoria.Tipo.RECEITA
        )
        Movimento.objects.create(
            usuario=user,
            banco=banco,
            categoria=categoria,
            valor="10.00",
            data=date.today(),
        )
        Contrato.objects.create(
            usuario=user,
            cliente=cliente,
            referencia="CTR-TEST",
            descricao="Contrato teste",
            valor_total="100.00",
        )
        Cobranca.objects.create(
            usuario=user,
            cliente=cliente,
            descricao="Cobranca teste",
            valor_original="50.00",
            data_vencimento=date.today(),
        )
        MetaComercial.objects.create(
            usuario=user,
            ano=date.today().year,
            meta_anual="1200.00",
            meta_mensal="100.00",
            vigencia_inicio=date.today(),
            vigencia_fim=date.today() + timedelta(days=30),
        )
        ComercialAuditLog.objects.create(usuario=user, acao=ComercialAuditLog.ACAO_META_CRIADA)
        AdvGrowthScoreSnapshot.objects.create(
            usuario=user,
            data_ref=date.today(),
            score=10,
            aquisicao=1,
            atendimento=1,
            conversao=1,
            gestao=1,
            financeiro=1,
            dados=1,
        )
        ContentProfile.objects.create(usuario=user)
        ContentIdea.objects.create(usuario=user, titulo="Ideia teste")
        MarketingIntegracao.objects.create(
            usuario=user, plataforma=PlataformaMarketing.INSTAGRAM
        )
        return collect_root_counts(user.pk)

    def test_dry_run_nao_escreve(self):
        self._user1()
        antes_org = Organization.objects.count()
        antes_mem = Membership.objects.count()
        out, _ = _run("--dry-run")
        self.assertIn(RESULT_READY, out)
        self.assertIn("MODE: DRY-RUN", out)
        self.assertIn(ORGANIZATION_NAME, out)
        self.assertEqual(Organization.objects.count(), antes_org)
        self.assertEqual(Membership.objects.count(), antes_mem)

    def test_apply_cria_org_e_membership(self):
        user = self._user1()
        out, _ = _run("--apply")
        self.assertIn(RESULT_PROVISIONED, out)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)
        org = Organization.objects.get()
        self.assertEqual(org.name, ORGANIZATION_NAME)
        self.assertEqual(org.status, Organization.Status.ACTIVE)
        self.assertEqual(org.created_by_id, user.pk)
        mem = Membership.objects.get()
        self.assertEqual(mem.user_id, user.pk)
        self.assertEqual(mem.organization_id, org.pk)
        self.assertEqual(mem.role, Membership.Role.OWNER)
        self.assertEqual(mem.status, Membership.Status.ACTIVE)
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Comercial — acesso completo").exists())
        self.assertTrue(user.groups.filter(name="Marketing — acesso completo").exists())
        self.assertIn("comercial.view_dashboard", user.get_all_permissions())
        self.assertIn("marketing.view_marketing", user.get_all_permissions())
        self.assertIn(
            "marketing.manage_integracoes_marketing", user.get_all_permissions()
        )
        self.assertIn("usuarios.view_documentos", user.get_all_permissions())
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    def test_resolver_apos_apply(self):
        user = self._user1()
        _run("--apply")
        org, ctx = resolver_organization(user)
        self.assertEqual(ctx, CONTEXT_RESOLVED)
        self.assertEqual(org.name, ORGANIZATION_NAME)

    def test_apply_idempotente(self):
        self._user1()
        _run("--apply")
        out, _ = _run("--apply")
        self.assertIn(RESULT_ALREADY, out)
        self.assertIn("OWNER_CAPABILITIES:", out)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)
        user = User.objects.get(pk=TARGET_USER_ID)
        self.assertIn("comercial.view_dashboard", user.get_all_permissions())

    def test_outro_user_intacto(self):
        self._user1()
        user4 = self._user(4, "demo_user_4")
        _run("--apply")
        self.assertEqual(user4.organization_memberships.count(), 0)
        org, ctx = resolver_organization(user4)
        self.assertIsNone(org)
        self.assertEqual(ctx, CONTEXT_NONE)

    def test_membership_conflitante(self):
        user = self._user1()
        outra = self._org("Outra Org")
        self._membership(user, outra)
        antes_org = Organization.objects.count()
        antes_mem = Membership.objects.count()
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_EXISTING_MEMBERSHIP, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), antes_org)
        self.assertEqual(Membership.objects.count(), antes_mem)

    def test_ambiguous_aborta(self):
        user = self._user1()
        self._membership(user, self._org("Org A"))
        self._membership(user, self._org("Org B"))
        antes_org = Organization.objects.count()
        antes_mem = Membership.objects.count()
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_AMBIGUOUS, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), antes_org)
        self.assertEqual(Membership.objects.count(), antes_mem)

    def test_org_demo_orfa_nao_conecta(self):
        self._user1()
        self._org(ORGANIZATION_NAME)
        antes_org = Organization.objects.count()
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_ORPHAN_DEMO, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), antes_org)
        self.assertEqual(Membership.objects.count(), 0)

    def test_created_by_nao_e_ownership(self):
        user = self._user1()
        self._org("Org criada sem vinculo", created_by=user)
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_CREATED_BY, str(ctx.exception))
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(Organization.objects.count(), 1)

    def test_membership_inativa_nao_reativa(self):
        user = self._user1()
        org = self._org("Org inativa vinculo")
        self._membership(user, org, status=Membership.Status.INACTIVE)
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_INACTIVE_MEMBERSHIP, str(ctx.exception))
        mem = Membership.objects.get()
        self.assertEqual(mem.status, Membership.Status.INACTIVE)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)

    def test_org_inativa_nao_reativa(self):
        user = self._user1()
        org = self._org("Org morta", status=Organization.Status.INACTIVE)
        self._membership(user, org)
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_INACTIVE_ORG, str(ctx.exception))
        org.refresh_from_db()
        self.assertEqual(org.status, Organization.Status.INACTIVE)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)

    def test_user_ausente(self):
        self.assertFalse(User.objects.filter(pk=TARGET_USER_ID).exists())
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_USER_NOT_FOUND, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_user_inativo(self):
        self._user1(is_active=False)
        with self.assertRaises(CommandError) as ctx:
            _run("--apply")
        self.assertIn(ERR_USER_INACTIVE, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_rollback_remove_somente_demo(self):
        user = self._user1()
        _run("--apply")
        out, _ = _run("--rollback")
        self.assertIn(RESULT_ROLLED_BACK, out)
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        org, ctx = resolver_organization(user)
        self.assertIsNone(org)
        self.assertEqual(ctx, CONTEXT_NONE)

    def test_rollback_idempotente(self):
        self._user1()
        out, _ = _run("--rollback")
        self.assertIn(RESULT_NOTHING_ROLLBACK, out)
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_rollback_com_outra_membership(self):
        user = self._user1()
        outro = self._user(4, "outro_membro")
        _run("--apply")
        org = Organization.objects.get()
        self._membership(outro, org, role=Membership.Role.MEMBER)
        antes_org = Organization.objects.count()
        antes_mem = Membership.objects.count()
        with self.assertRaises(CommandError) as ctx:
            _run("--rollback")
        self.assertIn(ERR_ROLLBACK_REVIEW, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), antes_org)
        self.assertEqual(Membership.objects.count(), antes_mem)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_modos_invalidos(self):
        self._user1()
        with self.assertRaises(CommandError) as ctx:
            _run()
        self.assertIn(ERR_USAGE, str(ctx.exception))
        with self.assertRaises(CommandError) as ctx:
            _run("--dry-run", "--apply")
        self.assertIn(ERR_USAGE, str(ctx.exception))
        with self.assertRaises(CommandError) as ctx:
            _run("--apply", "--rollback")
        self.assertIn(ERR_USAGE, str(ctx.exception))
        with self.assertRaises(CommandError) as ctx:
            _run("--dry-run", "--apply", "--rollback")
        self.assertIn(ERR_USAGE, str(ctx.exception))
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_apply_nao_altera_roots(self):
        user = self._user1()
        antes = self._seed_roots(user)
        _run("--apply")
        self.assertEqual(collect_root_counts(user.pk), antes)

    def test_rollback_nao_apaga_modulos(self):
        user = self._user1()
        antes = self._seed_roots(user)
        _run("--apply")
        _run("--rollback")
        self.assertEqual(collect_root_counts(user.pk), antes)
        self.assertTrue(Cliente.objects.filter(user=user).exists())
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_slug_gerado_pelo_model(self):
        self._user1()
        _run("--apply")
        org = Organization.objects.get()
        self.assertTrue(org.slug)
        self.assertNotEqual(org.slug, "")
