"""MT-JOBS-W6 — jobs assíncronos tenant-aware (cobrança, recorrência, lembrete)."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from financeiro.choices import StatusCobranca
from financeiro.models import Cobranca
from financeiro.services.cobranca_agenda import (
    META_COBRANCA_ID,
    META_LEMBRETE_AUTO,
    compromisso_lembrete_automatico,
)
from financeiro.tasks import sincronizar_lembretes_cobrancas_abertas
from organizacoes.models import Membership, Organization
from usuarios.choices import Recorrencia
from usuarios.models import AgendaLembrete, Cliente, Compromisso
from usuarios.services.compromisso_recorrencia import manter_series_recorrentes
from usuarios.tasks import disparar_lembrete_compromisso


def _dt(d, hora=10):
    from datetime import datetime, time

    naive = datetime.combine(d, time(hour=hora))
    return timezone.make_aware(naive)


class MtJobsW6AdversarialTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="W6 Org A")
        self.org_b = Organization.objects.create(name="W6 Org B")
        self.a1 = User.objects.create_user(username="w6_a1", password="senha123")
        self.a2 = User.objects.create_user(username="w6_a2", password="senha123")
        self.b1 = User.objects.create_user(username="w6_b1", password="senha123")
        self._member(self.a1, self.org_a, role=Membership.Role.OWNER)
        self._member(self.a2, self.org_a)
        self._member(self.b1, self.org_b, role=Membership.Role.OWNER)
        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente CA W6",
            email="ca.w6@ex.test",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente CB W6",
            email="cb.w6@ex.test",
        )
        self.hoje = timezone.localdate()
        self.cob_a = self._cobranca(
            self.a2,
            self.org_a,
            self.ca,
            descricao="Honorarios CA",
            responsavel=self.a2,
        )
        self.cob_b = self._cobranca(
            self.b1,
            self.org_b,
            self.cb,
            descricao="Honorarios CB",
            responsavel=self.b1,
        )

    def _member(self, user, org, role=Membership.Role.MEMBER):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=role,
            status=Membership.Status.ACTIVE,
        )

    def _cobranca(self, usuario, organization, cliente, **kwargs):
        defaults = {
            "usuario": usuario,
            "organization": organization,
            "cliente": cliente,
            "descricao": "Cobrança W6",
            "valor_original": Decimal("800.00"),
            "data_vencimento": self.hoje + timedelta(days=7),
            "status": StatusCobranca.PENDING,
            "criado_por": usuario,
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def _auto_qs(self, cobranca):
        return Compromisso.objects.filter(
            metadados__cobranca_id=cobranca.pk,
            metadados__lembrete_cobranca_automatico=True,
        )

    def test_cobranca_same_org_governa_mesmo_com_usuario_a2(self):
        sincronizar_lembretes_cobrancas_abertas()
        comp = compromisso_lembrete_automatico(self.cob_a)
        self.assertIsNotNone(comp)
        self.assertEqual(comp.organization_id, self.org_a.pk)
        self.assertEqual(comp.user_id, self.a2.pk)
        self.assertEqual(comp.responsavel_id, self.a2.pk)

    def test_cobranca_cross_org_side_effect_so_org_b(self):
        sincronizar_lembretes_cobrancas_abertas()
        comp_b = compromisso_lembrete_automatico(self.cob_b)
        self.assertIsNotNone(comp_b)
        self.assertEqual(comp_b.organization_id, self.org_b.pk)
        self.assertFalse(
            self._auto_qs(self.cob_b).filter(organization=self.org_a).exists()
        )
        self.assertFalse(
            self._auto_qs(self.cob_a).filter(organization=self.org_b).exists()
        )

    def test_cobranca_null_nao_gera_via_usuario(self):
        cob_nula = self._cobranca(
            self.a1,
            None,
            self.ca,
            descricao="NULL org",
        )
        antes = Compromisso.objects.count()
        sincronizar_lembretes_cobrancas_abertas()
        self.assertIsNone(compromisso_lembrete_automatico(cob_nula))
        self.assertEqual(self._auto_qs(cob_nula).count(), 0)
        self.assertEqual(Compromisso.objects.filter(user=self.a1, titulo__contains="NULL").count(), 0)
        self.assertGreaterEqual(Compromisso.objects.count(), antes)

    def test_cobranca_conflito_nao_move_nem_sobrescreve(self):
        preso = Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo="Lembrete preso Org B",
            data_hora=_dt(self.hoje + timedelta(days=7), hora=9),
            metadados={
                META_COBRANCA_ID: self.cob_a.pk,
                META_LEMBRETE_AUTO: True,
            },
        )
        sincronizar_lembretes_cobrancas_abertas()
        preso.refresh_from_db()
        self.assertEqual(preso.organization_id, self.org_b.pk)
        self.assertEqual(preso.titulo, "Lembrete preso Org B")
        self.assertFalse(
            self._auto_qs(self.cob_a).filter(organization=self.org_a).exists()
        )

    def test_cobranca_job_idempotente(self):
        sincronizar_lembretes_cobrancas_abertas()
        n1 = self._auto_qs(self.cob_a).count()
        sincronizar_lembretes_cobrancas_abertas()
        self.assertEqual(self._auto_qs(self.cob_a).count(), n1)
        self.assertEqual(n1, 1)

    def test_recorrencia_org_a_e_org_b(self):
        ra = Compromisso.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo="Serie Org A",
            data_hora=_dt(self.hoje - timedelta(days=14), hora=9),
            recorrencia=Recorrencia.SEMANAL,
            responsavel=self.a2,
            cliente=self.ca,
        )
        rb = Compromisso.objects.create(
            user=self.b1,
            organization=self.org_b,
            titulo="Serie Org B",
            data_hora=_dt(self.hoje - timedelta(days=14), hora=11),
            recorrencia=Recorrencia.SEMANAL,
            responsavel=self.b1,
            cliente=self.cb,
        )
        gerados = manter_series_recorrentes()
        self.assertGreater(gerados, 0)
        clones_a = Compromisso.objects.filter(titulo="Serie Org A").exclude(pk=ra.pk)
        clones_b = Compromisso.objects.filter(titulo="Serie Org B").exclude(pk=rb.pk)
        self.assertTrue(clones_a.exists())
        self.assertTrue(clones_b.exists())
        self.assertTrue(all(c.organization_id == self.org_a.pk for c in clones_a))
        self.assertTrue(all(c.organization_id == self.org_b.pk for c in clones_b))
        self.assertTrue(all(c.responsavel_id == self.a2.pk for c in clones_a))
        self.assertTrue(all(c.responsavel_id == self.b1.pk for c in clones_b))

    def test_recorrencia_null_nao_clona_via_user(self):
        nulo = Compromisso.objects.create(
            user=self.a1,
            organization=None,
            titulo="Serie NULL",
            data_hora=_dt(self.hoje - timedelta(days=14), hora=8),
            recorrencia=Recorrencia.SEMANAL,
            responsavel=self.a1,
        )
        manter_series_recorrentes()
        self.assertEqual(Compromisso.objects.filter(titulo="Serie NULL").count(), 1)
        self.assertEqual(Compromisso.objects.filter(pk=nulo.pk).count(), 1)

    def test_recorrencia_cliente_cross_org_skip(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Serie Cliente B",
            data_hora=_dt(self.hoje - timedelta(days=14), hora=7),
            recorrencia=Recorrencia.SEMANAL,
            responsavel=self.a1,
            cliente=self.cb,
        )
        manter_series_recorrentes()
        self.assertEqual(Compromisso.objects.filter(titulo="Serie Cliente B").count(), 1)

    def test_recorrencia_idempotente(self):
        Compromisso.objects.create(
            user=self.a1,
            organization=self.org_a,
            titulo="Serie idem",
            data_hora=_dt(self.hoje - timedelta(days=21), hora=9),
            recorrencia=Recorrencia.SEMANAL,
            responsavel=self.a1,
        )
        g1 = manter_series_recorrentes()
        total = Compromisso.objects.filter(titulo="Serie idem").count()
        g2 = manter_series_recorrentes()
        self.assertEqual(g2, 0)
        self.assertEqual(Compromisso.objects.filter(titulo="Serie idem").count(), total)
        self.assertGreater(g1, 0)

    def test_disparar_lembrete_pk_valida_organization(self):
        futuro = Compromisso.objects.create(
            user=self.a2,
            organization=self.org_a,
            titulo="Lembrete org A",
            data_hora=_dt(self.hoje + timedelta(days=2), hora=14),
            lembrete_minutos=60,
        )
        disparar_lembrete_compromisso(futuro.pk)
        self.assertTrue(
            AgendaLembrete.objects.filter(compromisso=futuro, user=self.a2).exists()
        )

        nulo = Compromisso.objects.create(
            user=self.a1,
            organization=None,
            titulo="Lembrete NULL",
            data_hora=_dt(self.hoje + timedelta(days=2), hora=15),
            lembrete_minutos=60,
        )
        disparar_lembrete_compromisso(nulo.pk)
        self.assertFalse(AgendaLembrete.objects.filter(compromisso=nulo).exists())
