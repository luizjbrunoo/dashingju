"""COMERCIAL-PRO-02 — temperatura, desfecho, projeção comercial e DEMO."""

from datetime import timedelta
from decimal import Decimal
from math import isfinite, isinf, isnan

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.advisor import montar_advisor
from comercial.services.dashboard import contexto_dashboard
from comercial.services.demo_seed import (
    DEMO_NAME,
    UTM_CONTENT,
    popular_comercial_demo,
    resolver_organization_demo,
)
from comercial.services.metrics import calcular_kpis, periodo_mes, receita_no_periodo
from comercial.services.money import ZERO
from comercial.services.periodo import PeriodoComercial
from comercial.services.pipeline import (
    TEMP_FERVENDO,
    TEMP_FRIO,
    TEMP_MORNO,
    TEMP_NAO_CLASSIFICADO,
    TEMP_QUENTE,
    SinaisTemperatura,
    analisar_pipeline,
    classificar_temperatura,
    parse_motivo_perda,
)
from comercial.services.projections import projetar_faturamento_mensal
from financeiro.choices import StatusContrato
from financeiro.models import Contrato
from financeiro.tenancy_write import organization_for_finance_write
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso


def _sinais(**kwargs):
    base = dict(
        etapa_proposta=False,
        aguardando_decisao=False,
        consulta_realizada=False,
        atividade_recente=False,
        proxima_acao=False,
        idade_dias=0,
    )
    base.update(kwargs)
    return SinaisTemperatura(**base)


class TemperaturaTests(TestCase):
    def test_nao_classificado_sem_sinais(self):
        temp, motivos = classificar_temperatura("primeiro_contato", _sinais(idade_dias=1))
        self.assertEqual(temp, TEMP_NAO_CLASSIFICADO)
        self.assertTrue(motivos)

    def test_frio_com_idade_sem_avanco(self):
        temp, motivos = classificar_temperatura("primeiro_contato", _sinais(idade_dias=14))
        self.assertEqual(temp, TEMP_FRIO)
        self.assertTrue(motivos)

    def test_morno_com_consulta(self):
        temp, _m = classificar_temperatura(
            "primeiro_contato", _sinais(consulta_realizada=True, idade_dias=10)
        )
        self.assertEqual(temp, TEMP_MORNO)

    def test_quente_proposta(self):
        temp, motivos = classificar_temperatura(
            "proposta_enviada", _sinais(etapa_proposta=True, atividade_recente=True)
        )
        self.assertEqual(temp, TEMP_QUENTE)
        self.assertIn("proposta enviada", " ".join(motivos))

    def test_fervendo_nao_e_ganho(self):
        temp, motivos = classificar_temperatura(
            "aguardando_decisao",
            _sinais(
                etapa_proposta=True,
                aguardando_decisao=True,
                proxima_acao=True,
                atividade_recente=True,
            ),
        )
        self.assertEqual(temp, TEMP_FERVENDO)
        self.assertNotIn("garantia", " ".join(motivos).lower())

    def test_frio_nao_e_perdido(self):
        self.assertNotEqual(TEMP_FRIO, "perdido")


class DesfechoPipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("p2_a", password="senha123")
        self.org = Organization.objects.create(name="P2 Org A")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.hoje = timezone.localdate()
        self.periodo = PeriodoComercial.ultimos_dias(30, referencia=self.hoje)
        self.ticket = Decimal("10000.00")

    def test_parado_nao_e_perdido_ganho_exige_contrato(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Parado",
            email="parado@p2.test",
            status="em_prospeccao",
            fase_funil="proposta_enviada",
        )
        ganho = Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Ganho",
            email="ganho@p2.test",
            status="ativo",
            fase_funil="aguardando_decisao",
        )
        Contrato.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=ganho,
            referencia="P2-G",
            descricao="Ganho",
            valor_total=Decimal("8000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user,
        )
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Perdido",
            email="perdido@p2.test",
            status="inativo",
            fase_funil="proposta_enviada",
            relatorio_prospeccao="motivo_perda:honorarios",
        )
        painel = analisar_pipeline(self.org, self.periodo, ticket=self.ticket)
        self.assertGreaterEqual(painel.abertos, 1)
        self.assertEqual(painel.ganhos, 1)
        self.assertEqual(painel.perdidos, 1)
        self.assertEqual(painel.valor_ganho, Decimal("8000.00"))
        self.assertEqual(parse_motivo_perda("motivo_perda:honorarios"), "honorarios")
        self.assertEqual(parse_motivo_perda("texto livre antigo"), "nao_informado")
        rec = receita_no_periodo(self.org, periodo_mes())
        self.assertEqual(rec.contratado, Decimal("8000.00"))
        self.assertEqual(rec.recebido, ZERO)
        kpis = calcular_kpis(
            self.org, meta=None, receita_risco=ZERO, ocultar_financeiro=False, projecao_anual=None
        )
        self.assertEqual(kpis.realizado_mes, Decimal("8000.00"))
        self.assertNotEqual(kpis.realizado_mes, rec.recebido)

    def test_perda_inativa_com_contrato_nao_conta_como_perdida(self):
        cli = Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Ex-cliente",
            email="ex@p2.test",
            status="inativo",
        )
        Contrato.objects.create(
            usuario=self.user,
            organization=self.org,
            cliente=cli,
            referencia="P2-EX",
            descricao="Já foi ganho",
            valor_total=Decimal("1000.00"),
            status=StatusContrato.CLOSED,
            criado_por=self.user,
        )
        painel = analisar_pipeline(self.org, self.periodo, ticket=self.ticket)
        self.assertEqual(painel.perdidos, 0)

    def test_projecao_exclui_perdidos_e_nao_fabrica_com_amostra_pequena(self):
        Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Perda unica",
            email="p1@p2.test",
            status="inativo",
        )
        proj = projetar_faturamento_mensal(self.org, Decimal("50000"), ticket=self.ticket)
        self.assertIsNone(proj.projecao)
        self.assertIn("insuficientes", proj.mensagem.lower())
        self.assertIn("não representa garantia", proj.mensagem.lower())

    def test_projecao_com_historico_suficiente(self):
        for i in range(3):
            cli = Cliente.objects.create(
                user=self.user,
                organization=self.org,
                nome=f"G{i}",
                email=f"g{i}@p2.test",
                status="ativo",
            )
            Contrato.objects.create(
                usuario=self.user,
                organization=self.org,
                cliente=cli,
                referencia=f"P2-G{i}",
                descricao="G",
                valor_total=Decimal("10000.00"),
                status=StatusContrato.ACTIVE,
                criado_por=self.user,
            )
        for i in range(5):
            Cliente.objects.create(
                user=self.user,
                organization=self.org,
                nome=f"L{i}",
                email=f"l{i}@p2.test",
                status="inativo",
                fase_funil="proposta_enviada",
                relatorio_prospeccao="motivo_perda:sem_retorno",
            )
        quente = Cliente.objects.create(
            user=self.user,
            organization=self.org,
            nome="Quente aberto",
            email="q@p2.test",
            status="em_prospeccao",
            fase_funil="proposta_enviada",
        )
        Compromisso.objects.create(
            user=self.user,
            organization=self.org,
            cliente=quente,
            titulo="Reuniao",
            tipo=TipoCompromisso.REUNIAO,
            status=StatusCompromisso.REALIZADO,
            data_hora=timezone.now() - timedelta(days=1),
        )
        proj = projetar_faturamento_mensal(self.org, Decimal("100000"), ticket=self.ticket)
        self.assertEqual(proj.realizado, Decimal("30000.00"))
        self.assertIsNotNone(proj.projecao)
        self.assertGreaterEqual(proj.projecao, proj.realizado)
        self.assertNotEqual(proj.projecao, proj.pipeline_total)
        self.assertTrue(isfinite(float(proj.projecao)))
        self.assertFalse(isinf(float(proj.projecao)))
        self.assertFalse(isnan(float(proj.projecao)))
        self.assertEqual(proj.gap, proj.meta - proj.projecao)
        self.assertIn("estimativa", proj.mensagem.lower())
        painel = analisar_pipeline(self.org, self.periodo, ticket=self.ticket)
        self.assertTrue(painel.amostra_perdas_suficiente)
        self.assertEqual(painel.perda_etapa_principal.etapa, "proposta_enviada")
        self.assertEqual(painel.perda_motivo_principal.codigo, "sem_retorno")
        advisor = montar_advisor(
            self.user, organization=self.org, ticket=self.ticket, pipeline=painel
        )
        self.assertIsNotNone(advisor.principal)
        keys = [advisor.principal.key] + [s.key for s in advisor.secundarias]
        self.assertTrue({"avancados_sem_acao", "perdas_etapa", "propostas_aguardando"} & set(keys))


class PipelineTenantTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="P2 Ten A")
        self.org_b = Organization.objects.create(name="P2 Ten B")
        self.a1 = User.objects.create_user("p2a1", password="senha123")
        self.a2 = User.objects.create_user("p2a2", password="senha123")
        self.b1 = User.objects.create_user("p2b1", password="senha123")
        for u, org in ((self.a1, self.org_a), (self.a2, self.org_a), (self.b1, self.org_b)):
            Membership.objects.create(
                user=u, organization=org, role=Membership.Role.MEMBER, status=Membership.Status.ACTIVE
            )
        self.periodo = PeriodoComercial.ultimos_dias(30)
        Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="MARKER-A-PIPE",
            email="a@p2ten.test",
            status="inativo",
            relatorio_prospeccao="motivo_perda:honorarios",
        )
        Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="MARKER-B-PIPE",
            email="b@p2ten.test",
            status="inativo",
            relatorio_prospeccao="motivo_perda:desistiu",
        )

    def test_same_org_pipeline_compartilhado(self):
        pa = analisar_pipeline(self.org_a, self.periodo, ticket=Decimal("1000"))
        pb = analisar_pipeline(self.org_a, self.periodo, ticket=Decimal("1000"))
        self.assertEqual(pa.perdidos, pb.perdidos)
        self.assertEqual(pa.perdidos, 1)

    def test_cross_org_zero_leak(self):
        pa = analisar_pipeline(self.org_a, self.periodo)
        pb = analisar_pipeline(self.org_b, self.periodo)
        self.assertEqual(pa.perdidos, 1)
        self.assertEqual(pb.perdidos, 1)
        ctx_a = contexto_dashboard(self.a1, organization=self.org_a, get_params={"aba": "visao"})
        self.assertNotIn("MARKER-B-PIPE", str(ctx_a.pipeline))
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.a1)
        html = Client()
        html.force_login(self.a1)
        resp = html.get(reverse("comercial_dashboard"))
        self.assertNotContains(resp, "MARKER-B-PIPE")
        self.assertContains(resp, "Temperatura do pipeline")
        self.assertContains(resp, "Ganhos e perdas")
        self.assertContains(resp, "Realizado comercial")

    def test_invalid_context_fail_closed(self):
        request = RequestFactory().get(reverse("comercial_dashboard"))
        request.user = self.a1
        request.session = SessionStore()
        request.session.save()
        request._messages = FallbackStorage(request)
        request.organization = None
        request.organization_context = CONTEXT_NONE
        self.assertIsNone(organization_for_finance_write(request))
        painel = analisar_pipeline(None, self.periodo)
        self.assertEqual(painel.perdidos, 0)
        ctx = contexto_dashboard(self.a1, organization=None, get_params={"aba": "visao"})
        self.assertEqual(ctx.pipeline.perdidos, 0)
        self.assertEqual(ctx.kpis.realizado_mes, ZERO)
        req = RequestFactory().get("/")
        req.user = self.a1
        req.organization = self.org_a
        req.organization_context = CONTEXT_AMBIGUOUS
        self.assertIsNone(organization_for_finance_write(req))
        req.organization = None
        req.organization_context = CONTEXT_RESOLVED
        self.assertIsNone(organization_for_finance_write(req))

    def test_capability_oculta_valor_do_pipeline(self):
        ct = ContentType.objects.get_for_model(MetaComercial)
        view = Permission.objects.get(content_type=ct, codename="view_dashboard")
        grupo = Group.objects.create(name="P2 sem receita")
        grupo.permissions.add(view)
        self.a1.groups.add(grupo)
        ctx = contexto_dashboard(self.a1, organization=self.org_a, get_params={"aba": "visao"})
        self.assertTrue(ctx.ocultar_financeiro)
        self.assertIsNone(ctx.pipeline.valor_potencial_perdido)
        self.assertEqual(ctx.pipeline.valor_ganho, ZERO)


class DemoSeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("demo_owner", password="senha123")
        self.org = Organization.objects.create(name=DEMO_NAME)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )

    def test_resolver_slug_estavel(self):
        org = resolver_organization_demo()
        self.assertIsNotNone(org)
        self.assertEqual(org.pk, self.org.pk)

    def test_seed_idempotente_isolado_e_gera_engine_real(self):
        r1 = popular_comercial_demo()
        n1 = Cliente.objects.filter(organization=self.org, utm_content=UTM_CONTENT).count()
        r2 = popular_comercial_demo()
        n2 = Cliente.objects.filter(organization=self.org, utm_content=UTM_CONTENT).count()
        self.assertEqual(n1, n2)
        self.assertEqual(r1.clientes, r2.clientes)
        self.assertGreaterEqual(r2.ganhos, 3)
        self.assertGreaterEqual(r2.perdas, 5)
        periodo = PeriodoComercial.ultimos_dias(31)
        painel = analisar_pipeline(self.org, periodo, ticket=Decimal("15000"))
        temps = {t.key: t.quantidade for t in painel.temperaturas}
        self.assertGreaterEqual(temps[TEMP_FRIO], 1)
        self.assertGreaterEqual(temps[TEMP_MORNO], 1)
        self.assertGreaterEqual(temps[TEMP_QUENTE], 1)
        self.assertGreaterEqual(temps[TEMP_FERVENDO], 1)
        self.assertGreaterEqual(temps[TEMP_NAO_CLASSIFICADO], 1)
        self.assertGreaterEqual(painel.ganhos, 3)
        self.assertGreaterEqual(painel.perdidos, 5)
        self.assertTrue(painel.amostra_perdas_suficiente)
        self.assertIsNotNone(painel.perda_motivo_principal)
        proj = projetar_faturamento_mensal(self.org, Decimal("100000"), ticket=Decimal("15000"))
        self.assertGreater(proj.realizado, 0)
        rec = receita_no_periodo(self.org, periodo_mes())
        self.assertGreater(rec.contratado, rec.recebido)
        advisor = montar_advisor(
            self.user, organization=self.org, ticket=Decimal("15000"), pipeline=painel
        )
        self.assertIsNotNone(advisor.principal)
        self.assertTrue(advisor.principal.cta_ver)
        outro = Organization.objects.create(name="Tenant real X")
        self.assertEqual(
            Cliente.objects.filter(organization=outro, utm_content=UTM_CONTENT).count(), 0
        )

    def test_command_recusa_sem_tenant_demo(self):
        self.org.name = "Outro escritorio"
        self.org.slug = "outro-escritorio-nao-demo"
        self.org.save(update_fields=["name", "slug"])
        with self.assertRaises(CommandError):
            call_command("seed_comercial_demo")
