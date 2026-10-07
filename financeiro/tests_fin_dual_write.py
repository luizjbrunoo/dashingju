"""FIN-DUAL-WRITE: novos writes HTTP materializam Organization pelo TenantContext."""

from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import RequestFactory, TestCase
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
from financeiro.tenancy_write import MSG_TENANT_INDETERMINADO, organization_for_finance_write
from financeiro.views import banco_novo, categoria_nova
from financeiro.tests_helpers import grant_all_finance_permissions
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente


class FinDualWriteHelpers(TestCase):
    def _user(self, username):
        user = User.objects.create_user(username=username, password="senha123")
        grant_all_finance_permissions(user)
        return user

    def _org(self, name, *, status=Organization.Status.ACTIVE):
        return Organization.objects.create(name=name, status=status)

    def _membership(self, user, org, *, status=Membership.Status.ACTIVE):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.OWNER,
            status=status,
        )

    def _cliente(self, user, org=None, *, nome="Cli"):
        return Cliente.objects.create(
            user=user,
            nome=nome,
            email=f"{user.username}.{nome.replace(' ', '')}@ex.test",
            organization=org,
        )

    def _resolved(self, username="dw"):
        user = self._user(username)
        org = self._org(f"Org {username}")
        self._membership(user, org)
        return user, org

    def _post_view(self, view, user, data, *, organization=None, context=None, omit_tenant=False, path="/"):
        request = RequestFactory().post(path, data)
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        return view(request)


class FinDualWriteContextTests(FinDualWriteHelpers):
    def test_01_resolved_cria_banco(self):
        user, org = self._resolved("dw1")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Banco Resolved", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(resp.status_code, 302)
        banco = Banco.objects.get(nome="Banco Resolved")
        self.assertEqual(banco.organization_id, org.pk)
        self.assertEqual(banco.usuario_id, user.pk)

    def test_02_none_nao_cria(self):
        user = self._user("dw_none")
        self.client.force_login(user)
        antes = Banco.objects.count()
        resp = self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Banco None", "agencia": "", "conta": "", "saldo_inicial": ""},
            follow=True,
        )
        self.assertEqual(Banco.objects.count(), antes)
        self.assertContains(resp, MSG_TENANT_INDETERMINADO)

    def test_03_ambiguous_nao_cria(self):
        user = self._user("dw_amb")
        self._membership(user, self._org("Amb A"))
        self._membership(user, self._org("Amb B"))
        self.client.force_login(user)
        antes = Banco.objects.count()
        self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Banco Amb", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(Banco.objects.count(), antes)

    def test_04_inconsistent_resolved_sem_org(self):
        user = self._user("dw_inc")
        antes = Banco.objects.count()
        self._post_view(
            banco_novo,
            user,
            {"nome": "Inc", "agencia": "", "conta": "", "saldo_inicial": ""},
            organization=None,
            context=CONTEXT_RESOLVED,
            path=reverse("financeiro_banco_novo"),
        )
        self.assertEqual(Banco.objects.count(), antes)

    def test_04b_inconsistent_none_com_org(self):
        user = self._user("dw_inc2")
        org = self._org("Inc2")
        antes = Banco.objects.count()
        self._post_view(
            banco_novo,
            user,
            {"nome": "Inc2", "agencia": "", "conta": "", "saldo_inicial": ""},
            organization=org,
            context=CONTEXT_NONE,
            path=reverse("financeiro_banco_novo"),
        )
        self.assertEqual(Banco.objects.count(), antes)

    def test_05_missing_nao_cria(self):
        user = self._user("dw_miss")
        antes = Banco.objects.count()
        self._post_view(
            banco_novo,
            user,
            {"nome": "Miss", "agencia": "", "conta": "", "saldo_inicial": ""},
            omit_tenant=True,
            path=reverse("financeiro_banco_novo"),
        )
        self.assertEqual(Banco.objects.count(), antes)

    def test_05b_context_desconhecido(self):
        user = self._user("dw_unk")
        org = self._org("Unk")
        antes = Banco.objects.count()
        self._post_view(
            banco_novo,
            user,
            {"nome": "Unk", "agencia": "", "conta": "", "saldo_inicial": ""},
            organization=org,
            context="qualquer_coisa",
            path=reverse("financeiro_banco_novo"),
        )
        self.assertEqual(Banco.objects.count(), antes)

    def test_06_07_post_nao_controla_tenant(self):
        user, org_a = self._resolved("dw_mal")
        org_b = self._org("Org Mal B")
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_banco_novo"),
            {
                "nome": "Banco Mal",
                "agencia": "",
                "conta": "",
                "saldo_inicial": "",
                "organization": str(org_b.pk),
                "organization_id": str(org_b.pk),
            },
        )
        banco = Banco.objects.get(nome="Banco Mal")
        self.assertEqual(banco.organization_id, org_a.pk)
        self.assertNotEqual(banco.organization_id, org_b.pk)


