"""FIN-CUTOVER-W2 — Billing Core organization-scoped, fail-closed, sem fallback User."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import StatusContrato
from financeiro.forms import CobrancaForm, ContratoForm
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.tests_fin_isolation import FinIsolationAdversarialBase
from financeiro.tests_helpers import grant_finance_permissions
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente


class FinCutoverW2ContratoTests(FinIsolationAdversarialBase):
    def test_matriz_list_detail(self):
        resp = self._get("financeiro_contrato_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-CTR-A1", "ISO-CTR-A2", "ISO-CTR-B1")
        self.assertEqual(
            self._get(
                "financeiro_contrato_detalhe", self.a1, pk=self.pack_a1.contrato.pk
            ).status_code,
            200,
        )
        self.assertEqual(
            self._get(
                "financeiro_contrato_detalhe", self.a1, pk=self.pack_a2.contrato.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_contrato_detalhe", self.a1, pk=self.pack_b1.contrato.pk
        )
        self.assertEqual(
            self._get(
                "financeiro_contrato_detalhe", self.a2, pk=self.pack_a1.contrato.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_contrato_detalhe", self.b1, pk=self.pack_a1.contrato.pk
        )

    def test_capability_necessaria(self):
        a3 = User.objects.create_user("w2_a3_ctr", password="senha123")
        self._membership(a3, self.org_a)
        self._login(a3)
        self.assertNotEqual(
            self.client.get(reverse("financeiro_contrato_listar")).status_code, 200
        )

    def test_edit_same_org_preserva_usuario_legado(self):
        self._login(self.a1)
        resp = self.client.post(
            reverse(
                "financeiro_contrato_editar",
                kwargs={"pk": self.pack_a2.contrato.pk},
            ),
            {
                "cliente": self.pack_a2.cliente.pk,
                "referencia": "ISO-CTR-A2",
                "descricao": "Contrato A2 editado por A1",
                "valor_total": "6000,00",
                "status": StatusContrato.ACTIVE,
                "responsavel": self.a2.pk,
                "observacoes": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.pack_a2.contrato.refresh_from_db()
        self.assertEqual(self.pack_a2.contrato.usuario_id, self.a2.pk)
        self.assertEqual(self.pack_a2.contrato.organization_id, self.org_a.pk)
        self.assertEqual(self.pack_a2.contrato.descricao, "Contrato A2 editado por A1")

    def test_create_cliente_same_org_peer(self):
        self._login(self.a1)
        resp = self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": self.pack_a2.cliente.pk,
                "referencia": "W2-CTR-A2CLI",
                "descricao": "Contrato A1 para cliente A2",
                "valor_total": "10,00",
                "status": StatusContrato.DRAFT,
                "responsavel": self.a1.pk,
                "observacoes": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        ctr = Contrato.objects.get(referencia="W2-CTR-A2CLI")
        self.assertEqual(ctr.organization_id, self.org_a.pk)
        self.assertEqual(ctr.usuario_id, self.a1.pk)
        self.assertEqual(ctr.cliente_id, self.pack_a2.cliente.pk)

    def test_form_choices_e_post_tampering(self):
        cli_null = Cliente.objects.create(
            user=self.a1, organization=None, nome="ISO-CLI-NULL", email="n@ex.test"
        )
        form = ContratoForm(usuario=self.a1, organization=self.org_a)
        ids = set(form.fields["cliente"].queryset.values_list("pk", flat=True))
        self.assertIn(self.pack_a1.cliente.pk, ids)
        self.assertIn(self.pack_a2.cliente.pk, ids)
        self.assertNotIn(self.pack_b1.cliente.pk, ids)
        self.assertNotIn(cli_null.pk, ids)

        self._login(self.a1)
        antes = Contrato.objects.count()
        self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": self.pack_b1.cliente.pk,
                "referencia": "W2-HACK-B1",
                "descricao": "tamper",
                "valor_total": "10,00",
                "status": StatusContrato.DRAFT,
                "responsavel": self.a1.pk,
                "observacoes": "",
            },
        )
        self.assertEqual(Contrato.objects.count(), antes)
        self.assertFalse(Contrato.objects.filter(referencia="W2-HACK-B1").exists())


class FinCutoverW2CobrancaTests(FinIsolationAdversarialBase):
    def test_matriz_list_detail_edit_cancel(self):
        resp = self._get("financeiro_cobranca_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-COB-A1", "ISO-COB-A2", "ISO-COB-B1")
        self.assertEqual(resp.context["kpis"].recebido_mes, Decimal("300.00"))

        self.assertEqual(
            self._get(
                "financeiro_cobranca_detalhe", self.a1, pk=self.pack_a1.cobranca.pk
            ).status_code,
            200,
        )
        self.assertEqual(
            self._get(
                "financeiro_cobranca_detalhe", self.a1, pk=self.pack_a2.cobranca.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_cobranca_detalhe", self.a1, pk=self.pack_b1.cobranca.pk
        )
        self.assertEqual(
            self._get(
                "financeiro_cobranca_detalhe", self.a2, pk=self.pack_a1.cobranca.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_cobranca_detalhe", self.b1, pk=self.pack_a1.cobranca.pk
        )

        self.assertEqual(
            self._get(
                "financeiro_cobranca_editar", self.a1, pk=self.pack_a2.cobranca.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_cobranca_editar", self.a1, pk=self.pack_b1.cobranca.pk
        )
        self.assertEqual(
            self._get(
                "financeiro_cobranca_cancelar", self.a1, pk=self.pack_a2.cobranca.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_cobranca_cancelar", self.a1, pk=self.pack_b1.cobranca.pk
        )

    def test_responsavel_nao_e_tenant(self):
        cob = self.pack_a2.cobranca
        self.assertEqual(cob.responsavel_id, self.a2.pk)
        self.assertEqual(cob.usuario_id, self.a2.pk)
        self.assertEqual(
            self._get("financeiro_cobranca_detalhe", self.a1, pk=cob.pk).status_code,
            200,
        )
        self.assertEqual(
            self._get("financeiro_cobranca_editar", self.a1, pk=cob.pk).status_code,
            200,
        )
        a3 = User.objects.create_user("w2_a3_resp", password="senha123")
        self._membership(a3, self.org_a)
        self._login(a3)
        self.assertNotEqual(
            self.client.get(
                reverse("financeiro_cobranca_detalhe", kwargs={"pk": cob.pk})
            ).status_code,
            200,
        )

    def test_cancel_same_org_post_e_cross_org(self):
        self._login(self.a1)
        resp = self.client.post(
            reverse(
                "financeiro_cobranca_cancelar",
                kwargs={"pk": self.pack_a2.cobranca.pk},
            ),
            {"motivo": "W2 same-org"},
        )
        self.assertEqual(resp.status_code, 302)
        self.pack_a2.cobranca.refresh_from_db()
        self.assertEqual(self.pack_a2.cobranca.status, "canceled")
        self._login(self.a1)
        resp_b = self.client.post(
            reverse(
                "financeiro_cobranca_cancelar",
                kwargs={"pk": self.pack_b1.cobranca.pk},
            ),
            {"motivo": "hack"},
        )
        self.assertEqual(resp_b.status_code, 404)
        self.pack_b1.cobranca.refresh_from_db()
        self.assertNotEqual(self.pack_b1.cobranca.status, "canceled")

    def test_agendar_cobrar_lookup_org(self):
        self.assertEqual(
            self._get(
                "financeiro_cobranca_cobrar", self.a1, pk=self.pack_a2.cobranca.pk
            ).status_code,
            200,
        )
        self._assert_404_for(
            "financeiro_cobranca_cobrar", self.a1, pk=self.pack_b1.cobranca.pk
        )
        self._assert_404_for(
            "financeiro_cobranca_agendar", self.a1, pk=self.pack_b1.cobranca.pk
        )

    def test_form_choices_e_post_tampering(self):
        form = CobrancaForm(usuario=self.a1, organization=self.org_a)
        clientes = set(form.fields["cliente"].queryset.values_list("pk", flat=True))
        contratos = set(form.fields["contrato"].queryset.values_list("pk", flat=True))
        self.assertIn(self.pack_a1.cliente.pk, clientes)
        self.assertIn(self.pack_a2.cliente.pk, clientes)
        self.assertNotIn(self.pack_b1.cliente.pk, clientes)
        self.assertIn(self.pack_a2.contrato.pk, contratos)
        self.assertNotIn(self.pack_b1.contrato.pk, contratos)

        self._login(self.a1)
        antes = Cobranca.objects.count()
        self.client.post(
            reverse("financeiro_cobranca_nova"),
            {
                "tipo_lancamento": "unica",
                "cliente": self.pack_b1.cliente.pk,
                "contrato": self.pack_b1.contrato.pk,
                "descricao": "tamper B1",
                "valor": "10,00",
                "data_vencimento": timezone.localdate().isoformat(),
                "categoria": "honorarios",
                "responsavel": self.a1.pk,
            },
        )
        self.assertEqual(Cobranca.objects.count(), antes)

    def test_create_same_org_peer_parents(self):
        self._login(self.a1)
        resp = self.client.post(
            reverse("financeiro_cobranca_nova"),
            {
                "tipo_lancamento": "unica",
                "cliente": self.pack_a2.cliente.pk,
                "contrato": self.pack_a2.contrato.pk,
                "descricao": "W2-COB-PEER",
                "valor": "15,00",
                "data_vencimento": timezone.localdate().isoformat(),
                "categoria": "honorarios",
                "responsavel": self.a1.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        cob = Cobranca.objects.get(descricao="W2-COB-PEER")
        self.assertEqual(cob.organization_id, self.org_a.pk)
        self.assertEqual(cob.usuario_id, self.a1.pk)
        self.assertEqual(cob.cliente_id, self.pack_a2.cliente.pk)
        self.assertEqual(cob.contrato_id, self.pack_a2.contrato.pk)


class FinCutoverW2RecebimentoTests(FinIsolationAdversarialBase):
    def test_view_same_org_e_capability(self):
        resp = self._get(
            "financeiro_cobranca_detalhe", self.a1, pk=self.pack_a2.cobranca.pk
        )
        self.assertEqual(resp.status_code, 200)
        rec_ids = {r.pk for r in resp.context["recebimentos"]}
        self.assertIn(self.pack_a2.recebimento.pk, rec_ids)
        self.assertNotIn(self.pack_b1.recebimento.pk, rec_ids)

        viewer = User.objects.create_user("w2_view_only", password="senha123")
        self._membership(viewer, self.org_a)
        grant_finance_permissions(viewer, "view_cobrancas")
        self._login(viewer)
        resp = self.client.get(
            reverse(
                "financeiro_cobranca_detalhe", kwargs={"pk": self.pack_a2.cobranca.pk}
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["recebimentos"]), [])

    def test_registro_same_org_actor_e_cross_org(self):
        self._login(self.a1)
        resp = self.client.post(
            reverse(
                "financeiro_cobranca_recebimento",
                kwargs={"pk": self.pack_a2.cobranca.pk},
            ),
            {
                "valor": "10,00",
                "data_recebimento": timezone.localdate().isoformat(),
                "forma_pagamento": "pix",
                "referencia": "W2-A1-ON-A2",
                "observacao": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        rec = CobrancaRecebimento.objects.get(referencia="W2-A1-ON-A2")
        self.assertEqual(rec.organization_id, self.org_a.pk)
        self.assertEqual(rec.registrado_por_id, self.a1.pk)
        self.assertEqual(rec.usuario_id, self.a2.pk)

        antes = CobrancaRecebimento.objects.count()
        resp_b = self.client.post(
            reverse(
                "financeiro_cobranca_recebimento",
                kwargs={"pk": self.pack_b1.cobranca.pk},
            ),
            {
                "valor": "10,00",
                "data_recebimento": timezone.localdate().isoformat(),
                "forma_pagamento": "pix",
            },
        )
        self.assertEqual(resp_b.status_code, 404)
        self.assertEqual(CobrancaRecebimento.objects.count(), antes)

    def test_estorno_same_org_e_cross_org(self):
        peer = self._get(
            "financeiro_cobranca_estornar_recebimento",
            self.a1,
            pk=self.pack_a2.cobranca.pk,
            recebimento_id=self.pack_a2.recebimento.pk,
        )
        self.assertEqual(peer.status_code, 200)
        self._login(self.a1)
        resp = self.client.post(
            reverse(
                "financeiro_cobranca_estornar_recebimento",
                kwargs={
                    "pk": self.pack_a2.cobranca.pk,
                    "recebimento_id": self.pack_a2.recebimento.pk,
                },
            ),
            {"motivo": "W2 estorno same-org"},
        )
        self.assertEqual(resp.status_code, 302)
        self.pack_a2.recebimento.refresh_from_db()
        self.assertIsNotNone(self.pack_a2.recebimento.cancelado_em)
        self.assertEqual(self.pack_a2.recebimento.registrado_por_id, self.a2.pk)
        self._assert_404_for(
            "financeiro_cobranca_estornar_recebimento",
            self.a1,
            pk=self.pack_b1.cobranca.pk,
            recebimento_id=self.pack_b1.recebimento.pk,
        )


class FinCutoverW2InvariantsTests(FinIsolationAdversarialBase):
    def test_contrato_cliente_org(self):
        ok = Contrato(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.pack_a2.cliente,
            referencia="W2-INV-OK",
            descricao="ok",
            valor_total=Decimal("1.00"),
        )
        ok.full_clean()
        bad = Contrato(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.pack_b1.cliente,
            referencia="W2-INV-BAD",
            descricao="bad",
            valor_total=Decimal("1.00"),
        )
        with self.assertRaises(ValidationError):
            bad.full_clean()

    def test_cobranca_cliente_contrato_org(self):
        Cobranca(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.pack_a2.cliente,
            contrato=self.pack_a2.contrato,
            descricao="ok",
            valor_original=Decimal("1.00"),
            data_vencimento=timezone.localdate(),
        ).full_clean()
        with self.assertRaises(ValidationError):
            Cobranca(
                usuario=self.a1,
                organization=self.org_a,
                cliente=self.pack_b1.cliente,
                descricao="bad cli",
                valor_original=Decimal("1.00"),
                data_vencimento=timezone.localdate(),
            ).full_clean()
        with self.assertRaises(ValidationError):
            Cobranca(
                usuario=self.a1,
                organization=self.org_a,
                cliente=self.pack_a1.cliente,
                contrato=self.pack_b1.contrato,
                descricao="bad ctr",
                valor_original=Decimal("1.00"),
                data_vencimento=timezone.localdate(),
            ).full_clean()

    def test_recebimento_cobranca_movimento_org(self):
        CobrancaRecebimento(
            cobranca=self.pack_a2.cobranca,
            usuario=self.a1,
            organization=self.org_a,
            valor=Decimal("1.00"),
            data_recebimento=timezone.localdate(),
            movimento=self.pack_a2.mov_r,
        ).full_clean()
        with self.assertRaises(ValidationError):
            CobrancaRecebimento(
                cobranca=self.pack_b1.cobranca,
                usuario=self.a1,
                organization=self.org_a,
                valor=Decimal("1.00"),
                data_recebimento=timezone.localdate(),
            ).full_clean()
        with self.assertRaises(ValidationError):
            CobrancaRecebimento(
                cobranca=self.pack_a1.cobranca,
                usuario=self.a1,
                organization=self.org_a,
                valor=Decimal("1.00"),
                data_recebimento=timezone.localdate(),
                movimento=self.pack_b1.mov_r,
            ).full_clean()


class FinCutoverW2TenantW1W3Tests(FinIsolationAdversarialBase):
    def test_tenantcontext_resolved_none_ambiguous_missing_inconsistent(self):
        from financeiro.views_cobrancas import cobranca_detalhe, cobranca_listar
        from financeiro.views_contratos import contrato_detalhe, contrato_listar

        request = RequestFactory().get(reverse("financeiro_cobranca_listar"))
        request.user = self.a1
        request.organization = self.org_a
        request.organization_context = CONTEXT_RESOLVED
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ISO-COB-A2", self._body(resp))

        for ctx, org in (
            (CONTEXT_NONE, None),
            (CONTEXT_AMBIGUOUS, self.org_a),
            (CONTEXT_RESOLVED, None),
        ):
            request = RequestFactory().get(reverse("financeiro_contrato_listar"))
            request.user = self.a1
            request.organization = org
            request.organization_context = ctx
            resp = contrato_listar(request)
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("ISO-CTR-A1", self._body(resp))

        request = RequestFactory().get(reverse("financeiro_cobranca_listar"))
        request.user = self.a1
        resp = cobranca_listar(request)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("ISO-COB-A1", self._body(resp))

        request = RequestFactory().get(
            reverse(
                "financeiro_cobranca_detalhe",
                kwargs={"pk": self.pack_a1.cobranca.pk},
            )
        )
        request.user = self.a1
        request.organization = None
        request.organization_context = CONTEXT_NONE
        with self.assertRaises(Exception):
            cobranca_detalhe(request, pk=self.pack_a1.cobranca.pk)
        with self.assertRaises(Exception):
            contrato_detalhe(request, pk=self.pack_a1.contrato.pk)

    def test_w1_caixa_continua_org_scoped(self):
        resp = self._get("financeiro_banco_listar", self.a1)
        self._assert_org_scoped_html(resp, "ISO-BANCO-A1", "ISO-BANCO-A2", "ISO-BANCO-B1")

    def test_w3_billing_dashboard_kpi_inad_prev_org_scoped(self):
        from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas
        from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization

        dash = calcular_dashboard_cobrancas(self.org_a)
        self.assertEqual(dash.recebido_mes, Decimal("300.00"))
        self.assertIn(
            "ISO-CLI-A2", {c.cliente_nome for c in dash.clientes_inadimplentes}
        )
        self.assertNotIn(
            "ISO-CLI-B1", {c.cliente_nome for c in dash.clientes_inadimplentes}
        )
        kpis = calcular_kpis_cobrancas_organization(self.org_a)
        self.assertEqual(kpis.recebido_mes, Decimal("300.00"))

        inad = self._get("financeiro_cobranca_inadimplencia", self.a1)
        self.assertIn("ISO-CLI-A1", self._body(inad))
        self.assertIn("ISO-CLI-A2", self._body(inad))
        self.assertNotIn("ISO-CLI-B1", self._body(inad))
        prev = self._get("financeiro_cobranca_previsao", self.a1)
        self.assertIn("ISO-COB-A1", self._body(prev))
        self.assertIn("ISO-COB-A2", self._body(prev))
        self.assertNotIn("ISO-COB-B1", self._body(prev))
        dash_http = self._get("financeiro_dashboard", self.a1)
        self.assertIn("ISO-COB-A2", self._body(dash_http))
        self.assertNotIn("ISO-COB-B1", self._body(dash_http))
        self.assertIn("ISO-BANCO-A2", self._body(dash_http))
