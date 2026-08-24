"""Testes Fase 2 — service de métricas Resultados do Negócio (Google Ads)."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from marketing.services.google_ads_resultados import (
    calcular_resultados_negocio,
    get_cpl,
    get_investimento,
    get_receita_recebida,
)
from marketing.services.periodo import PeriodoMarketing
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

User = get_user_model()


def _dt_no_dia(d, hora=10):
    from datetime import datetime, time

    naive = datetime.combine(d, time(hour=hora))
    return timezone.make_aware(naive)


class GoogleAdsResultadosFase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mkt_res", password="senha123")
        self.user_b = User.objects.create_user(username="mkt_b", password="senha123")
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(30, referencia=self.hoje)
        self.cliente_ads = Cliente.objects.create(
            user=self.user,
            nome="Lead Ads",
            email="ads@mkt.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            fase_funil="proposta_enviada",
        )
        self.cliente_ind = Cliente.objects.create(
            user=self.user,
            nome="Indicado",
            email="ind@mkt.com",
            origem=OrigemLead.INDICACAO,
            atribuicao_confiavel=True,
        )

    def test_leads_somente_google_ads_confiavel(self):
        res = calcular_resultados_negocio(self.user, self.periodo)
        self.assertEqual(res.funil.leads, 1)

    def test_consulta_realizada_e_agendada(self):
        Compromisso.objects.create(
            user=self.user,
            cliente=self.cliente_ads,
            titulo="Consulta ok",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        Compromisso.objects.create(
            user=self.user,
            cliente=self.cliente_ads,
            titulo="Consulta cancelada",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.CANCELADO,
            data_hora=_dt_no_dia(self.hoje, hora=14),
        )
        Compromisso.objects.create(
            user=self.user,
            cliente=self.cliente_ads,
            titulo="Consulta tarde",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.CONFIRMADO,
            data_hora=_dt_no_dia(self.hoje, hora=15),
        )
        res = calcular_resultados_negocio(self.user, self.periodo)
        self.assertEqual(res.funil.consultas_agendadas, 2)
        self.assertEqual(res.funil.consultas_realizadas, 1)

    def test_proposta_e_contrato(self):
        Contrato.objects.create(
            usuario=self.user,
            cliente=self.cliente_ads,
            referencia="CTR-01",
            descricao="Honorários",
            valor_total=Decimal("10000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user,
        )
        res = calcular_resultados_negocio(self.user, self.periodo)
        self.assertEqual(res.funil.propostas, 1)
        self.assertEqual(res.funil.contratos, 1)
        self.assertEqual(res.funil.receita_contratada, Decimal("10000.00"))

    def test_receita_recebida_exclui_estorno(self):
        cob = Cobranca.objects.create(
            usuario=self.user,
            cliente=self.cliente_ads,
            descricao="Honorários",
            valor_original=Decimal("5000.00"),
            data_vencimento=self.hoje,
            criado_por=self.user,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=self.user,
            valor=Decimal("3000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=self.user,
            valor=Decimal("500.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user,
            cancelado_em=timezone.now(),
        )
        receita = get_receita_recebida(self.user, self.periodo)
        self.assertEqual(receita, Decimal("3000.00"))

    def test_cpl_e_conversoes(self):
        Contrato.objects.create(
            usuario=self.user,
            cliente=self.cliente_ads,
            referencia="CTR-02",
            descricao="X",
            valor_total=Decimal("7000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user,
        )
        Compromisso.objects.create(
            user=self.user,
            cliente=self.cliente_ads,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        res = calcular_resultados_negocio(
            self.user, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        self.assertIsNotNone(res.funil.investimento)
        self.assertTrue(res.funil.investimento_demo)
        self.assertIsNotNone(res.eficiencia.cpl)
        self.assertEqual(res.eficiencia.conv_lead_contrato_pct, Decimal("100.00"))
        self.assertEqual(res.eficiencia.conv_lead_consulta_pct, Decimal("100.00"))
        self.assertEqual(res.eficiencia.ticket_medio, Decimal("7000.00"))

    def test_cpl_nao_divide_por_zero(self):
        self.assertIsNone(get_cpl(Decimal("1000"), 0))
        inv, demo = get_investimento(self.user, self.periodo, modo_demo=False)
        self.assertIsNone(inv)
        self.assertFalse(demo)

    def test_investimento_demo_periodo(self):
        inv, demo = get_investimento(
            self.user, self.periodo, modo_demo=True, nicho=""
        )
        self.assertTrue(demo)
        self.assertGreater(inv, Decimal("0"))

    def test_tenant_isolado(self):
        Cliente.objects.create(
            user=self.user_b,
            nome="Outro tenant",
            email="b@mkt.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        res_a = calcular_resultados_negocio(self.user, self.periodo)
        res_b = calcular_resultados_negocio(self.user_b, self.periodo)
        self.assertEqual(res_a.funil.leads, 1)
        self.assertEqual(res_b.funil.leads, 1)

    def test_periodo_filtra_lead_fora_intervalo(self):
        cliente_antigo = Cliente.objects.create(
            user=self.user,
            nome="Antigo",
            email="old@mkt.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        Cliente.objects.filter(pk=cliente_antigo.pk).update(
            criado_em=timezone.now() - timedelta(days=60)
        )
        res = calcular_resultados_negocio(self.user, self.periodo)
        self.assertEqual(res.funil.leads, 1)

    def test_sem_dados_suficientes(self):
        Cliente.objects.filter(user=self.user).delete()
        res = calcular_resultados_negocio(self.user, self.periodo, modo_demo=True)
        self.assertFalse(res.tem_dados_suficientes)
        self.assertIsNotNone(res.funil.investimento)

    def test_periodo_anterior(self):
        anterior = self.periodo.periodo_anterior()
        self.assertEqual(anterior.dias, self.periodo.dias)
        self.assertLess(anterior.data_fim, self.periodo.data_inicio)


class DashboardResultadosIntegracaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dash_mkt", password="senha123")
        self.http = Client()
        self.http.login(username="dash_mkt", password="senha123")
        self.hoje = timezone.localdate()

    def test_dashboard_exibe_resultados_negocio(self):
        Cliente.objects.create(
            user=self.user,
            nome="Lead Dash",
            email="dash@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Resultados do negócio")
        self.assertContains(resp, "Eficiência")
        self.assertContains(resp, "CAC de mídia")
        self.assertContains(resp, "Receita / mídia")
        self.assertContains(resp, "Não é ROI")

    def test_dashboard_sem_dados_mostra_vazio(self):
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "Sem dados suficientes")

    def test_tendencias_periodo_anterior(self):
        from marketing.services.google_ads_resultados import calcular_tendencias_resultados

        p = PeriodoMarketing.ultimos_dias(30)
        r1 = calcular_resultados_negocio(self.user, p, modo_demo=True)
        r2 = calcular_resultados_negocio(self.user, p.periodo_anterior(), modo_demo=True)
        trends = calcular_tendencias_resultados(r1, r2)
        self.assertIn("leads", trends)
        self.assertIn("trend", trends["leads"])


class ResultadosNegocioFase9Tests(TestCase):
    """UI Fase 9 — seção Resultados do Negócio no dashboard Marketing."""

    def setUp(self):
        self.user = User.objects.create_user(username="fase9_mkt", password="senha123")
        self.http = Client()
        self.http.login(username="fase9_mkt", password="senha123")

    def test_secao_abaixo_kpis_midia(self):
        resp = self.http.get(reverse("marketing_dashboard"))
        content = resp.content.decode()
        idx_custo = content.find("Custo (exemplo)")
        idx_resultados = content.find('id="resultados-negocio-titulo"')
        idx_planejamento = content.find("Planejamento Google Ads")
        self.assertGreater(idx_custo, -1)
        self.assertGreater(idx_resultados, idx_custo)
        self.assertGreater(idx_planejamento, idx_resultados)

    def test_kpis_midia_preservados(self):
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "Impressões")
        self.assertContains(resp, "Funil de conversão")
        self.assertContains(resp, "Histórico diário")

    def test_comparacao_periodo_anterior(self):
        Cliente.objects.create(
            user=self.user,
            nome="Lead F9",
            email="f9@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        resp = self.http.get(reverse("marketing_dashboard") + "?dias=30")
        self.assertContains(resp, "30 dias vs. período anterior")

    def test_funil_etapas_com_lead(self):
        Cliente.objects.create(
            user=self.user,
            nome="Lead Funil",
            email="funil@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            fase_funil="proposta_enviada",
        )
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "Investimento")
        self.assertContains(resp, "Propostas")
        self.assertContains(resp, "Leads Google Ads por fase do funil")
        self.assertContains(resp, "Proposta enviada")

    def test_rbac_oculta_receita(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

        restrito = User.objects.create_user(username="fase9_sem_fin", password="senha123")
        grupo, _ = Group.objects.get_or_create(name="Marketing F9 — sem financeiro")
        grupo.permissions.clear()
        ct = ContentType.objects.get(app_label="marketing", model="marketingintegracao")
        for codename in ("view_marketing", "view_resultados_marketing"):
            perm = Permission.objects.get(content_type=ct, codename=codename)
            grupo.permissions.add(perm)
        restrito.groups.add(grupo)

        Cliente.objects.create(
            user=restrito,
            nome="Lead Fin",
            email="fin@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        self.http.logout()
        self.http.login(username="fase9_sem_fin", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertContains(resp, "ocultos conforme suas permiss")
        self.assertNotContains(resp, "Receita / mídia")
        self.assertNotContains(resp, "Ticket médio")
        self.assertContains(resp, "Leads")

    def test_contexto_dashboard_leads_por_fase(self):
        from marketing.services.google_ads_resultados import contexto_dashboard_resultados

        Cliente.objects.create(
            user=self.user,
            nome="A",
            email="a@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            fase_funil="primeiro_contato",
        )
        Cliente.objects.create(
            user=self.user,
            nome="B",
            email="b@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            fase_funil="aguardando_decisao",
        )
        ctx = contexto_dashboard_resultados(
            self.user, PeriodoMarketing.ultimos_dias(30), modo_demo=True
        )
        self.assertEqual(len(ctx.leads_por_fase), 2)
        labels = {x["label"] for x in ctx.leads_por_fase}
        self.assertIn("Primeiro contato", labels)
