"""FIN-AUTH: capabilities explícitas fail-closed. Caixa W1 é Organization-scoped."""

from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.models import Banco, Categoria, Cobranca, Contrato, Movimento
from financeiro.permissions import (
    PERM_CREATE_COBRANCAS,
    PERM_MANAGE_CAIXA,
    PERM_VIEW_CAIXA,
    PERM_VIEW_COBRANCAS,
    _tem_perm,
    pode_ver_caixa,
)
from financeiro.tests_helpers import (
    grant_all_finance_permissions,
    grant_billing_permissions,
    grant_caixa_permissions,
    grant_finance_permissions,
)
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente


class FinAuthHelpers(TestCase):
    def _user(self, username, *, perms=(), superuser=False):
        if superuser:
            user = User.objects.create_superuser(
                username=username, email=f"{username}@ex.test", password="senha123"
            )
        else:
            user = User.objects.create_user(username=username, password="senha123")
        if perms:
            grant_finance_permissions(user, *perms)
        return user

    def _org(self, name="Org A"):
        return Organization.objects.create(name=name)

    def _membership(self, user, org, *, role=Membership.Role.MEMBER):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=role,
            status=Membership.Status.ACTIVE,
        )

    def _resolved(self, username, *, perms=(), role=Membership.Role.MEMBER):
        user = self._user(username, perms=perms)
        org = self._org(f"Org {username}")
        self._membership(user, org, role=role)
        return user, org

    def _cliente(self, user, org, nome="Cli"):
        return Cliente.objects.create(
            user=user,
            nome=nome,
            email=f"{user.username}.{nome}@ex.test",
            organization=org,
        )

    def _denied(self, resp):
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))


class FinAuthFailOpenTests(FinAuthHelpers):
    def test_01_autenticado_sem_group_sem_perm_deny(self):
        user = self._user("nulo")
        self.assertFalse(user.groups.exists())
        self.client.force_login(user)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self._denied(resp)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self._denied(resp)
        resp = self.client.get(reverse("financeiro_cobranca_listar"))
        self._denied(resp)

    def test_02_group_sem_perm_deny(self):
        user = self._user("gempty")
        g = Group.objects.create(name="Vazio")
        user.groups.add(g)
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_listar")))
        self._denied(self.client.get(reverse("financeiro_extrato")))

    def test_03_permission_explicita_allow(self):
        user, _org = self._resolved("pex", perms=("view_cobrancas",))
        self.client.force_login(user)
        resp = self.client.get(reverse("financeiro_cobranca_listar"))
        self.assertEqual(resp.status_code, 200)

    def test_04_permission_via_group_allow(self):
        user, _org = self._resolved("gperm")
        g = Group.objects.create(name="Fin view")
        perm = Permission.objects.get(
            content_type__app_label="financeiro", codename="view_cobrancas"
        )
        g.permissions.add(perm)
        user.groups.add(g)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("financeiro_cobranca_listar")).status_code, 200)

    def test_05_membership_sem_perm_deny(self):
        user, _org = self._resolved("mem")
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_dashboard")))

    def test_06_owner_sem_perm_deny(self):
        user, _org = self._resolved("own", role=Membership.Role.OWNER)
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_listar")))
        self._denied(self.client.post(reverse("financeiro_banco_novo"), {"nome": "X"}))
        self.assertFalse(Banco.objects.filter(nome="X").exists())

    def test_07_responsavel_sem_perm_deny(self):
        user, org = self._resolved("resp")
        cli = self._cliente(user, org)
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            descricao="Resp",
            valor_original=Decimal("10.00"),
            data_vencimento=timezone.localdate(),
            responsavel=user,
            criado_por=user,
        )
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_detalhe", args=[cob.pk])))

    def test_08_cliente_user_sem_perm_deny(self):
        user, org = self._resolved("cliuser")
        self._cliente(user, org)
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_listar")))

    def test_09_10_11_group_vazio_nao_aumenta(self):
        user = self._user("gv")
        self.assertFalse(_tem_perm(user, PERM_VIEW_COBRANCAS))
        g = Group.objects.create(name="Nada")
        user.groups.add(g)
        self.assertFalse(_tem_perm(user, PERM_VIEW_COBRANCAS))
        user.groups.clear()
        self.assertFalse(_tem_perm(user, PERM_VIEW_COBRANCAS))

    def test_12_group_perm_somente_acao(self):
        user, _org = self._resolved("so_view")
        g = Group.objects.create(name="So view cob")
        g.permissions.add(
            Permission.objects.get(
                content_type__app_label="financeiro", codename="view_cobrancas"
            )
        )
        user.groups.add(g)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("financeiro_cobranca_listar")).status_code, 200)
        self._denied(self.client.get(reverse("financeiro_cobranca_nova")))
        self._denied(self.client.get(reverse("financeiro_extrato")))


