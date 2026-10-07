"""FIN-ISOLATION — auditoria adversarial de read paths do Financeiro.

W1 Caixa, W2 Billing operacional e W3 Billing analytics: organization-scoped.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.admin import BancoAdmin, CobrancaAdmin, ContratoAdmin
from financeiro.choices import (
    AcaoCobrancaHistorico,
    CategoriaCobranca,
    FormaPagamento,
    StatusCobranca,
    StatusContrato,
)
from financeiro.forms import CobrancaForm, ContratoForm, MovimentoForm
from financeiro.models import (
    Banco,
    Categoria,
    Cobranca,
    CobrancaHistorico,
    CobrancaRecebimento,
    Contrato,
    Movimento,
)
from financeiro.tests_helpers import grant_all_finance_permissions
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente


class FinIsolationAdversarialBase(TestCase):
    """Org A (A1, A2) × Org B (B1) com dataset financeiro equivalente."""

    AUDIT_CURRENT_BEHAVIOR = True

    def setUp(self):
        self.org_a = Organization.objects.create(name="ISO Org A")
        self.org_b = Organization.objects.create(name="ISO Org B")
        self.a1 = self._user("iso_a1")
        self.a2 = self._user("iso_a2")
        self.b1 = self._user("iso_b1")
        self._membership(self.a1, self.org_a)
        self._membership(self.a2, self.org_a)
        self._membership(self.b1, self.org_b)
        hoje = timezone.localdate()
        self.pack_a1 = self._pack(
            self.a1,
            self.org_a,
            "A1",
            saldo_ini=Decimal("1000.00"),
            mov_rec=Decimal("100.00"),
            mov_desp=Decimal("10.00"),
            ctr=Decimal("5000.00"),
            cob=Decimal("1000.00"),
            rec=Decimal("100.00"),
            hoje=hoje,
        )
        self.pack_a2 = self._pack(
            self.a2,
            self.org_a,
            "A2",
            saldo_ini=Decimal("2000.00"),
            mov_rec=Decimal("200.00"),
            mov_desp=Decimal("20.00"),
            ctr=Decimal("6000.00"),
            cob=Decimal("2000.00"),
            rec=Decimal("200.00"),
            hoje=hoje,
        )
        self.pack_b1 = self._pack(
            self.b1,
            self.org_b,
            "B1",
            saldo_ini=Decimal("3000.00"),
            mov_rec=Decimal("300.00"),
            mov_desp=Decimal("30.00"),
            ctr=Decimal("7000.00"),
            cob=Decimal("3000.00"),
            rec=Decimal("300.00"),
            hoje=hoje,
        )

    def _user(self, username):
        user = User.objects.create_user(username=username, password="senha123")
        grant_all_finance_permissions(user)
        return user

    def _membership(self, user, org):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )

    def _pack(
        self,
        user,
        org,
        tag,
        *,
        saldo_ini,
        mov_rec,
        mov_desp,
        ctr,
        cob,
        rec,
        hoje,
    ):
        cli = Cliente.objects.create(
            user=user,
            organization=org,
            nome=f"ISO-CLI-{tag}",
            email=f"iso.{tag.lower()}@ex.test",
        )
        banco = Banco.objects.create(
            usuario=user,
            organization=org,
            nome=f"ISO-BANCO-{tag}",
            agencia="0001",
            conta=f"c-{tag}",
            saldo_inicial=saldo_ini,
        )
        cat_r = Categoria.objects.create(
            usuario=user,
            organization=org,
            nome=f"ISO-CAT-REC-{tag}",
            tipo=Categoria.Tipo.RECEITA,
        )
        cat_d = Categoria.objects.create(
            usuario=user,
            organization=org,
            nome=f"ISO-CAT-DES-{tag}",
            tipo=Categoria.Tipo.DESPESA,
        )
        mov_r = Movimento.objects.create(
            usuario=user,
            organization=org,
            banco=banco,
            categoria=cat_r,
            valor=mov_rec,
            data=hoje,
            descricao=f"ISO-MOV-REC-{tag}",
        )
        mov_d = Movimento.objects.create(
            usuario=user,
            organization=org,
            banco=banco,
            categoria=cat_d,
            valor=mov_desp,
            data=hoje,
            descricao=f"ISO-MOV-DES-{tag}",
        )
        contrato = Contrato.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            referencia=f"ISO-CTR-{tag}",
            descricao=f"Contrato {tag}",
            valor_total=ctr,
            status=StatusContrato.ACTIVE,
            criado_por=user,
            responsavel=user,
        )
        cobranca = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            contrato=contrato,
            contrato_referencia=contrato.referencia,
            descricao=f"ISO-COB-{tag}",
            valor_original=cob,
            data_vencimento=hoje + timedelta(days=10),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PENDING,
            criado_por=user,
            responsavel=user,
        )
        vencida = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            contrato=contrato,
            contrato_referencia=contrato.referencia,
            descricao=f"ISO-COB-VENC-{tag}",
            valor_original=Decimal("50.00"),
            data_vencimento=hoje - timedelta(days=5),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=user,
            responsavel=user,
        )
        recebimento = CobrancaRecebimento.objects.create(
            cobranca=cobranca,
            usuario=user,
            organization=org,
            valor=rec,
            data_recebimento=hoje,
            forma_pagamento=FormaPagamento.PIX,
            registrado_por=user,
        )
        hist = CobrancaHistorico.objects.create(
            cobranca=cobranca,
            usuario=user,
            acao=AcaoCobrancaHistorico.CRIADA,
            descricao=f"ISO-HIST-{tag}",
            autor=user,
        )
        return SimpleNamespace(
            tag=tag,
            user=user,
            org=org,
            cliente=cli,
            banco=banco,
            cat_r=cat_r,
            cat_d=cat_d,
            mov_r=mov_r,
            mov_d=mov_d,
            contrato=contrato,
            cobranca=cobranca,
            vencida=vencida,
            recebimento=recebimento,
            historico=hist,
            markers=(
                f"ISO-BANCO-{tag}",
                f"ISO-CAT-REC-{tag}",
                f"ISO-MOV-REC-{tag}",
                f"ISO-CLI-{tag}",
                f"ISO-CTR-{tag}",
                f"ISO-COB-{tag}",
            ),
        )

    def _login(self, user):
        self.client.force_login(user)

    def _get(self, name, user, **kwargs):
        self._login(user)
        url = reverse(name, kwargs=kwargs) if kwargs else reverse(name)
        return self.client.get(url)

    def _body(self, resp):
        return resp.content.decode("utf-8", errors="replace")

    def _assert_user_scoped_html(self, resp, own_marker, peer_marker, foreign_marker):
        """BILLING legado: vê próprio; NÃO vê same-org peer; NÃO vaza cross-tenant."""
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn(own_marker, body)
        self.assertNotIn(peer_marker, body)
        self.assertNotIn(foreign_marker, body)

    def _assert_org_scoped_html(self, resp, own_marker, peer_marker, foreign_marker):
        """W1: vê próprio + peer same-org; nunca cross-tenant."""
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn(own_marker, body)
        self.assertIn(peer_marker, body)
        self.assertNotIn(foreign_marker, body)

    def _assert_404_for(self, name, actor, **kwargs):
        resp = self._get(name, actor, **kwargs)
        self.assertEqual(resp.status_code, 404)


class FinCutoverW1CaixaTests(FinIsolationAdversarialBase):
    def test_caixa_same_org_members_share_bancos_when_authorized(self):
        resp = self._get("financeiro_banco_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-BANCO-A1", "ISO-BANCO-A2", "ISO-BANCO-B1")
        resp_a2 = self._get("financeiro_banco_listar", self.a2)
        self._assert_org_scoped_html(resp_a2, "ISO-BANCO-A2", "ISO-BANCO-A1", "ISO-BANCO-B1")

    def test_caixa_banco_list_b1_does_not_see_org_a(self):
        resp = self._get("financeiro_banco_listar", self.b1)
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn("ISO-BANCO-B1", body)
        self.assertNotIn("ISO-BANCO-A1", body)
        self.assertNotIn("ISO-BANCO-A2", body)

    def test_caixa_banco_edit_same_org_peer_ok_cross_org_404(self):
        own = self._get("financeiro_banco_editar", self.a1, pk=self.pack_a1.banco.pk)
        peer = self._get("financeiro_banco_editar", self.a1, pk=self.pack_a2.banco.pk)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(peer.status_code, 200)
        self._assert_404_for("financeiro_banco_editar", self.a1, pk=self.pack_b1.banco.pk)
        self._assert_404_for("financeiro_banco_editar", self.b1, pk=self.pack_a1.banco.pk)

    def test_caixa_banco_delete_same_org_peer_ok_cross_org_404(self):
        own = self._get("financeiro_banco_excluir", self.a1, pk=self.pack_a1.banco.pk)
        peer = self._get("financeiro_banco_excluir", self.a1, pk=self.pack_a2.banco.pk)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(peer.status_code, 200)
        self._assert_404_for("financeiro_banco_excluir", self.a1, pk=self.pack_b1.banco.pk)

    def test_caixa_same_org_members_share_categorias_when_authorized(self):
        resp = self._get("financeiro_categoria_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-CAT-REC-A1", "ISO-CAT-REC-A2", "ISO-CAT-REC-B1")

    def test_caixa_categoria_edit_delete_same_org_peer_ok_cross_org_404(self):
        self.assertEqual(
            self._get("financeiro_categoria_editar", self.a1, pk=self.pack_a1.cat_r.pk).status_code,
            200,
        )
        self.assertEqual(
            self._get("financeiro_categoria_editar", self.a1, pk=self.pack_a2.cat_r.pk).status_code,
            200,
        )
        self._assert_404_for("financeiro_categoria_editar", self.a1, pk=self.pack_b1.cat_r.pk)
        self.assertEqual(
            self._get("financeiro_categoria_excluir", self.a1, pk=self.pack_a2.cat_d.pk).status_code,
            200,
        )
        self._assert_404_for("financeiro_categoria_excluir", self.a1, pk=self.pack_b1.cat_d.pk)

    def test_caixa_extrato_same_org_share_sem_b1(self):
        resp = self._get("financeiro_extrato", self.a1)
        self._assert_org_scoped_html(resp, "ISO-MOV-REC-A1", "ISO-MOV-REC-A2", "ISO-MOV-REC-B1")
        body = self._body(resp)
        self.assertIn("ISO-MOV-DES-A1", body)
        self.assertIn("ISO-MOV-DES-A2", body)
        self.assertNotIn("ISO-MOV-DES-B1", body)

    def test_caixa_extrato_filtro_banco_a2_mostra_a2_nao_vaza_b1(self):
        self._login(self.a1)
        resp = self.client.get(
            reverse("financeiro_extrato"),
            {"banco": str(self.pack_a2.banco.pk)},
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn("ISO-MOV-REC-A2", body)
        self.assertNotIn("ISO-MOV-REC-B1", body)

    def test_caixa_extrato_filtro_banco_b1_nao_atravessa_tenant(self):
        self._login(self.a1)
        resp = self.client.get(
            reverse("financeiro_extrato"),
            {"banco": str(self.pack_b1.banco.pk)},
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertNotIn("ISO-MOV-REC-B1", body)
        self.assertNotIn("ISO-MOV-DES-B1", body)

    def test_caixa_movimento_edit_delete_same_org_peer_ok_cross_org_404(self):
        own = self._get("financeiro_movimento_editar", self.a1, pk=self.pack_a1.mov_r.pk)
        peer = self._get("financeiro_movimento_editar", self.a1, pk=self.pack_a2.mov_r.pk)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(peer.status_code, 200)
        self._assert_404_for("financeiro_movimento_editar", self.a1, pk=self.pack_b1.mov_r.pk)
        self._assert_404_for("financeiro_movimento_excluir", self.a1, pk=self.pack_b1.mov_d.pk)
        self.assertEqual(
            self._get("financeiro_movimento_excluir", self.a1, pk=self.pack_a2.mov_d.pk).status_code,
            200,
        )

    def test_caixa_dashboard_agrega_a1_mais_a2_exclui_b1(self):
        resp = self._get("financeiro_dashboard", self.a1)
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn("ISO-BANCO-A1", body)
        self.assertIn("ISO-BANCO-A2", body)
        self.assertNotIn("ISO-BANCO-B1", body)
        self.assertEqual(self.pack_a1.banco.saldo_atual(), Decimal("1090.00"))
        self.assertEqual(self.pack_a2.banco.saldo_atual(), Decimal("2180.00"))
        self.assertEqual(self.pack_b1.banco.saldo_atual(), Decimal("3270.00"))


class FinCutoverW2BillingTests(FinIsolationAdversarialBase):
    def test_contrato_list_same_org_share_sem_b1(self):
        resp = self._get("financeiro_contrato_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-CTR-A1", "ISO-CTR-A2", "ISO-CTR-B1")
        resp_a2 = self._get("financeiro_contrato_listar", self.a2)
        self._assert_org_scoped_html(resp_a2, "ISO-CTR-A2", "ISO-CTR-A1", "ISO-CTR-B1")

    def test_contrato_detail_edit_pk_matrix(self):
        own = self._get("financeiro_contrato_detalhe", self.a1, pk=self.pack_a1.contrato.pk)
        peer = self._get("financeiro_contrato_detalhe", self.a1, pk=self.pack_a2.contrato.pk)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(peer.status_code, 200)
        self.assertIn("ISO-CTR-A1", self._body(own))
        self.assertIn("ISO-CTR-A2", self._body(peer))
        self.assertEqual(
            self._get("financeiro_contrato_editar", self.a1, pk=self.pack_a2.contrato.pk).status_code,
            200,
        )
        self._assert_404_for("financeiro_contrato_detalhe", self.a1, pk=self.pack_b1.contrato.pk)
        self._assert_404_for("financeiro_contrato_editar", self.a1, pk=self.pack_b1.contrato.pk)
        self._assert_404_for("financeiro_contrato_editar", self.b1, pk=self.pack_a1.contrato.pk)
        self._assert_404_for("financeiro_contrato_detalhe", self.b1, pk=self.pack_a1.contrato.pk)

    def test_cobranca_list_same_org_share_kpis_org(self):
        resp = self._get("financeiro_cobranca_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-COB-A1", "ISO-COB-A2", "ISO-COB-B1")
        self.assertEqual(resp.context["kpis"].recebido_mes, Decimal("300.00"))
        self.assertEqual(resp.context["kpis"].a_receber, Decimal("2800.00"))
        self.assertEqual(resp.context["kpis"].vencido, Decimal("100.00"))
        resp_a2 = self._get("financeiro_cobranca_listar", self.a2)
        self.assertEqual(resp_a2.context["kpis"].recebido_mes, Decimal("300.00"))


    def test_cobranca_detail_edit_cancel_pk_matrix(self):
        own = self._get("financeiro_cobranca_detalhe", self.a1, pk=self.pack_a1.cobranca.pk)
        peer = self._get("financeiro_cobranca_detalhe", self.a1, pk=self.pack_a2.cobranca.pk)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(peer.status_code, 200)
        body = self._body(own)
        self.assertIn("ISO-COB-A1", body)
        self.assertNotIn("ISO-COB-B1", body)
        self.assertIn("ISO-COB-A2", self._body(peer))
        self.assertEqual(
            self._get("financeiro_cobranca_editar", self.a1, pk=self.pack_a2.cobranca.pk).status_code,
            200,
        )
        self.assertEqual(
            self._get("financeiro_cobranca_cancelar", self.a1, pk=self.pack_a2.cobranca.pk).status_code,
            200,
        )
        self._assert_404_for("financeiro_cobranca_detalhe", self.a1, pk=self.pack_b1.cobranca.pk)
        self._assert_404_for("financeiro_cobranca_editar", self.a1, pk=self.pack_b1.cobranca.pk)
        self._assert_404_for("financeiro_cobranca_cancelar", self.a1, pk=self.pack_b1.cobranca.pk)
        self._assert_404_for("financeiro_cobranca_cobrar", self.a1, pk=self.pack_b1.cobranca.pk)
        self._assert_404_for("financeiro_cobranca_agendar", self.a1, pk=self.pack_b1.cobranca.pk)
        self.assertEqual(
            self._get("financeiro_cobranca_cobrar", self.a1, pk=self.pack_a2.cobranca.pk).status_code,
            200,
        )

    def test_recebimento_estorno_same_org_ok_cross_org_404(self):
        peer = self._get(
            "financeiro_cobranca_estornar_recebimento",
            self.a1,
            pk=self.pack_a2.cobranca.pk,
            recebimento_id=self.pack_a2.recebimento.pk,
        )
        self.assertEqual(peer.status_code, 200)
        self._assert_404_for(
            "financeiro_cobranca_estornar_recebimento",
            self.a1,
            pk=self.pack_b1.cobranca.pk,
            recebimento_id=self.pack_b1.recebimento.pk,
        )

    def test_audit_inadimplencia_previsao_auditoria_org_scoped(self):
        inad = self._get("financeiro_cobranca_inadimplencia", self.a1)
        self.assertEqual(inad.status_code, 200)
        body = self._body(inad)
        self.assertIn("ISO-CLI-A1", body)
        self.assertIn("ISO-CLI-A2", body)
        self.assertNotIn("ISO-CLI-B1", body)
        self.assertEqual(inad.context["resumo"].total_vencido, Decimal("100.00"))
        self.assertEqual(inad.context["resumo"].quantidade, 2)

        prev = self._get("financeiro_cobranca_previsao", self.a1)
        self.assertEqual(prev.status_code, 200)
        pbody = self._body(prev)
        self.assertIn("ISO-COB-A1", pbody)
        self.assertIn("ISO-COB-A2", pbody)
        self.assertNotIn("ISO-COB-B1", pbody)
        self.assertEqual(prev.context["resumo"].total_previsto, Decimal("2700.00"))

        aud = self._get("financeiro_cobranca_auditoria", self.a1)
        self.assertEqual(aud.status_code, 200)
        abody = self._body(aud)
        self.assertIn("ISO-HIST-A1", abody)
        self.assertIn("ISO-HIST-A2", abody)
        self.assertNotIn("ISO-HIST-B1", abody)



class FinIsolationKpiDashboardPdfAuditTests(FinIsolationAdversarialBase):
    def test_kpis_cobrancas_agrega_same_org_exclui_b1(self):
        from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization

        kpis_a = calcular_kpis_cobrancas_organization(self.org_a)
        kpis_b = calcular_kpis_cobrancas_organization(self.org_b)
        kpis_none = calcular_kpis_cobrancas_organization(None)
        self.assertEqual(kpis_a.recebido_mes, Decimal("300.00"))
        self.assertEqual(kpis_a.a_receber, Decimal("2800.00"))
        self.assertEqual(kpis_a.vencido, Decimal("100.00"))
        self.assertEqual(kpis_b.recebido_mes, Decimal("300.00"))
        self.assertEqual(kpis_b.a_receber, Decimal("2750.00"))
        self.assertEqual(kpis_none.recebido_mes, Decimal("0"))
        self.assertEqual(kpis_none.a_receber, Decimal("0"))

    def test_dashboard_cobrancas_service_org_scoped(self):
        from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas

        dash = calcular_dashboard_cobrancas(self.org_a)
        self.assertEqual(dash.recebido_mes, Decimal("300.00"))
        self.assertEqual(dash.a_receber, Decimal("2800.00"))
        self.assertEqual(dash.vencido, Decimal("100.00"))
        nomes = {c.cliente_nome for c in dash.clientes_inadimplentes}
        self.assertIn("ISO-CLI-A1", nomes)
        self.assertIn("ISO-CLI-A2", nomes)
        self.assertNotIn("ISO-CLI-B1", nomes)

    def test_dashboard_http_caixa_e_billing_org_scoped(self):
        resp = self._get("financeiro_dashboard", self.a1)
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn("ISO-BANCO-A1", body)
        self.assertIn("ISO-BANCO-A2", body)
        self.assertNotIn("ISO-BANCO-B1", body)
        self.assertIn("ISO-COB-A2", body)
        self.assertNotIn("ISO-COB-B1", body)
        cob = resp.context["cobrancas"]
        self.assertEqual(cob.recebido_mes, Decimal("300.00"))
        self.assertNotIn("ISO-COB-B1", {c.descricao for c in cob.proximos_vencimentos})


    def test_caixa_pdf_query_root_org_scoped(self):
        from organizacoes.services import CONTEXT_RESOLVED
        from financeiro.views import _extrato_queryset

        request = RequestFactory().get(reverse("financeiro_relatorio_pdf"))
        request.user = self.a1
        request.organization = self.org_a
        request.organization_context = CONTEXT_RESOLVED
        qs, *_rest = _extrato_queryset(request)
        descricoes = set(qs.values_list("descricao", flat=True))
        self.assertIn("ISO-MOV-REC-A1", descricoes)
        self.assertIn("ISO-MOV-REC-A2", descricoes)
        self.assertNotIn("ISO-MOV-REC-B1", descricoes)
        bancos = set(
            Banco.objects.filter(organization=self.org_a).values_list("nome", flat=True)
        )
        self.assertIn("ISO-BANCO-A1", bancos)
        self.assertIn("ISO-BANCO-A2", bancos)
        self.assertNotIn("ISO-BANCO-B1", bancos)
        with self.assertRaises(TypeError):
            self._get("financeiro_relatorio_pdf", self.a1)


class FinIsolationFormsValidationServicesAuditTests(FinIsolationAdversarialBase):
    def test_caixa_movimento_form_choices_same_org_sem_foreign(self):
        form = MovimentoForm(usuario=self.a1, organization=self.org_a)
        bancos = set(form.fields["banco"].queryset.values_list("pk", flat=True))
        cats = set(form.fields["categoria"].queryset.values_list("pk", flat=True))
        self.assertIn(self.pack_a1.banco.pk, bancos)
        self.assertIn(self.pack_a2.banco.pk, bancos)
        self.assertNotIn(self.pack_b1.banco.pk, bancos)
        self.assertIn(self.pack_a1.cat_r.pk, cats)
        self.assertIn(self.pack_a2.cat_r.pk, cats)
        self.assertNotIn(self.pack_b1.cat_r.pk, cats)

    def test_cobranca_contrato_form_cliente_choices_org_scoped(self):
        cob_form = CobrancaForm(usuario=self.a1, organization=self.org_a)
        clientes = set(cob_form.fields["cliente"].queryset.values_list("pk", flat=True))
        contratos = set(cob_form.fields["contrato"].queryset.values_list("pk", flat=True))
        self.assertIn(self.pack_a1.cliente.pk, clientes)
        self.assertIn(self.pack_a2.cliente.pk, clientes)
        self.assertNotIn(self.pack_b1.cliente.pk, clientes)
        self.assertIn(self.pack_a1.contrato.pk, contratos)
        self.assertIn(self.pack_a2.contrato.pk, contratos)
        self.assertNotIn(self.pack_b1.contrato.pk, contratos)

        ctr_form = ContratoForm(usuario=self.a1, organization=self.org_a)
        ctr_cli = set(ctr_form.fields["cliente"].queryset.values_list("pk", flat=True))
        self.assertIn(self.pack_a1.cliente.pk, ctr_cli)
        self.assertIn(self.pack_a2.cliente.pk, ctr_cli)
        self.assertNotIn(self.pack_b1.cliente.pk, ctr_cli)

    def test_caixa_model_clean_aceita_parent_same_org_recusa_cross_org(self):
        mov_ok = Movimento(
            usuario=self.a1,
            organization=self.org_a,
            banco=self.pack_a2.banco,
            categoria=self.pack_a2.cat_r,
            valor=Decimal("1.00"),
            data=timezone.localdate(),
        )
        mov_ok.full_clean()

        mov_bad = Movimento(
            usuario=self.a1,
            organization=self.org_a,
            banco=self.pack_b1.banco,
            categoria=self.pack_a1.cat_r,
            valor=Decimal("1.00"),
            data=timezone.localdate(),
        )
        with self.assertRaises(Exception):
            mov_bad.full_clean()

        cob_ok = Cobranca(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.pack_a2.cliente,
            descricao="same-org-peer",
            valor_original=Decimal("10.00"),
            data_vencimento=timezone.localdate(),
        )
        cob_ok.full_clean()

        cob_bad = Cobranca(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.pack_b1.cliente,
            descricao="cross-org",
            valor_original=Decimal("10.00"),
            data_vencimento=timezone.localdate(),
        )
        with self.assertRaises(Exception):
            cob_bad.full_clean()

    def test_read_services_billing_org_kpi_ainda_user(self):
        from financeiro.services.cobranca_crud import cobrancas_queryset
        from financeiro.services.cobranca_listagem import queryset_anotado
        from financeiro.services.cobranca_parcelamento import parcelas_do_grupo
        from financeiro.services.contrato_crud import contratos_queryset

        ids_cob = set(cobrancas_queryset(self.org_a).values_list("pk", flat=True))
        self.assertIn(self.pack_a1.cobranca.pk, ids_cob)
        self.assertIn(self.pack_a2.cobranca.pk, ids_cob)
        self.assertNotIn(self.pack_b1.cobranca.pk, ids_cob)

        ids_anot = set(queryset_anotado(self.a1).values_list("pk", flat=True))
        self.assertIn(self.pack_a1.cobranca.pk, ids_anot)
        self.assertNotIn(self.pack_a2.cobranca.pk, ids_anot)

        ids_ctr = set(contratos_queryset(self.org_a).values_list("pk", flat=True))
        self.assertIn(self.pack_a1.contrato.pk, ids_ctr)
        self.assertIn(self.pack_a2.contrato.pk, ids_ctr)
        self.assertNotIn(self.pack_b1.contrato.pk, ids_ctr)

        self.assertFalse(parcelas_do_grupo(self.pack_a1.cobranca).exists())


class FinIsolationJobsAdminConsumersAuditTests(FinIsolationAdversarialBase):
    def test_audit_job_scan_global_current_behavior(self):
        from financeiro.tasks import sincronizar_lembretes_cobrancas_abertas

        total = sincronizar_lembretes_cobrancas_abertas()
        self.assertGreaterEqual(total, 6)
        from usuarios.models import Compromisso

        users_com_lembrete = set(
            Compromisso.objects.filter(
                metadados__lembrete_cobranca_automatico=True
            ).values_list("user_id", flat=True)
        )
        self.assertIn(self.a1.pk, users_com_lembrete)
        self.assertIn(self.a2.pk, users_com_lembrete)
        self.assertIn(self.b1.pk, users_com_lembrete)

    def test_audit_admin_lista_globalmente(self):
        site = AdminSite()
        request = RequestFactory().get("/admin/")
        request.user = self.a1
        qs = BancoAdmin(Banco, site).get_queryset(request)
        pks = set(qs.values_list("pk", flat=True))
        self.assertIn(self.pack_a1.banco.pk, pks)
        self.assertIn(self.pack_a2.banco.pk, pks)
        self.assertIn(self.pack_b1.banco.pk, pks)
        cob_qs = set(
            CobrancaAdmin(Cobranca, site).get_queryset(request).values_list("pk", flat=True)
        )
        self.assertIn(self.pack_b1.cobranca.pk, cob_qs)
        ctr_qs = set(
            ContratoAdmin(Contrato, site).get_queryset(request).values_list("pk", flat=True)
        )
        self.assertIn(self.pack_a2.contrato.pk, ctr_qs)

    def test_audit_cliente_360_same_org_financeiro_compartilhado(self):
        self._login(self.a1)
        resp = self.client.get(reverse("cliente", kwargs={"id": self.pack_a2.cliente.pk}))
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn("ISO-CLI-A2", body)
        self.assertIn("ISO-COB-A2", body)
        self.assertIn("ISO-CTR-A2", body)

    def test_audit_cliente_360_own_mostra_financeiro(self):
        self._login(self.a1)
        resp = self.client.get(reverse("cliente", kwargs={"id": self.pack_a1.cliente.pk}))
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertIn("ISO-COB-A1", body)
        self.assertIn("ISO-CTR-A1", body)

    def test_audit_comercial_metrics_user_scoped(self):
        from comercial.services.metrics import periodo_mes, receita_no_periodo

        rec_a1 = receita_no_periodo(self.org_a, periodo_mes())
        rec_a2 = receita_no_periodo(self.org_a, periodo_mes())
        rec_b1 = receita_no_periodo(self.org_b, periodo_mes())
        self.assertEqual(rec_a1.recebido, Decimal("300.00"))
        self.assertEqual(rec_a2.recebido, Decimal("300.00"))
        self.assertEqual(rec_b1.recebido, Decimal("300.00"))
        self.assertEqual(rec_a1.contratado, Decimal("11000.00"))
        self.assertEqual(rec_b1.contratado, Decimal("7000.00"))
        self.assertNotEqual(rec_a1.contratado, rec_b1.contratado)

    def test_audit_marketing_receita_recebida_user_scoped(self):
        from datetime import date as date_cls

        from marketing.services.google_ads_resultados import get_receita_recebida
        from marketing.services.periodo import PeriodoMarketing

        hoje = timezone.localdate()
        periodo = PeriodoMarketing(
            data_inicio=date_cls(hoje.year, hoje.month, 1),
            data_fim=hoje,
        )
        valor_a1 = get_receita_recebida(self.a1, periodo, organization=self.org_a)
        valor_a2 = get_receita_recebida(self.a2, periodo, organization=self.org_a)
        valor_b1 = get_receita_recebida(self.b1, periodo, organization=self.org_b)
        self.assertEqual(valor_a1, Decimal("0"))
        self.assertEqual(valor_a2, Decimal("0"))
        self.assertEqual(valor_b1, Decimal("0"))

    def test_audit_agenda_cliente_cobrancas_vencidas_org_scoped(self):
        from usuarios.services.agenda_cliente import _cobrancas_vencidas_cliente

        qs_a1 = _cobrancas_vencidas_cliente(self.org_a, self.pack_a1.cliente)
        qs_a2 = _cobrancas_vencidas_cliente(self.org_a, self.pack_a2.cliente)
        qs_cross = _cobrancas_vencidas_cliente(self.org_a, self.pack_b1.cliente)
        qs_b1 = _cobrancas_vencidas_cliente(self.org_b, self.pack_b1.cliente)
        self.assertTrue(qs_a1.filter(pk=self.pack_a1.vencida.pk).exists())
        self.assertTrue(qs_a2.filter(pk=self.pack_a2.vencida.pk).exists())
        self.assertFalse(qs_cross.filter(pk=self.pack_b1.vencida.pk).exists())
        self.assertTrue(qs_b1.filter(pk=self.pack_b1.vencida.pk).exists())


class FinIsolationTenantNullSuperuserAuditTests(FinIsolationAdversarialBase):
    def test_caixa_read_without_tenant_context_fail_closed(self):
        solo = self._user("iso_none")
        Banco.objects.create(
            usuario=solo,
            organization=None,
            nome="ISO-BANCO-NONE",
            saldo_inicial=Decimal("1.00"),
        )
        self._login(solo)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-BANCO-NONE", self._body(resp))

    def test_caixa_null_organization_invisivel(self):
        Banco.objects.create(
            usuario=self.a1,
            organization=None,
            nome="ISO-BANCO-NULL",
            saldo_inicial=Decimal("7.00"),
        )
        resp = self._get("financeiro_banco_listar", self.a1)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-BANCO-NULL", self._body(resp))
        self._assert_404_for(
            "financeiro_banco_editar",
            self.a1,
            pk=Banco.objects.get(nome="ISO-BANCO-NULL").pk,
        )

    def test_audit_superuser_nao_ve_queryset_global_http(self):
        su = User.objects.create_superuser("iso_su", "su@ex.test", "senha123")
        self._login(su)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertNotIn("ISO-BANCO-A1", body)
        self.assertNotIn("ISO-BANCO-A2", body)
        self.assertNotIn("ISO-BANCO-B1", body)

    def test_billing_capability_presente_compartilha_same_org(self):
        resp = self._get("financeiro_cobranca_listar", self.a1)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.a1.has_perm("financeiro.view_cobrancas"))
        self.assertTrue(self.a2.has_perm("financeiro.view_cobrancas"))
        self.assertIn("ISO-COB-A2", self._body(resp))
        self.assertNotIn("ISO-COB-B1", self._body(resp))

    def test_caixa_requestfactory_none_nao_le_por_user(self):
        from financeiro.views import banco_listar

        request = RequestFactory().get(reverse("financeiro_banco_listar"))
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        resp = banco_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-BANCO-A1", self._body(resp))
        self.assertNotIn("ISO-BANCO-A2", self._body(resp))

    def test_caixa_ambiguous_fail_closed(self):
        user = self._user("iso_amb")
        self._membership(user, self.org_a)
        self._membership(user, self.org_b)
        self._login(user)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertNotIn("ISO-BANCO-A1", body)
        self.assertNotIn("ISO-BANCO-A2", body)
        self.assertNotIn("ISO-BANCO-B1", body)

    def test_caixa_missing_inconsistent_fail_closed(self):
        from financeiro.views import banco_listar

        request = RequestFactory().get(reverse("financeiro_banco_listar"))
        request.user = self.a1
        resp = banco_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-BANCO-A1", self._body(resp))

        request.organization = None
        request.organization_context = CONTEXT_RESOLVED
        resp = banco_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-BANCO-A1", self._body(resp))

        request.organization = self.org_a
        request.organization_context = CONTEXT_AMBIGUOUS
        resp = banco_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-BANCO-A1", self._body(resp))

    def test_caixa_same_org_sem_capability_deny(self):
        a3 = User.objects.create_user("iso_a3", password="senha123")
        self._membership(a3, self.org_a)
        self._login(a3)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self.assertNotEqual(resp.status_code, 200)

    def test_caixa_form_sem_org_choices_vazios(self):
        form = MovimentoForm(usuario=self.a1, organization=None)
        self.assertFalse(form.fields["banco"].queryset.exists())
        self.assertFalse(form.fields["categoria"].queryset.exists())

    def test_caixa_dashboard_totais_org_a_sem_b1(self):
        from financeiro.views import _serie_historico_mensal

        hoje = timezone.localdate()
        serie_a = _serie_historico_mensal(self.org_a, hoje, 12)
        serie_b = _serie_historico_mensal(self.org_b, hoje, 12)
        self.assertEqual(serie_a["receitas"][-1], 300.0)
        self.assertEqual(serie_a["despesas"][-1], 30.0)
        self.assertEqual(serie_b["receitas"][-1], 300.0)
        self.assertEqual(serie_b["despesas"][-1], 30.0)

    def test_billing_null_organization_invisivel(self):
        ctr_null = Contrato.objects.create(
            usuario=self.a1,
            organization=None,
            cliente=self.pack_a1.cliente,
            referencia="ISO-CTR-NULL",
            descricao="Contrato NULL",
            valor_total=Decimal("1.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a1,
        )
        cob_null = Cobranca.objects.create(
            usuario=self.a1,
            organization=None,
            cliente=self.pack_a1.cliente,
            descricao="ISO-COB-NULL",
            valor_original=Decimal("1.00"),
            data_vencimento=timezone.localdate(),
            criado_por=self.a1,
        )
        rec_null = CobrancaRecebimento.objects.create(
            cobranca=self.pack_a1.cobranca,
            usuario=self.a1,
            organization=None,
            valor=Decimal("1.00"),
            data_recebimento=timezone.localdate(),
            registrado_por=self.a1,
        )
        resp_ctr = self._get("financeiro_contrato_listar", self.a1)
        self.assertNotIn("ISO-CTR-NULL", self._body(resp_ctr))
        resp_cob = self._get("financeiro_cobranca_listar", self.a1)
        self.assertNotIn("ISO-COB-NULL", self._body(resp_cob))
        self._assert_404_for("financeiro_contrato_detalhe", self.a1, pk=ctr_null.pk)
        self._assert_404_for("financeiro_cobranca_detalhe", self.a1, pk=cob_null.pk)
        peer_detalhe = self._get(
            "financeiro_cobranca_detalhe", self.a1, pk=self.pack_a1.cobranca.pk
        )
        rec_ids = {r.pk for r in peer_detalhe.context["recebimentos"]}
        self.assertNotIn(rec_null.pk, rec_ids)
        self._assert_404_for(
            "financeiro_cobranca_estornar_recebimento",
            self.a1,
            pk=self.pack_a1.cobranca.pk,
            recebimento_id=rec_null.pk,
        )

    def test_billing_requestfactory_none_nao_le_por_user(self):
        from financeiro.views_cobrancas import cobranca_listar
        from financeiro.views_contratos import contrato_listar

        request = RequestFactory().get(reverse("financeiro_contrato_listar"))
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        resp = contrato_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-CTR-A1", self._body(resp))
        self.assertNotIn("ISO-CTR-A2", self._body(resp))

        request = RequestFactory().get(reverse("financeiro_cobranca_listar"))
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-COB-A1", self._body(resp))
        self.assertNotIn("ISO-COB-A2", self._body(resp))

    def test_billing_ambiguous_missing_inconsistent_fail_closed(self):
        from financeiro.views_cobrancas import cobranca_detalhe, cobranca_listar
        from financeiro.views_contratos import contrato_detalhe, contrato_listar

        user = self._user("iso_amb_bill")
        self._membership(user, self.org_a)
        self._membership(user, self.org_b)
        self._login(user)
        resp = self.client.get(reverse("financeiro_contrato_listar"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-CTR-A1", self._body(resp))
        self.assertNotIn("ISO-CTR-B1", self._body(resp))
        resp_c = self.client.get(reverse("financeiro_cobranca_listar"))
        self.assertEqual(resp_c.status_code, 200)
        self.assertNotIn("ISO-COB-A1", self._body(resp_c))
        self.assertNotIn("ISO-COB-B1", self._body(resp_c))

        request = RequestFactory().get(reverse("financeiro_cobranca_listar"))
        request.user = self.a1
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-COB-A1", self._body(resp))

        request.organization = None
        request.organization_context = CONTEXT_RESOLVED
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-COB-A1", self._body(resp))

        request.organization = self.org_a
        request.organization_context = CONTEXT_AMBIGUOUS
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-COB-A1", self._body(resp))

        request = RequestFactory().get(
            reverse("financeiro_contrato_detalhe", kwargs={"pk": self.pack_a1.contrato.pk})
        )
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        with self.assertRaises(Exception):
            contrato_detalhe(request, pk=self.pack_a1.contrato.pk)

        request = RequestFactory().get(
            reverse("financeiro_cobranca_detalhe", kwargs={"pk": self.pack_a1.cobranca.pk})
        )
        request.user = self.a1
        request.organization = self.org_a
        request.organization_context = CONTEXT_AMBIGUOUS
        with self.assertRaises(Exception):
            cobranca_detalhe(request, pk=self.pack_a1.cobranca.pk)

        resp = contrato_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-CTR-A1", self._body(resp))

    def test_billing_same_org_sem_capability_deny(self):
        a3 = User.objects.create_user("iso_a3_bill", password="senha123")
        self._membership(a3, self.org_a)
        self._login(a3)
        resp = self.client.get(reverse("financeiro_cobranca_listar"))
        self.assertNotEqual(resp.status_code, 200)
        resp_ctr = self.client.get(reverse("financeiro_contrato_listar"))
        self.assertNotEqual(resp_ctr.status_code, 200)

    def test_caixa_null_categoria_movimento_invisiveis(self):
        cat_null = Categoria.objects.create(
            usuario=self.a1,
            organization=None,
            nome="ISO-CAT-NULL",
            tipo=Categoria.Tipo.RECEITA,
        )
        mov_null = Movimento.objects.create(
            usuario=self.a1,
            organization=None,
            banco=self.pack_a1.banco,
            categoria=self.pack_a1.cat_r,
            valor=Decimal("9.00"),
            data=timezone.localdate(),
            descricao="ISO-MOV-NULL",
        )
        resp = self._get("financeiro_categoria_listar", self.a1)
        self.assertNotIn("ISO-CAT-NULL", self._body(resp))
        resp_ex = self._get("financeiro_extrato", self.a1)
        self.assertNotIn("ISO-MOV-NULL", self._body(resp_ex))
        self._assert_404_for("financeiro_categoria_editar", self.a1, pk=cat_null.pk)
        self._assert_404_for("financeiro_movimento_editar", self.a1, pk=mov_null.pk)