class FinDualWriteBancoCategoriaTests(FinDualWriteHelpers):
    def test_08_banco_usuario_e_org(self):
        user, org = self._resolved("b8")
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Caixa 8", "agencia": "1", "conta": "2", "saldo_inicial": "10,00"},
        )
        banco = Banco.objects.get(nome="Caixa 8")
        self.assertEqual(banco.organization_id, org.pk)
        self.assertEqual(banco.usuario_id, user.pk)

    def test_09_banco_cross_context_update(self):
        user_a, org_a = self._resolved("b9a")
        user_b, org_b = self._resolved("b9b")
        banco = Banco.objects.create(usuario=user_a, nome="Do A", organization=org_a)
        self.client.force_login(user_b)
        resp = self.client.post(
            reverse("financeiro_banco_editar", args=[banco.pk]),
            {"nome": "Hack", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(resp.status_code, 404)
        banco.refresh_from_db()
        self.assertEqual(banco.nome, "Do A")
        self.assertEqual(banco.organization_id, org_a.pk)

    def test_10_categoria_nova(self):
        user, org = self._resolved("c10")
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_categoria_nova"),
            {"nome": "Honorarios", "tipo": Categoria.Tipo.RECEITA},
        )
        cat = Categoria.objects.get(nome="Honorarios")
        self.assertEqual(cat.organization_id, org.pk)
        self.assertEqual(cat.usuario_id, user.pk)

    def test_11_categoria_ignora_frontend(self):
        user, org_a = self._resolved("c11")
        org_b = self._org("Cat B")
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_categoria_nova"),
            {
                "nome": "Despesa",
                "tipo": Categoria.Tipo.DESPESA,
                "organization_id": str(org_b.pk),
            },
        )
        cat = Categoria.objects.get(nome="Despesa")
        self.assertEqual(cat.organization_id, org_a.pk)


