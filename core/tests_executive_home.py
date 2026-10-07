"""HOME-EXECUTIVA-GROWTH-ADVISOR-01 — snapshot, engine, routing e isolamento."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from core.services.executive_home import (
    DOMAIN_AGENDA,
    DOMAIN_CLIENTES,
    DOMAIN_COMERCIAL,
    DOMAIN_FINANCEIRO,
    DOMAIN_MARKETING,
    SEV_ALTA,
    SEV_ATENCAO,
    SEV_CRITICA,
    CapsExecutivas,
    ExecutiveSignal,
    montar_home_executiva,
    priorizar_sinais,
)
from financeiro.choices import StatusCobranca
from financeiro.models import Cobranca
from financeiro.tests_helpers import grant_finance_permissions
from marketing.models import MarketingIntegracao
from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS
from usuarios.choices import StatusTarefa, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa
from usuarios.tests_helpers import grant_agenda_permissions

MARKER_A = "EXEC_ORG_A_7F3K"
MARKER_B = "EXEC_ORG_B_9Q2M"
_DOMAIN_ORDER = (
    DOMAIN_AGENDA,
    DOMAIN_FINANCEIRO,
    DOMAIN_CLIENTES,
    DOMAIN_COMERCIAL,
    DOMAIN_MARKETING,
)


def _dt(d, hora=10):
    return timezone.make_aware(datetime.combine(d, time(hour=hora)))


def _sig(**kwargs):
    hoje = timezone.localdate()
    base = dict(
        domain=DOMAIN_COMERCIAL,
        signal_type="x",
        severity=SEV_ATENCAO,
        title="Sinal",
        evidence="evidência",
        business_context="",
        recommended_action="agir",
        cta_label="abrir",
        cta_url="/x/",
        capability_required="comercial",
        observed_at="t",
        period_start=hoje,
        period_end=hoje,
        organization_id=1,
        urgency=1,
        eligible=True,
    )
    base.update(kwargs)
    return ExecutiveSignal(**base)


def _grant_comercial(user, *codes):
    ct = ContentType.objects.get_for_model(MetaComercial)
    for code in codes or ("view_dashboard", "view_revenue"):
        perm = Permission.objects.get(content_type=ct, codename=code)
        user.user_permissions.add(perm)


def _grant_marketing(user, *codes):
    ct = ContentType.objects.get_for_model(MarketingIntegracao)
    for code in codes or ("view_marketing",):
        perm = Permission.objects.get(content_type=ct, codename=code)
        user.user_permissions.add(perm)


class PriorityEngineTests(TestCase):
    def test_somente_um_dominio(self):
        for domain in _DOMAIN_ORDER:
            p, sec = priorizar_sinais(
                [_sig(domain=domain, title=domain, severity=SEV_ALTA)]
            )
            self.assertEqual(p.domain, domain)
            self.assertEqual(sec, ())

    def test_agenda_critica_vence_comercial_atencao(self):
        p, _ = priorizar_sinais(
            [
                _sig(domain=DOMAIN_COMERCIAL, severity=SEV_ATENCAO, title="C", urgency=2),
                _sig(domain=DOMAIN_AGENDA, severity=SEV_CRITICA, title="A", urgency=3),
            ]
        )
        self.assertEqual(p.domain, DOMAIN_AGENDA)

    def test_financeiro_nao_vence_sempre(self):
        p, _ = priorizar_sinais(
            [
                _sig(domain=DOMAIN_FINANCEIRO, severity=SEV_ATENCAO, title="F", urgency=2),
                _sig(domain=DOMAIN_AGENDA, severity=SEV_CRITICA, title="A", urgency=3),
            ]
        )
        self.assertEqual(p.domain, DOMAIN_AGENDA)

    def test_comercial_nao_vence_sempre(self):
        p, _ = priorizar_sinais(
            [
                _sig(domain=DOMAIN_COMERCIAL, severity=SEV_CRITICA, title="C", urgency=2),
                _sig(domain=DOMAIN_FINANCEIRO, severity=SEV_ALTA, title="F", urgency=2),
            ]
        )
        self.assertEqual(p.domain, DOMAIN_COMERCIAL)

    def test_empate_deterministico(self):
        a = _sig(domain=DOMAIN_FINANCEIRO, severity=SEV_ALTA, title="F", urgency=2)
        b = _sig(domain=DOMAIN_AGENDA, severity=SEV_ALTA, title="A", urgency=2)
        p1, _ = priorizar_sinais([a, b])
        p2, _ = priorizar_sinais([b, a])
        self.assertEqual(p1.domain, p2.domain)
        self.assertEqual(p1.domain, DOMAIN_AGENDA)

    def test_sem_sinais_e_sem_action(self):
        p, sec = priorizar_sinais([])
        self.assertIsNone(p)
        self.assertEqual(sec, ())
        com_cta = _sig(title="Com CTA", severity=SEV_ALTA, cta_url="/ok/")
        sem_cta = _sig(
            title="Sem CTA", severity=SEV_ALTA, cta_url="", domain=DOMAIN_AGENDA
        )
        p, _ = priorizar_sinais([sem_cta, com_cta])
        self.assertEqual(p.title, "Com CTA")

    def test_dados_insuficientes_nao_elegivel(self):
        p, _ = priorizar_sinais(
            [_sig(eligible=False, severity=SEV_CRITICA, title="Insuf")]
        )
        self.assertIsNone(p)

    def test_max_tres_secundarios(self):
        sinais = [
            _sig(domain=d, title=d, severity=SEV_ATENCAO, signal_type=d)
            for d in _DOMAIN_ORDER
        ]
        _, sec = priorizar_sinais(sinais)
        self.assertLessEqual(len(sec), 3)


class HomeExecutivaTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Exec Org A")
        self.org_b = Organization.objects.create(name="Exec Org B")
        self.a1 = User.objects.create_user("exec_a1", password="senha123")
        self.a2 = User.objects.create_user("exec_a2", password="senha123")
        self.b1 = User.objects.create_user("exec_b1", password="senha123")
        self.sem_fin = User.objects.create_user("exec_sem_fin", password="senha123")
        self.sem_mkt = User.objects.create_user("exec_sem_mkt", password="senha123")
        self.hoje = timezone.localdate()
        grupo = Group.objects.create(name="Exec RBAC")
        for u in (self.a1, self.a2, self.b1, self.sem_fin, self.sem_mkt):
            u.groups.add(grupo)
            grant_agenda_permissions(u)
            _grant_comercial(u, "view_dashboard", "view_revenue")
        for u in (self.a1, self.a2, self.b1, self.sem_mkt):
            grant_finance_permissions(u, "view_cobrancas", "view_recebimentos")
        for u in (self.a1, self.a2, self.b1, self.sem_fin):
            _grant_marketing(u, "view_marketing")
        for u, org in (
            (self.a1, self.org_a),
            (self.a2, self.org_a),
            (self.sem_fin, self.org_a),
            (self.sem_mkt, self.org_a),
            (self.b1, self.org_b),
        ):
            Membership.objects.create(
                user=u, organization=org, status=Membership.Status.ACTIVE
            )
        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome=f"Cliente A {MARKER_A}",
            email="a@exec.test",
            status="ativo",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome=f"Cliente B {MARKER_B}",
            email="b@exec.test",
            status="ativo",
        )
        Tarefa.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo=f"Ação vencida {MARKER_A}",
            prazo=self.hoje - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
            responsavel=self.a2,
            cliente=self.ca,
        )
        Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo=f"Segredo B {MARKER_B}",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt(self.hoje, 9),
            responsavel=self.b1,
            cliente=self.cb,
        )
        Cobranca.objects.create(
            usuario=self.a1,
            organization=self.org_a,
            cliente=self.ca,
            descricao=f"Honorarios {MARKER_A}",
            valor_original=Decimal("1500.00"),
            data_vencimento=self.hoje - timedelta(days=4),
            status=StatusCobranca.OVERDUE,
            criado_por=self.a1,
        )
        Cobranca.objects.create(
            usuario=self.b1,
            organization=self.org_b,
            cliente=self.cb,
            descricao=f"Honorarios {MARKER_B}",
            valor_original=Decimal("9900.00"),
            data_vencimento=self.hoje - timedelta(days=2),
            status=StatusCobranca.OVERDUE,
            criado_por=self.b1,
        )
        self.http = Client()

    def _caps(self, **kwargs):
        defaults = dict(
            marketing=True,
            comercial=True,
            comercial_receita=True,
            clientes=True,
            agenda=True,
            financeiro=True,
            financeiro_recebimentos=True,
            marketing_financeiro=True,
        )
        defaults.update(kwargs)
        return CapsExecutivas(**defaults)

    def test_login_normal_e_next(self):
        resp = self.http.post(
            reverse("login"),
            {"username": "exec_a1", "password": "senha123"},
        )
        self.assertRedirects(resp, reverse("home"))
        self.http.logout()
        resp = self.http.post(
            reverse("login") + "?next=/usuarios/agenda/",
            {
                "username": "exec_a1",
                "password": "senha123",
                "next": "/usuarios/agenda/",
            },
        )
        self.assertRedirects(resp, "/usuarios/agenda/")

    def test_home_executiva_e_deep_link(self):
        self.http.login(username="exec_a1", password="senha123")
        resp = self.http.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Dashboard executivo", html)
        self.assertIn("Growth Advisor", html)
        self.assertIn(MARKER_A, html)
        self.assertNotIn(MARKER_B, html)
        self.assertIn("Por quê", html)
        self.assertIn("Próxima ação", html)
        self.assertIn("Pulso do escritório", html)
        self.assertNotIn("Saúde do escritório", html)
        self.assertNotIn("Business Health Score", html)
        self.assertIn("Realizado Comercial", html)
        self.assertIn("não é recebimento", html.lower())
        self.assertNotIn("ADV Growth Score", html)
        self.assertNotIn("Maturidade digital/operacional", html)
        self.assertNotIn("CPL", html)
        self.assertNotIn("CAC", html)
        self.assertNotIn("ROAS", html)
        self.assertNotIn("prazo jurídico perdido", html.lower())
        self.assertNotIn("intempestividade", html.lower())
        self.assertNotIn("xl:grid-cols-4", html)
        n_cards = len(resp.context["executiva"].snapshot)
        if n_cards >= 5:
            self.assertIn("xl:grid-cols-5", html)
        elif n_cards == 4:
            self.assertIn("lg:grid-cols-4", html)
        self.assertEqual(self.http.get(reverse("agenda")).status_code, 200)
        self.assertEqual(self.http.get(reverse("clientes")).status_code, 200)

    def test_same_org_cross_org_invalid(self):
        self.http.login(username="exec_a2", password="senha123")
        html = self.http.get(reverse("home")).content.decode()
        self.assertIn(MARKER_A, html)
        self.assertNotIn(MARKER_B, html)
        self.http.login(username="exec_b1", password="senha123")
        html_b = self.http.get(reverse("home")).content.decode()
        self.assertIn(MARKER_B, html_b)
        self.assertNotIn(MARKER_A, html_b)
        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.a1
        request.organization = self.org_a
        request.organization_context = CONTEXT_AMBIGUOUS
        from core.views import home as home_view

        resp = home_view(request)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn(MARKER_A, body)
        self.assertNotIn(MARKER_B, body)
        nulo = montar_home_executiva(None, user=self.a1)
        self.assertFalse(nulo.tenant_ok)

    def test_capability_filter_before_priority(self):
        sem = montar_home_executiva(
            self.org_a,
            user=self.sem_fin,
            caps=self._caps(
                financeiro=False,
                financeiro_recebimentos=False,
                marketing_financeiro=False,
            ),
        )
        visiveis = ((sem.advisor,) if sem.advisor else ()) + sem.secundarios
        self.assertTrue(all(s.domain != DOMAIN_FINANCEIRO for s in visiveis))
        self.assertTrue(all(c.domain != DOMAIN_FINANCEIRO for c in sem.snapshot))
        self.assertTrue(all(p.domain != DOMAIN_FINANCEIRO for p in sem.pulse))
        blob = " ".join(
            [
                sem.advisor.evidence if sem.advisor else "",
                *(s.evidence for s in sem.secundarios),
                *(c.primary_value for c in sem.snapshot),
                *(c.secondary_value for c in sem.snapshot),
                *(p.detalhe for p in sem.pulse),
                *(t.value or "" for t in sem.trajectory),
            ]
        )
        self.assertNotIn(MARKER_B, blob)
        self.assertNotIn("9900", blob)
        self.assertNotIn("1500", blob)
        self.assertTrue(
            all(
                getattr(s, "signal_type", "") != "cobrancas_vencidas"
                for s in visiveis
                if s
            )
        )

        sem_m = montar_home_executiva(
            self.org_a,
            user=self.sem_mkt,
            caps=self._caps(marketing=False, marketing_financeiro=False),
        )
        vis_m = ((sem_m.advisor,) if sem_m.advisor else ()) + sem_m.secundarios
        self.assertTrue(all(s.domain != DOMAIN_MARKETING for s in vis_m))
        self.assertTrue(all(c.domain != DOMAIN_MARKETING for c in sem_m.snapshot))

    def test_semantica_e_ctas(self):
        painel = montar_home_executiva(self.org_a, user=self.a1, caps=self._caps())
        blob = " ".join(
            [
                *(c.hint for c in painel.snapshot),
                painel.advisor.evidence if painel.advisor else "",
                *(s.nota for s in painel.trajectory),
            ]
        ).lower()
        self.assertIn("contratado", blob)
        self.assertIn("causalidade", blob)
        self.assertIn("contrato ≠ recebimento", blob)
        self.assertIn("relationship health", blob)
        if painel.advisor:
            self.assertTrue(painel.advisor.cta_url.startswith("/"))
        self.assertLessEqual(len(painel.secundarios), 3)
        self.assertLessEqual(len(painel.snapshot), 5)
        for nome in (
            "comercial_dashboard",
            "financeiro_dashboard",
            "agenda",
            "clientes",
            "marketing_dashboard",
        ):
            reverse(nome)

    def test_landing_publica_preservada(self):
        resp = self.http.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Bem-vindo ao ADV Growth 90")
        self.assertNotContains(resp, "Dashboard executivo")
        self.assertNotContains(resp, MARKER_A)

    def test_score_ausente_home_sem_dados_e_null(self):
        vazio_org = Organization.objects.create(name="Exec Vazio")
        vazio_u = User.objects.create_user("exec_vazio", password="senha123")
        vazio_u.groups.add(Group.objects.get(name="Exec RBAC"))
        grant_agenda_permissions(vazio_u)
        _grant_comercial(vazio_u, "view_dashboard", "view_revenue")
        grant_finance_permissions(vazio_u, "view_cobrancas", "view_recebimentos")
        _grant_marketing(vazio_u, "view_marketing")
        Membership.objects.create(
            user=vazio_u, organization=vazio_org, status=Membership.Status.ACTIVE
        )
        painel = montar_home_executiva(vazio_org, user=vazio_u, caps=self._caps())
        self.assertIsNone(painel.adv_score)
        self.assertTrue(all(p.estado == "Dados insuficientes" for p in painel.pulse))
        fin = next(c for c in painel.snapshot if c.domain == DOMAIN_FINANCEIRO)
        self.assertEqual(fin.primary_value, "Não disponível")
        com = next(c for c in painel.snapshot if c.domain == DOMAIN_COMERCIAL)
        self.assertEqual(com.secondary_value, "Não configurada")
        self.assertNotIn("R$ 0,00", fin.primary_value)
        mkt_cards = [c for c in painel.snapshot if c.domain == DOMAIN_MARKETING]
        self.assertEqual(mkt_cards, [])
        self.assertNotIn("CPL", " ".join(c.hint for c in painel.snapshot))
        self.assertNotIn("CAC", " ".join(c.primary_label + c.secondary_label for c in painel.snapshot))
        self.assertIsNone(painel.advisor)
        self.http.login(username="exec_vazio", password="senha123")
        html = self.http.get(reverse("home")).content.decode()
        self.assertNotIn("ADV Growth Score", html)
        self.assertNotIn("CPL", html)
        self.assertNotIn("ROAS", html)
