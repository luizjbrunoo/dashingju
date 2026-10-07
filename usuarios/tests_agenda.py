from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.choices import (
    Prioridade,
    StatusCompromisso,
    StatusTarefa,
    TipoCompromisso,
)
from usuarios.models import Cliente, Compromisso, CompromissoParticipante, Tarefa
from organizacoes.models import Membership, Organization
from usuarios.services.agenda import (
    VIEW_HOJE,
    VIEW_LISTA,
    VIEW_MES,
    VIEW_SEMANA,
    AgendaFiltros,
    calcular_kpis,
    calcular_resumo_semana,
    compromissos_para_agenda,
    fim_semana,
    inicio_semana,
    itens_atencao,
    montar_grade_mes,
    montar_lista_unificada,
    montar_semana,
    montar_timeline_hoje,
    tarefas_para_agenda,
)

User = get_user_model()


def _dt_no_dia(d, hora=10):
    from datetime import datetime, time

    naive = datetime.combine(d, time(hour=hora))
    return timezone.make_aware(naive)


def _provision_agenda_org(user, *clientes, name=None):
    org = Organization.objects.create(name=name or f"Org {user.username}-{user.pk}")
    Membership.objects.create(
        user=user,
        organization=org,
        role=Membership.Role.OWNER,
        status=Membership.Status.ACTIVE,
    )
    for cli in clientes:
        if cli is None:
            continue
        cli.organization = org
        cli.save(update_fields=["organization"])
    return org


