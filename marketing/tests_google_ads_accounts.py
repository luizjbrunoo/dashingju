"""Listagem e seleção de contas Google Ads. Sem rede Google, sem Mutate."""

from __future__ import annotations

import inspect
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketing.context_processors import marketing_permissoes
from marketing.models import OrganizationGoogleAdsConnection
from marketing.permissions import (
    pode_gerenciar_integracoes_marketing,
    pode_ver_marketing,
)
from marketing.services import google_ads_accounts as accounts_mod
from marketing.services.google_ads_accounts import (
    MSG_CLOUD_PROJECT,
    MSG_REAUTH,
    MSG_SELECT_INVALID,
    GoogleAdsAccount,
    GoogleAdsAccountsError,
    _discover_accounts,
)
from marketing.services.google_ads_client import GoogleAdsClientError
from marketing.services.google_ads_metrics import GoogleAdsMetricsError
from marketing.tests_rbac import _grupo_restrito
from organizacoes.models import Membership
from organizacoes.provider_credentials import get, has, put
from organizacoes.role_capabilities import create_organization_with_owner

User = get_user_model()

_OAUTH = {
    "GOOGLE_ADS_CLIENT_ID": "cid.apps.googleusercontent.com",
    "GOOGLE_ADS_CLIENT_SECRET": "oauth-client-secret-test",
    "GOOGLE_ADS_OAUTH_REDIRECT_URI": (
        "https://staging.example.com/marketing/google-ads/callback/"
    ),
}


def _fernet_settings():
    cfg = dict(_OAUTH)
    cfg["INTEGRATION_CREDENTIALS_KEY"] = Fernet.generate_key().decode("ascii")
    return cfg


def _account(
    customer_id,
    *,
    login_customer_id="",
    name="Conta",
    currency="BRL",
    manager=False,
):
    return GoogleAdsAccount(
        customer_id=customer_id,
        login_customer_id=login_customer_id,
        descriptive_name=name,
        currency_code=currency,
        manager=manager,
        status="ENABLED",
        test_account=False,
    )


