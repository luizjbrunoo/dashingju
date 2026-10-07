"""MARKETING-PRO-01 — atribuição, Advisor, multi-tenant e capabilities."""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.tests_helpers import grant_finance_permissions
from marketing.models import ContentIdea, MarketingIntegracao
from marketing.services.google_ads_resultados import get_investimento
from marketing.services.marketing_pro import (
    montar_marketing_pro,
    performance_origens,
)
from marketing.services.periodo import PeriodoMarketing
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead
from usuarios.models import Cliente

MARKER_A = "MARKETING_ORG_A_7F3K"
MARKER_B = "MARKETING_ORG_B_9Q2M"


def _perm_mkt(*codenames):
    ct = ContentType.objects.get_for_model(MarketingIntegracao)
    return [
        Permission.objects.get(content_type=ct, codename=code) for code in codenames
    ]


class MarketingProAttributionTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Mkt Pro Org A")
        self.org_b = Organization.objects.create(name="Mkt Pro Org B")
        self.a1 = User.objects.create_user("mktpro_a1", password="senha123")
        self.a2 = User.objects.create_user("mktpro_a2", password="senha123")
        self.b1 = User.objects.create_user("mktpro_b1", password="senha123")
        self.sem_fin = User.objects.create_user("mktpro_sem_fin", password="senha123")
        self.sem_mkt = User.objects.create_user("mktpro_sem_mkt", password="senha123")
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(30, referencia=self.hoje)

        grant_finance_permissions(self.a1, "view_cobrancas", "view_recebimentos")
        grant_finance_permissions(self.a2, "view_cobrancas", "view_recebimentos")
        grant_finance_permissions(self.b1, "view_cobrancas", "view_recebimentos")

        for user, org in (
            (self.a1, self.org_a),
            (self.a2, self.org_a),
            (self.sem_fin, self.org_a),
            (self.sem_mkt, self.org_a),
            (self.b1, self.org_b),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=Membership.Role.MEMBER,
                status=Membership.Status.ACTIVE,
            )

        grupo_mkt = Group.objects.create(name="Mkt PRO resultados")
        grupo_mkt.permissions.add(
            *_perm_mkt("view_marketing", "view_resultados_marketing", "view_conteudo_marketing")
        )
        self.sem_fin.groups.add(grupo_mkt)

        grupo_vazio = Group.objects.create(name="Mkt PRO vazio")
        self.sem_mkt.groups.add(grupo_vazio)

        self.lead_ads = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome=f"Lead Ads {MARKER_A}",
            email="ads-a@mktpro.test",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            gclid="MKTPRO-GCLID-A",
            fase_funil="proposta_enviada",
        )
        self.ctr_a = Contrato.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.lead_ads,
            referencia=f"CTR-{MARKER_A}",
            descricao="Contrato A",
            valor_total=Decimal("8000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a2,
            responsavel=self.a2,
        )
        cob_a = Cobranca.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.lead_ads,
            contrato=self.ctr_a,
            descricao="COB-A",
            valor_original=Decimal("8000.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.a2,
            responsavel=self.a2,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_a,
            usuario=self.a2,
            organization=self.org_a,
            valor=Decimal("3000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.a2,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_a,
            usuario=self.a2,
            organization=self.org_a,
            valor=Decimal("1500.00"),
            data_recebimento=self.hoje,
            registrado_por=self.a2,
        )

        for i in range(5):
            Cliente.objects.create(
                user=self.a1,
                organization=self.org_a,
                nome=f"Lead IG {i} {MARKER_A}",
                email=f"ig{i}@mktpro.test",
                origem=OrigemLead.INSTAGRAM,
                atribuicao_confiavel=True,
            )

        self.lead_b = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome=f"Lead B {MARKER_B}",
            email="b@mktpro.test",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            gclid="MKTPRO-GCLID-B",
            fase_funil="proposta_enviada",
        )
        Contrato.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.lead_b,
            referencia=f"CTR-{MARKER_B}",
            descricao="Contrato B",
            valor_total=Decimal("999999.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.b1,
            responsavel=self.b1,
        )
        cob_b = Cobranca.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.lead_b,
            descricao="COB-B",
            valor_original=Decimal("999999.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.b1,
            responsavel=self.b1,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_b,
            usuario=self.b1,
            organization=self.org_b,
            valor=Decimal("888888.00"),
            data_recebimento=self.hoje,
            registrado_por=self.b1,
        )

        ContentIdea.objects.create(
            usuario=self.a1,
            titulo="Ideia pendente PRO",
            descricao="Não é insight automático em massa.",
        )

    def test_cadeia_origem_lead_oportunidade_contrato_recebido(self):
        origens = performance_origens(
            self.org_a, self.periodo, ocultar_financeiro=False
        )
        ads = next(l for l in origens.linhas if l.origem == OrigemLead.GOOGLE_ADS)
        ig = next(l for l in origens.linhas if l.origem == OrigemLead.INSTAGRAM)

        self.assertEqual(ads.leads, 1)
        self.assertEqual(ads.oportunidades, 1)
        self.assertEqual(ads.contratos, 1)
        self.assertEqual(ads.valor_comercial, Decimal("8000.00"))
        self.assertEqual(ads.recebido, Decimal("4500.00"))

        self.assertEqual(ig.leads, 5)
        self.assertEqual(ig.oportunidades, 0)
        self.assertEqual(ig.contratos, 0)
        self.assertNotEqual(ig.leads, ig.oportunidades)
        self.assertNotEqual(ads.valor_comercial, ads.recebido)

    def test_semantica_null_nao_e_zero_investimento(self):
        valor, demo = get_investimento(self.a1, self.periodo, modo_demo=False)
        self.assertIsNone(valor)
        self.assertFalse(demo)
        painel = montar_marketing_pro(
            self.a1,
            self.periodo,
            organization=self.org_a,
            ocultar_financeiro=False,
        )
        self.assertIsNone(painel.cadeia.investimento)
        self.assertIsNone(painel.cadeia.cpl)
        self.assertIsNone(painel.cadeia.cac_midia)
        self.assertIsNone(painel.cadeia.roas_comercial)

    def test_cpl_denominador_zero_permanece_none(self):
        from marketing.services.google_ads_resultados import _safe_div

        self.assertIsNone(_safe_div(Decimal("100"), 0))

    def test_sem_dupla_contagem_recebimentos(self):
        origens = performance_origens(
            self.org_a, self.periodo, ocultar_financeiro=False
        )
        ads = next(l for l in origens.linhas if l.origem == OrigemLead.GOOGLE_ADS)
        self.assertEqual(ads.recebido, Decimal("4500.00"))
        self.assertEqual(ads.valor_comercial, Decimal("8000.00"))
        self.assertNotEqual(ads.recebido, Decimal("8000.00") + Decimal("4500.00"))

    def test_volume_nao_e_qualidade(self):
        origens = performance_origens(
            self.org_a, self.periodo, ocultar_financeiro=False
        )
        ig = next(l for l in origens.linhas if l.origem == OrigemLead.INSTAGRAM)
        ads = next(l for l in origens.linhas if l.origem == OrigemLead.GOOGLE_ADS)
        self.assertGreater(ig.leads, ads.leads)
        self.assertGreater(ads.contratos, ig.contratos)
        self.assertIn("não significa melhor origem", origens.mensagem.lower())

    def test_same_org_a1_a2(self):
        p1 = montar_marketing_pro(
            self.a1, self.periodo, organization=self.org_a, ocultar_financeiro=False
        )
        p2 = montar_marketing_pro(
            self.a2, self.periodo, organization=self.org_a, ocultar_financeiro=False
        )
        self.assertEqual(p1.cadeia.leads, p2.cadeia.leads)
        self.assertEqual(p1.cadeia.contratos, p2.cadeia.contratos)
        self.assertEqual(p1.cadeia.valor_comercial, p2.cadeia.valor_comercial)
        self.assertEqual(p1.cadeia.recebido, p2.cadeia.recebido)

    def test_cross_org_isolamento(self):
        painel_a = montar_marketing_pro(
            self.a1, self.periodo, organization=self.org_a, ocultar_financeiro=False
        )
        painel_b = montar_marketing_pro(
            self.b1, self.periodo, organization=self.org_b, ocultar_financeiro=False
        )
        self.assertEqual(painel_a.cadeia.valor_comercial, Decimal("8000.00"))
        self.assertEqual(painel_b.cadeia.valor_comercial, Decimal("999999.00"))
        textos_a = " ".join(
            [
                painel_a.origens.mensagem,
                painel_a.diagnostico.detalhe,
                painel_a.advisor.principal.evidence if painel_a.advisor.principal else "",
                painel_a.advisor.principal.impacto or "" if painel_a.advisor.principal else "",
            ]
        )
        self.assertNotIn(MARKER_B, textos_a)
        self.assertNotIn("999999", textos_a)
        self.assertNotIn("888888", textos_a)
        origens_a = {l.origem for l in painel_a.origens.linhas}
        self.assertIn(OrigemLead.INSTAGRAM, origens_a)
        self.assertEqual(painel_a.cadeia.leads, 6)
        self.assertEqual(painel_b.cadeia.leads, 1)

    def test_tenant_invalido_fail_closed(self):
        painel = montar_marketing_pro(
            self.a1, self.periodo, organization=None, ocultar_financeiro=False
        )
        self.assertFalse(painel.tenant_ok)
        self.assertIsNone(painel.cadeia.valor_comercial)
        self.assertIsNone(painel.cadeia.recebido)
        self.assertEqual(painel.cadeia.leads, 0)
        self.assertEqual(painel.origens.estado, "tenant_invalido")
        blob = ""
        if painel.advisor.principal:
            blob = painel.advisor.principal.evidence + (painel.advisor.principal.impacto or "")
        self.assertNotIn("999999", blob)
        self.assertNotIn(MARKER_B, blob)

    def test_capability_sem_financeiro_nao_vaza(self):
        painel = montar_marketing_pro(
            self.sem_fin,
            self.periodo,
            organization=self.org_a,
            ocultar_financeiro=True,
        )
        self.assertTrue(painel.ocultar_financeiro)
        self.assertIsNone(painel.cadeia.valor_comercial)
        self.assertIsNone(painel.cadeia.recebido)
        self.assertIsNone(painel.cadeia.roas_comercial)
        for linha in painel.origens.linhas:
            self.assertIsNone(linha.valor_comercial)
            self.assertIsNone(linha.recebido)
        if painel.advisor.principal:
            self.assertIsNone(painel.advisor.principal.impacto)
            blob = (
                painel.advisor.principal.evidence
                + (painel.advisor.principal.impacto or "")
            )
            self.assertNotIn("8000", blob)
            self.assertNotIn("999999", blob)

    def test_sem_marketing_oculta_resultados(self):
        from marketing.services.google_ads_resultados import contexto_dashboard_resultados

        ctx = contexto_dashboard_resultados(
            self.sem_mkt, self.periodo, organization=self.org_a, modo_demo=False
        )
        self.assertTrue(ctx.ocultar_resultados_negocio)
        self.assertTrue(ctx.ocultar_financeiro)

    def test_advisor_uma_prioridade_deterministica(self):
        painel = montar_marketing_pro(
            self.a1, self.periodo, organization=self.org_a, ocultar_financeiro=False
        )
        self.assertIsNotNone(painel.advisor.principal)
        self.assertTrue(painel.advisor.principal.evidence)
        self.assertTrue(painel.advisor.principal.url)
        self.assertTrue(painel.advisor.principal.cta)
        self.assertLessEqual(len(painel.advisor.alertas), 2)
        self.assertNotIn("campanha ruim", painel.advisor.principal.titulo.lower())
        self.assertNotIn("gerou", painel.advisor.principal.evidence.lower())
        if painel.advisor.principal.impacto:
            self.assertNotIn("gerou", painel.advisor.principal.impacto.lower())

    def test_advisor_tracking_insuficiente(self):
        org = Organization.objects.create(name="Mkt Pro Track")
        user = User.objects.create_user("mktpro_track", password="senha123")
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        for i in range(6):
            Cliente.objects.create(
                user=user,
                organization=org,
                nome=f"Sem origem {i}",
                email=f"track{i}@mktpro.test",
                origem=OrigemLead.NAO_IDENTIFICADA,
            )
        painel = montar_marketing_pro(
            user, self.periodo, organization=org, ocultar_financeiro=True
        )
        self.assertEqual(painel.origens.estado, "sem_atribuicao")
        self.assertIsNotNone(painel.advisor.principal)
        self.assertIn(painel.advisor.principal.key, {"tracking_insuficiente", "dados_insuficientes"})

    def test_advisor_nao_alerta_performance_quando_saudavel(self):
        org = Organization.objects.create(name="Mkt Pro Ok")
        user = User.objects.create_user("mktpro_ok", password="senha123")
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        lead = Cliente.objects.create(
            user=user,
            organization=org,
            nome="Lead ok",
            email="ok@mktpro.test",
            origem=OrigemLead.INDICACAO,
            atribuicao_confiavel=True,
            fase_funil="proposta_enviada",
        )
        Contrato.objects.create(
            usuario=user,
            organization=org,
            cliente=lead,
            referencia="CTR-OK",
            descricao="ok",
            valor_total=Decimal("1000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=user,
        )
        resultados = SimpleNamespace(
            funil=SimpleNamespace(investimento=None, investimento_demo=False, leads=0),
            eficiencia=SimpleNamespace(cpl=None, cac_midia=None, receita_midia_ratio=None),
            operacional=SimpleNamespace(leads_sem_proxima_acao=0, followups_atrasados=0),
            qualidade=SimpleNamespace(
                total_clientes_periodo=1,
                identificados=1,
                nao_identificados=0,
                pct_identificados=100.0,
            ),
        )
        painel = montar_marketing_pro(
            user,
            self.periodo,
            organization=org,
            ocultar_financeiro=True,
            resultados=resultados,
        )
        if painel.advisor.principal:
            self.assertNotEqual(painel.advisor.principal.key, "investimento_sem_captacao")
            self.assertNotIn("CRÍTICO", painel.advisor.principal.prioridade.upper())

    def test_elo_ausente_sem_dados(self):
        org = Organization.objects.create(name="Mkt Pro Vazio")
        user = User.objects.create_user("mktpro_vazio", password="senha123")
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        origens = performance_origens(org, self.periodo, ocultar_financeiro=False)
        self.assertEqual(origens.estado, "sem_dados")
        self.assertFalse(origens.disponivel)

    def test_dashboard_preserva_google_ads_e_exibe_advisor(self):
        from marketing.tests_helpers import grant_marketing_permissions

        grant_marketing_permissions(self.a1)
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Growth Advisor")
        self.assertContains(resp, "Performance por origem")
        self.assertContains(resp, "Valor comercial atribuído")
        self.assertContains(resp, "Recebido relacionado")
        self.assertContains(resp, "atribuição")
        self.assertNotContains(resp, MARKER_B)
        self.assertContains(resp, "Google Ads")
