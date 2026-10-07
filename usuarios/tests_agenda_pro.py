"""AGENDA-PRO-01 — criticidade operacional, Assistente/Advisor e isolamento MT."""

from datetime import datetime, time, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from organizacoes.models import Membership, Organization
from organizacoes.services import CONTEXT_AMBIGUOUS, CONTEXT_NONE, CONTEXT_RESOLVED
from usuarios.choices import (
    Prioridade,
    StatusCompromisso,
    StatusConfirmacaoConsulta,
    StatusTarefa,
    TipoCompromisso,
)
from usuarios.models import AgendaLembrete, Cliente, Compromisso, Tarefa
from usuarios.services.agenda_pro import (
    FRASES_JURIDICAS_PROIBIDAS,
    CapsAgendaPro,
    classificar_compromisso,
    classificar_tarefa,
    detectar_conflitos,
    detectar_concentracoes,
    montar_agenda_pro,
    origem_evento,
)
from usuarios.tenancy_agenda import organization_for_agenda
from usuarios.tests_helpers import grant_agenda_permissions

MARKER_A = "AGENDA_ORG_A_7F3K"
MARKER_B = "AGENDA_ORG_B_9Q2M"


def _dt(d, hora=10, minuto=0):
    naive = datetime.combine(d, time(hour=hora, minute=minuto))
    return timezone.make_aware(naive)


def _blob(*partes) -> str:
    return " ".join(str(p).lower() for p in partes)


class AgendaProTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Agenda Pro Org A")
        self.org_b = Organization.objects.create(name="Agenda Pro Org B")
        self.a1 = User.objects.create_user("agpro_a1", password="senha123")
        self.a2 = User.objects.create_user("agpro_a2", password="senha123")
        self.b1 = User.objects.create_user("agpro_b1", password="senha123")
        self.sem_fin = User.objects.create_user("agpro_sem_fin", password="senha123")
        self.hoje = timezone.localdate()

        grant_agenda_permissions(self.a1)
        grant_agenda_permissions(
            self.a2, "view_agenda", "create_agenda", "edit_agenda", "cancel_agenda"
        )
        grant_agenda_permissions(self.b1)
        grant_agenda_permissions(self.sem_fin)

        grupo = Group.objects.create(name="Agenda PRO RBAC")
        for user in (self.a1, self.a2, self.b1, self.sem_fin):
            user.groups.add(grupo)

        for user, org in (
            (self.a1, self.org_a),
            (self.a2, self.org_a),
            (self.sem_fin, self.org_a),
            (self.b1, self.org_b),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=Membership.Role.MEMBER,
                status=Membership.Status.ACTIVE,
            )

        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente A Pro",
            email="ca.agpro@test",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente B Pro",
            email="cb.agpro@test",
        )
        self.http = Client()

    def _caps(self, **kwargs):
        defaults = dict(ver_financeiro=False, ver_auditoria=False, ver_cliente=True)
        defaults.update(kwargs)
        return CapsAgendaPro(**defaults)

    def test_tarefa_vencida_nao_e_prazo_juridico_perdido(self):
        t = Tarefa.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Petição vencida",
            prazo=self.hoje - timedelta(days=3),
            status=StatusTarefa.PENDENTE,
            prioridade=Prioridade.ALTA,
            responsavel=self.a1,
        )
        pro = classificar_tarefa(t, self.hoje)
        self.assertEqual(pro.nivel, "critico")
        self.assertTrue(pro.vencido)
        blob = _blob(*pro.reasons)
        self.assertIn("não significa prazo jurídico perdido", blob)
        for frase in FRASES_JURIDICAS_PROIBIDAS:
            self.assertNotIn(frase, blob)

    def test_data_ultrapassada_nao_e_intempestividade(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Reunião atrasada",
            tipo=TipoCompromisso.REUNIAO,
            data_hora=_dt(self.hoje - timedelta(days=1), 9),
            status=StatusCompromisso.AGENDADO,
            responsavel=self.a1,
        )
        pro = classificar_compromisso(c, self.hoje)
        self.assertTrue(pro.vencido)
        self.assertEqual(pro.nivel, "atencao")
        blob = _blob(*pro.reasons)
        self.assertIn("não é intempestividade", blob)
        self.assertNotIn("intempestividade", blob.replace("não é intempestividade", ""))

    def test_sem_confirmacao_nao_e_nao_realizado(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Consulta pendente",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt(self.hoje, 14),
            status=StatusCompromisso.AGENDADO,
            confirmacao_consulta=StatusConfirmacaoConsulta.PENDENTE,
            responsavel=self.a1,
        )
        pro = classificar_compromisso(c, self.hoje)
        self.assertEqual(pro.confirmacao, "pendente")
        blob = _blob(*pro.reasons)
        self.assertIn("evento não realizado", blob)
        self.assertNotIn("não realizado)", blob.split("≠")[0] if False else "perda de audiência")
        self.assertNotIn("perda de audiência", blob)
        realizado = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Consulta feita",
            tipo=TipoCompromisso.CONSULTA,
            data_hora=_dt(self.hoje, 15),
            status=StatusCompromisso.REALIZADO,
            confirmacao_consulta=StatusConfirmacaoConsulta.CONFIRMADA,
            responsavel=self.a1,
        )
        pro_ok = classificar_compromisso(realizado, self.hoje)
        self.assertEqual(pro_ok.confirmacao, "realizado")
        self.assertFalse(pro_ok.elevado)

    def test_confirmado_diferente_de_realizado(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Audiência confirmada",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt(self.hoje, 11),
            status=StatusCompromisso.CONFIRMADO,
            responsavel=self.a1,
        )
        pro = classificar_compromisso(c, self.hoje)
        self.assertEqual(pro.confirmacao, "confirmado")
        self.assertNotEqual(pro.confirmacao, "realizado")
        self.assertTrue(pro.elevado)

    def test_prioridade_nao_e_criticidade_juridica(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Urgente interno",
            tipo=TipoCompromisso.INTERNO,
            data_hora=_dt(self.hoje + timedelta(days=1), 10),
            status=StatusCompromisso.AGENDADO,
            prioridade=Prioridade.URGENTE,
            responsavel=self.a1,
        )
        pro = classificar_compromisso(c, self.hoje)
        blob = _blob(*pro.reasons)
        self.assertIn("não é gravidade jurídica", blob)
        self.assertTrue(pro.elevado)

    def test_dados_insuficientes_tarefa_sem_prazo(self):
        t = Tarefa.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Sem prazo",
            status=StatusTarefa.PENDENTE,
            responsavel=self.a1,
        )
        pro = classificar_tarefa(t, self.hoje)
        self.assertEqual(pro.nivel, "dados_insuficientes")
        self.assertFalse(pro.vencido)

    def test_sem_responsavel_eleva_atencao(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Sem dono",
            tipo=TipoCompromisso.REUNIAO,
            data_hora=_dt(self.hoje + timedelta(days=5), 10),
            status=StatusCompromisso.AGENDADO,
            responsavel=None,
        )
        pro = classificar_compromisso(c, self.hoje)
        self.assertTrue(pro.sem_responsavel)
        self.assertEqual(pro.nivel, "atencao")

    def test_origem_financeiro_nao_e_recebimento(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Lembrete cobrança",
            tipo=TipoCompromisso.COBRANCA,
            data_hora=_dt(self.hoje, 16),
            status=StatusCompromisso.AGENDADO,
            metadados={"cobranca_id": 999},
            responsavel=self.a1,
        )
        self.assertEqual(origem_evento(c), "financeiro")
        pro = classificar_compromisso(c, self.hoje)
        blob = _blob(*pro.reasons)
        self.assertIn("evento de cobrança ≠ recebimento", blob)

    def test_normal_futuro_sem_sinais(self):
        c = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Reunião distante",
            tipo=TipoCompromisso.REUNIAO,
            data_hora=_dt(self.hoje + timedelta(days=10), 10),
            status=StatusCompromisso.AGENDADO,
            prioridade=Prioridade.NORMAL,
            responsavel=self.a1,
        )
        pro = classificar_compromisso(c, self.hoje)
        self.assertEqual(pro.nivel, "normal")
        self.assertFalse(pro.elevado)

    def test_overlap_com_duracao_e_mesmo_horario_sem_fim(self):
        a = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Bloco A",
            data_hora=_dt(self.hoje, 10),
            data_hora_fim=_dt(self.hoje, 11),
            responsavel=self.a2,
        )
        b = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Bloco B",
            data_hora=_dt(self.hoje, 10, 30),
            data_hora_fim=_dt(self.hoje, 11, 30),
            responsavel=self.a2,
        )
        conflitos = detectar_conflitos([a, b])
        self.assertEqual(len(conflitos), 1)

        c1 = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Ponto 1",
            data_hora=_dt(self.hoje, 15),
            responsavel=self.a1,
        )
        c2 = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Ponto 2",
            data_hora=_dt(self.hoje, 15),
            responsavel=self.a1,
        )
        self.assertEqual(len(detectar_conflitos([c1, c2])), 1)

        d1 = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Com fim",
            data_hora=_dt(self.hoje, 18),
            data_hora_fim=_dt(self.hoje, 19),
            responsavel=self.a1,
        )
        d2 = Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Sem fim outro horário",
            data_hora=_dt(self.hoje, 18, 30),
            responsavel=self.a1,
        )
        self.assertEqual(len(detectar_conflitos([d1, d2])), 0)

    def test_concentracao_nao_e_sobrecarga(self):
        itens = []
        for i in range(4):
            itens.append(
                Compromisso.objects.create(
                    user=self.a1,
                    organization=self.org_a,
                    titulo=f"Lote {i}",
                    data_hora=_dt(self.hoje, 8 + i),
                    responsavel=self.a1,
                )
            )
        conc = detectar_concentracoes(itens, self.hoje)
        self.assertEqual(len(conc), 1)
        self.assertEqual(conc[0].quantidade, 4)
        painel = montar_agenda_pro(self.org_a, ref=self.hoje, caps=self._caps())
        blob = _blob(
            painel.advisor.principal.evidence if painel.advisor.principal else "",
            painel.advisor.principal.contexto if painel.advisor.principal else "",
        )
        self.assertIn("concentração", blob)
        self.assertNotIn("advogado sobrecarregado", blob)
        self.assertNotIn("sobrecarga humana", blob.replace("não comprova sobrecarga humana", ""))

    def test_advisor_vencido_e_saudavel_e_vazio(self):
        Tarefa.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo=f"Ação vencida {MARKER_A}",
            prazo=self.hoje - timedelta(days=1),
            status=StatusTarefa.PENDENTE,
            prioridade=Prioridade.URGENTE,
            responsavel=self.a2,
        )
        painel = montar_agenda_pro(self.org_a, ref=self.hoje, caps=self._caps())
        self.assertIsNotNone(painel.advisor.principal)
        self.assertIn(MARKER_A, painel.advisor.principal.titulo)
        self.assertEqual(painel.advisor.status, "critico")
        self.assertTrue(painel.advisor.principal.cta)
        self.assertTrue(painel.advisor.principal.url)
        blob = _blob(painel.advisor.principal.evidence, *painel.criterios.split("."))
        self.assertIn("não significa prazo jurídico perdido", blob)

        org_ok = Organization.objects.create(name="Agenda saudável")
        Membership.objects.create(
            user=self.a1, organization=org_ok, status=Membership.Status.ACTIVE
        )
        Compromisso.objects.create(
            user=self.a1,
            organization=org_ok,
            titulo="Futuro ok",
            data_hora=_dt(self.hoje + timedelta(days=10), 10),
            status=StatusCompromisso.AGENDADO,
            responsavel=self.a1,
        )
        saudavel = montar_agenda_pro(org_ok, ref=self.hoje, caps=self._caps())
        self.assertIsNone(saudavel.advisor.principal)
        self.assertIn(saudavel.advisor.status, ("saudavel", "vazio"))

        org_vazio = Organization.objects.create(name="Agenda vazia")
        vazio = montar_agenda_pro(org_vazio, ref=self.hoje, caps=self._caps())
        self.assertIsNone(vazio.advisor.principal)
        self.assertEqual(vazio.advisor.status, "vazio")

        nulo = montar_agenda_pro(None, ref=self.hoje, caps=self._caps())
        self.assertEqual(nulo.advisor.status, "vazio")
        self.assertEqual(nulo.kpis.criticos, 0)

    def test_advisor_nao_duplica_assistente_na_ui(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo=f"Hoje {MARKER_A}",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt(self.hoje, 9),
            status=StatusCompromisso.AGENDADO,
            responsavel=self.a2,
            cliente=self.ca,
        )
        antes = AgendaLembrete.objects.count()
        self.http.login(username="agpro_a1", password="senha123")
        resp = self.http.get(reverse("agenda"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(html.lower().count("assistente do dia"), 1)
        self.assertEqual(html.count("Growth Advisor"), 1)
        self.assertIn(MARKER_A, html)
        self.assertNotIn(MARKER_B, html)
        self.assertIn("Por quê", html)
        self.assertIn("Próxima ação", html)
        self.assertEqual(AgendaLembrete.objects.count(), antes)
        for frase in FRASES_JURIDICAS_PROIBIDAS:
            self.assertNotIn(frase, html.lower())
        self.assertIn("Tarefas atrasadas", html)
        self.assertIn("Crítico operacional", html)

    def test_views_preservadas(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo=f"Cal {MARKER_A}",
            data_hora=_dt(self.hoje, 11),
            responsavel=self.a1,
        )
        self.http.login(username="agpro_a1", password="senha123")
        for view, trecho in (
            ("hoje", "Timeline do dia"),
            ("semana", "compromisso"),
            ("mes", "comp."),
            ("lista", "Lista unificada"),
        ):
            resp = self.http.get(reverse("agenda"), {"view": view})
            self.assertEqual(resp.status_code, 200, view)
            self.assertContains(resp, trecho)
            self.assertContains(resp, "Assistente do dia")
            self.assertNotContains(resp, MARKER_B)

    def test_same_org_e_cross_org_e_idor(self):
        ca = Compromisso.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo=f"Item A2 {MARKER_A}",
            data_hora=_dt(self.hoje, 10),
            cliente=self.ca,
            responsavel=self.a2,
        )
        cb = Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo=f"Segredo B {MARKER_B}",
            data_hora=_dt(self.hoje, 10),
            cliente=self.cb,
            responsavel=self.b1,
        )
        Tarefa.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo=f"Tarefa B {MARKER_B}",
            prazo=self.hoje,
            responsavel=self.b1,
        )
        painel_a = montar_agenda_pro(self.org_a, ref=self.hoje, caps=self._caps())
        painel_b = montar_agenda_pro(self.org_b, ref=self.hoje, caps=self._caps())
        texto_a = _blob(
            painel_a.advisor.principal.titulo if painel_a.advisor.principal else "",
            *(s.titulo for s in painel_a.advisor.secundarios),
        )
        texto_b = _blob(
            painel_b.advisor.principal.titulo if painel_b.advisor.principal else "",
            *(s.titulo for s in painel_b.advisor.secundarios),
        )
        self.assertNotIn(MARKER_B.lower(), texto_a)
        self.assertNotIn(MARKER_A.lower(), texto_b)

        self.http.login(username="agpro_a1", password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        html = resp.content.decode()
        self.assertIn(MARKER_A, html)
        self.assertNotIn(MARKER_B, html)

        resp = self.http.get(
            reverse("agenda"),
            {"modal": "compromisso", "compromisso_id": cb.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, MARKER_B)

        resp = self.http.post(
            reverse("agenda"),
            {"action": "realizar_compromisso", "compromisso_id": cb.pk},
        )
        self.assertEqual(resp.status_code, 404)
        cb.refresh_from_db()
        self.assertEqual(cb.status, StatusCompromisso.AGENDADO)

        form = __import__("usuarios.forms", fromlist=["CompromissoForm"]).CompromissoForm(
            user=self.a1, organization=self.org_a
        )
        ids_cli = set(form.fields["cliente"].queryset.values_list("pk", flat=True))
        ids_resp = set(form.fields["responsavel"].queryset.values_list("pk", flat=True))
        self.assertIn(self.ca.pk, ids_cli)
        self.assertNotIn(self.cb.pk, ids_cli)
        self.assertNotIn(self.b1.pk, ids_resp)

    def test_capability_advisor_sem_financeiro_e_audit(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Cobrança interna",
            tipo=TipoCompromisso.COBRANCA,
            data_hora=_dt(self.hoje, 9),
            metadados={"cobranca_id": 4242},
            responsavel=self.a1,
        )
        sem = montar_agenda_pro(
            self.org_a, ref=self.hoje, caps=self._caps(ver_financeiro=False)
        )
        com = montar_agenda_pro(
            self.org_a, ref=self.hoje, caps=self._caps(ver_financeiro=True)
        )
        self.assertIsNotNone(sem.advisor.principal)
        self.assertNotIn("/financeiro/", sem.advisor.principal.url)
        self.assertIn("/financeiro/", com.advisor.principal.url)

        self.http.login(username="agpro_a2", password="senha123")
        resp = self.http.get(reverse("agenda_auditoria"))
        self.assertEqual(resp.status_code, 302)
        resp = self.http.get(reverse("agenda"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Assistente do dia")

    def test_null_e_tenantcontext_invalido(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=None,
            titulo="NULL inseguro",
            data_hora=_dt(self.hoje, 12),
            responsavel=self.a1,
        )
        self.http.login(username="agpro_a1", password="senha123")
        resp = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertNotContains(resp, "NULL inseguro")
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

    def test_prevenir_nao_cria_lembrete(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Sem lembrete extra",
            tipo=TipoCompromisso.AUDIENCIA,
            data_hora=_dt(self.hoje, 8),
            lembrete_minutos=60,
            responsavel=self.a1,
        )
        n = AgendaLembrete.objects.count()
        montar_agenda_pro(self.org_a, ref=self.hoje, caps=self._caps())
        self.assertEqual(AgendaLembrete.objects.count(), n)

    def test_job_pk_b_nao_vaza_para_a(self):
        from usuarios.tasks import disparar_lembrete_compromisso

        futuro = timezone.now() + timedelta(days=2)
        cb = Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo=f"Lembrete B {MARKER_B}",
            data_hora=futuro,
            lembrete_minutos=60,
            responsavel=self.b1,
        )
        disparar_lembrete_compromisso(cb.pk)
        self.assertFalse(
            AgendaLembrete.objects.filter(
                compromisso=cb, compromisso__organization=self.org_a
            ).exists()
        )
        self.assertTrue(AgendaLembrete.objects.filter(compromisso=cb).exists())
        nulo = Compromisso.objects.create(
            user=self.a1,
            organization=None,
            titulo="Job NULL",
            data_hora=futuro,
            lembrete_minutos=60,
        )
        disparar_lembrete_compromisso(nulo.pk)
        self.assertFalse(AgendaLembrete.objects.filter(compromisso=nulo).exists())