@override_settings(**_fernet_settings())
class GoogleAdsAccountsTests(TestCase):
    def setUp(self):
        self.http_a = Client()
        self.http_b = Client()
        self.user_a = User.objects.create_user("ads_acc_a", password="senha123")
        self.user_b = User.objects.create_user("ads_acc_b", password="senha123")
        self.org_a, _, _ = create_organization_with_owner(
            name="Ads Acc Org A", user=self.user_a
        )
        self.org_b, _, _ = create_organization_with_owner(
            name="Ads Acc Org B", user=self.user_b
        )
        self.http_a.login(username="ads_acc_a", password="senha123")
        self.http_b.login(username="ads_acc_b", password="senha123")
        self.account_a = _account("1112223333", name="Conta Direta A")
        self.account_b = _account("9998887777", name="Conta Direta B")
        self.account_mcc = _account(
            "1231231234",
            login_customer_id="5555555555",
            name="Cliente MCC A",
        )

    def _authorize(self, org, refresh="rt-org-a-secret"):
        put(
            organization=org,
            provider="google_ads",
            secrets={"refresh_token": refresh},
        )
        return OrganizationGoogleAdsConnection.objects.create(
            organization=org,
            status=OrganizationGoogleAdsConnection.Status.AUTHORIZED,
            connected_at=timezone.now(),
        )

    def _list_for_org(self, org, accounts):
        def _fake(*, organization):
            if organization.pk == org.pk:
                return list(accounts)
            return []

        return _fake

    def test_org_a_authorized_lista_contas_da_a(self):
        self._authorize(self.org_a)
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            side_effect=self._list_for_org(self.org_a, [self.account_a]),
        ) as mocked:
            resp = self.http_a.get(reverse("marketing_google_ads_accounts"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Escolher conta Google Ads")
        self.assertContains(resp, "Conta Direta A")
        self.assertContains(resp, "111-222-3333")
        self.assertNotContains(resp, "Conta Direta B")
        mocked.assert_called()
        self.assertEqual(mocked.call_args.kwargs["organization"].pk, self.org_a.pk)

    def test_org_b_nao_recebe_conta_da_a(self):
        self._authorize(self.org_a)
        self._authorize(self.org_b, refresh="rt-org-b-secret")

        def _fake(*, organization):
            if organization.pk == self.org_a.pk:
                return [self.account_a]
            if organization.pk == self.org_b.pk:
                return [self.account_b]
            return []

        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            side_effect=_fake,
        ):
            resp_a = self.http_a.get(reverse("marketing_google_ads_accounts"))
            resp_b = self.http_b.get(reverse("marketing_google_ads_accounts"))
        self.assertContains(resp_a, "Conta Direta A")
        self.assertNotContains(resp_a, "Conta Direta B")
        self.assertContains(resp_b, "Conta Direta B")
        self.assertNotContains(resp_b, "Conta Direta A")

    def test_tenant_none_zero_vault_zero_api(self):
        solo = User.objects.create_user("ads_acc_solo", password="senha123")
        Group.objects.get(name="Marketing — acesso completo").user_set.add(solo)
        http = Client()
        http.login(username="ads_acc_solo", password="senha123")
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts"
        ) as mocked_list, patch(
            "organizacoes.provider_credentials.get"
        ) as mocked_get:
            resp = http.get(reverse("marketing_google_ads_accounts"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("marketing_dashboard"))
        mocked_list.assert_not_called()
        mocked_get.assert_not_called()

    def test_tenant_ambiguous_zero_vault_zero_api(self):
        Membership.objects.create(
            user=self.user_a,
            organization=self.org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts"
        ) as mocked_list, patch(
            "organizacoes.provider_credentials.get"
        ) as mocked_get:
            resp = self.http_a.get(reverse("marketing_google_ads_accounts"))
            resp_post = self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": "1112223333"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp_post.status_code, 302)
        mocked_list.assert_not_called()
        mocked_get.assert_not_called()

    def test_sem_manage_endpoint_negado_zero_api(self):
        user = User.objects.create_user("ads_acc_view", password="senha123")
        Membership.objects.create(
            user=user,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        user.groups.add(_grupo_restrito("Ads acc só view", "view_marketing"))
        http = Client()
        http.login(username="ads_acc_view", password="senha123")
        self.assertTrue(pode_ver_marketing(user))
        self.assertFalse(pode_gerenciar_integracoes_marketing(user))
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts"
        ) as mocked:
            resp = http.get(reverse("marketing_google_ads_accounts"))
            resp_post = http.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": "1112223333"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("clientes"))
        self.assertEqual(resp_post.url, reverse("clientes"))
        mocked.assert_not_called()
        dash = http.get(reverse("marketing_dashboard"))
        self.assertNotContains(dash, "Escolher conta")
        self.assertNotContains(dash, "Trocar conta")

    def test_view_marketing_sem_manage_nao_basta(self):
        self.test_sem_manage_endpoint_negado_zero_api()

    def test_selecao_direta_grava_configured_sem_login_customer(self):
        conn = self._authorize(self.org_a)
        connected_at = conn.connected_at
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            return_value=[self.account_a],
        ), patch("organizacoes.provider_credentials.put") as mocked_put:
            resp = self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": "111-222-3333", "login_customer_id": ""},
            )
        self.assertEqual(resp.status_code, 302)
        conn.refresh_from_db()
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.CONFIGURED)
        self.assertEqual(conn.customer_id, "1112223333")
        self.assertEqual(conn.login_customer_id, "")
        self.assertEqual(conn.descriptive_name, "Conta Direta A")
        self.assertEqual(conn.currency_code, "BRL")
        self.assertEqual(conn.connected_at, connected_at)
        mocked_put.assert_not_called()
        self.assertEqual(
            get(organization=self.org_a, provider="google_ads"),
            {"refresh_token": "rt-org-a-secret"},
        )

    def test_mcc_grava_cliente_e_login_do_manager(self):
        self._authorize(self.org_a)
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            return_value=[self.account_mcc],
        ):
            resp = self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {
                    "customer_id": "1231231234",
                    "login_customer_id": "5555555555",
                },
            )
        self.assertEqual(resp.status_code, 302)
        conn = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.CONFIGURED)
        self.assertEqual(conn.customer_id, "1231231234")
        self.assertEqual(conn.login_customer_id, "5555555555")
        self.assertEqual(conn.descriptive_name, "Cliente MCC A")

    def test_post_adulterado_sem_write(self):
        self._authorize(self.org_a)
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            return_value=[self.account_a],
        ):
            resp = self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": "0000000000", "login_customer_id": ""},
            )
        self.assertEqual(resp.status_code, 302)
        conn = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.AUTHORIZED)
        self.assertEqual(conn.customer_id, "")
        follow = self.http_a.get(resp.url)
        self.assertContains(follow, MSG_SELECT_INVALID)

    def test_conta_da_org_b_no_post_da_org_a_sem_write(self):
        self._authorize(self.org_a)
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            return_value=[self.account_a],
        ):
            self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": self.account_b.customer_id, "login_customer_id": ""},
            )
        conn = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.AUTHORIZED)
        self.assertEqual(conn.customer_id, "")
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(
                organization=self.org_a, customer_id="9998887777"
            ).exists()
        )

    def test_falha_google_na_selecao_nao_corrompe_configured(self):
        conn = self._authorize(self.org_a)
        conn.status = OrganizationGoogleAdsConnection.Status.CONFIGURED
        conn.customer_id = "1112223333"
        conn.descriptive_name = "Conta Direta A"
        conn.save()
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            side_effect=GoogleAdsAccountsError("unexpected", "falhou"),
        ):
            self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": "2223334444", "login_customer_id": ""},
            )
        conn.refresh_from_db()
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.CONFIGURED)
        self.assertEqual(conn.customer_id, "1112223333")
        self.assertEqual(conn.descriptive_name, "Conta Direta A")

    def test_cloud_project_nao_desconecta(self):
        conn = self._authorize(self.org_a)
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            side_effect=GoogleAdsAccountsError(
                "cloud_project_not_approved", MSG_CLOUD_PROJECT
            ),
        ):
            resp = self.http_a.get(reverse("marketing_google_ads_accounts"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, MSG_CLOUD_PROJECT)
        conn.refresh_from_db()
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.AUTHORIZED)
        self.assertTrue(has(organization=self.org_a, provider="google_ads"))

    def test_refresh_token_ausente_fail_closed(self):
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            status=OrganizationGoogleAdsConnection.Status.AUTHORIZED,
        )
        with patch(
            "marketing.services.google_ads_accounts.google_ads_client_for_organization",
            side_effect=GoogleAdsClientError("missing_refresh"),
        ) as mocked:
            resp = self.http_a.get(reverse("marketing_google_ads_accounts"))
        mocked.assert_called()
        self.assertEqual(resp.status_code, 302)
        follow = self.http_a.get(resp.url)
        self.assertContains(follow, MSG_REAUTH)
        conn = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.AUTHORIZED)

    def test_refresh_token_revogado_nao_apaga(self):
        conn = self._authorize(self.org_a)
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            side_effect=GoogleAdsAccountsError("token_revoked", MSG_REAUTH),
        ):
            resp = self.http_a.get(reverse("marketing_google_ads_accounts"))
        self.assertEqual(resp.status_code, 302)
        follow = self.http_a.get(resp.url)
        self.assertContains(follow, MSG_REAUTH)
        conn.refresh_from_db()
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.AUTHORIZED)
        self.assertEqual(
            get(organization=self.org_a, provider="google_ads")["refresh_token"],
            "rt-org-a-secret",
        )

    def test_configured_troca_para_outra_conta_da_mesma_org(self):
        conn = self._authorize(self.org_a)
        conn.status = OrganizationGoogleAdsConnection.Status.CONFIGURED
        conn.customer_id = "1112223333"
        conn.descriptive_name = "Conta Direta A"
        conn.save()
        outra = _account("2223334444", name="Conta Direta A2")
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            return_value=[self.account_a, outra],
        ), patch(
            "marketing.services.google_ads_metrics.fetch_google_ads_metrics",
            side_effect=GoogleAdsMetricsError("skipped"),
        ):
            dash = self.http_a.get(reverse("marketing_dashboard"))
            self.assertContains(dash, "Trocar conta")
            self.assertContains(dash, "Conta Direta A")
            self.http_a.post(
                reverse("marketing_google_ads_account_select"),
                {"customer_id": "2223334444", "login_customer_id": ""},
            )
        conn.refresh_from_db()
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.CONFIGURED)
        self.assertEqual(conn.customer_id, "2223334444")
        self.assertEqual(conn.descriptive_name, "Conta Direta A2")
        self.assertTrue(has(organization=self.org_a, provider="google_ads"))

    def test_disconnect_apaga_vault_e_zera_conexao(self):
        conn = self._authorize(self.org_a)
        conn.status = OrganizationGoogleAdsConnection.Status.CONFIGURED
        conn.customer_id = "1112223333"
        conn.login_customer_id = "5555555555"
        conn.descriptive_name = "Cliente MCC A"
        conn.currency_code = "BRL"
        conn.save()
        resp = self.http_a.post(reverse("marketing_google_ads_disconnect"))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(has(organization=self.org_a, provider="google_ads"))
        conn.refresh_from_db()
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.DISCONNECTED)
        self.assertEqual(conn.customer_id, "")
        self.assertEqual(conn.login_customer_id, "")
        self.assertEqual(conn.descriptive_name, "")
        self.assertEqual(conn.currency_code, "")
        self.assertIsNone(conn.connected_at)

    def test_repr_log_mensagens_sem_secrets(self):
        self._authorize(self.org_a)
        account = self.account_a
        self.assertNotIn("refresh_token", repr(account))
        self.assertNotIn("access_token", str(account))
        self.assertNotIn("oauth-client-secret-test", repr(account))
        with patch(
            "marketing.services.google_ads_accounts.list_accessible_accounts",
            return_value=[account],
        ):
            resp = self.http_a.get(reverse("marketing_google_ads_accounts"))
        body = resp.content.decode()
        self.assertNotIn("rt-org-a-secret", body)
        self.assertNotIn("oauth-client-secret-test", body)
        self.assertNotIn("access_token", body)
        perms = marketing_permissoes(resp.wsgi_request)["mkt_perms"]
        self.assertTrue(perms.gerenciar_integracoes)

    def test_nenhuma_operacao_mutate(self):
        src = inspect.getsource(accounts_mod)
        self.assertNotIn(".mutate(", src)
        self.assertNotIn("MutateOperation", src)
        from marketing.services import google_ads_client as client_mod
        from marketing import views_google_ads_accounts as views_mod

        self.assertNotIn(".mutate(", inspect.getsource(client_mod))
        self.assertNotIn("MutateOperation", inspect.getsource(client_mod))
        self.assertNotIn(".mutate(", inspect.getsource(views_mod))
        self.assertNotIn("MutateOperation", inspect.getsource(views_mod))


