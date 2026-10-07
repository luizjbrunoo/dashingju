"""Testes Fase 1 — atribuição de origem do lead (Google Ads)."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from marketing.services.atribuicao import (
    clientes_google_ads,
    resolver_atribuicao,
)
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead
from usuarios.models import Cliente

User = get_user_model()


class AtribuicaoLeadFase1Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adv_attr", password="senha123")
        self.org = Organization.objects.create(name="Attr Org A")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.http = Client()

    def test_gclid_atribui_google_ads_confiavel(self):
        atrib = resolver_atribuicao({"gclid": "CjwKCAiA123"})
        self.assertEqual(atrib.origem, OrigemLead.GOOGLE_ADS)
        self.assertTrue(atrib.atribuicao_confiavel)

    def test_utm_google_cpc_atribui_google_ads(self):
        atrib = resolver_atribuicao(
            {"utm_source": "google", "utm_medium": "cpc", "utm_campaign": "trabalhista"}
        )
        self.assertEqual(atrib.origem, OrigemLead.GOOGLE_ADS)
        self.assertTrue(atrib.atribuicao_confiavel)
        self.assertEqual(atrib.utm_campaign, "trabalhista")

    def test_lead_organico_nao_e_google_ads(self):
        atrib = resolver_atribuicao({"utm_source": "google", "utm_medium": "organic"})
        self.assertEqual(atrib.origem, OrigemLead.GOOGLE_ORGANIC)
        self.assertTrue(atrib.atribuicao_confiavel)
        self.assertNotEqual(atrib.origem, OrigemLead.GOOGLE_ADS)

    def test_sem_origem_nao_atribui_google_ads(self):
        atrib = resolver_atribuicao({})
        self.assertEqual(atrib.origem, OrigemLead.NAO_IDENTIFICADA)
        self.assertFalse(atrib.atribuicao_confiavel)

    def test_google_ads_manual_sem_prova_nao_confiavel(self):
        atrib = resolver_atribuicao({}, origem_manual=OrigemLead.GOOGLE_ADS)
        self.assertEqual(atrib.origem, OrigemLead.GOOGLE_ADS)
        self.assertFalse(atrib.atribuicao_confiavel)

    def test_indicacao_manual_confiavel(self):
        atrib = resolver_atribuicao({}, origem_manual=OrigemLead.INDICACAO)
        self.assertEqual(atrib.origem, OrigemLead.INDICACAO)
        self.assertTrue(atrib.atribuicao_confiavel)

    def test_criar_cliente_com_gclid_via_post(self):
        self.http.login(username="adv_attr", password="senha123")
        resp = self.http.post(
            reverse("clientes"),
            {
                "nome": "Lead Ads",
                "email": "ads@test.com",
                "tipo": "PF",
                "status": "em_prospeccao",
                "gclid": "CjwKCAiA999",
                "utm_campaign": "camp_trab",
            },
        )
        self.assertEqual(resp.status_code, 302)
        c = Cliente.objects.get(email="ads@test.com")
        self.assertEqual(c.origem, OrigemLead.GOOGLE_ADS)
        self.assertTrue(c.atribuicao_confiavel)
        self.assertEqual(c.gclid, "CjwKCAiA999")
        self.assertEqual(c.user_id, self.user.pk)

    def test_criar_cliente_indicacao_nao_conta_como_ads(self):
        self.http.login(username="adv_attr", password="senha123")
        self.http.post(
            reverse("clientes"),
            {
                "nome": "Indicado",
                "email": "ind@test.com",
                "tipo": "PF",
                "status": "em_prospeccao",
                "origem": OrigemLead.INDICACAO,
            },
        )
        c = Cliente.objects.get(email="ind@test.com")
        self.assertEqual(c.origem, OrigemLead.INDICACAO)
        self.assertFalse(c.origem_google_ads_atribuida)
        self.assertEqual(clientes_google_ads(self.user).count(), 0)

    def test_sessao_preserva_gclid_entre_paginas(self):
        self.http.login(username="adv_attr", password="senha123")
        self.http.get(reverse("clientes"), {"gclid": "SessaoGclid123"})
        resp = self.http.post(
            reverse("clientes"),
            {
                "nome": "Lead Sessão",
                "email": "sessao@test.com",
                "tipo": "PF",
                "status": "em_prospeccao",
            },
        )
        self.assertEqual(resp.status_code, 302)
        c = Cliente.objects.get(email="sessao@test.com")
        self.assertEqual(c.gclid, "SessaoGclid123")
        self.assertTrue(c.origem_google_ads_atribuida)

    def test_tenant_isolado(self):
        user_b = User.objects.create_user(username="adv_b", password="senha123")
        org_b = Organization.objects.create(name="Attr Org B")
        Membership.objects.create(
            user=user_b,
            organization=org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="A",
            email="a@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        Cliente.objects.create(
            user=user_b,
            organization=org_b,
            nome="B",
            email="b@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        self.assertEqual(clientes_google_ads(self.user, organization=self.org).count(), 1)
        self.assertEqual(clientes_google_ads(user_b, organization=org_b).count(), 1)

    def test_origem_lead_consulta_tem_google_ads(self):
        from usuarios.choices import OrigemLeadConsulta

        valores = {c.value for c in OrigemLeadConsulta}
        self.assertIn("google_ads", valores)