class FinAuthCaixaTests(FinAuthHelpers):
    def test_13_sem_view_caixa(self):
        user, _ = self._resolved("nv", perms=("view_cobrancas",))
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_banco_listar")))
        self._denied(self.client.get(reverse("financeiro_categoria_listar")))
        self._denied(self.client.get(reverse("financeiro_extrato")))

    def test_14_com_view_caixa(self):
        user, _ = self._resolved("vc", perms=("view_caixa",))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("financeiro_banco_listar")).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro_extrato")).status_code, 200)

    def test_15_view_sem_manage_nao_cria(self):
        user, _ = self._resolved("vnm", perms=("view_caixa",))
        self.client.force_login(user)
        before = Banco.objects.count()
        self._denied(
            self.client.post(
                reverse("financeiro_banco_novo"),
                {"nome": "Nao", "agencia": "", "conta": "", "saldo_inicial": ""},
            )
        )
        self.assertEqual(Banco.objects.count(), before)

    def test_16_manage_caixa_movimenta(self):
        user, org = self._resolved("mc", perms=("manage_caixa",))
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Caixa Manage", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(resp.status_code, 302)
        banco = Banco.objects.get(nome="Caixa Manage")
        self.assertEqual(banco.organization_id, org.pk)
        self.assertTrue(pode_ver_caixa(user))

    def test_17_direct_url_sem_perm(self):
        user, _ = self._resolved("du")
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_banco_listar")))
        self._denied(self.client.post(reverse("financeiro_banco_novo"), {"nome": "X"}))

    def test_18_19_20_creates_sem_capability(self):
        user, org = self._resolved("noc")
        self.client.force_login(user)
        self._denied(self.client.post(reverse("financeiro_banco_novo"), {"nome": "B"}))
        self._denied(
            self.client.post(
                reverse("financeiro_categoria_nova"),
                {"nome": "C", "tipo": Categoria.Tipo.RECEITA},
            )
        )
        self.assertFalse(Banco.objects.filter(usuario=user).exists())
        self.assertFalse(Categoria.objects.filter(usuario=user).exists())

    def test_21_capability_nao_remove_tenantcontext(self):
        user = self._user("notenant", perms=("manage_caixa",))
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Sem Tenant", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Banco.objects.filter(nome="Sem Tenant").exists())