class GoogleAdsAccountsCsrfTests(TestCase):
    def test_select_sem_csrf_bloqueado(self):
        user = User.objects.create_user("ads_acc_csrf", password="senha123")
        create_organization_with_owner(name="Ads Acc CSRF Org", user=user)
        http = Client(enforce_csrf_checks=True)
        http.login(username="ads_acc_csrf", password="senha123")
        resp = http.post(
            reverse("marketing_google_ads_account_select"),
            {"customer_id": "1112223333"},
        )
        self.assertEqual(resp.status_code, 403)


class GoogleAdsAccountDtoTests(TestCase):
    def test_dto_via_mcc_e_sem_secret(self):
        direto = _account("1112223333")
        mcc = _account("1231231234", login_customer_id="5555555555")
        self.assertFalse(direto.via_mcc)
        self.assertTrue(mcc.via_mcc)
        self.assertEqual(direto.customer_id_display, "111-222-3333")
        self.assertNotIn("refresh_token", repr(mcc))
        self.assertNotIn("client_secret", str(direto))


class _Attr:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _RecordingService:
    def __init__(self, mutate_calls):
        self._mutate_calls = mutate_calls

    def mutate(self, *args, **kwargs):
        self._mutate_calls.append((args, kwargs))
        raise AssertionError("Mutate não deve ser chamado")


