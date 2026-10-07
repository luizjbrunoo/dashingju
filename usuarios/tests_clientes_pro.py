"""CLIENTES-PRO-01 — health, NBA, timeline, Advisor e isolamento multi-tenant."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, Contrato
from financeiro.tests_helpers import grant_finance_permissions
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead, StatusCompromisso, StatusTarefa, TipoCompromisso
from usuarios.models import Cliente, Compromisso, Tarefa
from usuarios.services.clientes_pro import (
    CapsClientePro,
    montar_clientes_pro,
    sinais_listagem_clientes,
)
from usuarios.tests_helpers import grant_agenda_permissions

MARKER_A = "CLIENTE_ORG_A_7F3K"
MARKER_B = "CLIENTE_ORG_B_9Q2M"


class ClientesProTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Cli Pro Org A")
        self.org_b = Organization.objects.create(name="Cli Pro Org B")
        self.a1 = User.objects.create_user("clipro_a1", password="senha123")
        self.a2 = User.objects.create_user("clipro_a2", password="senha123")
        self.b1 = User.objects.create_user("clipro_b1", password="senha123")
        self.sem_fin = User.objects.create_user("clipro_sem_fin", password="senha123")
        self.hoje = timezone.localdate()
        self.agora = timezone.now()

        grant_finance_permissions(self.a1, "view_cobrancas", "view_recebimentos")
        grant_finance_permissions(self.a2, "view_cobrancas", "view_recebimentos")
        grant_finance_permissions(self.b1, "view_cobrancas", "view_recebimentos")
        grant_agenda_permissions(self.a1)
        grant_agenda_permissions(self.a2)
        grant_agenda_permissions(self.b1)
        grant_agenda_permissions(self.sem_fin)

        grupo = Group.objects.create(name="Cli PRO sem financeiro")
        self.sem_fin.groups.add(grupo)

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

        self.cli_a = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome=f"Prospect A {MARKER_A}",
            email="a@clipro.test",
            status="em_prospeccao",
            fase_funil="aguardando_decisao",
            origem=OrigemLead.INDICACAO,
            atribuicao_confiavel=True,
        )
        self.cli_b = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome=f"Prospect B {MARKER_B}",
            email="b@clipro.test",
            status="em_prospeccao",
            fase_funil="proposta_enviada",
            origem=OrigemLead.GOOGLE_ADS,
            atribuicao_confiavel=True,
        )
        self.caps_full = CapsClientePro(
            agenda=True, financeiro=True, recebimentos=True, documentos=True
        )
        self.caps_sem_fin = CapsClientePro(
            agenda=True, financeiro=False, recebimentos=False, documentos=False
        )

    def test_semantica_status_fase_temperatura_health(self):
        painel = montar_clientes_pro(
            self.org_a, self.cli_a, caps=self.caps_full, incluir_titulos=True
        )
        self.assertEqual(painel.status, "em_prospeccao")
        self.assertEqual(painel.fase, "aguardando_decisao")
        self.assertNotEqual(painel.status, painel.fase)
        self.assertNotEqual(painel.temperatura, painel.health.estado)
        self.assertNotEqual(painel.health.estado, painel.nba.key)
        self.assertEqual(painel.ultima_interacao.label, "Sem interação registrada")

    def test_health_dados_insuficientes(self):
        cli = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Novo sem rastros",
            email="novo@clipro.test",
            status="em_prospeccao",
            fase_funil="primeiro_contato",
        )
        painel = montar_clientes_pro(self.org_a, cli, caps=self.caps_full)
        self.assertIn(painel.health.estado, {"dados_insuficientes", "atencao"})
        self.assertNotEqual(painel.health.estado, "saudavel")

    def test_proposta_sem_acao_e_acao_existente_nao_duplica(self):
        sem_acao = montar_clientes_pro(
            self.org_a, self.cli_a, caps=self.caps_full, incluir_titulos=True
        )
        self.assertIn(
            sem_acao.nba.key, {"revisar_proposta", "agendar_contato", "followup_quente"}
        )
        self.assertFalse(sem_acao.nba.ja_existe)

        Compromisso.objects.create(
            user=self.a2,
            organization=self.org_a,
            cliente=self.cli_a,
            titulo="Follow-up já marcado",
            tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
            status=StatusCompromisso.AGENDADO,
            data_hora=self.agora + timedelta(days=1),
        )
        com_acao = montar_clientes_pro(
            self.org_a, self.cli_a, caps=self.caps_full, incluir_titulos=True
        )
        self.assertTrue(com_acao.proxima_acao.ja_agendada)
        self.assertEqual(com_acao.nba.key, "acompanhar_existente")
        self.assertTrue(com_acao.nba.ja_existe)
        self.assertIn("não criar duplicata", com_acao.nba.acao.lower())

    def test_tarefa_atrasada_nao_e_prazo_juridico(self):
        Tarefa.objects.create(
            user=self.a1,
            organization=self.org_a,
            cliente=self.cli_a,
            titulo="Ligação",
            status=StatusTarefa.PENDENTE,
            prazo=self.hoje - timedelta(days=3),
        )
        painel = montar_clientes_pro(self.org_a, self.cli_a, caps=self.caps_full)
        blob = " ".join(painel.health.motivos).lower()
        self.assertIn("não significa prazo jurídico", blob)
        self.assertNotIn("prazo processual perdido", blob)

    def test_cobranca_vencida_capability_e_leak(self):
        Contrato.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.cli_a,
            referencia=f"CTR-{MARKER_A}",
            descricao="Honorarios A",
            valor_total=Decimal("15000.00"),
            status=StatusContrato.ACTIVE,
            criado_por=self.a2,
        )
        Cobranca.objects.create(
            usuario=self.a2,
            organization=self.org_a,
            cliente=self.cli_a,
            descricao="Honorarios A vencido",
            valor_original=Decimal("15000.00"),
            data_vencimento=self.hoje - timedelta(days=5),
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.OVERDUE,
            criado_por=self.a2,
        )
        com_fin = montar_clientes_pro(
            self.org_a, self.cli_a, caps=self.caps_full, incluir_titulos=True
        )
        self.assertEqual(com_fin.health.estado, "critico")
        self.assertEqual(com_fin.nba.key, "acompanhar_cobranca")
        self.assertIsNotNone(com_fin.financeiro)
        self.assertGreater(com_fin.financeiro.vencido, 0)
        self.assertNotIn("cliente ruim", " ".join(com_fin.health.motivos).lower())

        sem_fin = montar_clientes_pro(
            self.org_a, self.cli_a, caps=self.caps_sem_fin, incluir_titulos=False
        )
        self.assertIsNone(sem_fin.financeiro)
        self.assertNotEqual(sem_fin.nba.key, "acompanhar_cobranca")
        blob = sem_fin.advisor.evidence + (sem_fin.advisor.impacto or "")
        self.assertNotIn("15000", blob)
        tipos = {e.tipo for e in sem_fin.timeline}
        self.assertNotIn("cobranca", tipos)
        self.assertNotIn("contrato", tipos)
        self.assertNotIn("recebimento", tipos)

    def test_timeline_ordenacao_limite_e_cross_org(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            cliente=self.cli_a,
            titulo="Consulta A",
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=self.agora - timedelta(days=1),
        )
        Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            cliente=self.cli_b,
            titulo=MARKER_B,
            tipo=TipoCompromisso.CONSULTA,
            status=StatusCompromisso.REALIZADO,
            data_hora=self.agora,
        )
        painel_a = montar_clientes_pro(
            self.org_a, self.cli_a, caps=self.caps_full, incluir_titulos=True
        )
        textos = " ".join(f"{e.titulo} {e.resumo}" for e in painel_a.timeline)
        self.assertNotIn(MARKER_B, textos)
        self.assertLessEqual(len(painel_a.timeline), 20)
        stamps = [e.timestamp for e in painel_a.timeline]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

        painel_b = montar_clientes_pro(
            self.org_b, self.cli_b, caps=self.caps_full, incluir_titulos=True
        )
        textos_b = " ".join(e.titulo for e in painel_b.timeline)
        self.assertIn(MARKER_B, textos_b)
        self.assertNotIn(MARKER_A, textos_b)

    def test_same_org_a2_ve_cliente_de_a1(self):
        self.client.force_login(self.a2)
        resp = self.client.get(reverse("cliente", args=[self.cli_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Growth Advisor")
        self.assertContains(resp, MARKER_A)
        self.assertNotContains(resp, MARKER_B)

    def test_cross_org_idor(self):
        self.client.force_login(self.a1)
        self.assertEqual(
            self.client.get(reverse("cliente", args=[self.cli_b.pk])).status_code, 404
        )

    def test_tenant_invalido_fail_closed(self):
        painel = montar_clientes_pro(None, self.cli_a, caps=self.caps_full)
        self.assertFalse(painel.tenant_ok)
        self.assertIsNone(painel.financeiro)
        self.assertEqual(painel.timeline, ())

    def test_listagem_batch_sem_cross_org(self):
        mapa = sinais_listagem_clientes(
            self.org_a, [self.cli_a.pk, self.cli_b.pk], caps=self.caps_full
        )
        self.assertIn(self.cli_a.pk, mapa)
        self.assertTrue(mapa[self.cli_a.pk].atencao)
        if self.cli_b.pk in mapa:
            self.assertNotEqual(mapa[self.cli_b.pk].health, mapa[self.cli_a.pk].health)
            self.assertFalse(mapa[self.cli_b.pk].atencao)

    def test_detail_preserva_e_exibe_pro(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("cliente", args=[self.cli_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Growth Advisor")
        self.assertContains(resp, "Linha do tempo")
        self.assertContains(resp, "causalidade")
        self.assertContains(resp, self.cli_a.email)
        self.assertNotContains(resp, MARKER_B)
