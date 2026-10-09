"""AGENDA-MT-01 — isolamento Organization, responsabilidade, capability e TenantContext."""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from io import StringIO

from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.choices import Prioridade, StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.forms import CompromissoForm, TarefaForm
from usuarios.models import Cliente, Compromisso, Tarefa
from usuarios.services.agenda import (
    AgendaFiltros,
    VIEW_LISTA,
    calcular_kpis,
    compromissos_para_agenda,
    tarefas_para_agenda,
)
from usuarios.tenancy_agenda import organization_for_agenda
from usuarios.tests_helpers import grant_agenda_permissions


def _dt(d, hora=10):
    from datetime import datetime, time

    naive = datetime.combine(d, time(hour=hora))
    return timezone.make_aware(naive)


class AgendaMtAdversarialTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Agenda Org A")
        self.org_b = Organization.objects.create(name="Agenda Org B")
        self.a1 = User.objects.create_user(username="mt_a1", password="senha123")
        self.a2 = User.objects.create_user(username="mt_a2", password="senha123")
        self.b1 = User.objects.create_user(username="mt_b1", password="senha123")
        self.owner_sem_perm = User.objects.create_user(
            username="mt_owner_np", password="senha123"
        )
        self._member(self.a1, self.org_a, role=Membership.Role.OWNER)
        self._member(self.a2, self.org_a)
        self._member(self.b1, self.org_b, role=Membership.Role.OWNER)
        self._member(self.owner_sem_perm, self.org_a, role=Membership.Role.OWNER)
        self._grupo_autorizado(self.a1, self.a2, self.b1)
        grant_agenda_permissions(self.a1)
        grant_agenda_permissions(self.a2)
        grant_agenda_permissions(self.b1)
        self._grupo_vazio(self.owner_sem_perm)
        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente CA",
            email="ca@mt.test",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente CB",
            email="cb@mt.test",
        )
        self.hoje = timezone.localdate()
        self.comp_a = Compromisso.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo="Compromisso CA A2",
            data_hora=_dt(self.hoje, hora=16),
            cliente=self.ca,
            responsavel=self.a2,
        )
        self.tar_a = Tarefa.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo="Tarefa CA A2",
            prazo=self.hoje,
            cliente=self.ca,
            responsavel=self.a2,
            status=StatusTarefa.PENDENTE,
        )
        self.comp_b = Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo="Compromisso CB B1",
            data_hora=_dt(self.hoje, hora=15),
            cliente=self.cb,
            responsavel=self.b1,
        )
        self.tar_b = Tarefa.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo="Tarefa CB B1",
            prazo=self.hoje,
            cliente=self.cb,
            responsavel=self.b1,
            status=StatusTarefa.PENDENTE,
        )
        self.http = Client()

    def _member(self, user, org, role=Membership.Role.MEMBER):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=role,
            status=Membership.Status.ACTIVE,
        )

    def _grupo_autorizado(self, *users):
        grupo = Group.objects.create(name="Agenda MT autorizado")
        for user in users:
            user.groups.add(grupo)

    def _grupo_vazio(self, user):
        grupo = Group.objects.create(name=f"Sem agenda {user.username}")
        grupo.permissions.clear()
        user.groups.add(grupo)

    def _ids_lista(self, username):
        self.http.login(username=username, password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        return html, resp

    def test_same_org_a1_ve_item_a2(self):
        html, _ = self._ids_lista("mt_a1")
        self.assertIn("Compromisso CA A2", html)
        self.assertIn("Tarefa CA A2", html)
        self.assertNotIn("Compromisso CB B1", html)
        self.assertNotIn("Tarefa CB B1", html)

    def test_same_org_a2_mesma_populacao(self):
        html, _ = self._ids_lista("mt_a2")
        self.assertIn("Compromisso CA A2", html)
        self.assertIn("Tarefa CA A2", html)
        self.assertNotIn("Compromisso CB B1", html)

    def test_minhas_atividades_responsibility(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Compromisso A1 meu",
            data_hora=_dt(self.hoje, hora=11),
            responsavel=self.a1,
        )
        self.http.login(username="mt_a1", password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista", "escopo": "minha"})
        html = resp.content.decode()
        self.assertIn("Compromisso A1 meu", html)
        self.assertNotIn("Compromisso CA A2", html)
        self.assertNotIn("Compromisso CB B1", html)

        self.http.login(username="mt_a2", password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista", "escopo": "minha"})
        html = resp.content.decode()
        self.assertIn("Compromisso CA A2", html)
        self.assertNotIn("Compromisso A1 meu", html)

    def test_cross_org_a1_nao_ve_nem_edita_b(self):
        html, _ = self._ids_lista("mt_a1")
        self.assertNotIn("Compromisso CB B1", html)
        self.http.login(username="mt_a1", password="senha123")
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "editar_compromisso",
                "compromisso_id": self.comp_b.pk,
                "titulo": "Hack B",
                "tipo": TipoCompromisso.REUNIAO,
                "status": "agendado",
                "prioridade": "normal",
                "data": self.hoje.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "responsavel": self.a1.pk,
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.comp_b.refresh_from_db()
        self.assertEqual(self.comp_b.titulo, "Compromisso CB B1")

        resp = self.http.post(
            reverse("agenda"),
            {"action": "excluir_compromisso", "compromisso_id": self.comp_b.pk},
        )
        self.assertEqual(resp.status_code, 404)
        self.comp_b.refresh_from_db()
        self.assertNotEqual(self.comp_b.status, StatusCompromisso.CANCELADO)

        resp = self.http.post(
            reverse("agenda"),
            {"action": "concluir_tarefa", "tarefa_id": self.tar_b.pk},
        )
        self.assertEqual(resp.status_code, 404)
        self.tar_b.refresh_from_db()
        self.assertEqual(self.tar_b.status, StatusTarefa.PENDENTE)

        resp = self.http.post(
            reverse("agenda"),
            {"action": "realizar_compromisso", "compromisso_id": self.comp_b.pk},
        )
        self.assertEqual(resp.status_code, 404)

    def test_cliente_cross_org_choices_e_post(self):
        form = CompromissoForm(user=self.a1, organization=self.org_a)
        ids = set(form.fields["cliente"].queryset.values_list("pk", flat=True))
        self.assertIn(self.ca.pk, ids)
        self.assertNotIn(self.cb.pk, ids)

        self.http.login(username="mt_a1", password="senha123")
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Com CB",
                "tipo": TipoCompromisso.REUNIAO,
                "status": "agendado",
                "prioridade": "normal",
                "data": self.hoje.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "responsavel": self.a1.pk,
                "cliente": self.cb.pk,
            },
        )
        self.assertFalse(Compromisso.objects.filter(titulo="Com CB").exists())

    def test_responsavel_cross_org_choices_e_post(self):
        form = CompromissoForm(user=self.a1, organization=self.org_a)
        ids = set(form.fields["responsavel"].queryset.values_list("pk", flat=True))
        self.assertIn(self.a1.pk, ids)
        self.assertIn(self.a2.pk, ids)
        self.assertNotIn(self.b1.pk, ids)

        self.http.login(username="mt_a1", password="senha123")
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_tarefa",
                "titulo": "Tarefa B1 na Org A",
                "status": "pendente",
                "prioridade": "normal",
                "responsavel": self.b1.pk,
            },
        )
        self.assertFalse(Tarefa.objects.filter(titulo="Tarefa B1 na Org A").exists())

    def test_capability_membership_nao_basta_owner_nao_bypass(self):
        self.http.login(username="mt_owner_np", password="senha123")
        resp = self.http.get(reverse("agenda"))
        self.assertEqual(resp.status_code, 302)
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_tarefa",
                "titulo": "Owner bypass",
                "status": "pendente",
                "prioridade": "normal",
                "responsavel": self.owner_sem_perm.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Tarefa.objects.filter(titulo="Owner bypass").exists())

    def test_tenantcontext_invalido_nao_fallback(self):
        factory = RequestFactory()
        for ctx, org in (
            (CONTEXT_NONE, None),
            (CONTEXT_AMBIGUOUS, self.org_a),
            (CONTEXT_RESOLVED, None),
            ("inconsistente", self.org_a),
        ):
            request = factory.get("/usuarios/agenda/")
            request.user = self.a1
            request.organization = org
            request.organization_context = ctx
            self.assertIsNone(organization_for_agenda(request))

        qs = compromissos_para_agenda(None, AgendaFiltros(view=VIEW_LISTA), user=self.a1)
        self.assertEqual(qs.count(), 0)
        qs_t = tarefas_para_agenda(None, AgendaFiltros(view=VIEW_LISTA), user=self.a1)
        self.assertEqual(qs_t.count(), 0)
        kpis = calcular_kpis(None, self.hoje)
        self.assertEqual(kpis.compromissos_hoje, 0)

        form = CompromissoForm(
            user=self.a1,
            organization=None,
            data={
                "titulo": "Sem tenant",
                "tipo": TipoCompromisso.REUNIAO,
                "status": "agendado",
                "prioridade": "normal",
                "data": self.hoje.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "responsavel": self.a1.pk,
            },
        )
        self.assertFalse(form.is_valid())

    def test_null_organization_nao_reaparece_via_user(self):
        nulo = Compromisso.objects.create(
            user=self.a1,
            organization=None,
            titulo="Legado NULL",
            data_hora=_dt(self.hoje, hora=16),
            responsavel=self.a1,
        )
        html, _ = self._ids_lista("mt_a1")
        self.assertNotIn("Legado NULL", html)
        qs = compromissos_para_agenda(
            self.org_a, AgendaFiltros(view=VIEW_LISTA), user=self.a1
        )
        self.assertNotIn(nulo.pk, set(qs.values_list("pk", flat=True)))

    def test_dual_write_usa_request_organization(self):
        self.http.login(username="mt_a1", password="senha123")
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Dual-write Org A",
                "tipo": TipoCompromisso.REUNIAO,
                "status": "agendado",
                "prioridade": "normal",
                "data": self.hoje.isoformat(),
                "hora_inicio": "12:00",
                "hora_fim": "13:00",
                "responsavel": self.a2.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        criado = Compromisso.objects.get(titulo="Dual-write Org A")
        self.assertEqual(criado.organization_id, self.org_a.pk)
        self.assertEqual(criado.user_id, self.a1.pk)
        self.assertEqual(criado.responsavel_id, self.a2.pk)

    def test_cliente_360_full_org_scoped(self):
        self.http.login(username="mt_a1", password="senha123")
        resp = self.http.get(reverse("cliente", kwargs={"id": self.ca.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Tarefa CA A2")
        self.assertContains(resp, "Compromisso CA A2")

    def test_kpis_organization(self):
        kpis_a = calcular_kpis(self.org_a, self.hoje)
        kpis_b = calcular_kpis(self.org_b, self.hoje)
        self.assertGreaterEqual(kpis_a.compromissos_hoje, 1)
        self.assertGreaterEqual(kpis_b.compromissos_hoje, 1)
        self.assertNotEqual(kpis_a.compromissos_hoje, kpis_a.compromissos_hoje + kpis_b.compromissos_hoje)


class AgendaMtBackfillTests(TestCase):
    def test_backfill_cliente_e_membership_unica(self):
        org = Organization.objects.create(name="BF Org")
        user = User.objects.create_user(username="bf_u", password="x")
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        cli = Cliente.objects.create(
            user=user, organization=org, nome="BF Cli", email="bf@t.com"
        )
        c = Compromisso.objects.create(
            user=user, titulo="BF C", data_hora=_dt(timezone.localdate())
        )
        t = Tarefa.objects.create(user=user, titulo="BF T", prazo=timezone.localdate())
        outro = User.objects.create_user(username="bf_out", password="x")
        nulo = Compromisso.objects.create(
            user=outro, titulo="BF NULL", data_hora=_dt(timezone.localdate())
        )
        c.cliente = cli
        c.save(update_fields=["cliente"])

        out = StringIO()
        call_command("backfill_agenda_organization", "--dry-run", stdout=out)
        self.assertIn("ELIGIBLE: 2", out.getvalue())
        call_command("backfill_agenda_organization", "--apply", stdout=StringIO())
        c.refresh_from_db()
        t.refresh_from_db()
        nulo.refresh_from_db()
        self.assertEqual(c.organization_id, org.pk)
        self.assertEqual(t.organization_id, org.pk)
        self.assertIsNone(nulo.organization_id)
        out2 = StringIO()
        call_command("backfill_agenda_organization", "--dry-run", stdout=out2)
        self.assertIn("ELIGIBLE: 0", out2.getvalue())
        self.assertIn("NOTHING_TO_APPLY", out2.getvalue())

    def test_backfill_ambiguous_nao_atribui(self):
        org1 = Organization.objects.create(name="BF1")
        org2 = Organization.objects.create(name="BF2")
        user = User.objects.create_user(username="bf_amb", password="x")
        Membership.objects.create(
            user=user, organization=org1, status=Membership.Status.ACTIVE
        )
        Membership.objects.create(
            user=user, organization=org2, status=Membership.Status.ACTIVE
        )
        c = Compromisso.objects.create(
            user=user, titulo="Amb", data_hora=_dt(timezone.localdate())
        )
        out = StringIO()
        call_command("backfill_agenda_organization", "--dry-run", stdout=out)
        self.assertIn("AMBIGUOUS:", out.getvalue())
        with self.assertRaises(Exception):
            call_command("backfill_agenda_organization", "--apply", stdout=StringIO())
        c.refresh_from_db()
        self.assertIsNone(c.organization_id)