class _FakeCustomerService(_RecordingService):
    def __init__(self, resource_names, mutate_calls):
        super().__init__(mutate_calls)
        self._resource_names = resource_names

    def list_accessible_customers(self):
        return _Attr(resource_names=list(self._resource_names))


class _FakeGoogleAdsService(_RecordingService):
    def __init__(self, customers, clients_by_manager, mutate_calls):
        super().__init__(mutate_calls)
        self._customers = customers
        self._clients_by_manager = clients_by_manager

    def search(self, customer_id, query):
        if "FROM customer_client" in query:
            rows = []
            for child in self._clients_by_manager.get(str(customer_id), []):
                rows.append(_Attr(customer_client=child))
            return rows
        customer = self._customers[str(customer_id)]
        return [_Attr(customer=customer)]


class _FakeGoogleAdsClient:
    def __init__(self, *, resource_names, customers, clients_by_manager=None):
        self.login_customer_id = None
        self.mutate_calls = []
        self.services_requested = []
        self._resource_names = resource_names
        self._customers = customers
        self._clients_by_manager = clients_by_manager or {}

    def get_service(self, name):
        self.services_requested.append(name)
        if "Mutate" in name:
            raise AssertionError(f"serviço Mutate inesperado: {name}")
        if name == "CustomerService":
            return _FakeCustomerService(self._resource_names, self.mutate_calls)
        if name == "GoogleAdsService":
            return _FakeGoogleAdsService(
                self._customers, self._clients_by_manager, self.mutate_calls
            )
        raise AssertionError(f"serviço inesperado: {name}")