class FinAuthBillingTests(FinAuthHelpers):
    def test_22_sem_view_cobrancas(self):
        user, _ = self._resolved("sv", perms=("view_caixa",))
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_listar")))

    def test_23_view_cobrancas_leitura_user_scoped(self):
        user, org = self._resolved("vcob", perms=("view_cobrancas",))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("financeiro_cobranca_listar")).status_code, 200)

    def test_24_create_cobrancas_necessaria(self):
        user, org = self._resolved("cc", perms=("view_cobrancas",))
        cli = self._cliente(user, org)
        self.client.force_login(user)
        self._denied(
            self.client.post(
                reverse("financeiro_cobranca_nova"),
                {
                    "cliente": cli.pk,
                    "descricao": "X",
                    "valor": "10,00",
                    "data_vencimento": timezone.localdate().isoformat(),
                    "categoria": "honorarios",
                    "tipo_lancamento": "unica",
                },
            )
        )
        self.assertFalse(Cobranca.objects.filter(usuario=user).exists())

    def test_25_26_edit_cancel(self):
        user, org = self._resolved("ec", perms=("view_cobrancas",))
        cli = self._cliente(user, org)
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            descricao="E",
            valor_original=Decimal("10.00"),
            data_vencimento=timezone.localdate(),
            criado_por=user,
        )
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_editar", args=[cob.pk])))
        self._denied(self.client.get(reverse("financeiro_cobranca_cancelar", args=[cob.pk])))

    def test_27_view_recebimentos_backend(self):
        user, org = self._resolved(
            "vr", perms=("view_cobrancas", "create_recebimentos")
        )
        cli = self._cliente(user, org)
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            descricao="Com rec",
            valor_original=Decimal("50.00"),
            data_vencimento=timezone.localdate(),
            criado_por=user,
        )
        from financeiro.models import CobrancaRecebimento

        CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=user,
            organization=org,
            valor=Decimal("10.00"),
            data_recebimento=timezone.localdate(),
            registrado_por=user,
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("financeiro_cobranca_detalhe", args=[cob.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["recebimentos"]), [])
        self.assertNotContains(resp, "Histórico de recebimentos")

    def test_28_create_recebimentos(self):
        user, org = self._resolved("cr", perms=("view_cobrancas",))
        cli = self._cliente(user, org)
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            descricao="Rec",
            valor_original=Decimal("50.00"),
            data_vencimento=timezone.localdate(),
            criado_por=user,
        )
        self.client.force_login(user)
        self._denied(
            self.client.get(reverse("financeiro_cobranca_recebimento", args=[cob.pk]))
        )

    def test_29_view_relatorios(self):
        user, _ = self._resolved("rel", perms=("view_cobrancas",))
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_inadimplencia")))
        self._denied(self.client.get(reverse("financeiro_cobranca_previsao")))
        grant_finance_permissions(user, "view_relatorios_cobrancas")
        user = User.objects.get(pk=user.pk)
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("financeiro_cobranca_inadimplencia")).status_code, 200
        )

    def test_30_uma_perm_nao_concede_outra(self):
        user, _ = self._resolved("one", perms=("view_cobrancas",))
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_cobranca_nova")))
        self._denied(self.client.get(reverse("financeiro_extrato")))
        self._denied(self.client.get(reverse("financeiro_cobranca_inadimplencia")))


class FinAuthContratoTests(FinAuthHelpers):
    def test_contrato_usa_billing(self):
        user, org = self._resolved("ctv", perms=("view_cobrancas",))
        cli = self._cliente(user, org)
        contrato = Contrato.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            referencia="00001",
            descricao="C",
            valor_total=Decimal("100.00"),
            criado_por=user,
            responsavel=user,
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("financeiro_contrato_listar")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("financeiro_contrato_detalhe", args=[contrato.pk])).status_code,
            200,
        )
        self._denied(self.client.get(reverse("financeiro_contrato_novo")))
        self._denied(self.client.get(reverse("financeiro_contrato_editar", args=[contrato.pk])))


