"""Estado Organization-owned da conexão Google Ads. Sem OAuth, sem secrets."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from marketing.models import OrganizationGoogleAdsConnection
from organizacoes.models import Organization

_SECRET_FIELD_NAMES = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "client_id",
        "developer_token",
        "api_key",
        "ciphertext",
        "token",
        "secret",
    }
)


class OrganizationGoogleAdsConnectionTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Ads Conn Org A")
        self.org_b = Organization.objects.create(name="Ads Conn Org B")

    def test_estados_independentes_org_a_org_b(self):
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            customer_id="123-456-7890",
            descriptive_name="Conta A",
        )
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_b,
            customer_id="098-765-4321",
            descriptive_name="Conta B",
        )
        conn_a = OrganizationGoogleAdsConnection.objects.get(organization=self.org_a)
        conn_b = OrganizationGoogleAdsConnection.objects.get(organization=self.org_b)
        self.assertEqual(conn_a.customer_id, "1234567890")
        self.assertEqual(conn_b.customer_id, "0987654321")
        self.assertNotEqual(conn_a.customer_id, conn_b.customer_id)
        self.assertEqual(conn_a.status, OrganizationGoogleAdsConnection.Status.DISCONNECTED)
        self.assertEqual(conn_b.status, OrganizationGoogleAdsConnection.Status.DISCONNECTED)

    def test_unique_por_organization(self):
        OrganizationGoogleAdsConnection.objects.create(organization=self.org_a)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationGoogleAdsConnection.objects.create(organization=self.org_a)

    def test_customer_id_de_a_nao_aparece_para_b(self):
        OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            customer_id="1112223333",
            login_customer_id="4445556666",
        )
        qs_b = OrganizationGoogleAdsConnection.objects.filter(organization=self.org_b)
        self.assertFalse(qs_b.exists())
        self.assertFalse(qs_b.filter(customer_id="1112223333").exists())
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(
                organization=self.org_b, login_customer_id="4445556666"
            ).exists()
        )
        conn_b_via_a_id = OrganizationGoogleAdsConnection.objects.filter(
            organization=self.org_b, customer_id="1112223333"
        )
        self.assertEqual(conn_b_via_a_id.count(), 0)

    def test_nenhum_campo_de_segredo_no_model(self):
        names = {f.name for f in OrganizationGoogleAdsConnection._meta.get_fields()}
        self.assertTrue(_SECRET_FIELD_NAMES.isdisjoint(names))
        conn = OrganizationGoogleAdsConnection.objects.create(organization=self.org_a)
        self.assertNotIn("refresh_token", str(conn))
        self.assertNotIn("access_token", repr(conn))
        self.assertEqual(str(conn), f"google_ads@org-{self.org_a.pk}")

    def test_normaliza_customer_id_antes_de_persistir(self):
        conn = OrganizationGoogleAdsConnection.objects.create(
            organization=self.org_a,
            customer_id="123-456-7890",
            login_customer_id=" 987 654 3210 ",
        )
        conn.refresh_from_db()
        self.assertEqual(conn.customer_id, "1234567890")
        self.assertEqual(conn.login_customer_id, "9876543210")

    def test_customer_id_invalido_nao_persiste(self):
        with self.assertRaises(ValidationError):
            OrganizationGoogleAdsConnection.objects.create(
                organization=self.org_a,
                customer_id="nao-e-id",
            )
        self.assertFalse(
            OrganizationGoogleAdsConnection.objects.filter(organization=self.org_a).exists()
        )

    def test_default_disconnected_sem_transicao_nesta_fase(self):
        conn = OrganizationGoogleAdsConnection.objects.create(organization=self.org_a)
        self.assertEqual(conn.status, OrganizationGoogleAdsConnection.Status.DISCONNECTED)
        self.assertIsNone(conn.connected_at)
        self.assertTrue(
            {s.value for s in OrganizationGoogleAdsConnection.Status}
            >= {"disconnected", "authorized", "configured"}
        )