class FinDualWriteContratoCobrancaTests(FinDualWriteHelpers):
    def _contrato_post(self, user, cliente, referencia="CTR-1"):
        return self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": cliente.pk,
                "referencia": referencia,
                "descricao": "Honorarios",
                "valor_total": "100,00",
                "status": "draft",
                "responsavel": user.pk,
                "observacoes": "",
            },
        )

    def test_12_13_contrato_mesma_org(self):
        user, org = self._resolved("ct12")
        cliente = self._cliente(user, org)
        self.client.force_login(user)
        resp = self._contrato_post(user, cliente)
        self.assertEqual(resp.status_code, 302)
        contrato = Contrato.objects.get(referencia="CTR-1")
        self.assertEqual(contrato.organization_id, org.pk)
        self.assertEqual(contrato.usuario_id, user.pk)
        self.assertEqual(contrato.criado_por_id, user.pk)
        self.assertEqual(contrato.responsavel_id, user.pk)

    def test_14_cliente_cross_org(self):
        user, org_a = self._resolved("ct14")
        org_b = self._org("CT14B")
        cliente = self._cliente(user, org_b, nome="Cross")
        self.client.force_login(user)
        antes = Contrato.objects.count()
        self._contrato_post(user, cliente, referencia="NO")
        self.assertEqual(Contrato.objects.count(), antes)

    def test_15_cliente_org_null(self):
        user, org = self._resolved("ct15")
        cliente = self._cliente(user, None, nome="NullOrg")
        self.client.force_login(user)
        antes = Contrato.objects.count()
        self._contrato_post(user, cliente, referencia="NULLC")
        self.assertEqual(Contrato.objects.count(), antes)

    def test_16_cliente_user_nao_substitui_tenant(self):
        user, org_a = self._resolved("ct16")
        org_b = self._org("CT16B")
        cliente = self._cliente(user, org_b, nome="MesmoUser")
        self.assertEqual(cliente.user_id, user.pk)
        self.client.force_login(user)
        antes = Contrato.objects.count()
        self._contrato_post(user, cliente, referencia="USERONLY")
        self.assertEqual(Contrato.objects.count(), antes)

    def _cobranca_post(self, user, cliente, contrato=None, descricao="Cob 1"):
        data = {
            "tipo_lancamento": "unica",
            "cliente": cliente.pk,
            "descricao": descricao,
            "valor": "50,00",
            "data_vencimento": timezone.localdate().isoformat(),
            "categoria": "honorarios",
            "responsavel": user.pk,
            "forma_prevista_pagamento": "",
            "observacoes_internas": "",
        }
        if contrato:
            data["contrato"] = contrato.pk
        return self.client.post(reverse("financeiro_cobranca_nova"), data)

    def test_17_cobranca_cliente_contrato_mesma_org(self):
        user, org = self._resolved("cb17")
        cliente = self._cliente(user, org)
        contrato = Contrato.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            referencia="C17",
            descricao="C",
            valor_total=Decimal("100"),
            criado_por=user,
            responsavel=user,
        )
        self.client.force_login(user)
        resp = self._cobranca_post(user, cliente, contrato, descricao="Cob OK")
        self.assertEqual(resp.status_code, 302)
        cob = Cobranca.objects.get(descricao="Cob OK")
        self.assertEqual(cob.organization_id, org.pk)
        self.assertEqual(cob.usuario_id, user.pk)
        self.assertEqual(cob.criado_por_id, user.pk)

    def test_18_cobranca_cliente_cross(self):
        user, org_a = self._resolved("cb18")
        org_b = self._org("CB18B")
        cliente = self._cliente(user, org_b)
        self.client.force_login(user)
        antes = Cobranca.objects.count()
        self._cobranca_post(user, cliente, descricao="No Cross")
        self.assertEqual(Cobranca.objects.count(), antes)

    def test_19_cobranca_contrato_cross(self):
        user, org_a = self._resolved("cb19")
        org_b = self._org("CB19B")
        cliente = self._cliente(user, org_a)
        contrato = Contrato.objects.create(
            usuario=user,
            organization=org_b,
            cliente=cliente,
            referencia="CX",
            descricao="X",
            valor_total=Decimal("10"),
        )
        self.client.force_login(user)
        antes = Cobranca.objects.count()
        self._cobranca_post(user, cliente, contrato, descricao="No Contr")
        self.assertEqual(Cobranca.objects.count(), antes)

    def test_20_cliente_a_contrato_b(self):
        user, org_a = self._resolved("cb20")
        org_b = self._org("CB20B")
        cliente = self._cliente(user, org_a)
        contrato = Contrato.objects.create(
            usuario=user,
            organization=org_b,
            cliente=cliente,
            referencia="AB",
            descricao="AB",
            valor_total=Decimal("10"),
        )
        self.client.force_login(user)
        antes = Cobranca.objects.count()
        self._cobranca_post(user, cliente, contrato, descricao="A vs B")
        self.assertEqual(Cobranca.objects.count(), antes)

    def test_21_parent_org_null(self):
        user, org = self._resolved("cb21")
        cliente = self._cliente(user, None)
        self.client.force_login(user)
        antes = Cobranca.objects.count()
        self._cobranca_post(user, cliente, descricao="Parent Null")
        self.assertEqual(Cobranca.objects.count(), antes)