class FinAuthDashboardTests(FinAuthHelpers):
    def test_sem_capability_nao_acessa_dashboard(self):
        user, _ = self._resolved("dash0")
        self.client.force_login(user)
        self._denied(self.client.get(reverse("financeiro_dashboard")))

    def test_billing_nao_vaza_caixa(self):
        user, org = self._resolved("dashb", perms=("view_cobrancas",))
        Banco.objects.create(usuario=user, organization=org, nome="Segredo Caixa")
        self.client.force_login(user)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["mostrar_caixa"])
        self.assertNotContains(resp, "Segredo Caixa")
        self.assertNotContains(resp, "Saldos por banco")

    def test_caixa_nao_vaza_billing(self):
        user, org = self._resolved("dashc", perms=("view_caixa",))
        cli = self._cliente(user, org)
        Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=cli,
            descricao="Segredo Billing",
            valor_original=Decimal("99.00"),
            data_vencimento=timezone.localdate(),
            criado_por=user,
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("financeiro_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["mostrar_caixa"])
        self.assertIsNone(resp.context["cobrancas"])
        self.assertNotContains(resp, "Segredo Billing")


class FinAuthTenantCapabilityTests(FinAuthHelpers):
    def test_31_resolved_com_capability_cria(self):
        user, org = self._resolved("okw", perms=("manage_caixa",))
        self.client.force_login(user)
        resp = self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "Auth Write", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Banco.objects.get(nome="Auth Write").organization_id, org.pk)

    def test_32_33_34_tenant_fail_closed(self):
        user = self._user("ctx", perms=("manage_caixa",))
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "NoneCtx", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertFalse(Banco.objects.filter(nome="NoneCtx").exists())

        org_a = self._org("A")
        org_b = self._org("B")
        self._membership(user, org_a)
        self._membership(user, org_b)
        self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "AmbCtx", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertFalse(Banco.objects.filter(nome="AmbCtx").exists())

    def test_35_superuser_sem_tenant_nao_cria(self):
        user = self._user("su", superuser=True)
        self.client.force_login(user)
        self.client.post(
            reverse("financeiro_banco_novo"),
            {"nome": "SU Bypass", "agencia": "", "conta": "", "saldo_inicial": ""},
        )
        self.assertFalse(Banco.objects.filter(nome="SU Bypass").exists())


class FinAuthSameOrgTests(FinAuthHelpers):
    def test_same_org_capability_diferente(self):
        org = self._org("Shared")
        a1 = self._user("a1", perms=("view_cobrancas",))
        a2 = self._user("a2")
        self._membership(a1, org)
        self._membership(a2, org)
        self.client.force_login(a1)
        self.assertEqual(self.client.get(reverse("financeiro_cobranca_listar")).status_code, 200)
        self.client.force_login(a2)
        self._denied(self.client.get(reverse("financeiro_cobranca_listar")))

    def test_read_scope_caixa_compartilha_same_org(self):
        org = self._org("SameRead")
        a1 = self._user("r1", perms=("view_caixa", "manage_caixa"))
        a2 = self._user("r2", perms=("view_caixa", "manage_caixa"))
        self._membership(a1, org)
        self._membership(a2, org)
        Banco.objects.create(usuario=a1, organization=org, nome="Banco A1")
        Banco.objects.create(usuario=a2, organization=org, nome="Banco A2")
        self.client.force_login(a1)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self.assertContains(resp, "Banco A1")
        self.assertContains(resp, "Banco A2")
        self.client.force_login(a2)
        resp = self.client.get(reverse("financeiro_banco_listar"))
        self.assertContains(resp, "Banco A2")
        self.assertContains(resp, "Banco A1")

    def test_cross_org_parent_ainda_recusado(self):
        org_a = self._org("OA")
        org_b = self._org("OB")
        user_a = self._user("xa", perms=("view_cobrancas", "create_cobrancas"))
        self._membership(user_a, org_a)
        cli_b = Cliente.objects.create(
            user=user_a,
            nome="Cli B",
            email="clib@ex.test",
            organization=org_b,
        )
        self.client.force_login(user_a)
        before = Cobranca.objects.count()
        self.client.post(
            reverse("financeiro_cobranca_nova"),
            {
                "cliente": cli_b.pk,
                "descricao": "Cross",
                "valor": "10,00",
                "data_vencimento": timezone.localdate().isoformat(),
                "categoria": "honorarios",
                "tipo_lancamento": "unica",
            },
        )
        self.assertEqual(Cobranca.objects.count(), before)

    def test_helper_fail_closed(self):
        user = self._user("hf")
        self.assertFalse(_tem_perm(user, PERM_VIEW_CAIXA))
        self.assertFalse(_tem_perm(user, PERM_MANAGE_CAIXA))
        self.assertFalse(_tem_perm(user, PERM_VIEW_COBRANCAS))
        self.assertFalse(_tem_perm(user, PERM_CREATE_COBRANCAS))
