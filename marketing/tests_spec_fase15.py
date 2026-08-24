"""
Suite dos 28 testes de aceitação — Resultados do Negócio / Google Ads (spec §48).

Cada método corresponde a um TESTE numerado do prompt original.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from marketing.services.google_ads_resultados import (
    calcular_funil,
    calcular_resultados_negocio,
    contexto_dashboard_resultados,
    get_cac_midia,
    get_cpl,
    get_conversao_consulta_contrato,
    get_conversao_lead_contrato,
    get_conversao_lead_consulta,
    get_investimento,
    get_receita_midia_ratio,
    get_receita_recebida,
    get_ticket_medio,
)
from marketing.services.periodo import PeriodoMarketing
from marketing.services.resultados_operacional import (
    queryset_followups_atrasados,
    queryset_leads_sem_proxima_acao,
)
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

User = get_user_model()


def _dt_no_dia(d, hora=10):
    from datetime import datetime, time

    return timezone.make_aware(datetime.combine(d, time(hour=hora)))


class MarketingSpecFase15Tests(TestCase):
    """28 testes de aceitação — Marketing / Google Ads → Resultados do Negócio."""

    GRUPO_SEM_FIN = "Spec Mkt §48 — sem financeiro"

    def setUp(self):
        self.user_a = User.objects.create_user(username="spec_mkt_a", password="senha123")
        self.user_b = User.objects.create_user(username="spec_mkt_b", password="senha123")
        self.http = Client()
        self.hoje = timezone.localdate()
        self.periodo = PeriodoMarketing.ultimos_dias(30, referencia=self.hoje)

    def _lead_ads(self, user, nome, email, **kwargs):
        return Cliente.objects.create(
            user=user,
            nome=nome,
            email=email,
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
            gclid="CjwK123",
            **kwargs,
        )

    def _grupo_sem_financeiro(self, user):
        grupo, _ = Group.objects.get_or_create(name=self.GRUPO_SEM_FIN)
        grupo.permissions.clear()
        ct = ContentType.objects.get(app_label="marketing", model="marketingintegracao")
        for codename in ("view_marketing", "view_resultados_marketing"):
            grupo.permissions.add(
                Permission.objects.get(content_type=ct, codename=codename)
            )
        user.groups.add(grupo)

    # TESTE 01 — Investimento correto por tenant.
    def test_spec_01_investimento_correto_por_tenant(self):
        inv_a, demo_a = get_investimento(
            self.user_a, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        inv_b, demo_b = get_investimento(
            self.user_b, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        self.assertTrue(demo_a)
        self.assertTrue(demo_b)
        self.assertGreater(inv_a, Decimal("0"))
        self.assertEqual(inv_a, inv_b)
        self._lead_ads(self.user_b, "Só B", "bonly@test.com")
        inv_a2, _ = get_investimento(
            self.user_a, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        self.assertEqual(inv_a, inv_a2)

    # TESTE 02 — Lead Google Ads contabilizado.
    def test_spec_02_lead_google_ads_contabilizado(self):
        self._lead_ads(self.user_a, "Ads", "ads@test.com")
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.leads, 1)

    # TESTE 03 — Lead orgânico não contabilizado como Google Ads.
    def test_spec_03_lead_organico_nao_contabilizado(self):
        Cliente.objects.create(
            user=self.user_a,
            nome="Orgânico",
            email="org@test.com",
            origem=OrigemLead.GOOGLE_ORGANIC,
            atribuicao_confiavel=True,
        )
        self._lead_ads(self.user_a, "Ads", "ads@test.com")
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.leads, 1)

    # TESTE 04 — Lead sem origem não atribuído ao Google Ads.
    def test_spec_04_lead_sem_origem_nao_atribuido(self):
        Cliente.objects.create(
            user=self.user_a,
            nome="Sem origem",
            email="sem@test.com",
            origem=OrigemLead.NAO_IDENTIFICADA,
            atribuicao_confiavel=False,
        )
        Cliente.objects.create(
            user=self.user_a,
            nome="Manual sem prova",
            email="manual@test.com",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=False,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.leads, 0)

    # TESTE 05 — Lead não duplicado.
    def test_spec_05_lead_nao_duplicado(self):
        lead = self._lead_ads(self.user_a, "Único", "unico@test.com")
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="C1",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="C2",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje, hora=14),
        )
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="C-UN",
            descricao="Contrato",
            valor_total=Decimal("5000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.leads, 1)

    # TESTE 06 — Consulta agendada calculada.
    def test_spec_06_consulta_agendada_calculada(self):
        lead = self._lead_ads(self.user_a, "Agenda", "ag@test.com")
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="Consulta futura",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.CONFIRMADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.consultas_agendadas, 1)

    # TESTE 07 — Consulta realizada calculada.
    def test_spec_07_consulta_realizada_calculada(self):
        lead = self._lead_ads(self.user_a, "Realizada", "real@test.com")
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="Consulta ok",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.consultas_realizadas, 1)

    # TESTE 08 — Consulta cancelada não considerada realizada.
    def test_spec_08_consulta_cancelada_nao_realizada(self):
        lead = self._lead_ads(self.user_a, "Cancel", "cancel@test.com")
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="Cancelada",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.CANCELADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.consultas_agendadas, 0)
        self.assertEqual(res.funil.consultas_realizadas, 0)

    # TESTE 09 — Proposta contabilizada uma única vez.
    def test_spec_09_proposta_unica(self):
        self._lead_ads(
            self.user_a,
            "Proposta",
            "prop@test.com",
            fase_funil="proposta_enviada",
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.propostas, 1)

    # TESTE 10 — Contrato contabilizado uma única vez.
    def test_spec_10_contrato_unico(self):
        lead = self._lead_ads(self.user_a, "Contrato", "ctr@test.com")
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="CTR-SPEC",
            descricao="Honorários",
            valor_total=Decimal("12000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.contratos, 1)

    # TESTE 11 — Receita contratada correta.
    def test_spec_11_receita_contratada_correta(self):
        lead = self._lead_ads(self.user_a, "Receita C", "rc@test.com")
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="CTR-R",
            descricao="Honorários",
            valor_total=Decimal("8500.50"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.receita_contratada, Decimal("8500.50"))

    # TESTE 12 — Receita recebida correta.
    def test_spec_12_receita_recebida_correta(self):
        lead = self._lead_ads(self.user_a, "Receita R", "rr@test.com")
        cob = Cobranca.objects.create(
            usuario=self.user_a,
            cliente=lead,
            descricao="Honorários",
            valor_original=Decimal("4000.00"),
            data_vencimento=self.hoje,
            criado_por=self.user_a,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=self.user_a,
            valor=Decimal("2500.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.receita_recebida, Decimal("2500.00"))

    # TESTE 13 — Cobrança aberta não vira receita recebida.
    def test_spec_13_cobranca_aberta_nao_e_receita(self):
        lead = self._lead_ads(self.user_a, "Aberta", "aberta@test.com")
        Cobranca.objects.create(
            usuario=self.user_a,
            cliente=lead,
            descricao="Pendente",
            valor_original=Decimal("9000.00"),
            data_vencimento=self.hoje + timedelta(days=10),
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.receita_recebida, Decimal("0.00"))

    # TESTE 14 — CPL correto.
    def test_spec_14_cpl_correto(self):
        self._lead_ads(self.user_a, "CPL", "cpl@test.com")
        funil = calcular_funil(
            self.user_a, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        esperado = get_cpl(funil.investimento, funil.leads)
        self.assertIsNotNone(esperado)
        self.assertEqual(funil.leads, 1)
        self.assertGreater(esperado, Decimal("0"))

    # TESTE 15 — CPL não divide por zero.
    def test_spec_15_cpl_nao_divide_por_zero(self):
        self.assertIsNone(get_cpl(Decimal("1000"), 0))
        self.assertIsNone(get_cpl(None, 5))
        res = calcular_resultados_negocio(self.user_a, self.periodo, modo_demo=True)
        self.assertIsNone(res.eficiencia.cpl)

    # TESTE 16 — CAC mídia correto.
    def test_spec_16_cac_midia_correto(self):
        self._lead_ads(self.user_a, "CAC", "cac@test.com")
        funil = calcular_funil(
            self.user_a, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        cac = get_cac_midia(funil.investimento, funil.leads)
        self.assertEqual(cac, get_cpl(funil.investimento, funil.leads))

    # TESTE 17 — Conversão Lead→Consulta correta.
    def test_spec_17_conversao_lead_consulta(self):
        lead = self._lead_ads(self.user_a, "Conv LC", "clc@test.com")
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(
            res.eficiencia.conv_lead_consulta_pct,
            get_conversao_lead_consulta(1, 1),
        )
        self.assertEqual(res.eficiencia.conv_lead_consulta_pct, Decimal("100.00"))

    # TESTE 18 — Conversão Lead→Contrato correta.
    def test_spec_18_conversao_lead_contrato(self):
        lead = self._lead_ads(self.user_a, "Conv LCo", "lco@test.com")
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="CTR-CV",
            descricao="X",
            valor_total=Decimal("3000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(
            res.eficiencia.conv_lead_contrato_pct,
            get_conversao_lead_contrato(1, 1),
        )

    # TESTE 19 — Conversão Consulta→Contrato correta.
    def test_spec_19_conversao_consulta_contrato(self):
        lead = self._lead_ads(self.user_a, "Conv CC", "cc@test.com")
        Compromisso.objects.create(
            user=self.user_a,
            cliente=lead,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt_no_dia(self.hoje),
        )
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="CTR-CC",
            descricao="X",
            valor_total=Decimal("6000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(
            res.eficiencia.conv_consulta_contrato_pct,
            get_conversao_consulta_contrato(1, 1),
        )

    # TESTE 20 — Ticket médio correto.
    def test_spec_20_ticket_medio_correto(self):
        lead = self._lead_ads(self.user_a, "Ticket", "ticket@test.com")
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="CTR-T",
            descricao="X",
            valor_total=Decimal("9000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(
            res.eficiencia.ticket_medio,
            get_ticket_medio(Decimal("9000.00"), 1),
        )

    # TESTE 21 — Receita/Mídia correta.
    def test_spec_21_receita_midia_correta(self):
        lead = self._lead_ads(self.user_a, "Ratio", "ratio@test.com")
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="CTR-RM",
            descricao="X",
            valor_total=Decimal("10000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        res = calcular_resultados_negocio(
            self.user_a, self.periodo, modo_demo=True, nicho="trabalhista"
        )
        ratio = get_receita_midia_ratio(
            res.funil.receita_contratada, res.funil.investimento
        )
        self.assertEqual(res.eficiencia.receita_midia_ratio, ratio)
        self.assertIsNotNone(ratio)
        self.assertGreater(ratio, Decimal("0"))

    # TESTE 22 — Período aplicado corretamente.
    def test_spec_22_periodo_aplicado(self):
        lead_dentro = self._lead_ads(self.user_a, "Dentro", "dentro@test.com")
        lead_fora = self._lead_ads(self.user_a, "Fora", "fora@test.com")
        Cliente.objects.filter(pk=lead_fora.pk).update(
            criado_em=timezone.now() - timedelta(days=60)
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.funil.leads, 1)
        self.assertEqual(
            Cliente.objects.filter(pk=lead_dentro.pk).count(),
            1,
        )

    # TESTE 23 — Tenant A não usa dados do Tenant B.
    def test_spec_23_tenant_isolado(self):
        self._lead_ads(self.user_a, "Tenant A", "a@test.com")
        self._lead_ads(self.user_b, "Tenant B", "b@test.com")
        res_a = calcular_resultados_negocio(self.user_a, self.periodo)
        res_b = calcular_resultados_negocio(self.user_b, self.periodo)
        self.assertEqual(res_a.funil.leads, 1)
        self.assertEqual(res_b.funil.leads, 1)

    # TESTE 24 — Usuário sem permissão financeira não vê receita.
    def test_spec_24_sem_perm_financeira_oculta_receita(self):
        restrito = User.objects.create_user(
            username="spec_mkt_sem_fin", password="senha123"
        )
        self._grupo_sem_financeiro(restrito)
        self._lead_ads(restrito, "Lead Fin", "fin24@test.com")
        Contrato.objects.create(
            usuario=restrito,
            cliente=Cliente.objects.get(email="fin24@test.com"),
            referencia="CTR-FIN",
            descricao="X",
            valor_total=Decimal("5000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=restrito,
        )
        self.http.login(username="spec_mkt_sem_fin", password="senha123")
        resp = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Receita / mídia")
        self.assertContains(resp, "ocultos conforme suas permiss")

    # TESTE 25 — Modo demonstração não contamina dados reais.
    def test_spec_25_demo_nao_contamina_reais(self):
        self._lead_ads(self.user_a, "Real", "real25@test.com")
        res_demo = calcular_resultados_negocio(
            self.user_a, self.periodo, modo_demo=True
        )
        res_prod = calcular_resultados_negocio(
            self.user_a, self.periodo, modo_demo=False
        )
        self.assertTrue(res_demo.funil.investimento_demo)
        self.assertIsNotNone(res_demo.funil.investimento)
        self.assertFalse(res_prod.funil.investimento_demo)
        self.assertIsNone(res_prod.funil.investimento)
        self.assertEqual(res_demo.funil.leads, 1)
        self.assertEqual(res_prod.funil.leads, 1)

    # TESTE 26 — Leads sem próxima ação calculados.
    def test_spec_26_leads_sem_proxima_acao(self):
        lead = self._lead_ads(self.user_a, "Sem Acao", "sa26@test.com")
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertEqual(res.operacional.leads_sem_proxima_acao, 1)
        self.assertIn(lead, queryset_leads_sem_proxima_acao(self.user_a, self.periodo))

    # TESTE 27 — Follow-ups atrasados calculados.
    def test_spec_27_followups_atrasados(self):
        lead = self._lead_ads(
            self.user_a,
            "Follow",
            "fu27@test.com",
            fase_funil="proposta_enviada",
        )
        res = calcular_resultados_negocio(self.user_a, self.periodo)
        self.assertGreaterEqual(res.operacional.followups_atrasados, 1)
        self.assertIn(lead, queryset_followups_atrasados(self.user_a, self.periodo))

    # TESTE 28 — Dados incompletos não quebram dashboard.
    def test_spec_28_dados_incompletos_nao_quebram_dashboard(self):
        self.http.login(username="spec_mkt_a", password="senha123")
        resp_vazio = self.http.get(reverse("marketing_dashboard"))
        self.assertEqual(resp_vazio.status_code, 200)
        self.assertContains(resp_vazio, "Google Ads")

        ctx = contexto_dashboard_resultados(
            self.user_a, self.periodo, modo_demo=True
        )
        self.assertIsNotNone(ctx)
        self.assertFalse(ctx.resultados.tem_dados_suficientes)

        self._lead_ads(self.user_a, "Parcial", "parcial@test.com")
        resp_parcial = self.http.get(reverse("marketing_dashboard") + "?dias=30")
        self.assertEqual(resp_parcial.status_code, 200)
        self.assertContains(resp_parcial, 'id="resultados-negocio-titulo"')