class FinDualWriteMovimentoRecebimentoTests(FinDualWriteHelpers):
    def test_22_26_movimento_mesma_org(self):
        user, org = self._resolved("mv22")
        banco = Banco.objects.create(usuario=user, nome="B", organization=org)
        cat = Categoria.objects.create(
            usuario=user, nome="R", tipo=Categoria.Tipo.RECEITA, organization=org
        )
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_movimento_novo"),
            {
                "tipo": Categoria.Tipo.RECEITA,
                "banco": banco.pk,
                "categoria": cat.pk,
                "valor": "10.00",
                "data": timezone.localdate().isoformat(),
                "descricao": "ok",
            },
        )
        self.assertEqual(resp.status_code, 302)
        mov = Movimento.objects.get(descricao="ok")
        self.assertEqual(mov.organization_id, org.pk)
        self.assertEqual(mov.usuario_id, user.pk)

    def test_23_banco_cross(self):
        user, org_a = self._resolved("mv23")
        org_b = self._org("MV23B")
        banco = Banco.objects.create(usuario=user, nome="BX", organization=org_b)
        cat = Categoria.objects.create(
            usuario=user, nome="R", tipo=Categoria.Tipo.RECEITA, organization=org_a
        )
        self.client.force_login(user)
        antes = Movimento.objects.count()
        self.client.post(
            reverse("financeiro_movimento_novo"),
            {
                "tipo": Categoria.Tipo.RECEITA,
                "banco": banco.pk,
                "categoria": cat.pk,
                "valor": "10.00",
                "data": timezone.localdate().isoformat(),
                "descricao": "crossb",
            },
        )
        self.assertEqual(Movimento.objects.count(), antes)

    def test_24_categoria_cross(self):
        user, org_a = self._resolved("mv24")
        org_b = self._org("MV24B")
        banco = Banco.objects.create(usuario=user, nome="B", organization=org_a)
        cat = Categoria.objects.create(
            usuario=user, nome="X", tipo=Categoria.Tipo.RECEITA, organization=org_b
        )
        self.client.force_login(user)
        antes = Movimento.objects.count()
        self.client.post(
            reverse("financeiro_movimento_novo"),
            {
                "tipo": Categoria.Tipo.RECEITA,
                "banco": banco.pk,
                "categoria": cat.pk,
                "valor": "10.00",
                "data": timezone.localdate().isoformat(),
                "descricao": "crossc",
            },
        )
        self.assertEqual(Movimento.objects.count(), antes)

    def test_25_parent_null(self):
        user, org = self._resolved("mv25")
        banco = Banco.objects.create(usuario=user, nome="BN")
        cat = Categoria.objects.create(
            usuario=user, nome="CN", tipo=Categoria.Tipo.RECEITA
        )
        self.client.force_login(user)
        antes = Movimento.objects.count()
        self.client.post(
            reverse("financeiro_movimento_novo"),
            {
                "tipo": Categoria.Tipo.RECEITA,
                "banco": banco.pk,
                "categoria": cat.pk,
                "valor": "10.00",
                "data": timezone.localdate().isoformat(),
                "descricao": "pnull",
            },
        )
        self.assertEqual(Movimento.objects.count(), antes)

    def test_27_31_recebimento_mesma_org(self):
        user, org = self._resolved("rc27")
        cliente = self._cliente(user, org)
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cliente,
            descricao="Cob Rec",
            valor_original=Decimal("80.00"),
            data_vencimento=timezone.localdate(),
            criado_por=user,
        )
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_cobranca_recebimento", args=[cob.pk]),
            {
                "valor": "20,00",
                "data_recebimento": timezone.localdate().isoformat(),
                "forma_pagamento": "pix",
            },
        )
        self.assertEqual(resp.status_code, 302)
        rec = CobrancaRecebimento.objects.get(cobranca=cob)
        self.assertEqual(rec.organization_id, org.pk)
        self.assertEqual(rec.usuario_id, user.pk)
        self.assertEqual(rec.registrado_por_id, user.pk)

    def test_28_cobranca_cross(self):
        user_a, org_a = self._resolved("rc28a")
        user_b, org_b = self._resolved("rc28b")
        cliente_b = self._cliente(user_b, org_b)
        cob = Cobranca.objects.create(
            usuario=user_b,
            organization=org_b,
            cliente=cliente_b,
            descricao="De B",
            valor_original=Decimal("80.00"),
            data_vencimento=timezone.localdate(),
        )
        self.client.force_login(user_a)
        resp = self.client.post(
            reverse("financeiro_cobranca_recebimento", args=[cob.pk]),
            {
                "valor": "10,00",
                "data_recebimento": timezone.localdate().isoformat(),
                "forma_pagamento": "pix",
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(CobrancaRecebimento.objects.filter(cobranca=cob).exists())

    def test_29_movimento_cross_quando_informado(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        user, org_a = self._resolved("rc29")
        org_b = self._org("RC29B")
        cliente = self._cliente(user, org_a)
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org_a,
            cliente=cliente,
            descricao="Cob",
            valor_original=Decimal("80.00"),
            data_vencimento=timezone.localdate(),
        )
        banco = Banco.objects.create(usuario=user, nome="BB", organization=org_b)
        cat = Categoria.objects.create(
            usuario=user, nome="CB", tipo=Categoria.Tipo.RECEITA, organization=org_b
        )
        mov = Movimento.objects.create(
            usuario=user,
            banco=banco,
            categoria=cat,
            valor=Decimal("10"),
            data=timezone.localdate(),
            organization=org_b,
        )
        with self.assertRaises(Exception):
            registrar_recebimento(
                cob,
                valor=Decimal("10.00"),
                data_recebimento=timezone.localdate(),
                forma_pagamento="pix",
                autor=user,
                organization=org_a,
                movimento=mov,
            )
        self.assertEqual(CobrancaRecebimento.objects.filter(cobranca=cob).count(), 0)

    def test_30_recebimento_parent_null(self):
        user, org = self._resolved("rc30")
        cliente = self._cliente(user, org)
        cob = Cobranca.objects.create(
            usuario=user,
            cliente=cliente,
            descricao="Null Org Cob",
            valor_original=Decimal("80.00"),
            data_vencimento=timezone.localdate(),
        )
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_cobranca_recebimento", args=[cob.pk]),
            {
                "valor": "10,00",
                "data_recebimento": timezone.localdate().isoformat(),
                "forma_pagamento": "pix",
            },
        )
        self.assertEqual(CobrancaRecebimento.objects.filter(cobranca=cob).count(), 0)


class FinDualWriteUpdateCompatTests(FinDualWriteHelpers):
    def test_32_frontend_nao_altera_org(self):
        user, org_a = self._resolved("up32")
        org_b = self._org("UP32B")
        banco = Banco.objects.create(usuario=user, nome="Fixo", organization=org_a)
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_banco_editar", args=[banco.pk]),
            {
                "nome": "Fixo2",
                "agencia": "",
                "conta": "",
                "saldo_inicial": "",
                "organization": str(org_b.pk),
                "organization_id": str(org_b.pk),
            },
        )
        banco.refresh_from_db()
        self.assertEqual(banco.nome, "Fixo2")
        self.assertEqual(banco.organization_id, org_a.pk)

    def test_33_parent_nao_troca_cross_org(self):
        user, org_a = self._resolved("up33")
        org_b = self._org("UP33B")
        banco_a = Banco.objects.create(usuario=user, nome="BA", organization=org_a)
        banco_b = Banco.objects.create(usuario=user, nome="BB", organization=org_b)
        cat = Categoria.objects.create(
            usuario=user, nome="R", tipo=Categoria.Tipo.RECEITA, organization=org_a
        )
        mov = Movimento.objects.create(
            usuario=user,
            banco=banco_a,
            categoria=cat,
            valor=Decimal("5"),
            data=timezone.localdate(),
            organization=org_a,
            descricao="m",
        )
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_movimento_editar", args=[mov.pk]),
            {
                "tipo": Categoria.Tipo.RECEITA,
                "banco": banco_b.pk,
                "categoria": cat.pk,
                "valor": "5.00",
                "data": timezone.localdate().isoformat(),
                "descricao": "m",
            },
        )
        mov.refresh_from_db()
        self.assertEqual(mov.banco_id, banco_a.pk)
        self.assertEqual(mov.organization_id, org_a.pk)

    def test_34_org_b_nao_adota_org_a(self):
        user = self._user("up34")
        org_a = self._org("UP34A")
        org_b = self._org("UP34B")
        self._membership(user, org_a)
        banco = Banco.objects.create(usuario=user, nome="Ado", organization=org_a)
        request = RequestFactory().post(
            reverse("financeiro_banco_editar", args=[banco.pk]),
            {"nome": "Adotado", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        request.organization = org_b
        request.organization_context = CONTEXT_RESOLVED
        from django.http import Http404
        from financeiro.views import banco_editar

        with self.assertRaises(Http404):
            banco_editar(request, banco.pk)
        banco.refresh_from_db()
        self.assertEqual(banco.nome, "Ado")
        self.assertEqual(banco.organization_id, org_a.pk)

    def test_35_legado_null_nao_backfill_em_update(self):
        user, org = self._resolved("up35")
        banco = Banco.objects.create(usuario=user, nome="Legado")
        self.assertIsNone(banco.organization_id)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_banco_editar", args=[banco.pk]),
            {"nome": "Legado2", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(resp.status_code, 404)
        banco.refresh_from_db()
        self.assertEqual(banco.nome, "Legado")
        self.assertIsNone(banco.organization_id)

    def test_36_39_compat_actors(self):
        user, org = self._resolved("up36")
        cliente = self._cliente(user, org)
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": cliente.pk,
                "referencia": "ACT",
                "descricao": "A",
                "valor_total": "10,00",
                "status": "draft",
                "responsavel": user.pk,
                "observacoes": "",
            },
        )
        contrato = Contrato.objects.get(referencia="ACT")
        self.assertEqual(contrato.usuario_id, user.pk)
        self.assertEqual(contrato.criado_por_id, user.pk)
        self.assertEqual(contrato.responsavel_id, user.pk)
        self.assertEqual(contrato.organization_id, org.pk)

    def test_40_constraint_categoria_unica_por_usuario(self):
        names = {c.name for c in Categoria._meta.constraints}
        self.assertIn("uniq_financeiro_categoria_usuario_nome_tipo", names)

    def test_41_historico_sem_organization(self):
        names = {f.name for f in CobrancaHistorico._meta.get_fields()}
        self.assertNotIn("organization", names)


class FinDualWriteSeisWritePathsTests(FinDualWriteHelpers):
    def test_seis_write_paths_reais_nunca_null(self):
        user, org = self._resolved("six")
        cliente = self._cliente(user, org)
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Six Banco", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.client.post(
            reverse("financeiro_categoria_nova"),
            {"nome": "Six Cat", "tipo": Categoria.Tipo.RECEITA},
        )
        banco = Banco.objects.get(nome="Six Banco")
        cat = Categoria.objects.get(nome="Six Cat")
        self.client.post(
            reverse("financeiro_movimento_novo"),
            {
                "tipo": Categoria.Tipo.RECEITA,
                "banco": banco.pk,
                "categoria": cat.pk,
                "valor": "15.00",
                "data": timezone.localdate().isoformat(),
                "descricao": "six mov",
            },
        )
        self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": cliente.pk,
                "referencia": "SIX",
                "descricao": "Six",
                "valor_total": "200,00",
                "status": "draft",
                "responsavel": user.pk,
                "observacoes": "",
            },
        )
        contrato = Contrato.objects.get(referencia="SIX")
        self.client.post(
            reverse("financeiro_cobranca_nova"),
            {
                "tipo_lancamento": "unica",
                "cliente": cliente.pk,
                "contrato": contrato.pk,
                "descricao": "Six Cob",
                "valor": "200,00",
                "data_vencimento": timezone.localdate().isoformat(),
                "categoria": "honorarios",
                "responsavel": user.pk,
                "forma_prevista_pagamento": "",
                "observacoes_internas": "",
            },
        )
        cob = Cobranca.objects.get(descricao="Six Cob")
        self.client.post(
            reverse("financeiro_cobranca_recebimento", args=[cob.pk]),
            {
                "valor": "50,00",
                "data_recebimento": timezone.localdate().isoformat(),
                "forma_pagamento": "pix",
            },
        )
        rec = CobrancaRecebimento.objects.get(cobranca=cob)
        mov = Movimento.objects.get(descricao="six mov")
        for obj in (banco, cat, mov, contrato, cob, rec):
            self.assertEqual(obj.organization_id, org.pk, obj.__class__.__name__)
            self.assertIsNotNone(obj.organization_id)

    def test_cross_tenant_write_paths(self):
        user_a, org_a = self._resolved("xa")
        user_b, org_b = self._resolved("xb")
        cli_b = self._cliente(user_b, org_b, nome="DoB")
        self.client.force_login(user_a)
        resp = self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": cli_b.pk,
                "referencia": "HACK",
                "descricao": "Hack",
                "valor_total": "1,00",
                "status": "draft",
                "responsavel": user_a.pk,
                "observacoes": "",
            },
        )
        self.assertFalse(Contrato.objects.filter(referencia="HACK").exists())
        self.client.force_login(user_b)
        resp = self.client.post(
            reverse("financeiro_contrato_novo"),
            {
                "cliente": self._cliente(user_a, org_a, nome="DoA").pk,
                "referencia": "HACK2",
                "descricao": "Hack2",
                "valor_total": "1,00",
                "status": "draft",
                "responsavel": user_b.pk,
                "observacoes": "",
            },
        )
        self.assertFalse(Contrato.objects.filter(referencia="HACK2").exists())

    def test_helper_nao_relê_membership(self):
        user, org = self._resolved("hlp")
        request = RequestFactory().post("/")
        request.user = user
        request.organization = org
        request.organization_context = CONTEXT_RESOLVED
        self.assertEqual(organization_for_finance_write(request).pk, org.pk)
        request.organization_context = CONTEXT_AMBIGUOUS
        request.organization = None
        self.assertIsNone(organization_for_finance_write(request))
