"""FIN-EXPAND: schema nullable Organization. Sem dual-write. Sem backfill."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import PROTECT
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.models import (
    Banco,
    Categoria,
    Cobranca,
    CobrancaHistorico,
    CobrancaRecebimento,
    Contrato,
    Movimento,
)
from financeiro.tests_helpers import grant_caixa_permissions
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente


class FinExpandSchemaTests(TestCase):
    MODELS_COM_ORG = (
        Banco,
        Categoria,
        Movimento,
        Contrato,
        Cobranca,
        CobrancaRecebimento,
    )

    def _org_field(self, model):
        return model._meta.get_field("organization")

    def test_seis_models_possuem_organization_nullable(self):
        for model in self.MODELS_COM_ORG:
            field = self._org_field(model)
            self.assertTrue(field.null, model.__name__)
            self.assertTrue(field.blank, model.__name__)

    def test_on_delete_protect(self):
        for model in self.MODELS_COM_ORG:
            self.assertIs(
                self._org_field(model).remote_field.on_delete,
                PROTECT,
                model.__name__,
            )

    def test_db_index_simples(self):
        for model in self.MODELS_COM_ORG:
            self.assertTrue(self._org_field(model).db_index, model.__name__)

    def test_related_model_organization(self):
        for model in self.MODELS_COM_ORG:
            self.assertEqual(
                self._org_field(model).related_model,
                Organization,
                model.__name__,
            )

    def test_historico_nao_possui_organization(self):
        names = {f.name for f in CobrancaHistorico._meta.get_fields()}
        self.assertNotIn("organization", names)

    def test_usuario_permanece_nos_seis(self):
        for model in self.MODELS_COM_ORG:
            self.assertIsNotNone(model._meta.get_field("usuario"), model.__name__)

    def test_actor_fields_permanecem(self):
        self.assertIsNotNone(Contrato._meta.get_field("criado_por"))
        self.assertIsNotNone(Cobranca._meta.get_field("criado_por"))
        self.assertIsNotNone(CobrancaRecebimento._meta.get_field("registrado_por"))
        self.assertIsNotNone(CobrancaHistorico._meta.get_field("autor"))

    def test_responsavel_permanece(self):
        self.assertIsNotNone(Contrato._meta.get_field("responsavel"))
        self.assertIsNotNone(Cobranca._meta.get_field("responsavel"))

    def test_constraints_antigas_permanecem(self):
        cat_names = {c.name for c in Categoria._meta.constraints}
        self.assertIn("uniq_financeiro_categoria_usuario_nome_tipo", cat_names)
        ctr_names = {c.name for c in Contrato._meta.constraints}
        self.assertIn("uniq_financeiro_contrato_usuario_referencia", ctr_names)

    def test_nenhuma_constraint_organization(self):
        for model in self.MODELS_COM_ORG:
            for constraint in model._meta.constraints:
                self.assertNotIn(
                    "organization",
                    getattr(constraint, "fields", ()),
                    model.__name__,
                )


class FinExpandAntiDualWriteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fin_expand", password="senha123")
        self.org = Organization.objects.create(name="Org Expand")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        self.cliente = Cliente.objects.create(
            user=self.user,
            nome="Cliente Expand",
            email="expand@example.com",
            organization=self.org,
        )
        grant_caixa_permissions(self.user)

    def test_banco_create_orm_organization_null(self):
        banco = Banco.objects.create(usuario=self.user, nome="Banco Expand")
        self.assertIsNone(banco.organization_id)

    def test_categoria_create_orm_organization_null(self):
        cat = Categoria.objects.create(
            usuario=self.user, nome="Honorários", tipo=Categoria.Tipo.RECEITA
        )
        self.assertIsNone(cat.organization_id)

    def test_movimento_create_orm_organization_null(self):
        banco = Banco.objects.create(usuario=self.user, nome="Caixa")
        cat = Categoria.objects.create(
            usuario=self.user, nome="Receita", tipo=Categoria.Tipo.RECEITA
        )
        mov = Movimento.objects.create(
            usuario=self.user,
            banco=banco,
            categoria=cat,
            valor=Decimal("10.00"),
            data=timezone.localdate(),
        )
        self.assertIsNone(mov.organization_id)

    def test_contrato_create_orm_organization_null(self):
        contrato = Contrato.objects.create(
            usuario=self.user,
            cliente=self.cliente,
            referencia="EXP-1",
            descricao="Contrato expand",
            valor_total=Decimal("100.00"),
        )
        self.assertIsNone(contrato.organization_id)

    def test_cobranca_create_orm_organization_null(self):
        cob = Cobranca.objects.create(
            usuario=self.user,
            cliente=self.cliente,
            descricao="Honorários expand",
            valor_original=Decimal("50.00"),
            data_vencimento=timezone.localdate(),
        )
        self.assertIsNone(cob.organization_id)

    def test_recebimento_create_orm_organization_null(self):
        cob = Cobranca.objects.create(
            usuario=self.user,
            cliente=self.cliente,
            descricao="Honorários rec",
            valor_original=Decimal("50.00"),
            data_vencimento=timezone.localdate(),
        )
        rec = CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=self.user,
            valor=Decimal("10.00"),
            data_recebimento=timezone.localdate(),
        )
        self.assertIsNone(rec.organization_id)

    def test_fluxo_legado_banco_novo_nao_preenche_organization(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Banco Legado Expand", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(response.status_code, 302)
        banco = Banco.objects.get(nome="Banco Legado Expand")
        self.assertEqual(banco.usuario_id, self.user.pk)
        self.assertEqual(banco.organization_id, self.org.pk)

    def test_membership_resolved_nao_preenche_create(self):
        """TenantContext resolved não implica dual-write nesta fase."""
        from organizacoes.services import CONTEXT_RESOLVED, resolver_organization

        org, ctx = resolver_organization(self.user)
        self.assertEqual(ctx, CONTEXT_RESOLVED)
        self.assertEqual(org.pk, self.org.pk)
        banco = Banco.objects.create(usuario=self.user, nome="Ainda Null")
        self.assertIsNone(banco.organization_id)
