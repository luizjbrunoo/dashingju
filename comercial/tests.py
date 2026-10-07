"""Testes Fase 1 — Comercial (isolamento, metas, funil, risco)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.funnel import calcular_funil_receita
from comercial.services.goals import calcular_meta_reversa, salvar_meta, ticket_medio_contratos
from comercial.services.metrics import receita_no_periodo
from comercial.services.periodo import PeriodoComercial
from comercial.services.revenue_risk import calcular_receita_em_risco
from financeiro.choices import StatusCobranca, StatusContrato
from financeiro.models import Cobranca, Contrato
from organizacoes.models import Membership, Organization
from usuarios.choices import StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

User = get_user_model()


def _dt(d, hora=10):
    from datetime import datetime, time

    return timezone.make_aware(datetime.combine(d, time(hour=hora)))


class ComercialFase1Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="com_a", password="senha123")
        self.user_b = User.objects.create_user(username="com_b", password="senha123")
        self.http = Client()
        self.hoje = timezone.localdate()
        self.periodo = PeriodoComercial.ultimos_dias(30, referencia=self.hoje)

    def _lead(self, user, nome, email, **kwargs):
        org = kwargs.pop("organization", None)
        if org is None:
            from organizacoes.backfill import organization_id_from_unique_active_membership
            from organizacoes.models import Organization as Org

            oid = organization_id_from_unique_active_membership(user.pk)
            if oid:
                org = Org.objects.get(pk=oid)
        return Cliente.objects.create(
            user=user, nome=nome, email=email, organization=org, **kwargs
        )

    def _org(self, user, name):
        org = Organization.objects.create(name=name)
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        return org

    def test_isolamento_meta_entre_tenants(self):
        org_a = self._org(self.user_a, "Meta Org A")
        org_b = self._org(self.user_b, "Meta Org B")
        salvar_meta(
            self.user_a,
            ator=self.user_a,
            ano=self.hoje.year,
            meta_anual=Decimal("1000000"),
            meta_mensal=Decimal("83333.33"),
            ticket_medio=Decimal("7000"),
            ticket_medio_manual=True,
            vigencia_inicio=self.hoje.replace(month=1, day=1),
            vigencia_fim=self.hoje.replace(month=12, day=31),
            organization=org_a,
        )
        from comercial.services.goals import meta_do_ano

        self.assertIsNotNone(meta_do_ano(self.user_a, organization=org_a))
        self.assertIsNone(meta_do_ano(self.user_b, organization=org_b))
        self.assertEqual(MetaComercial.objects.filter(organization=org_a).count(), 1)
        self.assertEqual(MetaComercial.objects.filter(organization=org_b).count(), 0)

    def test_receita_nao_vaza_entre_tenants(self):
        org_a = self._org(self.user_a, "Com Org A")
        org_b = self._org(self.user_b, "Com Org B")
        lead_a = self._lead(self.user_a, "A", "a@t.com")
        lead_b = self._lead(self.user_b, "B", "b@t.com")
        Contrato.objects.create(
            usuario=self.user_a,
            organization=org_a,
            cliente=lead_a,
            referencia="CA-1",
            descricao="A",
            valor_total=Decimal("10000"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        Contrato.objects.create(
            usuario=self.user_b,
            organization=org_b,
            cliente=lead_b,
            referencia="CB-1",
            descricao="B",
            valor_total=Decimal("99999"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_b,
        )
        rec_a = receita_no_periodo(org_a, self.periodo)
        rec_b = receita_no_periodo(org_b, self.periodo)
        self.assertEqual(rec_a.contratado, Decimal("10000.00"))
        self.assertEqual(rec_b.contratado, Decimal("99999.00"))
        self.assertNotEqual(rec_a.contratado, rec_b.contratado)

    def test_dashboard_tenant_b_nao_ve_dados_a(self):
        lead = self._lead(self.user_a, "Segredo", "seg@t.com")
        Contrato.objects.create(
            usuario=self.user_a,
            cliente=lead,
            referencia="SEC-1",
            descricao="Secreto",
            valor_total=Decimal("50000"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.user_b, "view_dashboard")
        self._org(self.user_b, "Com Org B dash")
        self._org(self.user_a, "Com Org A dash")
        self.http.login(username="com_b", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "50000")
        self.assertNotContains(resp, "Segredo")

    def test_funil_contagens(self):
        org_a = self._org(self.user_a, "Com Funil A")
        lead = self._lead(self.user_a, "L1", "l1@t.com", fase_funil="proposta_enviada")
        Compromisso.objects.create(
            user=self.user_a,
            organization=org_a,
            cliente=lead,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=_dt(self.hoje),
        )
        Contrato.objects.create(
            usuario=self.user_a,
            organization=org_a,
            cliente=lead,
            referencia="F-1",
            descricao="Contrato",
            valor_total=Decimal("8000"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        funil = calcular_funil_receita(self.user_a, self.periodo, organization=org_a)
        self.assertEqual(funil.leads, 1)
        self.assertEqual(funil.com_consulta, 1)
        self.assertEqual(funil.consultas, 1)
        self.assertEqual(funil.propostas, 1)
        self.assertEqual(funil.contratos, 1)
        self.assertEqual(funil.receita_contratada, Decimal("8000.00"))
        self.assertIsNone(funil.gargalo)
        self.assertIn("diagnóstico confiável", funil.mensagem.lower())

    def test_ticket_medio_e_meta_reversa(self):
        org_a = self._org(self.user_a, "Com Ticket A")
        lead = self._lead(self.user_a, "T", "t@t.com")
        Contrato.objects.create(
            usuario=self.user_a,
            organization=org_a,
            cliente=lead,
            referencia="T-1",
            descricao="T",
            valor_total=Decimal("5000"),
            status=StatusContrato.ACTIVE,
            criado_por=self.user_a,
        )
        ticket = ticket_medio_contratos(org_a)
        self.assertEqual(ticket.valor, Decimal("5000.00"))
        meta = salvar_meta(
            self.user_a,
            ator=self.user_a,
            ano=self.hoje.year,
            meta_anual=Decimal("60000"),
            meta_mensal=Decimal("5000"),
            ticket_medio=Decimal("5000"),
            ticket_medio_manual=True,
            vigencia_inicio=self.hoje.replace(month=1, day=1),
            vigencia_fim=self.hoje.replace(month=12, day=31),
            organization=org_a,
        )
        rev = calcular_meta_reversa(self.user_a, meta, ticket, organization=org_a)
        self.assertEqual(rev.contratos_ano, 12)
        self.assertEqual(rev.contratos_mes, 1)

    def test_receita_risco_cobranca_vencida(self):
        org_a = self._org(self.user_a, "Com Risco A")
        lead = self._lead(self.user_a, "R", "r@t.com")
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=org_a,
            cliente=lead,
            descricao="Honorários",
            valor_original=Decimal("2100"),
            data_vencimento=self.hoje - timedelta(days=10),
            status=StatusCobranca.OVERDUE,
            criado_por=self.user_a,
        )
        risco = calcular_receita_em_risco(self.user_a, organization=org_a)
        self.assertEqual(risco.total_confirmado_risco, Decimal("2100.00"))
        self.assertTrue(any(i.key == "cobrancas_vencidas" for i in risco.itens))

    def test_empty_state_sem_meta(self):
        from comercial.tests_helpers import grant_comercial_permissions

        grant_comercial_permissions(self.user_a, "view_dashboard")
        self._org(self.user_a, "Com Empty A")
        self.http.login(username="com_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Meta do mês")

    def test_rbac_nega_sem_perm(self):
        grupo = Group.objects.create(name="Comercial — sem acesso")
        self.user_a.groups.add(grupo)
        self.http.login(username="com_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_rbac_permite_com_perm(self):
        ct = ContentType.objects.get_for_model(MetaComercial)
        perm = Permission.objects.get(content_type=ct, codename="view_dashboard")
        grupo = Group.objects.create(name="Comercial — view")
        grupo.permissions.add(perm)
        self.user_a.groups.add(grupo)
        self.http.login(username="com_a", password="senha123")
        resp = self.http.get(reverse("comercial_dashboard"))
        self.assertEqual(resp.status_code, 200)