def _customer_row(
    customer_id,
    *,
    name,
    manager=False,
    status="ENABLED",
    currency="BRL",
):
    return _Attr(
        id=customer_id,
        descriptive_name=name,
        currency_code=currency,
        manager=manager,
        status=status,
        test_account=False,
    )


def _client_row(
    customer_id,
    *,
    name,
    manager=False,
    status="ENABLED",
    currency="BRL",
    level=1,
):
    return _Attr(
        id=customer_id,
        descriptive_name=name,
        currency_code=currency,
        manager=manager,
        status=status,
        test_account=False,
        level=level,
    )


class GoogleAdsDiscoveryAdapterTests(TestCase):
    def test_descoberta_direta_login_customer_vazio(self):
        client = _FakeGoogleAdsClient(
            resource_names=["customers/111-222-3333"],
            customers={
                "1112223333": _customer_row(
                    "1112223333", name="Conta Direta", manager=False
                )
            },
        )
        result = _discover_accounts(client)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].customer_id, "1112223333")
        self.assertEqual(result[0].login_customer_id, "")
        self.assertEqual(result[0].descriptive_name, "Conta Direta")
        self.assertFalse(result[0].via_mcc)
        self.assertEqual(client.mutate_calls, [])
        self.assertNotIn("Mutate", " ".join(client.services_requested))

    def test_descoberta_mcc_folha_com_login_do_manager(self):
        manager_id = "5555555555"
        leaf_id = "1231231234"
        canceled_id = "1111111111"
        closed_id = "2222222222"
        removed_id = "4444444444"
        nested_manager_id = "3333333333"
        client = _FakeGoogleAdsClient(
            resource_names=[f"customers/{manager_id}"],
            customers={
                manager_id: _customer_row(
                    manager_id, name="MCC Raiz", manager=True
                )
            },
            clients_by_manager={
                manager_id: [
                    _client_row(leaf_id, name="Cliente Folha"),
                    _client_row(
                        canceled_id, name="Cancelada", status="CANCELED"
                    ),
                    _client_row(closed_id, name="Fechada", status="CLOSED"),
                    _client_row(removed_id, name="Removida", status="REMOVED"),
                    _client_row(
                        nested_manager_id, name="Sub MCC", manager=True
                    ),
                ]
            },
        )
        result = _discover_accounts(client)
        by_cid = {item.customer_id: item for item in result if item.via_mcc}
        self.assertIn(leaf_id, by_cid)
        leaf = by_cid[leaf_id]
        self.assertEqual(leaf.customer_id, leaf_id)
        self.assertEqual(leaf.login_customer_id, manager_id)
        self.assertEqual(leaf.descriptive_name, "Cliente Folha")
        self.assertTrue(leaf.via_mcc)
        self.assertFalse(
            any(item.customer_id == manager_id and item.via_mcc for item in result)
        )
        self.assertFalse(
            any(
                item.customer_id
                in {canceled_id, closed_id, removed_id, nested_manager_id}
                for item in result
            )
        )
        self.assertEqual(client.mutate_calls, [])
        self.assertNotIn("Mutate", " ".join(client.services_requested))
        self.assertIn("CustomerService", client.services_requested)
        self.assertIn("GoogleAdsService", client.services_requested)
