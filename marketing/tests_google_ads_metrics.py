"""Métricas Google Ads read-only. Sem rede, sem Mutate."""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketing.models import OrganizationGoogleAdsConnection
from marketing.services import google_ads_metrics as metrics_mod
from marketing.services.google_ads_metrics import (
    MSG_UNAVAILABLE,
    GoogleAdsMetricsError,
    cpc_from_cost_and_clicks,
    ctr_to_percent,
    daily_gaql,
    fetch_google_ads_metrics,
    micros_to_decimal,
    sanitize_period_days,
    totals_gaql,
)
from marketing.services.periodo import PeriodoMarketing
from organizacoes.provider_credentials import put
from organizacoes.role_capabilities import create_organization_with_owner

User = get_user_model()


def _fernet_settings():
    return {
        "GOOGLE_ADS_CLIENT_ID": "cid.apps.googleusercontent.com",
        "GOOGLE_ADS_CLIENT_SECRET": "oauth-client-secret-test",
        "GOOGLE_ADS_OAUTH_REDIRECT_URI": (
            "https://staging.example.com/marketing/google-ads/callback/"
        ),
        "INTEGRATION_CREDENTIALS_KEY": Fernet.generate_key().decode("ascii"),
    }


class _FakeService:
    def __init__(self, recorder, totals_rows, daily_rows):
        self.recorder = recorder
        self.totals_rows = totals_rows
        self.daily_rows = daily_rows

    def search(self, customer_id, query):
        self.recorder.append({"customer_id": customer_id, "query": query})
        if "ORDER BY segments.date" in query:
            return list(self.daily_rows)
        return list(self.totals_rows)

    def mutate(self, *args, **kwargs):
        raise AssertionError("Mutate não deve ser chamado")


class _FakeClient:
    def __init__(self, recorder, totals_rows, daily_rows):
        self.login_customer_id = None
        self._service = _FakeService(recorder, totals_rows, daily_rows)

    def get_service(self, name):
        if name != "GoogleAdsService":
            raise AssertionError(f"serviço inesperado: {name}")
        if "Mutate" in name:
            raise AssertionError("Mutate não deve ser chamado")
        return self._service


def _metrics(**kwargs):
    return SimpleNamespace(
        clicks=kwargs.get("clicks", 10),
        impressions=kwargs.get("impressions", 200),
        cost_micros=kwargs.get("cost_micros", 5_000_000),
        ctr=kwargs.get("ctr", 0.05),
        conversions=kwargs.get("conversions", 1.5),
    )


def _totals_row(**kwargs):
    return SimpleNamespace(metrics=_metrics(**kwargs), segments=None)


def _daily_row(day, **kwargs):
    return SimpleNamespace(
        metrics=_metrics(**kwargs),
        segments=SimpleNamespace(date=day.isoformat()),
    )


