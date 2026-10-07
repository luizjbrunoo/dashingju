"""COMERCIAL-PRO-01 — camada de inteligência sobre o Comercial existente."""

from decimal import Decimal
from math import isfinite, isinf, isnan

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.advisor import montar_advisor
from comercial.services.dashboard import contexto_dashboard
from comercial.services.funnel import calcular_funil_receita
from comercial.services.goals import salvar_meta
from comercial.services.metrics import calcular_kpis, periodo_mes, receita_no_periodo
from comercial.services.money import MIN_AMOSTRAS_CONVERSAO, ZERO
from comercial.services.periodo import PeriodoComercial
from comercial.services.projections import projetar_faturamento_mensal
from comercial.services.recommendations import listar_recomendacoes
from comercial.services.simulator import simular_crescimento
from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from financeiro.tenancy_write import organization_for_finance_write
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.models import Cliente


class ComercialProUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pro_ui", password="senha123")
        self.org = Organization.objects.create(name="PRO Org UI")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.user)
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_visao_preserva_navegacao_e_hierarquia_pro(self):
        self.http.login(username="pro_ui", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Visão geral", html)
        self.assertIn("Oportunidades", html)
        self.assertIn("Origens", html)
        self.assertIn("Score", html)
        self.assertIn("Simulador", html)
        self.assertIn("Funil comercial", html)
        self.assertIn("Ações de hoje", html)
        self.assertIn("Growth Advisor", html)
        self.assertIn("Projeção / estimativa", html)
        self.assertIn("Realizado comercial", html)
        self.assertIn("Gap projetado", html)
        self.assertIn("Defina uma meta", html)
        self.assertNotIn("R$ 87.000", html)
        self.assertNotIn("87000", html)
        self.assertNotIn("Você está perdendo", html)
        self.assertLess(html.count("Growth Advisor"), 8)

    def test_advisor_principal_com_evidence_cta_e_proxima_acao(self):
        salvar_meta(
            self.user,
            ator=self.user,
            ano=self.hoje.year,
            meta_anual=Decimal("120000"),
            meta_mensal=Decimal("10000"),
            ticket_medio=Decimal("5000"),
            ticket_medio_manual=True,
            vigencia_inicio=self.hoje.replace(month=1, day=1),
            vigencia_fim=self.hoje.replace(month=12, day=31),
            organization=self.org,
        )
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Proposta Aberta",
            email="prop@t.com",
            fase_funil="proposta_enviada",
        )
        painel = montar_advisor(self.user, organization=self.org, ticket=Decimal("5000"))
        self.assertIsNotNone(painel.principal)
        self.assertEqual(painel.principal.key, "propostas_aguardando")
        self.assertTrue(painel.principal.acao)
        self.assertTrue(painel.principal.url)
        self.assertTrue(painel.principal.cta_ver)
        self.assertLessEqual(len(painel.acoes_hoje), 5)

        self.http.login(username="pro_ui", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertContains(resp, "Por quê")
        self.assertContains(resp, "Próxima ação")
        self.assertContains(resp, painel.principal.cta_ver)
        self.assertContains(resp, "valor potencial")
        self.assertContains(resp, "não é receita perdida")
        self.assertNotContains(resp, "Você está perdendo")

    def test_simulador_e_origens_nao_prometem_causalidade_nem_previsao(self):
        self.http.login(username="pro_ui", password="senha123")
        sim = self.http.get(reverse("comercial_dashboard"), {"aba": "simulador"})
        self.assertEqual(sim.status_code, 200)
        self.assertContains(sim, "não é previsão")
        self.assertContains(sim, "Simulação")
        orig = self.http.get(reverse("comercial_dashboard"), {"aba": "origens"})
        self.assertEqual(orig.status_code, 200)
        self.assertContains(orig, "não prova causalidade")
        self.assertContains(orig, "não é ranking de melhor área")

    def test_capability_financeira_nao_vaza_valor_no_advisor(self):
        ct = ContentType.objects.get_for_model(MetaComercial)
        view = Permission.objects.get(content_type=ct, codename="view_dashboard")
        grupo = Group.objects.create(name="PRO Comercial sem receita")
        grupo.permissions.add(view)
        self.user.groups.add(grupo)
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Lead Cap",
            email="cap@t.com",
            fase_funil="proposta_enviada",
        )
        salvar_meta(
            self.user,
            ator=self.user,
            ano=self.hoje.year,
            meta_anual=Decimal("120000"),
            meta_mensal=Decimal("10000"),
            ticket_medio=Decimal("5000"),
            ticket_medio_manual=True,
            vigencia_inicio=self.hoje.replace(month=1, day=1),
            vigencia_fim=self.hoje.replace(month=12, day=31),
            organization=self.org,
        )
        painel = montar_advisor(self.user, organization=self.org, ocultar_financeiro=True)
        self.assertIsNotNone(painel.principal)
        self.assertIsNone(painel.principal.valor_potencial)

        self.user.user_permissions.clear()
        self.http.login(username="pro_ui", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        html = resp.content.decode()
        self.assertIn("Growth Advisor", html)
        self.assertNotIn("valor potencial está associado", html)
        self.assertNotIn("R$ 10.000", html)
        self.assertNotIn("R$ 5.000", html)


class ComercialProSemanticaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pro_sem", password="senha123")
        self.org = Organization.objects.create(name="PRO Org Sem")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.hoje = timezone.localdate()
        self.periodo = PeriodoComercial.ultimos_dias(30, referencia=self.hoje)

    def test_proposta_aberta_nao_entra_no_realizado(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Proposta aberta",
            email="aberta@t.com",
            fase_funil="proposta_enviada",
        )
        rec = receita_no_periodo(self.org, periodo_mes())
        self.assertEqual(rec.recebido, ZERO)
        self.assertEqual(rec.contratado, ZERO)
        proj = projetar_faturamento_mensal(self.org, Decimal("10000"))
        self.assertEqual(proj.realizado, ZERO)
        self.assertIsNone(proj.projecao)

    def test_contrato_diferente_de_recebimento(self):
        cli = Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Contrato sem pagamento",
            email="ctr@t.com",
        )
        Contrato.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=cli,
            referencia="PRO-CTR",
            descricao="Contrato sem recebimento",
            valor_total=Decimal("8000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user,
        )
        rec = receita_no_periodo(self.org, periodo_mes())
        self.assertEqual(rec.contratado, Decimal("8000.00"))
        self.assertEqual(rec.recebido, ZERO)
        kpis = calcular_kpis(
            self.org,
            meta=None,
            receita_risco=ZERO,
            ocultar_financeiro=False,
            projecao_anual=None,
        )
        self.assertEqual(kpis.contratado_mes, Decimal("8000.00"))
        self.assertEqual(kpis.realizado_mes, Decimal("8000.00"))
        self.assertEqual(rec.recebido, ZERO)

    def test_gap_e_projecao_nao_usam_recebimento_como_realizado(self):
        cli = Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Pago",
            email="pago@t.com",
        )
        cob = Cobranca.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=cli,
            descricao="PRO-REC",
            valor_original=Decimal("1000.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.user,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=self.user,
            organization=self.org,
            valor=Decimal("1000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user,
        )
        meta = Decimal("10000.00")
        proj = projetar_faturamento_mensal(self.org, meta)
        self.assertEqual(proj.realizado, ZERO)
        self.assertIsNone(proj.projecao)
        self.assertIn("insuficientes", proj.mensagem.lower())
        self.assertIn("Não representa garantia", proj.mensagem)
        self.assertEqual(proj.restante_meta, meta)

    def test_simulacao_zero_nao_explode_e_nao_e_previsao(self):
        resultado = simular_crescimento(
            leads_atual=0,
            taxa_atual_pct=Decimal("0"),
            ticket_atual=Decimal("0"),
            leads_sim=10,
            taxa_sim_pct=Decimal("0"),
            ticket_sim=Decimal("0"),
        )
        self.assertEqual(resultado.simulado.contratos, ZERO)
        self.assertEqual(resultado.simulado.receita, ZERO)
        self.assertTrue(isfinite(float(resultado.simulado.receita)))
        self.assertFalse(isinf(float(resultado.simulado.receita)))
        self.assertFalse(isnan(float(resultado.simulado.receita)))
        self.assertIn("Não representa garantia", resultado.aviso)
        self.assertNotIn("Você terá", resultado.aviso)

    def test_funil_amostra_pequena_nao_diagnostica_gargalo(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Lead unico",
            email="unico@t.com",
        )
        funil = calcular_funil_receita(self.user, self.periodo, organization=self.org)
        self.assertEqual(funil.leads, 1)
        self.assertIsNone(funil.gargalo)
        self.assertIn("diagnóstico confiável", funil.mensagem.lower())

        for i in range(MIN_AMOSTRAS_CONVERSAO):
            Cliente.objects.create(
                user=self.user,
                organization=self.org,
                nome=f"Lead {i}",
                email=f"lead{i}@t.com",
            )
        funil_ok = calcular_funil_receita(self.user, self.periodo, organization=self.org)
        self.assertGreaterEqual(funil_ok.leads, MIN_AMOSTRAS_CONVERSAO)
        self.assertIsNotNone(funil_ok.gargalo)
        self.assertEqual(funil_ok.mensagem, "")

    def test_oportunidade_parada_nao_e_perdida(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Parado",
            email="parado@t.com",
            fase_funil="proposta_enviada",
        )
        recs = listar_recomendacoes(self.user, limite=5)
        textos = " ".join(
            f"{i.titulo} {i.acao_recomendada} {i.motivo}" for i in recs.itens
        ).lower()
        self.assertNotIn("perdida", textos)
        self.assertLessEqual(len(recs.itens), 5)


class ComercialProTenantTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="PRO Org A")
        self.org_b = Organization.objects.create(name="PRO Org B")
        self.a1 = User.objects.create_user("pro_a1", password="senha123")
        self.a2 = User.objects.create_user("pro_a2", password="senha123")
        self.b1 = User.objects.create_user("pro_b1", password="senha123")
        self.hoje = timezone.localdate()
        for user, org in (
            (self.a1, self.org_a),
            (self.a2, self.org_a),
            (self.b1, self.org_b),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=Membership.Role.MEMBER,
                status=Membership.Status.ACTIVE,
            )
        self.cli_a = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="PRO-CLI-A-MARKER",
            email="a@pro.test",
        )
        self.cli_b = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="PRO-CLI-B-MARKER",
            email="b@pro.test",
        )
        Contrato.objects.create(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.cli_a,
            referencia="PRO-A",
            descricao="Contrato A",
            valor_total=Decimal("3000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a1,
        )
        Contrato.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cli_b,
            referencia="PRO-B",
            descricao="Contrato B",
            valor_total=Decimal("99000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.b1,
        )
        cob_a = Cobranca.objects.create(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.cli_a,
            descricao="PRO-REC-A",
            valor_original=Decimal("500.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.a1,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_a,
            usuario=self.a1,
            organization=self.org_a,
            valor=Decimal("500.00"),
            data_recebimento=self.hoje,
            registrado_por=self.a1,
        )
        cob_b = Cobranca.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cli_b,
            descricao="PRO-REC-B",
            valor_original=Decimal("77000.00"),
            data_vencimento=self.hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=self.b1,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob_b,
            usuario=self.b1,
            organization=self.org_b,
            valor=Decimal("77000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.b1,
        )
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.a1)
        grant_comercial_permissions(self.a2)
        grant_comercial_permissions(self.b1)
        self.http = Client()

    def _tenant_request(self, user, *, organization=None, context=None, omit_tenant=False):
        request = RequestFactory().get(reverse("comercial_dashboard"))
        request.user = user
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        if not omit_tenant:
            request.organization = organization
            request.organization_context = context
        return request

    def test_same_org_a1_a2_veem_agregacao_financeira_compartilhada(self):
        rec_a1 = receita_no_periodo(self.org_a, periodo_mes())
        rec_a2 = receita_no_periodo(self.org_a, periodo_mes())
        self.assertEqual(rec_a1.recebido, rec_a2.recebido)
        self.assertEqual(rec_a1.contratado, Decimal("3000.00"))
        self.assertEqual(rec_a2.contratado, Decimal("3000.00"))
        ctx_a1 = contexto_dashboard(self.a1, organization=self.org_a, get_params={"aba": "visao"})
        ctx_a2 = contexto_dashboard(self.a2, organization=self.org_a, get_params={"aba": "visao"})
        self.assertEqual(ctx_a1.kpis.realizado_mes, ctx_a2.kpis.realizado_mes)
        self.assertEqual(ctx_a1.kpis.contratado_mes, Decimal("3000.00"))

    def test_cross_org_b_nao_entra_em_kpis_advisor_nem_html(self):
        rec_a = receita_no_periodo(self.org_a, periodo_mes())
        rec_b = receita_no_periodo(self.org_b, periodo_mes())
        self.assertEqual(rec_a.recebido, Decimal("500.00"))
        self.assertEqual(rec_b.recebido, Decimal("77000.00"))
        self.assertNotEqual(rec_a.contratado, rec_b.contratado)
        ctx_a = contexto_dashboard(self.a1, organization=self.org_a, get_params={"aba": "visao"})
        self.assertEqual(ctx_a.kpis.realizado_mes, Decimal("3000.00"))
        self.assertNotEqual(ctx_a.kpis.realizado_mes, Decimal("77000.00"))
        textos = " ".join(
            [
                ctx_a.advisor.mensagem,
                ctx_a.advisor.principal.titulo if ctx_a.advisor.principal else "",
                ctx_a.advisor.insight.texto if ctx_a.advisor.insight else "",
            ]
        )
        self.assertNotIn("PRO-CLI-B-MARKER", textos)
        self.assertNotIn("77000", textos)

        self.http.force_login(self.a1)
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn("PRO-CLI-B-MARKER", html)
        self.assertNotIn("R$ 77.000", html)
        self.assertNotIn("R$ 99.000", html)

    def test_tenantcontext_invalido_fail_closed(self):
        for kwargs in (
            {"organization": None, "context": CONTEXT_NONE},
            {"organization": self.org_a, "context": CONTEXT_AMBIGUOUS},
            {"omit_tenant": True},
            {"organization": None, "context": CONTEXT_RESOLVED},
            {"organization": self.org_a, "context": "inconsistent"},
            {"organization": self.org_a, "context": "missing"},
        ):
            request = self._tenant_request(self.a1, **kwargs)
            org = organization_for_finance_write(request)
            self.assertIsNone(org, msg=kwargs)
            ctx = contexto_dashboard(self.a1, organization=org, get_params={"aba": "visao"})
            self.assertEqual(ctx.kpis.realizado_mes, ZERO, msg=kwargs)
            self.assertEqual(ctx.kpis.contratado_mes, ZERO, msg=kwargs)
            self.assertNotEqual(ctx.kpis.realizado_mes, Decimal("500.00"), msg=kwargs)
            self.assertNotEqual(ctx.kpis.contratado_mes, Decimal("99000.00"), msg=kwargs)