class _AgendaOrgMixin:
    """Estampa Organization nos creates e resolve tenant para a suíte legada."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        orig_setup = cls.setUp

        def setUp(self, *args, **inner):
            orig_setup(self, *args, **inner)
            self._attach_agenda_org()

        cls.setUp = setUp

    def _attach_agenda_org(self):
        if getattr(self, "_agenda_org_attached", False):
            return
        self._agenda_org_attached = True
        owner = getattr(self, "user_a", None) or getattr(self, "user", None)
        if owner and not getattr(self, "org", None) and not getattr(self, "org_a", None):
            self.org = _provision_agenda_org(
                owner,
                getattr(self, "cliente_a", None),
                getattr(self, "cliente", None),
            )
        if getattr(self, "org_a", None) and not getattr(self, "org", None):
            self.org = self.org_a
        if getattr(self, "user_b", None) and not getattr(self, "org_b", None):
            self.org_b = _provision_agenda_org(
                self.user_b,
                getattr(self, "cliente_b", None),
                name=f"Org B {self.user_b.pk}",
            )
        self._orig_compromisso_create = Compromisso.objects.create
        self._orig_tarefa_create = Tarefa.objects.create

        def _create_compromisso(*args, **kwargs):
            if "organization" not in kwargs and "organization_id" not in kwargs:
                kwargs["organization"] = self._org_for_user(kwargs.get("user"))
            return self._orig_compromisso_create(*args, **kwargs)

        def _create_tarefa(*args, **kwargs):
            if "organization" not in kwargs and "organization_id" not in kwargs:
                kwargs["organization"] = self._org_for_user(kwargs.get("user"))
            return self._orig_tarefa_create(*args, **kwargs)

        Compromisso.objects.create = _create_compromisso
        Tarefa.objects.create = _create_tarefa
        self.addCleanup(self._restore_agenda_creates)
        from usuarios.tests_helpers import grant_agenda_permissions

        for attr in ("user_a", "user", "user_b"):
            u = getattr(self, attr, None)
            if u is not None:
                grant_agenda_permissions(u)

    def _org_for_user(self, user):
        if user is not None and user == getattr(self, "user_b", None):
            return getattr(self, "org_b", None) or getattr(self, "org", None)
        if user is not None and user in (
            getattr(self, "user_a", None),
            getattr(self, "user", None),
        ):
            return getattr(self, "org", None) or getattr(self, "org_a", None)
        if user is None:
            return getattr(self, "org", None) or getattr(self, "org_a", None)
        return None

    def _restore_agenda_creates(self):
        Compromisso.objects.create = self._orig_compromisso_create
        Tarefa.objects.create = self._orig_tarefa_create


class AgendaQueryTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.hoje = timezone.localdate()

    def test_view_hoje_somente_compromissos_do_dia(self):
        c_hoje = Compromisso.objects.create(
            user=self.user_a,
            titulo="Hoje",
            data_hora=_dt_no_dia(self.hoje),
        )
        c_amanha = Compromisso.objects.create(
            user=self.user_a,
            titulo="Amanhã",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
        )
        filtros = AgendaFiltros(view=VIEW_HOJE)
        ids = set(compromissos_para_agenda(self.org, filtros).values_list("pk", flat=True))
        self.assertIn(c_hoje.pk, ids)
        self.assertNotIn(c_amanha.pk, ids)

    def test_view_lista_inclui_todos_compromissos(self):
        c1 = Compromisso.objects.create(
            user=self.user_a, titulo="A", data_hora=_dt_no_dia(self.hoje)
        )
        c2 = Compromisso.objects.create(
            user=self.user_a,
            titulo="B",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=3)),
        )
        filtros = AgendaFiltros(view=VIEW_LISTA)
        ids = set(compromissos_para_agenda(self.org, filtros).values_list("pk", flat=True))
        self.assertEqual(ids, {c1.pk, c2.pk})

    def test_filtro_cliente_compromissos(self):
        c1 = Compromisso.objects.create(
            user=self.user_a,
            titulo="Com cliente",
            data_hora=_dt_no_dia(self.hoje),
            cliente=self.cliente_a,
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Sem cliente",
            data_hora=_dt_no_dia(self.hoje),
        )
        filtros = AgendaFiltros(view=VIEW_LISTA, cliente_id=self.cliente_a.pk)
        self.assertEqual(
            list(compromissos_para_agenda(self.org, filtros).values_list("pk", flat=True)),
            [c1.pk],
        )

    def test_view_hoje_tarefas_prazo_hoje_ou_atrasadas(self):
        t_hoje = Tarefa.objects.create(
            user=self.user_a,
            titulo="Prazo hoje",
            prazo=self.hoje,
            status=StatusTarefa.PENDENTE,
        )
        t_atrasada = Tarefa.objects.create(
            user=self.user_a,
            titulo="Atrasada",
            prazo=self.hoje - timedelta(days=2),
            status=StatusTarefa.PENDENTE,
        )
        t_futura = Tarefa.objects.create(
            user=self.user_a,
            titulo="Futura",
            prazo=self.hoje + timedelta(days=5),
            status=StatusTarefa.PENDENTE,
        )
        filtros = AgendaFiltros(view=VIEW_HOJE)
        ids = set(tarefas_para_agenda(self.org, filtros).values_list("pk", flat=True))
        self.assertIn(t_hoje.pk, ids)
        self.assertIn(t_atrasada.pk, ids)
        self.assertNotIn(t_futura.pk, ids)

    def test_view_semana_filtra_compromissos_da_semana(self):
        ref = self.hoje
        ini = inicio_semana(ref)
        c_semana = Compromisso.objects.create(
            user=self.user_a,
            titulo="Na semana",
            data_hora=_dt_no_dia(ini + timedelta(days=2)),
        )
        c_fora = Compromisso.objects.create(
            user=self.user_a,
            titulo="Fora",
            data_hora=_dt_no_dia(fim_semana(ref) + timedelta(days=3)),
        )
        filtros = AgendaFiltros(view=VIEW_SEMANA, data=ref)
        ids = set(compromissos_para_agenda(self.org, filtros).values_list("pk", flat=True))
        self.assertIn(c_semana.pk, ids)
        self.assertNotIn(c_fora.pk, ids)

    def test_view_mes_filtra_compromissos_do_mes(self):
        ref = self.hoje.replace(day=15)
        c_mes = Compromisso.objects.create(
            user=self.user_a,
            titulo="No mês",
            data_hora=_dt_no_dia(ref),
        )
        c_fora = Compromisso.objects.create(
            user=self.user_a,
            titulo="Outro mês",
            data_hora=_dt_no_dia(ref + timedelta(days=40)),
        )
        filtros = AgendaFiltros(view=VIEW_MES, data=ref)
        ids = set(compromissos_para_agenda(self.org, filtros).values_list("pk", flat=True))
        self.assertIn(c_mes.pk, ids)
        self.assertNotIn(c_fora.pk, ids)

    def test_montar_semana_agrupa_por_dia(self):
        ref = self.hoje
        ini = inicio_semana(ref)
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Seg",
            data_hora=_dt_no_dia(ini),
        )
        filtros = AgendaFiltros(view=VIEW_SEMANA, data=ref)
        qs = compromissos_para_agenda(self.org, filtros)
        dias, atrasadas = montar_semana(qs, [], filtros)
        self.assertEqual(len(dias), 7)
        self.assertEqual(len(dias[0].compromissos), 1)
        self.assertEqual(dias[0].compromissos[0].pk, c.pk)
        self.assertEqual(atrasadas, [])

    def test_montar_grade_mes_tem_seis_semanas(self):
        ref = self.hoje.replace(day=10)
        filtros = AgendaFiltros(view=VIEW_MES, data=ref)
        grade = montar_grade_mes([], [], filtros)
        self.assertEqual(len(grade), 6)
        self.assertEqual(len(grade[0]), 7)

    def test_kpis_contagem_basica(self):
        ref = self.hoje
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Hoje",
            data_hora=_dt_no_dia(ref),
        )
        Tarefa.objects.create(
            user=self.user_a,
            titulo="Atrasada",
            prazo=ref - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
            prioridade=Prioridade.URGENTE,
        )
        Tarefa.objects.create(
            user=self.user_a,
            titulo="Feita",
            status=StatusTarefa.CONCLUIDA,
        )
        kpis = calcular_kpis(self.org, ref)
        self.assertEqual(kpis.compromissos_hoje, 1)
        self.assertEqual(kpis.tarefas_atrasadas, 1)
        self.assertGreaterEqual(kpis.prazos_proximos, 0)

    def test_kpis_isolados_por_usuario(self):
        ref = self.hoje
        user_b = User.objects.create_user(username="adv_b2", password="senha123")
        Compromisso.objects.create(
            user=user_b,
            titulo="Outro",
            data_hora=_dt_no_dia(ref),
        )
        kpis = calcular_kpis(self.org, ref)
        self.assertEqual(kpis.compromissos_hoje, 0)

    def test_itens_atencao_inclui_tarefa_atrasada(self):
        ref = self.hoje
        t = Tarefa.objects.create(
            user=self.user_a,
            titulo="Petição atrasada",
            prazo=ref - timedelta(days=3),
            status=StatusTarefa.PENDENTE,
        )
        items = itens_atencao(self.org, ref)
        self.assertTrue(any(i.item_id == t.pk for i in items))
        self.assertTrue(any("Atrasada" in i.motivo for i in items))


class AgendaModelsTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a,
            nome="João",
            email="joao@test.com",
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b,
            nome="Maria",
            email="maria@test.com",
        )
        self.client = Client()

    def test_compromisso_defaults(self):
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião",
            data_hora=timezone.now(),
        )
        self.assertEqual(c.tipo, TipoCompromisso.OUTRO)
        self.assertEqual(c.status, StatusCompromisso.AGENDADO)

    def test_cliente_outro_tenant_invalido(self):
        c = Compromisso(
            user=self.user_a,
            organization=self.org,
            titulo="X",
            data_hora=timezone.now(),
            cliente=self.cliente_b,
        )
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_tarefa_atrasada(self):
        t = Tarefa.objects.create(
            user=self.user_a,
            titulo="Prazo",
            prazo=timezone.localdate() - timedelta(days=2),
            status=StatusTarefa.PENDENTE,
        )
        self.assertTrue(t.atrasada)
        self.assertEqual(t.dias_atraso, 2)

    def test_tarefa_concluida_nao_atrasada(self):
        t = Tarefa.objects.create(
            user=self.user_a,
            titulo="Feita",
            prazo=timezone.localdate() - timedelta(days=1),
            status=StatusTarefa.CONCLUIDA,
        )
        self.assertFalse(t.atrasada)

    def test_compromisso_cancelar_preserva_registro(self):
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Audiência",
            data_hora=timezone.now(),
        )
        c.cancelar()
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCompromisso.CANCELADO)
        self.assertIsNotNone(c.cancelado_em)
        self.assertTrue(Compromisso.objects.filter(pk=c.pk).exists())

    def test_prazo_oficial_e_interno(self):
        oficial = timezone.localdate() + timedelta(days=10)
        interno = timezone.localdate() + timedelta(days=8)
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Prazo recurso",
            tipo=TipoCompromisso.PRAZO,
            data_hora=timezone.now(),
            prazo_oficial=oficial,
            prazo_interno=interno,
        )
        self.assertEqual(c.prazo_oficial, oficial)
        self.assertEqual(c.prazo_interno, interno)

    def test_toggle_status_sincroniza_concluida(self):
        t = Tarefa.objects.create(user=self.user_a, titulo="T")
        t.status = StatusTarefa.CONCLUIDA
        t.save()
        t.refresh_from_db()
        self.assertTrue(t.concluida)

    def test_agenda_pagina_tem_botoes_modal(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"))
        self.assertContains(response, "+ Novo compromisso")
        self.assertContains(response, "+ Nova tarefa")
        self.assertContains(response, 'id="modal-compromisso"')
        self.assertContains(response, 'id="modal-tarefa"')
        self.assertNotContains(response, '<h2 class="text-lg font-semibold text-zinc-100">Novo compromisso</h2>')

    def test_erro_compromisso_reabre_modal(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.post(
            reverse("agenda"),
            {"action": "criar_compromisso", "titulo": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="modal-compromisso"')
        self.assertContains(response, "abrirModalCompromisso()")

    def test_criar_compromisso_com_tipo_e_prioridade(self):
        self.client.login(username="adv_a", password="senha123")
        data = timezone.localdate()
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Audiência teste",
                "tipo": TipoCompromisso.AUDIENCIA,
                "status": StatusCompromisso.CONFIRMADO,
                "prioridade": Prioridade.ALTA,
                "data": data.isoformat(),
                "hora_inicio": "14:00",
                "hora_fim": "15:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertRedirects(response, reverse("agenda"))
        c = Compromisso.objects.get(titulo="Audiência teste")
        self.assertEqual(c.tipo, TipoCompromisso.AUDIENCIA)
        self.assertEqual(c.status, StatusCompromisso.CONFIRMADO)
        self.assertEqual(c.prioridade, Prioridade.ALTA)
        self.assertEqual(c.responsavel, self.user_a)

    def test_criar_tarefa_com_status_e_prioridade(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_tarefa",
                "titulo": "Revisar petição",
                "status": StatusTarefa.EM_ANDAMENTO,
                "prioridade": Prioridade.URGENTE,
                "prazo": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertRedirects(response, reverse("agenda"))
        t = Tarefa.objects.get(titulo="Revisar petição")
        self.assertEqual(t.status, StatusTarefa.EM_ANDAMENTO)
        self.assertEqual(t.prioridade, Prioridade.URGENTE)

    def test_listagem_exibe_badges(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião",
            tipo=TipoCompromisso.REUNIAO,
            status=StatusCompromisso.AGENDADO,
            prioridade=Prioridade.ALTA,
            data_hora=timezone.now(),
            responsavel=self.user_a,
        )
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"))
        self.assertContains(response, "Reunião")
        self.assertContains(response, "Alta")

    def test_criar_compromisso_com_cliente_processo_e_participante(self):
        self.client.login(username="adv_a", password="senha123")
        data = timezone.localdate()
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Audiência c/ cliente",
                "tipo": TipoCompromisso.AUDIENCIA,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "cliente": self.cliente_a.pk,
                "processo_referencia": "1234567-89.2024.8.26.0100",
                "data": data.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
                "participantes": [self.user_a.pk],
            },
        )
        self.assertRedirects(response, reverse("agenda"))
        c = Compromisso.objects.get(titulo="Audiência c/ cliente")
        self.assertEqual(c.cliente, self.cliente_a)
        self.assertEqual(c.processo_referencia, "1234567-89.2024.8.26.0100")
        self.assertEqual(c.participantes.count(), 1)
        self.assertEqual(c.participantes.first().usuario, self.user_a)

    def test_criar_tarefa_com_cliente_e_processo(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_tarefa",
                "titulo": "Protocolar recurso",
                "status": StatusTarefa.PENDENTE,
                "prioridade": Prioridade.NORMAL,
                "cliente": self.cliente_a.pk,
                "processo_referencia": "9999999-99.2024.8.26.0100",
                "prazo": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertRedirects(response, reverse("agenda"))
        t = Tarefa.objects.get(titulo="Protocolar recurso")
        self.assertEqual(t.cliente, self.cliente_a)
        self.assertEqual(t.processo_referencia, "9999999-99.2024.8.26.0100")

    def test_form_rejeita_cliente_de_outro_usuario(self):
        self.client.login(username="adv_a", password="senha123")
        data = timezone.localdate()
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Tentativa inválida",
                "tipo": TipoCompromisso.OUTRO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "cliente": self.cliente_b.pk,
                "processo_referencia": "",
                "data": data.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Compromisso.objects.filter(titulo="Tentativa inválida").exists())

    def test_listagem_exibe_cliente_e_processo(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Consulta",
            data_hora=timezone.now(),
            cliente=self.cliente_a,
            processo_referencia="1111111-11.2024.8.26.0100",
        )
        CompromissoParticipante.objects.create(
            compromisso=Compromisso.objects.get(titulo="Consulta"),
            usuario=self.user_a,
        )
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"))
        self.assertContains(response, "João")
        self.assertContains(response, "1111111-11.2024.8.26.0100")
        self.assertContains(response, "adv_a")

    def test_subnav_hoje_e_lista(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"))
        self.assertContains(response, ">Hoje<")
        self.assertContains(response, ">Lista<")
        self.assertContains(response, ">Semana<")
        self.assertContains(response, ">Mês<")
        self.assertContains(response, "Timeline do dia")

    def test_view_semana_renderiza_grade(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"), {"view": "semana"})
        self.assertContains(response, "← Anterior")
        self.assertContains(response, "Próximo →")

    def test_view_mes_renderiza_calendario(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"), {"view": "mes"})
        self.assertContains(response, "Ir para mês atual")
        self.assertContains(response, ">Seg<")
        self.assertContains(response, "grid-cols-7")

    def test_pagina_exibe_kpis_e_atencao(self):
        ref = timezone.localdate()
        Tarefa.objects.create(
            user=self.user_a,
            titulo="Urgente atrasada",
            prazo=ref - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
            prioridade=Prioridade.URGENTE,
        )
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"))
        self.assertContains(response, "Hoje")
        self.assertContains(response, "Prazos próximos")
        self.assertContains(response, "Tarefas atrasadas")
        self.assertContains(response, "Audiências na semana")
        self.assertContains(response, "Atenção")
        self.assertContains(response, "Urgente atrasada")

    def test_criar_compromisso_prazo_via_form(self):
        self.client.login(username="adv_a", password="senha123")
        data = timezone.localdate()
        oficial = data + timedelta(days=10)
        interno = data + timedelta(days=8)
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Recurso especial",
                "tipo": TipoCompromisso.PRAZO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.ALTA,
                "cliente": "",
                "processo_referencia": "",
                "prazo_oficial": oficial.isoformat(),
                "prazo_interno": interno.isoformat(),
                "data": data.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "09:30",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertRedirects(response, reverse("agenda"))
        c = Compromisso.objects.get(titulo="Recurso especial")
        self.assertEqual(c.prazo_oficial, oficial)
        self.assertEqual(c.prazo_interno, interno)

    def test_criar_audiencia_com_metadados(self):
        self.client.login(username="adv_a", password="senha123")
        data = timezone.localdate()
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Audiência de instrução",
                "tipo": TipoCompromisso.AUDIENCIA,
                "status": StatusCompromisso.CONFIRMADO,
                "prioridade": Prioridade.NORMAL,
                "modalidade_audiencia": "online",
                "local_audiencia": "Zoom",
                "link_audiencia": "https://meet.example.com/abc",
                "vara_audiencia": "2ª Vara",
                "data": data.isoformat(),
                "hora_inicio": "14:00",
                "hora_fim": "15:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertRedirects(response, reverse("agenda"))
        c = Compromisso.objects.get(titulo="Audiência de instrução")
        self.assertEqual(c.metadados["modalidade"], "online")
        self.assertEqual(c.metadados["link"], "https://meet.example.com/abc")

    def test_prazo_interno_posterior_oficial_rejeitado(self):
        self.client.login(username="adv_a", password="senha123")
        data = timezone.localdate()
        response = self.client.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Prazo inválido",
                "tipo": TipoCompromisso.PRAZO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "prazo_oficial": (data + timedelta(days=5)).isoformat(),
                "prazo_interno": (data + timedelta(days=8)).isoformat(),
                "data": data.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Compromisso.objects.filter(titulo="Prazo inválido").exists())

    def test_modal_compromisso_tem_campos_dinamicos(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"))
        self.assertContains(response, 'id="campos-prazo"')
        self.assertContains(response, 'id="campos-audiencia"')
        self.assertContains(response, "atualizarCamposCompromisso")

    def test_view_lista_renderiza_tabela(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("agenda"), {"view": "lista"})
        self.assertContains(response, "<table")
        self.assertNotContains(response, "Compromissos de hoje")

    def test_filtro_tipo_na_listagem(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Audiência",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=timezone.now(),
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião",
            tipo=TipoCompromisso.REUNIAO,
            data_hora=timezone.now(),
        )
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(
            reverse("agenda"),
            {"view": "lista", "tipo": TipoCompromisso.AUDIENCIA},
        )
        self.assertContains(response, "Audiência")
        self.assertNotContains(
            response,
            '<td class="py-3 pr-4 font-medium text-zinc-100">Reunião</td>',
        )


class ClienteAgendaFase9Tests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.org_a = Organization.objects.create(name="Org Agenda A")
        self.org_b = Organization.objects.create(name="Org Agenda B")
        Membership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        self.cliente_a = Cliente.objects.create(
            user=self.user_a,
            nome="Maria",
            email="maria@test.com",
            status="em_prospeccao",
            fase_funil="aguardando_decisao",
            organization=self.org_a,
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b,
            nome="Outro",
            email="outro@test.com",
            organization=self.org_b,
        )
        self.http = Client()

    def test_ficha_cliente_exibe_bloco_agenda(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião inicial",
            cliente=self.cliente_a,
            data_hora=timezone.now() + timedelta(hours=2),
        )
        Tarefa.objects.create(
            user=self.user_a,
            titulo="Enviar proposta",
            cliente=self.cliente_a,
            prazo=timezone.localdate(),
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agenda")
        self.assertContains(response, "Reunião inicial")
        self.assertContains(response, "Enviar proposta")
        self.assertContains(response, "Ver na agenda")

    def test_ficha_cliente_bloqueia_outro_usuario(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_b.pk}))
        self.assertEqual(response.status_code, 404)

    def test_ficha_cliente_sugestao_crm_prospeccao(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertContains(response, "Agendar follow-up comercial")
        self.assertContains(response, "Vincular agenda ao cliente")

    def test_ficha_cliente_bloco_financeiro(self):
        from decimal import Decimal

        from financeiro.models import Cobranca

        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Honorários",
            valor_original=Decimal("1500.00"),
            data_vencimento=timezone.localdate() + timedelta(days=10),
        )
        from financeiro.tests_helpers import grant_finance_permissions

        grant_finance_permissions(self.user_a, "view_cobrancas")
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertContains(response, "Financeiro")
        self.assertContains(response, "A receber")
        self.assertContains(response, "Honorários")
        self.assertContains(response, reverse("financeiro_cobranca_listar"))

    def test_agenda_preseleciona_cliente_via_querystring(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(
            reverse("agenda"),
            {"cliente": self.cliente_a.pk, "modal": "compromisso", "view": "lista"},
        )
        self.assertContains(
            response,
            f'<option value="{self.cliente_a.pk}" selected',
        )

    def test_links_criar_na_ficha_apontam_para_agenda(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertContains(response, f"cliente={self.cliente_a.pk}")
        self.assertContains(response, "modal=compromisso")
        self.assertContains(response, "modal=tarefa")

    def test_nao_lista_compromisso_passado_na_ficha(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião antiga",
            cliente=self.cliente_a,
            data_hora=timezone.now() - timedelta(days=2),
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião futura",
            cliente=self.cliente_a,
            data_hora=timezone.now() + timedelta(days=1),
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        html = response.content.decode()
        self.assertContains(response, "Reunião futura")
        bloco_agenda = html.split("Linha do tempo")[0]
        self.assertNotIn("Reunião antiga", bloco_agenda)
        self.assertIn("Reunião antiga", html.split("Linha do tempo", 1)[-1])

    def test_sugestao_cobranca_vencida(self):
        from decimal import Decimal

        from financeiro.choices import StatusCobranca
        from financeiro.models import Cobranca

        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Parcela vencida",
            valor_original=Decimal("800.00"),
            data_vencimento=timezone.localdate() - timedelta(days=5),
            status=StatusCobranca.OVERDUE,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertContains(response, "Registrar follow-up de cobrança")
        self.assertContains(response, "modal=tarefa")

    def test_hook_financeiro_exibido_no_bloco_agenda(self):
        from decimal import Decimal

        from financeiro.choices import StatusCobranca
        from financeiro.models import Cobranca

        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Honorários vencidos",
            valor_original=Decimal("1200.00"),
            data_vencimento=timezone.localdate() - timedelta(days=3),
            status=StatusCobranca.OVERDUE,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertContains(response, "Cobranças vencidas")
        self.assertContains(response, "Tarefa de cobrança")

    def test_agenda_preseleciona_tipo_followup(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(
            reverse("agenda"),
            {
                "cliente": self.cliente_a.pk,
                "modal": "compromisso",
                "tipo": "followup_comercial",
                "view": "lista",
            },
        )
        self.assertContains(response, 'value="followup_comercial" selected')

    def test_item_agenda_na_ficha_tem_link_abrir(self):
        comp = Compromisso.objects.create(
            user=self.user_a,
            titulo="Audiência cliente",
            cliente=self.cliente_a,
            data_hora=timezone.now() + timedelta(hours=2),
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": self.cliente_a.pk}))
        self.assertContains(response, f"compromisso_id={comp.pk}")
        self.assertContains(response, "Abrir")


class RecorrenciaLembreteFase10Tests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_deslocar_recorrencia_semanal(self):
        from usuarios.services.compromisso_recorrencia import deslocar_data_recorrencia

        base = _dt_no_dia(self.hoje, hora=9)
        proximo = deslocar_data_recorrencia(base, "semanal")
        self.assertEqual((proximo - base).days, 7)

    def test_gerar_ocorrencias_serie(self):
        from usuarios.choices import Recorrencia
        from usuarios.services.compromisso_recorrencia import gerar_ocorrencias_serie

        raiz = Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião semanal",
            data_hora=_dt_no_dia(self.hoje, hora=10),
            recorrencia=Recorrencia.SEMANAL,
        )
        criados = gerar_ocorrencias_serie(raiz, quantidade=3)
        self.assertEqual(len(criados), 3)
        self.assertEqual(
            Compromisso.objects.filter(user=self.user_a, titulo="Reunião semanal").count(),
            4,
        )
        self.assertEqual(criados[0].metadados.get("serie_raiz_id"), raiz.pk)

    def test_disparar_lembrete_cria_notificacao(self):
        from usuarios.models import AgendaLembrete
        from usuarios.tasks import disparar_lembrete_compromisso

        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Audiência",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1), hora=14),
            lembrete_minutos=60,
        )
        disparar_lembrete_compromisso(c.pk)
        self.assertTrue(
            AgendaLembrete.objects.filter(user=self.user_a, compromisso=c).exists()
        )

    def test_sincronizar_lembrete_cria_schedule(self):
        from django_q.models import Schedule

        from usuarios.services.compromisso_lembrete import sincronizar_lembrete_compromisso

        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Com lembrete",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=2), hora=11),
            lembrete_minutos=1440,
        )
        sincronizar_lembrete_compromisso(c)
        self.assertTrue(
            Schedule.objects.filter(name=f"agenda-lembrete-{c.pk}").exists()
        )

    def test_criar_compromisso_recorrente_via_post(self):
        self.http.login(username="adv_a", password="senha123")
        data = self.hoje + timedelta(days=5)
        response = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Status semanal",
                "tipo": TipoCompromisso.REUNIAO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "recorrencia": "semanal",
                "lembrete_minutos": "60",
                "data": data.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Compromisso.objects.filter(user=self.user_a, titulo="Status semanal").count(),
            4,
        )

    def test_agenda_exibe_lembretes_pendentes(self):
        from usuarios.models import AgendaLembrete

        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Prazo importante",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
        )
        AgendaLembrete.objects.create(
            user=self.user_a,
            compromisso=c,
            titulo=c.titulo,
            mensagem="Em breve",
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("agenda"))
        self.assertContains(response, "Lembretes")
        self.assertContains(response, "Prazo importante")

    def test_cancelar_compromisso_remove_schedule(self):
        from django_q.models import Schedule

        from usuarios.services.compromisso_lembrete import sincronizar_lembrete_compromisso

        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Cancelável",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=3), hora=15),
            lembrete_minutos=60,
        )
        sincronizar_lembrete_compromisso(c)
        self.http.login(username="adv_a", password="senha123")
        self.http.post(
            reverse("agenda"),
            {
                "action": "excluir_compromisso",
                "compromisso_id": c.pk,
            },
        )
        self.assertFalse(
            Schedule.objects.filter(name=f"agenda-lembrete-{c.pk}").exists()
        )

    def test_editar_compromisso_nao_duplica_serie(self):
        from usuarios.choices import Recorrencia

        raiz = Compromisso.objects.create(
            user=self.user_a,
            titulo="Status semanal",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=5), hora=9),
            recorrencia=Recorrencia.SEMANAL,
        )
        from usuarios.services.compromisso_recorrencia import gerar_ocorrencias_serie

        gerar_ocorrencias_serie(raiz, quantidade=3)
        total_antes = Compromisso.objects.filter(user=self.user_a).count()
        self.http.login(username="adv_a", password="senha123")
        self.http.post(
            reverse("agenda"),
            {
                "action": "editar_compromisso",
                "compromisso_id": raiz.pk,
                "titulo": "Status semanal atualizado",
                "tipo": TipoCompromisso.REUNIAO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "recorrencia": "semanal",
                "data": (self.hoje + timedelta(days=5)).isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertEqual(
            Compromisso.objects.filter(user=self.user_a).count(),
            total_antes,
        )

    def test_editar_compromisso_reagenda_lembrete(self):
        from django_q.models import Schedule

        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Reagendar",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=4), hora=10),
            lembrete_minutos=60,
        )
        from usuarios.services.compromisso_lembrete import sincronizar_lembrete_compromisso

        sincronizar_lembrete_compromisso(c)
        nova_data = self.hoje + timedelta(days=6)
        self.http.login(username="adv_a", password="senha123")
        self.http.post(
            reverse("agenda"),
            {
                "action": "editar_compromisso",
                "compromisso_id": c.pk,
                "titulo": "Reagendar",
                "tipo": TipoCompromisso.REUNIAO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "lembrete_minutos": "60",
                "data": nova_data.isoformat(),
                "hora_inicio": "14:00",
                "hora_fim": "15:00",
                "descricao": "",
                "responsavel": self.user_a.pk,
            },
        )
        schedule = Schedule.objects.get(name=f"agenda-lembrete-{c.pk}")
        esperado = _dt_no_dia(nova_data, hora=14) - timedelta(minutes=60)
        self.assertEqual(
            schedule.next_run.replace(second=0, microsecond=0),
            esperado.replace(second=0, microsecond=0),
        )

    def test_manter_series_recorrentes_gera_futuros(self):
        from usuarios.choices import Recorrencia
        from usuarios.services.compromisso_recorrencia import manter_series_recorrentes

        Compromisso.objects.create(
            user=self.user_a,
            titulo="Daily standup",
            data_hora=_dt_no_dia(self.hoje - timedelta(days=14), hora=9),
            recorrencia=Recorrencia.SEMANAL,
        )
        agora = timezone.now()
        self.assertLess(
            Compromisso.objects.filter(
                user=self.user_a, data_hora__gt=agora
            ).count(),
            2,
        )
        gerados = manter_series_recorrentes()
        self.assertGreater(gerados, 0)
        self.assertGreaterEqual(
            Compromisso.objects.filter(
                user=self.user_a, data_hora__gt=agora
            ).count(),
            2,
        )

    def test_deslocar_recorrencia_mensal(self):
        from usuarios.services.compromisso_recorrencia import deslocar_data_recorrencia

        base = _dt_no_dia(self.hoje.replace(day=15), hora=9)
        proximo = deslocar_data_recorrencia(base, "mensal")
        self.assertEqual(proximo.month, (base.month % 12) + 1 if base.month < 12 else 1)
        self.assertEqual(proximo.day, 15)


class AgendaIaFase11Tests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_resumo_padrao_monta_bullets(self):
        from usuarios.services.agenda_ia import montar_contexto_resumo_dia, gerar_resumo_padrao

        Compromisso.objects.create(
            user=self.user_a,
            titulo="Audiência TRT",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(self.hoje, hora=14),
        )
        Tarefa.objects.create(
            user=self.user_a,
            titulo="Petição atrasada",
            prazo=self.hoje - timedelta(days=2),
        )
        ctx = montar_contexto_resumo_dia(self.org, self.hoje)
        resumo = gerar_resumo_padrao(ctx)
        self.assertEqual(resumo.origem, "padrao")
        self.assertIn("compromisso agendado", resumo.texto)
        self.assertIn("atraso", resumo.texto.lower())

    def test_pagina_agenda_exibe_assistente_dia(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("agenda"))
        self.assertContains(response, "Assistente do dia")
        self.assertContains(response, "Hoje (")

    def test_limpar_resumo_ia_volta_ao_padrao(self):
        self.http.login(username="adv_a", password="senha123")
        chave = f"agenda_resumo_ia_{self.user_a.pk}_{self.hoje.isoformat()}"
        session = self.http.session
        session[chave] = {"texto": "Resumo IA customizado", "destaques": []}
        session.save()
        self.http.post(
            reverse("agenda"),
            {"action": "limpar_resumo_dia_ia", "view": "hoje"},
        )
        response = self.http.get(reverse("agenda"))
        self.assertNotContains(response, "Resumo IA customizado")
        self.assertContains(response, "Visão rápida")

    def test_gerar_resumo_ia_sem_chave_retorna_erro(self):
        import os
        from unittest.mock import patch

        from usuarios.services.agenda_ia import AgendaIaError, gerar_resumo_ia, montar_contexto_resumo_dia

        ctx = montar_contexto_resumo_dia(self.org, self.hoje)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(AgendaIaError):
                gerar_resumo_ia(ctx)

    def test_post_gerar_resumo_ia_sem_chave_mostra_erro(self):
        import os
        from unittest.mock import patch

        self.http.login(username="adv_a", password="senha123")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            response = self.http.post(
                reverse("agenda"),
                {"action": "gerar_resumo_dia_ia", "view": "hoje"},
                follow=True,
            )
        self.assertContains(response, "OPENAI_API_KEY")


class AgendaRbacFase12Tests(_AgendaOrgMixin, TestCase):
    GRUPO_RESTRITO = "Assistente — sem agenda"
    GRUPO_COMPLETO = "Agenda — acesso completo"

    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

        self.user_a = User.objects.create_user(username="adv_a12", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b12", password="senha123")
        self.user_restrito = User.objects.create_user(
            username="assist12", password="senha123"
        )
        self.http = Client()
        self.hoje = timezone.localdate()

        ct = ContentType.objects.get(app_label="usuarios", model="compromisso")
        perms = Permission.objects.filter(
            content_type=ct,
            codename__in=[
                "view_agenda",
                "create_agenda",
                "edit_agenda",
                "cancel_agenda",
                "view_audit_agenda",
            ],
        )
        self.grupo_completo, _ = Group.objects.get_or_create(name=self.GRUPO_COMPLETO)
        self.grupo_completo.permissions.set(perms)
        self.grupo_restrito, _ = Group.objects.get_or_create(name=self.GRUPO_RESTRITO)
        self.grupo_restrito.permissions.clear()
        self.user_restrito.groups.add(self.grupo_restrito)

    def test_usuario_sem_grupo_sem_perm_fail_closed(self):
        sem_perm = User.objects.create_user(
            username="agenda_sem_perm_fo", password="senha123"
        )
        Membership.objects.get_or_create(
            user=sem_perm,
            organization=self.org,
            defaults={
                "role": Membership.Role.MEMBER,
                "status": Membership.Status.ACTIVE,
            },
        )
        self.assertFalse(sem_perm.groups.exists())
        self.assertFalse(sem_perm.user_permissions.exists())
        self.http.login(username="agenda_sem_perm_fo", password="senha123")
        response = self.http.get(reverse("agenda"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("clientes"))

    def test_usuario_grupo_sem_perm_bloqueado(self):
        self.http.login(username="assist12", password="senha123")
        response = self.http.get(reverse("agenda"))
        self.assertEqual(response.status_code, 302)

    def test_usuario_grupo_com_perm_acessa(self):
        from usuarios.tests_helpers import grant_agenda_permissions

        self.user_restrito.groups.remove(self.grupo_restrito)
        self.user_restrito.groups.add(self.grupo_completo)
        grant_agenda_permissions(self.user_restrito)
        Membership.objects.get_or_create(
            user=self.user_restrito,
            organization=self.org,
            defaults={
                "role": Membership.Role.MEMBER,
                "status": Membership.Status.ACTIVE,
            },
        )
        self.user_restrito = User.objects.get(pk=self.user_restrito.pk)
        self.http.force_login(self.user_restrito)
        response = self.http.get(reverse("agenda"))
        self.assertEqual(response.status_code, 200)

    def test_criar_bloqueado_sem_perm(self):
        self.http.login(username="assist12", password="senha123")
        response = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_tarefa",
                "titulo": "Tarefa proibida",
                "status": "pendente",
                "prioridade": "normal",
                "responsavel": self.user_restrito.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tarefa.objects.filter(titulo="Tarefa proibida").exists())

    def test_pagina_auditoria_renderiza(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Auditoria teste",
            data_hora=_dt_no_dia(self.hoje),
        )
        self.http.login(username="adv_a12", password="senha123")
        response = self.http.get(reverse("agenda_auditoria"))
        self.assertEqual(response.status_code, 200)

    def test_escopo_equipe_membros_grupo(self):
        from usuarios.choices import EscopoAgenda
        from usuarios.services.agenda_equipe import filtrar_compromissos_escopo, membros_agenda

        Membership.objects.get_or_create(
            user=self.user_b,
            organization=self.org,
            defaults={
                "role": Membership.Role.MEMBER,
                "status": Membership.Status.ACTIVE,
            },
        )
        self.assertGreaterEqual(membros_agenda(self.org).count(), 2)
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Do colega",
            data_hora=_dt_no_dia(self.hoje, hora=11),
            responsavel=self.user_b,
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Só meu",
            data_hora=_dt_no_dia(self.hoje, hora=15),
            responsavel=self.user_a,
        )
        qs = Compromisso.objects.filter(organization=self.org)
        equipe = filtrar_compromissos_escopo(
            qs, self.user_a, EscopoAgenda.EQUIPE, organization=self.org
        )
        self.assertEqual(equipe.count(), 2)
        minha = filtrar_compromissos_escopo(
            qs, self.user_a, EscopoAgenda.MINHA, organization=self.org
        )
        self.assertEqual(minha.count(), 1)


class AgendaFase1CamposTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fase1", password="senha123")
        self.cliente = Cliente.objects.create(
            user=self.user, nome="Maria", email="maria@test.com"
        )
        self.http = Client()
        self.http.login(username="fase1", password="senha123")
        self.hoje = timezone.localdate()

    def test_compromisso_consulta_campos_model(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Consulta inicial",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje),
            area_juridica="Direito Trabalhista",
            confirmacao_consulta="pendente",
            origem_lead="indicacao",
            cliente=self.cliente,
        )
        c.refresh_from_db()
        self.assertEqual(c.area_juridica, "Direito Trabalhista")
        self.assertEqual(c.confirmacao_consulta, "pendente")
        self.assertEqual(c.origem_lead, "indicacao")

    def test_criar_consulta_via_form_post(self):
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Consulta João",
                "tipo": TipoCompromisso.CONSULTA,
                "status": "agendado",
                "prioridade": "normal",
                "cliente": self.cliente.pk,
                "area_juridica": "Direito Civil",
                "origem_lead": "site",
                "confirmacao_consulta": "confirmada",
                "data": self.hoje.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "responsavel": self.user.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        c = Compromisso.objects.get(titulo="Consulta João")
        self.assertEqual(c.area_juridica, "Direito Civil")
        self.assertEqual(c.origem_lead, "site")
        self.assertEqual(c.confirmacao_consulta, "confirmada")

    def test_audiencia_tribunal_e_observacoes_metadados(self):
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Audiência TRT",
                "tipo": TipoCompromisso.AUDIENCIA,
                "status": "agendado",
                "prioridade": "alta",
                "modalidade_audiencia": "presencial",
                "tribunal_audiencia": "TRT5",
                "vara_audiencia": "1ª Vara",
                "observacoes_audiencia": "Levar testemunhas",
                "data": self.hoje.isoformat(),
                "hora_inicio": "14:00",
                "hora_fim": "15:00",
                "responsavel": self.user.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        c = Compromisso.objects.get(titulo="Audiência TRT")
        self.assertEqual(c.metadados["tribunal"], "TRT5")
        self.assertEqual(c.metadados["observacoes"], "Levar testemunhas")

    def test_followup_comercial_metadados(self):
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Retorno proposta",
                "tipo": TipoCompromisso.FOLLOWUP_COMERCIAL,
                "status": "agendado",
                "prioridade": "normal",
                "cliente": self.cliente.pk,
                "followup_oportunidade": "Proposta honorários",
                "followup_observacao": "Cliente pediu retorno em 2 dias",
                "data": self.hoje.isoformat(),
                "hora_inicio": "11:00",
                "hora_fim": "11:30",
                "responsavel": self.user.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        c = Compromisso.objects.get(titulo="Retorno proposta")
        self.assertEqual(c.metadados["followup_oportunidade"], "Proposta honorários")
        self.assertEqual(
            c.metadados["followup_observacao"], "Cliente pediu retorno em 2 dias"
        )

    def test_metadados_preserva_cobranca_ao_salvar_audiencia(self):
        from usuarios.services.compromisso_metadados import aplicar_metadados_por_tipo

        meta = aplicar_metadados_por_tipo(
            {"cobranca_id": 42, "lembrete_cobranca_automatico": True},
            tipo=TipoCompromisso.AUDIENCIA,
            audiencia={"tribunal": "TJSP", "modalidade": "online"},
        )
        self.assertEqual(meta["cobranca_id"], 42)
        self.assertEqual(meta["tribunal"], "TJSP")

    def test_tipo_nao_consulta_limpa_campos_consulta(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Reunião",
            tipo=TipoCompromisso.REUNIAO,
            data_hora=_dt_no_dia(self.hoje),
            area_juridica="",
            confirmacao_consulta="",
            origem_lead="",
        )
        self.assertEqual(c.confirmacao_consulta, "")
        self.assertEqual(c.origem_lead, "")

    def test_modal_tem_campos_consulta_e_followup(self):
        resp = self.http.get(reverse("agenda"))
        html = resp.content.decode()
        self.assertIn('id="campos-consulta"', html)
        self.assertIn('id="campos-followup"', html)
        self.assertIn("compromisso_tribunal_audiencia", html)


class AgendaFase2EdicaoTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fase2", password="senha123")
        self.http = Client()
        self.http.login(username="fase2", password="senha123")
        self.hoje = timezone.localdate()

    def test_editar_compromisso_via_modal(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Original",
            data_hora=_dt_no_dia(self.hoje, hora=10),
            data_hora_fim=_dt_no_dia(self.hoje, hora=11),
        )
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "editar_compromisso",
                "compromisso_id": c.pk,
                "titulo": "Atualizado",
                "tipo": TipoCompromisso.REUNIAO,
                "status": "confirmado",
                "prioridade": "alta",
                "data": self.hoje.isoformat(),
                "hora_inicio": "15:00",
                "hora_fim": "16:00",
                "responsavel": self.user.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.titulo, "Atualizado")
        self.assertEqual(c.status, StatusCompromisso.CONFIRMADO)

    def test_editar_tarefa_via_modal(self):
        t = Tarefa.objects.create(
            user=self.user,
            titulo="Tarefa original",
            prazo=self.hoje,
        )
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "editar_tarefa",
                "tarefa_id": t.pk,
                "titulo": "Tarefa editada",
                "status": "em_andamento",
                "prioridade": "urgente",
                "responsavel": self.user.pk,
            },
        )
        self.assertEqual(resp.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.titulo, "Tarefa editada")
        self.assertEqual(t.status, StatusTarefa.EM_ANDAMENTO)

    def test_iniciar_tarefa(self):
        t = Tarefa.objects.create(user=self.user, titulo="Iniciar", prazo=self.hoje)
        self.http.post(
            reverse("agenda"),
            {"action": "iniciar_tarefa", "tarefa_id": t.pk},
        )
        t.refresh_from_db()
        self.assertEqual(t.status, StatusTarefa.EM_ANDAMENTO)

    def test_confirmar_compromisso(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Consulta",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje),
        )
        self.http.post(
            reverse("agenda"),
            {"action": "confirmar_compromisso", "compromisso_id": c.pk},
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCompromisso.CONFIRMADO)

    def test_get_abre_modal_edicao_compromisso(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Editável",
            data_hora=_dt_no_dia(self.hoje),
            data_hora_fim=_dt_no_dia(self.hoje, hora=11),
        )
        resp = self.http.get(
            reverse("agenda"),
            {"modal": "compromisso", "compromisso_id": c.pk},
        )
        html = resp.content.decode()
        self.assertIn("Editar compromisso", html)
        self.assertIn('value="editar_compromisso"', html)
        self.assertIn("Editável", html)

    def test_edicao_compromisso_registra_audit_prazo(self):
        from usuarios.models import AgendaAuditLog

        c = Compromisso.objects.create(
            user=self.user,
            titulo="Prazo",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje),
            data_hora_fim=_dt_no_dia(self.hoje, hora=23),
            prazo_oficial=self.hoje,
            prazo_interno=self.hoje,
        )
        novo_interno = self.hoje - timedelta(days=2)
        self.http.post(
            reverse("agenda"),
            {
                "action": "editar_compromisso",
                "compromisso_id": c.pk,
                "titulo": "Prazo",
                "tipo": TipoCompromisso.PRAZO,
                "status": "agendado",
                "prioridade": "normal",
                "prazo_oficial": self.hoje.isoformat(),
                "prazo_interno": novo_interno.isoformat(),
                "data": self.hoje.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "responsavel": self.user.pk,
            },
        )
        self.assertTrue(
            AgendaAuditLog.objects.filter(
                item_tipo="compromisso",
                item_id=c.pk,
                acao="prazo_alterado",
                campo="prazo_interno",
            ).exists()
        )


class AgendaFase3StatusTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fase3", password="senha123")
        self.http = Client()
        self.http.login(username="fase3", password="senha123")
        self.hoje = timezone.localdate()

    def test_confirmar_consulta_sincroniza_confirmacao(self):
        from usuarios.choices import StatusConfirmacaoConsulta

        c = Compromisso.objects.create(
            user=self.user,
            titulo="Consulta Maria",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
            confirmacao_consulta=StatusConfirmacaoConsulta.PENDENTE,
        )
        self.http.post(
            reverse("agenda"),
            {"action": "confirmar_compromisso", "compromisso_id": c.pk},
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCompromisso.CONFIRMADO)
        self.assertEqual(c.confirmacao_consulta, StatusConfirmacaoConsulta.CONFIRMADA)

    def test_nao_compareceu_consulta(self):
        from usuarios.choices import StatusConfirmacaoConsulta

        c = Compromisso.objects.create(
            user=self.user,
            titulo="Consulta João",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje),
        )
        self.http.post(
            reverse("agenda"),
            {"action": "nao_compareceu_compromisso", "compromisso_id": c.pk},
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCompromisso.NAO_COMPARECEU)
        self.assertEqual(c.confirmacao_consulta, StatusConfirmacaoConsulta.CANCELADA)

    def test_consulta_nova_recebe_confirmacao_pendente(self):
        from usuarios.choices import StatusConfirmacaoConsulta

        self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Nova consulta",
                "tipo": TipoCompromisso.CONSULTA,
                "status": "agendado",
                "prioridade": "normal",
                "data": self.hoje.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "responsavel": self.user.pk,
            },
        )
        c = Compromisso.objects.get(titulo="Nova consulta")
        self.assertEqual(c.confirmacao_consulta, StatusConfirmacaoConsulta.PENDENTE)

    def test_itens_atencao_consulta_sem_confirmacao(self):
        from usuarios.choices import StatusConfirmacaoConsulta

        c = Compromisso.objects.create(
            user=self.user,
            titulo="Consulta pendente",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
            confirmacao_consulta=StatusConfirmacaoConsulta.PENDENTE,
        )
        itens = itens_atencao(self.org, self.hoje)
        ids = [i.item_id for i in itens if i.item_tipo == "compromisso"]
        self.assertIn(c.pk, ids)

    def test_lista_exibe_badges_tipo_e_prioridade(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Audiência teste",
            tipo=TipoCompromisso.AUDIENCIA,
            prioridade=Prioridade.URGENTE,
            data_hora=_dt_no_dia(self.hoje),
        )
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        html = resp.content.decode()
        self.assertIn("Audiência", html)
        self.assertIn("Urgente", html)

    def test_filtro_status_tarefa_cancelada(self):
        Tarefa.objects.create(
            user=self.user,
            titulo="Cancelada",
            status=StatusTarefa.CANCELADA,
        )
        Tarefa.objects.create(
            user=self.user,
            titulo="Ativa",
            status=StatusTarefa.PENDENTE,
        )
        resp = self.http.get(reverse("agenda"), {"view": "lista", "status": "cancelada"})
        html = resp.content.decode()
        self.assertIn("Cancelada", html)
        self.assertNotIn(">Ativa<", html)


class AgendaFase4VinculosTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a4", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b4", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="Cliente A4", email="a4@test.com"
        )
        self.http = Client()
        self.http.login(username="adv_a4", password="senha123")
        self.hoje = timezone.localdate()

    def test_filtro_processo(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Compromisso Alpha",
            data_hora=_dt_no_dia(self.hoje),
            processo_referencia="0001111-11.2024.8.26.0100",
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Compromisso Beta",
            data_hora=_dt_no_dia(self.hoje),
            processo_referencia="9999999-99.2024.8.26.0100",
        )
        resp = self.http.get(
            reverse("agenda"),
            {"view": "lista", "processo": "0001111"},
        )
        html = resp.content.decode()
        self.assertIn("Compromisso Alpha", html)
        self.assertNotIn("Compromisso Beta", html)

    def test_processos_distintos_por_cliente(self):
        from usuarios.services.agenda_equipe import processos_distintos

        Compromisso.objects.create(
            user=self.user_a,
            titulo="C1",
            cliente=self.cliente_a,
            data_hora=_dt_no_dia(self.hoje),
            processo_referencia="1111111-11.2024.8.26.0100",
        )
        refs = processos_distintos(self.org, cliente_id=self.cliente_a.pk)
        self.assertIn("1111111-11.2024.8.26.0100", refs)

    def test_responsavel_outro_tenant_rejeitado(self):
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_compromisso",
                "titulo": "Inválido",
                "tipo": TipoCompromisso.REUNIAO,
                "status": "agendado",
                "prioridade": "normal",
                "data": self.hoje.isoformat(),
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "responsavel": self.user_b.pk,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Compromisso.objects.filter(titulo="Inválido").exists())

    def test_escopo_minha_agenda_filtra_por_responsavel(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Meu compromisso",
            data_hora=_dt_no_dia(self.hoje),
            responsavel=self.user_a,
        )
        resp = self.http.get(reverse("agenda"), {"view": "lista", "escopo": "minha"})
        self.assertContains(resp, "Meu compromisso")

    def test_pagina_tem_filtro_escopo_e_processo(self):
        resp = self.http.get(reverse("agenda"))
        html = resp.content.decode()
        self.assertIn('name="escopo"', html)
        self.assertIn('name="processo"', html)
        self.assertIn("Minha agenda", html)

    def test_cliente_outro_tenant_nao_vincula_tarefa(self):
        cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Cliente B", email="b@test.com"
        )
        resp = self.http.post(
            reverse("agenda"),
            {
                "action": "criar_tarefa",
                "titulo": "Tarefa inválida",
                "status": "pendente",
                "prioridade": "normal",
                "cliente": cliente_b.pk,
                "responsavel": self.user_a.pk,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Tarefa.objects.filter(titulo="Tarefa inválida").exists())


class AgendaFase5TimelineTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adv_f5", password="senha123")
        self.cliente = Cliente.objects.create(
            user=self.user, nome="Cliente F5", email="f5@test.com"
        )
        self.hoje = timezone.localdate()
        self.http = Client()
        self.http.login(username="adv_f5", password="senha123")

    def test_timeline_ordenacao_cronologica(self):
        c_manha = Compromisso.objects.create(
            user=self.user,
            titulo="Manhã",
            data_hora=_dt_no_dia(self.hoje, hora=9),
        )
        c_tarde = Compromisso.objects.create(
            user=self.user,
            titulo="Tarde",
            data_hora=_dt_no_dia(self.hoje, hora=14),
        )
        t_hoje = Tarefa.objects.create(
            user=self.user,
            titulo="Tarefa prazo",
            prazo=self.hoje,
            status=StatusTarefa.PENDENTE,
        )
        filtros = AgendaFiltros(view=VIEW_HOJE)
        compromissos = list(compromissos_para_agenda(self.org, filtros))
        tarefas = list(tarefas_para_agenda(self.org, filtros))
        timeline = montar_timeline_hoje(compromissos, tarefas, self.hoje)
        titulos = [
            (item.compromisso or item.tarefa).titulo
            for item in timeline.eventos
        ]
        self.assertEqual(titulos, [c_manha.titulo, c_tarde.titulo, t_hoje.titulo])

    def test_timeline_separa_atrasadas(self):
        Tarefa.objects.create(
            user=self.user,
            titulo="Atrasada",
            prazo=self.hoje - timedelta(days=2),
            status=StatusTarefa.PENDENTE,
        )
        Compromisso.objects.create(
            user=self.user,
            titulo="Hoje",
            data_hora=_dt_no_dia(self.hoje, hora=10),
        )
        filtros = AgendaFiltros(view=VIEW_HOJE)
        timeline = montar_timeline_hoje(
            list(compromissos_para_agenda(self.org, filtros)),
            list(tarefas_para_agenda(self.org, filtros)),
            self.hoje,
        )
        self.assertEqual(len(timeline.atrasadas), 1)
        self.assertEqual(timeline.atrasadas[0].tarefa.titulo, "Atrasada")
        self.assertEqual(len(timeline.eventos), 1)

    def test_pagina_hoje_exibe_timeline_unificada(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Consulta F5",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje, hora=11),
            cliente=self.cliente,
        )
        resp = self.http.get(reverse("agenda"))
        self.assertContains(resp, "Timeline do dia")
        self.assertContains(resp, "Consulta F5")
        self.assertNotContains(resp, "Compromissos de hoje")

    def test_lista_unificada_mistura_tipos(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Compromisso lista",
            data_hora=_dt_no_dia(self.hoje, hora=8),
        )
        Tarefa.objects.create(
            user=self.user,
            titulo="Tarefa lista",
            prazo=self.hoje + timedelta(days=1),
            status=StatusTarefa.PENDENTE,
        )
        filtros = AgendaFiltros(view=VIEW_LISTA)
        itens = montar_lista_unificada(
            list(compromissos_para_agenda(self.org, filtros)),
            list(tarefas_para_agenda(self.org, filtros)),
        )
        tipos = {item.item_tipo for item in itens}
        self.assertEqual(tipos, {"compromisso", "tarefa"})

    def test_pagina_lista_tabela_unificada(self):
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        html = resp.content.decode()
        self.assertIn("Lista unificada", html)
        self.assertEqual(html.count("<table"), 1)
        self.assertIn(">Horário<", html)
        self.assertNotIn("Compromissos de hoje", html)


class AgendaFase6SemanaMesTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adv_f6", password="senha123")
        self.hoje = timezone.localdate()
        self.http = Client()
        self.http.login(username="adv_f6", password="senha123")

    def test_resumo_celula_mes_conta_tipos(self):
        ref = self.hoje.replace(day=10)
        dia = ref
        Compromisso.objects.create(
            user=self.user,
            titulo="Audiência",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(dia, hora=14),
        )
        Compromisso.objects.create(
            user=self.user,
            titulo="Prazo",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(dia, hora=17),
        )
        Tarefa.objects.create(
            user=self.user,
            titulo="Tarefa mes",
            prazo=dia,
            status=StatusTarefa.PENDENTE,
        )
        filtros = AgendaFiltros(view=VIEW_MES, data=ref)
        grade = montar_grade_mes(
            list(compromissos_para_agenda(self.org, filtros)),
            list(tarefas_para_agenda(self.org, filtros)),
            filtros,
        )
        celula = next(
            c for semana in grade for c in semana if c.data == dia
        )
        self.assertEqual(celula.resumo.compromissos, 2)
        self.assertEqual(celula.resumo.audiencias, 1)
        self.assertEqual(celula.resumo.prazos, 1)
        self.assertEqual(celula.resumo.tarefas, 1)

    def test_resumo_semana_agrega_totais(self):
        ref = self.hoje
        ini = inicio_semana(ref)
        Compromisso.objects.create(
            user=self.user,
            titulo="Aud",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(ini, hora=10),
        )
        Tarefa.objects.create(
            user=self.user,
            titulo="Atrasada",
            prazo=ini - timedelta(days=3),
            status=StatusTarefa.PENDENTE,
        )
        filtros = AgendaFiltros(view=VIEW_SEMANA, data=ref)
        dias, atrasadas = montar_semana(
            list(compromissos_para_agenda(self.org, filtros)),
            list(tarefas_para_agenda(self.org, filtros)),
            filtros,
        )
        resumo = calcular_resumo_semana(dias, atrasadas)
        self.assertEqual(resumo.compromissos, 1)
        self.assertEqual(resumo.audiencias, 1)
        self.assertEqual(resumo.atrasadas, 1)

    def test_semana_ordenacao_por_horario(self):
        ref = self.hoje
        ini = inicio_semana(ref)
        tarde = Compromisso.objects.create(
            user=self.user,
            titulo="Tarde",
            data_hora=_dt_no_dia(ini, hora=15),
        )
        manha = Compromisso.objects.create(
            user=self.user,
            titulo="Manhã",
            data_hora=_dt_no_dia(ini, hora=9),
        )
        filtros = AgendaFiltros(view=VIEW_SEMANA, data=ref)
        dias, _ = montar_semana(
            list(compromissos_para_agenda(self.org, filtros)),
            [],
            filtros,
        )
        titulos = [c.titulo for c in dias[0].compromissos]
        self.assertEqual(titulos, [manha.titulo, tarde.titulo])

    def test_pagina_mes_exibe_contadores(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Evento",
            data_hora=_dt_no_dia(self.hoje, hora=11),
        )
        resp = self.http.get(reverse("agenda"), {"view": "mes"})
        html = resp.content.decode()
        self.assertIn("comp.", html)
        self.assertIn("Clique no dia", html)

    def test_pagina_semana_exibe_resumo(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Semana",
            data_hora=_dt_no_dia(self.hoje, hora=10),
        )
        resp = self.http.get(reverse("agenda"), {"view": "semana"})
        html = resp.content.decode()
        self.assertIn("compromisso", html)
        self.assertIn(self.hoje.strftime("%d/%m"), html)


class AgendaFase7KpisAtencaoTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adv_f7", password="senha123")
        self.hoje = timezone.localdate()
        self.http = Client()
        self.http.login(username="adv_f7", password="senha123")

    def test_kpis_spec_quatro_indicadores(self):
        ref = self.hoje
        ini = inicio_semana(ref)
        Compromisso.objects.create(
            user=self.user,
            titulo="Aud 1",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(ini + timedelta(days=1), hora=10),
        )
        Compromisso.objects.create(
            user=self.user,
            titulo="Aud 2",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(ini + timedelta(days=4), hora=14),
        )
        futuro = ref + timedelta(days=1)
        Compromisso.objects.create(
            user=self.user,
            titulo="Prazo prox",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(futuro, hora=17),
        )
        Tarefa.objects.create(
            user=self.user,
            titulo="Tarefa prox",
            prazo=futuro + timedelta(days=1),
            status=StatusTarefa.PENDENTE,
        )
        Tarefa.objects.create(
            user=self.user,
            titulo="Atrasada",
            prazo=ref - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
        )
        kpis = calcular_kpis(self.org, ref)
        self.assertEqual(kpis.audiencias_semana, 2)
        self.assertEqual(kpis.tarefas_atrasadas, 1)
        self.assertGreaterEqual(kpis.prazos_proximos, 2)

    def test_itens_atencao_prazo_interno_proximo(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Recurso",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje + timedelta(days=10)),
            prazo_interno=self.hoje + timedelta(days=1),
        )
        itens = itens_atencao(self.org, self.hoje)
        ids = [i.item_id for i in itens if i.motivo == "Prazo interno próximo"]
        self.assertIn(c.pk, ids)

    def test_itens_atencao_tem_url_e_acao(self):
        t = Tarefa.objects.create(
            user=self.user,
            titulo="Tarefa URL",
            prazo=self.hoje - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
        )
        itens = itens_atencao(self.org, self.hoje)
        item = next(i for i in itens if i.item_id == t.pk)
        self.assertIn(f"tarefa_id={t.pk}", item.url)
        self.assertEqual(item.acao, "Concluir")

    def test_pagina_kpis_quatro_cards(self):
        resp = self.http.get(reverse("agenda"))
        html = resp.content.decode()
        self.assertIn("Prazos próximos", html)
        self.assertIn("Audiências na semana", html)
        self.assertEqual(html.count("text-2xl font-semibold"), 4)

    def test_atencao_link_confirmar_consulta(self):
        from usuarios.choices import StatusConfirmacaoConsulta

        c = Compromisso.objects.create(
            user=self.user,
            titulo="Consulta F7",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
            confirmacao_consulta=StatusConfirmacaoConsulta.PENDENTE,
        )
        resp = self.http.get(reverse("agenda"))
        html = resp.content.decode()
        self.assertIn("Consulta F7", html)
        self.assertIn("Confirmar", html)
        self.assertIn(f"compromisso_id={c.pk}", html)


class AgendaFase8PrazosAudienciaTests(_AgendaOrgMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adv_f8", password="senha123")
        self.hoje = timezone.localdate()
        self.http = Client()
        self.http.login(username="adv_f8", password="senha123")

    def test_prazo_interno_vencido_property(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Prazo vencido",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje),
            prazo_interno=self.hoje - timedelta(days=1),
            prazo_oficial=self.hoje + timedelta(days=5),
        )
        self.assertTrue(c.prazo_interno_vencido)
        self.assertFalse(c.prazo_oficial_vencido)

    def test_model_rejeita_interno_posterior_oficial(self):
        c = Compromisso(
            user=self.user,
            titulo="Inválido",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje),
            prazo_interno=self.hoje + timedelta(days=10),
            prazo_oficial=self.hoje + timedelta(days=5),
        )
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_form_prazo_exige_oficial_ou_interno(self):
        from usuarios.forms import CompromissoForm

        form = CompromissoForm(
            user=self.user,
            organization=self.org,
            data={
                "titulo": "Sem prazo",
                "tipo": TipoCompromisso.PRAZO,
                "status": StatusCompromisso.AGENDADO,
                "prioridade": Prioridade.NORMAL,
                "data": self.hoje.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "responsavel": self.user.pk,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("prazo_oficial", form.errors)

    def test_itens_atencao_prazo_interno_vencido(self):
        c = Compromisso.objects.create(
            user=self.user,
            titulo="Interno vencido",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje),
            prazo_interno=self.hoje - timedelta(days=2),
            prazo_oficial=self.hoje + timedelta(days=3),
        )
        itens = itens_atencao(self.org, self.hoje)
        motivos = [i.motivo for i in itens if i.item_id == c.pk]
        self.assertTrue(any("interno vencido" in m.lower() for m in motivos))

    def test_pagina_exibe_badge_prazo(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Recurso urgente",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje, hora=11),
            prazo_interno=self.hoje,
            prazo_oficial=self.hoje + timedelta(days=5),
        )
        resp = self.http.get(reverse("agenda"))
        html = resp.content.decode()
        self.assertIn("Interno hoje", html)
        self.assertIn("Recurso urgente", html)

    def test_audiencia_exibe_tribunal_na_timeline(self):
        Compromisso.objects.create(
            user=self.user,
            titulo="Audiência TRT",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(self.hoje, hora=14),
            metadados={"tribunal": "TRT2", "modalidade": "presencial"},
        )
        resp = self.http.get(reverse("agenda"))
        self.assertContains(resp, "TRT2")
        self.assertContains(resp, "Presencial")


class AgendaSpecFase13Tests(_AgendaOrgMixin, TestCase):
    """
    Suite dos 18 testes de aceitação do módulo Agenda (spec §40).
    Cada método corresponde a um TESTE numerado do prompt original.
    """

    GRUPO_SEM_AGENDA = "Spec — sem agenda"
    GRUPO_COMPLETO = "Agenda — acesso completo"

    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

        self.user_a = User.objects.create_user(username="spec_ag_a", password="senha123")
        self.user_b = User.objects.create_user(username="spec_ag_b", password="senha123")
        self.user_restrito = User.objects.create_user(
            username="spec_ag_r", password="senha123"
        )
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João Spec", email="joao_spec@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Maria Spec", email="maria_spec@test.com"
        )
        self.http = Client()
        self.hoje = timezone.localdate()

        ct = ContentType.objects.get(app_label="usuarios", model="compromisso")
        perms = Permission.objects.filter(
            content_type=ct,
            codename__in=[
                "view_agenda",
                "create_agenda",
                "edit_agenda",
                "cancel_agenda",
                "view_audit_agenda",
            ],
        )
        self.grupo_completo, _ = Group.objects.get_or_create(name=self.GRUPO_COMPLETO)
        self.grupo_completo.permissions.set(perms)
        self.grupo_restrito, _ = Group.objects.get_or_create(name=self.GRUPO_SEM_AGENDA)
        self.grupo_restrito.permissions.clear()
        self.user_restrito.groups.add(self.grupo_restrito)

    def _post_compromisso(self, **extra):
        data = {
            "action": "criar_compromisso",
            "titulo": extra.pop("titulo", "Compromisso spec"),
            "tipo": extra.pop("tipo", TipoCompromisso.REUNIAO),
            "status": "agendado",
            "prioridade": "normal",
            "data": self.hoje.isoformat(),
            "hora_inicio": "10:00",
            "hora_fim": "11:00",
            "responsavel": self.user_a.pk,
        }
        data.update(extra)
        return self.http.post(reverse("agenda"), data)

    def _post_tarefa(self, **extra):
        data = {
            "action": "criar_tarefa",
            "titulo": extra.pop("titulo", "Tarefa spec"),
            "status": "pendente",
            "prioridade": "normal",
            "responsavel": self.user_a.pk,
        }
        data.update(extra)
        return self.http.post(reverse("agenda"), data)

    # TESTE 01 — Criar compromisso.
    def test_spec_01_criar_compromisso(self):
        self.http.login(username="spec_ag_a", password="senha123")
        resp = self._post_compromisso(titulo="Reunião spec 01")
        self.assertEqual(resp.status_code, 302)
        c = Compromisso.objects.get(titulo="Reunião spec 01")
        self.assertEqual(c.user_id, self.user_a.pk)
        self.assertEqual(c.tipo, TipoCompromisso.REUNIAO)

    # TESTE 02 — Criar tarefa.
    def test_spec_02_criar_tarefa(self):
        self.http.login(username="spec_ag_a", password="senha123")
        resp = self._post_tarefa(titulo="Tarefa spec 02", prazo=self.hoje.isoformat())
        self.assertEqual(resp.status_code, 302)
        t = Tarefa.objects.get(titulo="Tarefa spec 02")
        self.assertEqual(t.user_id, self.user_a.pk)
        self.assertEqual(t.status, StatusTarefa.PENDENTE)

    # TESTE 03 — Cliente de outro tenant não pode ser vinculado.
    def test_spec_03_cliente_outro_tenant_rejeitado(self):
        self.http.login(username="spec_ag_a", password="senha123")
        resp = self._post_tarefa(
            titulo="Tarefa cliente inválido",
            cliente=self.cliente_b.pk,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Tarefa.objects.filter(titulo="Tarefa cliente inválido").exists()
        )

    # TESTE 04 — Responsável de outro tenant não pode ser vinculado.
    def test_spec_04_responsavel_outro_tenant_rejeitado(self):
        self.http.login(username="spec_ag_a", password="senha123")
        resp = self._post_compromisso(
            titulo="Compromisso resp inválido",
            responsavel=self.user_b.pk,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Compromisso.objects.filter(titulo="Compromisso resp inválido").exists()
        )

    # TESTE 05 — Tenant A não vê agenda Tenant B.
    def test_spec_05_tenant_a_nao_ve_agenda_b(self):
        Compromisso.objects.create(
            user=self.user_b,
            titulo="Segredo tenant B",
            data_hora=_dt_no_dia(self.hoje),
        )
        self.http.login(username="spec_ag_a", password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertNotContains(resp, "Segredo tenant B")
        self.assertEqual(
            Compromisso.objects.filter(user=self.user_a).count(),
            0,
        )

    # TESTE 06 — Minha agenda mostra apenas eventos permitidos.
    def test_spec_06_minha_agenda_filtra_responsavel(self):
        self.http.login(username="spec_ag_a", password="senha123")
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Meu evento",
            data_hora=_dt_no_dia(self.hoje),
            responsavel=self.user_a,
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="De outro responsável",
            data_hora=_dt_no_dia(self.hoje, hora=15),
            responsavel=self.user_b,
        )
        resp = self.http.get(reverse("agenda"), {"view": "lista", "escopo": "minha"})
        self.assertContains(resp, "Meu evento")
        self.assertNotContains(resp, "De outro responsável")

    # TESTE 07 — Equipe respeita RBAC.
    def test_spec_07_equipe_respeita_rbac(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

        from usuarios.tests_helpers import grant_agenda_permissions

        ct = ContentType.objects.get(app_label="usuarios", model="compromisso")
        perms = Permission.objects.filter(
            content_type=ct,
            codename__in=[
                "view_agenda",
                "create_agenda",
                "edit_agenda",
                "cancel_agenda",
            ],
        )
        grupo = Group.objects.create(name="Escritório Spec 13")
        grupo.permissions.set(perms)
        self.user_a.groups.add(grupo)
        self.user_b.groups.add(grupo)
        grant_agenda_permissions(self.user_a)
        self.user_a = User.objects.get(pk=self.user_a.pk)
        Membership.objects.get_or_create(
            user=self.user_b,
            organization=self.org,
            defaults={
                "role": Membership.Role.MEMBER,
                "status": Membership.Status.ACTIVE,
            },
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Equipe visível",
            data_hora=_dt_no_dia(self.hoje),
            responsavel=self.user_b,
        )
        self.http.login(username="spec_ag_r", password="senha123")
        self.assertEqual(self.http.get(reverse("agenda")).status_code, 302)

        self.http.force_login(self.user_a)
        resp = self.http.get(
            reverse("agenda"), {"view": "lista", "escopo": "equipe"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Equipe visível")

    # TESTE 08 — Tarefa atrasada é identificada corretamente.
    def test_spec_08_tarefa_atrasada_identificada(self):
        t = Tarefa.objects.create(
            user=self.user_a,
            titulo="Atrasada spec",
            prazo=self.hoje - timedelta(days=2),
            status=StatusTarefa.PENDENTE,
        )
        self.assertTrue(t.atrasada)

    # TESTE 09 — Tarefa concluída deixa de aparecer como atrasada.
    def test_spec_09_tarefa_concluida_nao_atrasada(self):
        t = Tarefa.objects.create(
            user=self.user_a,
            titulo="Concluída spec",
            prazo=self.hoje - timedelta(days=3),
            status=StatusTarefa.CONCLUIDA,
        )
        self.assertFalse(t.atrasada)
        itens = itens_atencao(self.org, self.hoje)
        ids = [i.item_id for i in itens if i.item_tipo == "tarefa"]
        self.assertNotIn(t.pk, ids)

    # TESTE 10 — Prazo oficial e prazo interno armazenados separadamente.
    def test_spec_10_prazos_oficial_e_interno_separados(self):
        oficial = self.hoje + timedelta(days=10)
        interno = self.hoje + timedelta(days=7)
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Prazo spec",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(oficial),
            prazo_oficial=oficial,
            prazo_interno=interno,
        )
        c.refresh_from_db()
        self.assertEqual(c.prazo_oficial, oficial)
        self.assertEqual(c.prazo_interno, interno)

    # TESTE 11 — Alteração de prazo entra no AuditLog.
    def test_spec_11_alteracao_prazo_registra_audit(self):
        from usuarios.models import AgendaAuditLog

        self.http.login(username="spec_ag_a", password="senha123")
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Prazo audit",
            tipo=TipoCompromisso.PRAZO,
            data_hora=_dt_no_dia(self.hoje),
            prazo_oficial=self.hoje,
            prazo_interno=self.hoje,
        )
        novo_interno = self.hoje - timedelta(days=1)
        self.http.post(
            reverse("agenda"),
            {
                "action": "editar_compromisso",
                "compromisso_id": c.pk,
                "titulo": "Prazo audit",
                "tipo": TipoCompromisso.PRAZO,
                "status": "agendado",
                "prioridade": "normal",
                "prazo_oficial": self.hoje.isoformat(),
                "prazo_interno": novo_interno.isoformat(),
                "data": self.hoje.isoformat(),
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertTrue(
            AgendaAuditLog.objects.filter(
                item_tipo="compromisso",
                item_id=c.pk,
                acao="prazo_alterado",
            ).exists()
        )

    # TESTE 12 — Follow-up comercial aparece na Agenda.
    def test_spec_12_followup_comercial_na_agenda(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Follow-up proposta",
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            data_hora=_dt_no_dia(self.hoje, hora=16),
            cliente=self.cliente_a,
        )
        self.http.login(username="spec_ag_a", password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertContains(resp, "Follow-up proposta")
        self.assertContains(resp, "Follow-up comercial")

    # TESTE 13 — Filtro por responsável funciona.
    def test_spec_13_filtro_responsavel(self):
        self.http.login(username="spec_ag_a", password="senha123")
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Do advogado A",
            data_hora=_dt_no_dia(self.hoje),
            responsavel=self.user_a,
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Do advogado B",
            data_hora=_dt_no_dia(self.hoje, hora=14),
            responsavel=self.user_b,
        )
        resp = self.http.get(
            reverse("agenda"),
            {"view": "lista", "responsavel": self.user_a.pk},
        )
        self.assertContains(resp, "Do advogado A")
        self.assertNotContains(resp, "Do advogado B")

    # TESTE 14 — Filtro por cliente funciona.
    def test_spec_14_filtro_cliente(self):
        self.http.login(username="spec_ag_a", password="senha123")
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Cliente João",
            data_hora=_dt_no_dia(self.hoje),
            cliente=self.cliente_a,
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Sem cliente",
            data_hora=_dt_no_dia(self.hoje, hora=12),
        )
        resp = self.http.get(
            reverse("agenda"),
            {"view": "lista", "cliente": self.cliente_a.pk},
        )
        self.assertContains(resp, "Cliente João")
        self.assertNotContains(resp, ">Sem cliente<")

    # TESTE 15 — Filtro por tipo funciona.
    def test_spec_15_filtro_tipo(self):
        self.http.login(username="spec_ag_a", password="senha123")
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Audiência spec",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt_no_dia(self.hoje),
        )
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Reunião spec",
            tipo=TipoCompromisso.REUNIAO,
            data_hora=_dt_no_dia(self.hoje, hora=15),
        )
        resp = self.http.get(
            reverse("agenda"),
            {"view": "lista", "tipo": TipoCompromisso.AUDIENCIA},
        )
        self.assertContains(resp, "Audiência spec")
        self.assertNotContains(resp, "Reunião spec")

    # TESTE 16 — Evento recorrente gera ocorrências corretamente.
    def test_spec_16_recorrencia_gera_ocorrencias(self):
        from usuarios.choices import Recorrencia
        from usuarios.services.compromisso_recorrencia import gerar_ocorrencias_serie

        raiz = Compromisso.objects.create(
            user=self.user_a,
            titulo="Série semanal",
            data_hora=_dt_no_dia(self.hoje),
            recorrencia=Recorrencia.SEMANAL,
        )
        criados = gerar_ocorrencias_serie(raiz, quantidade=3)
        self.assertEqual(len(criados), 3)
        for clone in criados:
            self.assertEqual(clone.metadados.get("serie_raiz_id"), raiz.pk)
        self.assertNotEqual(criados[0].data_hora, raiz.data_hora)

    # TESTE 17 — Cancelamento preserva histórico.
    def test_spec_17_cancelamento_preserva_historico(self):
        from usuarios.models import AgendaAuditLog
        from usuarios.services.agenda import registrar_audit

        self.http.login(username="spec_ag_a", password="senha123")
        c = Compromisso.objects.create(
            user=self.user_a,
            titulo="Cancelar spec",
            data_hora=_dt_no_dia(self.hoje + timedelta(days=1)),
        )
        registrar_audit(
            usuario=self.user_a,
            item_tipo="compromisso",
            item_id=c.pk,
            acao="criado",
        )
        qtd_antes = AgendaAuditLog.objects.filter(item_id=c.pk).count()
        self.http.post(
            reverse("agenda"),
            {
                "action": "excluir_compromisso",
                "compromisso_id": c.pk,
            },
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCompromisso.CANCELADO)
        self.assertGreater(
            AgendaAuditLog.objects.filter(item_id=c.pk).count(),
            qtd_antes,
        )
        self.assertTrue(
            AgendaAuditLog.objects.filter(item_id=c.pk, acao="criado").exists()
        )

    # TESTE 18 — Cards do topo usam somente dados do tenant atual.
    def test_spec_18_kpis_apenas_tenant_atual(self):
        Compromisso.objects.create(
            user=self.user_a,
            titulo="Hoje A",
            data_hora=_dt_no_dia(self.hoje),
        )
        Tarefa.objects.create(
            user=self.user_a,
            titulo="Atrasada A",
            prazo=self.hoje - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
        )
        Compromisso.objects.create(
            user=self.user_b,
            titulo="Hoje B",
            data_hora=_dt_no_dia(self.hoje),
        )
        Tarefa.objects.create(
            user=self.user_b,
            titulo="Atrasada B",
            prazo=self.hoje - timedelta(days=5),
            status=StatusTarefa.PENDENTE,
        )
        kpis_a = calcular_kpis(self.org, self.hoje)
        kpis_b = calcular_kpis(self.org_b, self.hoje)
        self.assertEqual(kpis_a.compromissos_hoje, 1)
        self.assertEqual(kpis_a.tarefas_atrasadas, 1)
        self.assertEqual(kpis_b.compromissos_hoje, 1)
        self.assertEqual(kpis_b.tarefas_atrasadas, 1)