@override_settings(**_fernet_settings())
class GoogleAdsMetricsTests(TestCase):
    def setUp(self):
        self.http_a = Client()
        self.http_b = Client()
        self.user_a = User.objects.create_user("ads_met_a", password="senha123")
        self.user_b = User.objects.create_user("ads_met_b", password="senha123")
        self.org_a, _, _ = create_organization_with_owner(
            name="Ads Met Org A", user=self.user_a
        )
        self.org_b, _, _ = create_organization_with_owner(
            name="Ads Met Org B", user=self.user_b
        )
        self.http_a.login(username="ads_met_a", password="senha123")
        self.http_b.login(username="ads_met_b", password="senha123")
        self.calls = []

    def _configured(self, org, *, customer_id="1112223333", login=""):
        put(
            organization=org,
            provider="google_ads",
            secrets={"refresh_token": f"rt-{org.pk}-secret"},
        )
        return OrganizationGoogleAdsConnection.objects.create(
            organization=org,
            status=OrganizationGoogleAdsConnection.Status.CONFIGURED,
            customer_id=customer_id,
            login_customer_id=login,
            descriptive_name="Conta Live A",
            connected_at=timezone.now(),
        )

    def _client(self, totals=None, daily=None):
        return _FakeClient(
            self.calls,
            totals if totals is not None else [_totals_row()],
            daily if daily is not None else [],
        )

    def test_configured_api_ok_live(self):
        self._configured(self.org_a)
        today = timezone.localdate()
        daily = [
            _daily_row(today - timedelta(days=1), clicks=4, impressions=80),
            _daily_row(today, clicks=6, impressions=120),
        ]
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(daily=daily),
        ):
            resp = self.http_a.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dados reais")
        self.assertContains(resp, "Google Ads · Conta Live A")
        self.assertNotContains(resp, "Demonstração")
        self.assertContains(resp, "10")
        self.assertEqual(resp.context["google_ads_mode"], "live")
        self.assertEqual(resp.context["totals"]["clicks"], 10)
        self.assertEqual(resp.context["totals"]["impressions"], 200)

    def test_disconnected_demo_zero_api(self):
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            status=OrganizationGoogleAdsConnection.Status.DISCONNECTED,
        )
        with patch(
            "marketing.services.google_ads_metrics.fetch_google_ads_metrics"
        ) as mocked:
            resp = self.http_a.get(reverse("marketing_dashboard"))
        mocked.assert_not_called()
        self.assertEqual(resp.context["google_ads_mode"], "demo")
        self.assertContains(resp, "Demonstração")
        self.assertContains(resp, "Custo (exemplo)")

    def test_authorized_sem_customer_id_demo_zero_api(self):
        put(
            organization=self.org_a,
            provider="google_ads",
            secrets={"refresh_token": "rt-a-secret"},
        )
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            status=OrganizationGoogleAdsConnection.Status.AUTHORIZED,
        )
        with patch(
            "marketing.services.google_ads_metrics.fetch_google_ads_metrics"
        ) as mocked:
            resp = self.http_a.get(reverse("marketing_dashboard"))
        mocked.assert_not_called()
        self.assertEqual(resp.context["google_ads_mode"], "demo")
        self.assertContains(resp, "Demonstração")

    def test_configured_api_erro_live_error_nunca_demo_silencioso(self):
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.fetch_google_ads_metrics",
            side_effect=GoogleAdsMetricsError("api_failure", MSG_UNAVAILABLE),
        ):
            resp = self.http_a.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["google_ads_mode"], "live_error")
        self.assertContains(resp, MSG_UNAVAILABLE)
        self.assertNotContains(resp, "Demonstração")
        self.assertNotContains(resp, "Dados reais")
        self.assertNotContains(resp, "Custo (exemplo)")
        self.assertIsNone(resp.context["totals"]["clicks"])
        conn = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.CONFIGURED)

    def test_org_a_nao_consulta_customer_da_org_b(self):
        self._configured(self.org_a, customer_id="1112223333")
        self._configured(
            self.org_b, customer_id="9998887777", login="5555555555"
        )
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(),
        ) as mocked:
            fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(mocked.call_args.kwargs["organization"].pk, self.org_a.pk)
        self.assertTrue(self.calls)
        for call in self.calls:
            self.assertEqual(call["customer_id"], "1112223333")
            self.assertNotIn("9998887777", call["query"])
            self.assertNotIn("rt-", call["query"])

    def test_organization_none_fail_closed(self):
        with self.assertRaises(GoogleAdsMetricsError) as ctx:
            fetch_google_ads_metrics(organization=None, days=7)
        self.assertEqual(ctx.exception.reason, "missing_org")

    def test_mcc_usa_login_customer_id(self):
        self._configured(
            self.org_a, customer_id="1231231234", login="5555555555"
        )
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(),
        ) as mocked:
            fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(mocked.call_args.kwargs["login_customer_id"], "5555555555")
        self.assertEqual(self.calls[0]["customer_id"], "1231231234")

    def test_folha_abaixo_do_mcc_staging_usa_login_raiz(self):
        self._configured(
            self.org_a, customer_id="1231231234", login="3998742827"
        )
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(),
        ) as mocked:
            fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(mocked.call_args.kwargs["login_customer_id"], "3998742827")
        self.assertEqual(mocked.call_args.kwargs["organization"].pk, self.org_a.pk)
        self.assertTrue(self.calls)
        for call in self.calls:
            self.assertEqual(call["customer_id"], "1231231234")
            self.assertNotIn("3998742827", call["query"])

    def test_acesso_direto_sem_login_customer_id(self):
        self._configured(self.org_a, customer_id="1112223333", login="")
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(),
        ) as mocked:
            fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(mocked.call_args.kwargs["login_customer_id"], "")

    def test_clicks_impressions_corretos(self):
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(clicks=42, impressions=840)]
            ),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.clicks, 42)
        self.assertEqual(snap.kpis.impressions, 840)

    def test_cost_micros_decimal(self):
        self.assertEqual(micros_to_decimal(5_000_000), Decimal("5.0000"))
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(cost_micros=1_250_000)]
            ),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.cost, Decimal("1.2500"))
        self.assertEqual(snap.kpis.cost_micros, 1_250_000)

    def test_ctr_percentual(self):
        self.assertEqual(ctr_to_percent(0.0532), Decimal("5.32"))
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(totals=[_totals_row(ctr=0.05, impressions=200)]),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.ctr_percent, Decimal("5.00"))

    def test_cpc_cost_micros_clicks(self):
        self.assertEqual(
            cpc_from_cost_and_clicks(1_500_000, 1), Decimal("1.50")
        )
        self.assertEqual(
            cpc_from_cost_and_clicks(3_000_000, 2), Decimal("1.50")
        )
        self.assertEqual(cpc_from_cost_and_clicks(1_500_000, 0), Decimal("0"))
        self.assertNotEqual(
            cpc_from_cost_and_clicks(1_500_000, 1), Decimal("0.0000015")
        )
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(cost_micros=1_500_000, clicks=1)]
            ),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.average_cpc, Decimal("1.50"))
        self.assertGreater(snap.kpis.average_cpc, Decimal("1"))
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(cost_micros=3_000_000, clicks=2)]
            ),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.average_cpc, Decimal("1.50"))
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(cost_micros=1_500_000, clicks=0)]
            ),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.average_cpc, Decimal("0"))

    def test_cpc_exibido_nao_e_um_milhao_vezes_menor(self):
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(cost_micros=1_500_000, clicks=1)]
            ),
        ):
            resp = self.http_a.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["cost_per_click"], Decimal("1.50"))
        self.assertNotEqual(resp.context["cost_per_click"], Decimal("0.0000015"))
        self.assertContains(resp, "1,50")
        self.assertNotContains(resp, "0,000001")

    def test_conversions_fracionario(self):
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(
                totals=[_totals_row(conversions=2.75)]
            ),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(snap.kpis.conversions, Decimal("2.75"))

    def test_serie_diaria_ordenada_e_zeros(self):
        self._configured(self.org_a)
        today = timezone.localdate()
        start = today - timedelta(days=6)
        daily = [
            _daily_row(today, clicks=9, impressions=90, cost_micros=900_000),
            _daily_row(start, clicks=1, impressions=10, cost_micros=100_000),
        ]
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(daily=daily),
        ):
            snap = fetch_google_ads_metrics(organization=self.org_a, days=7)
        self.assertEqual(len(snap.series), 7)
        self.assertEqual(snap.series[0].day, start)
        self.assertEqual(snap.series[-1].day, today)
        self.assertEqual(snap.series[0].clicks, 1)
        self.assertEqual(snap.series[-1].clicks, 9)
        self.assertEqual(snap.series[1].clicks, 0)
        self.assertEqual(snap.series[1].impressions, 0)
        self.assertEqual(snap.series[1].cost, Decimal("0"))
        days = [point.day for point in snap.series]
        self.assertEqual(days, sorted(days))

    def test_periodos_somente_whitelist(self):
        self.assertEqual(sanitize_period_days(7), 7)
        self.assertEqual(sanitize_period_days(15), 15)
        self.assertEqual(sanitize_period_days(30), 30)
        self.assertEqual(sanitize_period_days(90), 90)
        self.assertEqual(sanitize_period_days(45), 30)
        self.assertEqual(sanitize_period_days("abc"), 30)
        self.assertEqual(sanitize_period_days("; DROP TABLE --"), 30)

    def test_parametro_invalido_sem_injecao_gaql(self):
        self._configured(self.org_a)
        with patch(
            "marketing.services.google_ads_metrics.google_ads_client_for_organization",
            return_value=self._client(),
        ):
            snap = fetch_google_ads_metrics(
                organization=self.org_a, days="; DROP TABLE metrics --"
            )
        self.assertEqual(snap.period_days, 30)
        for call in self.calls:
            self.assertNotIn("DROP", call["query"])
            self.assertNotIn(";", call["query"])
            self.assertRegex(
                call["query"],
                r"segments\.date BETWEEN '\d{4}-\d{2}-\d{2}' AND '\d{4}-\d{2}-\d{2}'",
            )

    def test_nenhuma_operacao_mutate(self):
        src = inspect.getsource(metrics_mod)
        self.assertNotIn(".mutate(", src)
        self.assertNotIn("MutateOperation", src)
        from marketing import views as views_mod

        self.assertNotIn(".mutate(", inspect.getsource(views_mod))

    def test_logs_sem_secrets(self):
        with self.assertLogs("marketing.services.google_ads_metrics", level="WARNING") as captured:
            with self.assertRaises(GoogleAdsMetricsError):
                fetch_google_ads_metrics(organization=None, days=7)
        joined = "\n".join(captured.output)
        self.assertIn("missing_org", joined)
        self.assertNotIn("refresh_token", joined)
        self.assertNotIn("access_token", joined)
        self.assertNotIn("client_secret", joined)
        self.assertNotIn("oauth-client-secret-test", joined)

    def test_gaql_somente_customer_e_datas_seguras(self):
        periodo = PeriodoMarketing.ultimos_dias(7)
        totals = totals_gaql(periodo)
        daily = daily_gaql(periodo)
        self.assertIn("FROM customer", totals)
        self.assertIn("FROM customer", daily)
        self.assertIn("metrics.clicks", totals)
        self.assertIn("metrics.impressions", totals)
        self.assertIn("metrics.cost_micros", totals)
        self.assertIn("metrics.ctr", totals)
        self.assertIn("metrics.conversions", totals)
        self.assertNotIn("metrics.average_cpc", totals)
        self.assertNotIn("metrics.average_cpc", daily)
        self.assertIn("segments.date", daily)
        self.assertNotIn("Mutate", totals + daily)
