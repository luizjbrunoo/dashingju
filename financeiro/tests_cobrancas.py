from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from financeiro.choices import StatusCobranca
from financeiro.models import Cobranca, CobrancaHistorico, CobrancaRecebimento
from financeiro.services.cobrancas import resolver_status_cobranca, saldo_cobranca
from financeiro.services.historico_cobranca import registrar_historico_cobranca
from financeiro.tests_helpers import grant_billing_permissions
from usuarios.models import Cliente
from organizacoes.models import Membership, Organization

User = get_user_model()


def _provision_tenant(user, *clientes):
    org = Organization.objects.create(name=f"Org {user.username}-{user.pk}")
    Membership.objects.create(
        user=user,
        organization=org,
        role=Membership.Role.OWNER,
        status=Membership.Status.ACTIVE,
    )
    grant_billing_permissions(user)
    for cli in clientes:
        if cli is None:
            continue
        cli.organization = org
        cli.save(update_fields=["organization"])
    return org


class CobrancaModelTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.hoje = timezone.localdate()

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários",
            "valor_original": Decimal("5000.00"),
            "data_vencimento": self.hoje + timedelta(days=15),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_criar_cobranca(self):
        c = self._criar_cobranca()
        self.assertEqual(c.status, StatusCobranca.PENDING)
        self.assertEqual(c.saldo, Decimal("5000.00"))

    def test_valor_zero_rejeitado(self):
        c = Cobranca(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="X",
            valor_original=Decimal("0"),
            data_vencimento=self.hoje,
        )
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_cliente_outro_usuario_rejeitado(self):
        c = Cobranca(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_b,
            descricao="X",
            valor_original=Decimal("100"),
            data_vencimento=self.hoje,
        )
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_saldo_apos_recebimento_parcial(self):
        c = self._criar_cobranca()
        CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_a,
            valor=Decimal("2000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user_a,
        )
        self.assertEqual(c.total_recebido, Decimal("2000.00"))
        self.assertEqual(saldo_cobranca(c), Decimal("3000.00"))
        self.assertEqual(
            resolver_status_cobranca(c, hoje=self.hoje),
            StatusCobranca.PARTIALLY_PAID,
        )

    def test_status_recebido_quando_saldo_zero(self):
        c = self._criar_cobranca()
        CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_a,
            valor=Decimal("5000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user_a,
        )
        self.assertEqual(
            resolver_status_cobranca(c, hoje=self.hoje),
            StatusCobranca.PAID,
        )

    def test_status_vencido(self):
        c = self._criar_cobranca(data_vencimento=self.hoje - timedelta(days=3))
        self.assertEqual(
            resolver_status_cobranca(c, hoje=self.hoje),
            StatusCobranca.OVERDUE,
        )

    def test_status_vence_em_breve(self):
        c = self._criar_cobranca(data_vencimento=self.hoje + timedelta(days=5))
        self.assertEqual(
            resolver_status_cobranca(c, hoje=self.hoje),
            StatusCobranca.DUE_SOON,
        )

    def test_cancelamento(self):
        c = self._criar_cobranca()
        c.cancelar(motivo="Acordo")
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCobranca.CANCELED)
        self.assertIsNotNone(c.cancelado_em)

    def test_recebimento_estornado_nao_conta_no_saldo(self):
        c = self._criar_cobranca()
        r = CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_a,
            valor=Decimal("1000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user_a,
        )
        r.estornar(motivo="Erro de lançamento")
        self.assertEqual(c.total_recebido, Decimal("0"))

    def test_historico_registrado(self):
        c = self._criar_cobranca()
        h = registrar_historico_cobranca(
            c,
            "criada",
            descricao="Cobrança criada.",
            autor=self.user_a,
        )
        self.assertEqual(CobrancaHistorico.objects.filter(cobranca=c).count(), 1)
        self.assertEqual(h.usuario, self.user_a)

    def test_parcela_rotulo(self):
        c = self._criar_cobranca(parcela_numero=2, parcela_total=6)
        self.assertEqual(c.parcela_rotulo, "Parcela 2/6")


class CobrancaCrudTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Outro", email="outro@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.org_b = _provision_tenant(self.user_b, self.cliente_b)
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_criar_cobranca_via_post(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_nova"),
            {
                "cliente": self.cliente_a.pk,
                "contrato_referencia": "00042",
                "descricao": "Honorários trabalhistas",
                "valor": "5000,00",
                "data_vencimento": (self.hoje + timedelta(days=20)).isoformat(),
                "categoria": "honorarios",
                "responsavel": self.user_a.pk,
                "forma_prevista_pagamento": "pix",
                "observacoes_internas": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        c = Cobranca.objects.get(descricao="Honorários trabalhistas")
        self.assertEqual(c.cliente, self.cliente_a)
        self.assertEqual(c.valor_original, Decimal("5000.00"))
        self.assertEqual(c.historico.count(), 1)

    def test_criar_cobranca_cliente_outro_usuario_rejeitada(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_nova"),
            {
                "cliente": self.cliente_b.pk,
                "descricao": "Inválida",
                "valor": "100,00",
                "data_vencimento": self.hoje.isoformat(),
                "categoria": "honorarios",
                "responsavel": self.user_a.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cobranca.objects.filter(descricao="Inválida").exists())

    def test_detalhe_bloqueia_outro_usuario(self):
        c = Cobranca.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_b,
            descricao="Secreta",
            valor_original=Decimal("100"),
            data_vencimento=self.hoje,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_detalhe", args=[c.pk]))
        self.assertEqual(response.status_code, 404)

    def test_editar_cobranca_registra_historico(self):
        c = Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Original",
            valor_original=Decimal("1000"),
            data_vencimento=self.hoje,
            criado_por=self.user_a,
        )
        self.http.login(username="adv_a", password="senha123")
        novo_venc = self.hoje + timedelta(days=10)
        response = self.http.post(
            reverse("financeiro_cobranca_editar", args=[c.pk]),
            {
                "cliente": self.cliente_a.pk,
                "contrato_referencia": "",
                "descricao": "Original atualizada",
                "valor": "1000,00",
                "data_vencimento": novo_venc.isoformat(),
                "categoria": "honorarios",
                "responsavel": self.user_a.pk,
                "forma_prevista_pagamento": "",
                "observacoes_internas": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.descricao, "Original atualizada")
        self.assertTrue(c.historico.filter(acao="editada").exists())
        self.assertTrue(c.historico.filter(acao="vencimento_alterado").exists())

    def test_cancelar_cobranca(self):
        c = Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Cancelável",
            valor_original=Decimal("500"),
            data_vencimento=self.hoje,
            criado_por=self.user_a,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_cancelar", args=[c.pk]),
            {"motivo": "Acordo com cliente"},
        )
        self.assertEqual(response.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCobranca.CANCELED)
        self.assertTrue(c.historico.filter(acao="cancelada").exists())

    def test_listar_cobrancas_usuario(self):
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Minha",
            valor_original=Decimal("100"),
            data_vencimento=self.hoje,
        )
        Cobranca.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_b,
            descricao="De outro",
            valor_original=Decimal("200"),
            data_vencimento=self.hoje,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_listar"))
        self.assertContains(response, "Minha")
        self.assertNotContains(response, "De outro")


class CobrancaListagemFase3Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_a, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a, self.cliente_b)
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_listagem_exibe_kpis(self):
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Aberta",
            valor_original=Decimal("1000"),
            data_vencimento=self.hoje + timedelta(days=10),
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_listar"))
        self.assertContains(response, "A receber")
        self.assertContains(response, "Taxa de recebimento")

    def test_filtro_status_vencido(self):
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Vencida",
            valor_original=Decimal("500"),
            data_vencimento=self.hoje - timedelta(days=5),
        )
        Cobranca.objects.create(
            usuario=self.user_a,
            cliente=self.cliente_b,
            descricao="Futura",
            valor_original=Decimal("300"),
            data_vencimento=self.hoje + timedelta(days=20),
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(
            reverse("financeiro_cobranca_listar"),
            {"status": StatusCobranca.OVERDUE},
        )
        self.assertContains(response, "Vencida")
        self.assertNotContains(response, ">Futura<")

    def test_filtro_busca_descricao(self):
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Honorários trabalhistas",
            valor_original=Decimal("100"),
            data_vencimento=self.hoje,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(
            reverse("financeiro_cobranca_listar"),
            {"q": "trabalhistas"},
        )
        self.assertContains(response, "Honorários trabalhistas")

    def test_kpis_excluem_canceladas(self):
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Cancelada",
            valor_original=Decimal("9000"),
            data_vencimento=self.hoje + timedelta(days=5),
            status=StatusCobranca.CANCELED,
        )
        from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization

        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=self.hoje)
        self.assertEqual(kpis.a_receber, Decimal("0"))


class CobrancaRecebimentoFase4Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.org_b = _provision_tenant(self.user_b, self.cliente_b)
        self.http = Client()
        self.hoje = timezone.localdate()

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários",
            "valor_original": Decimal("5000.00"),
            "data_vencimento": self.hoje + timedelta(days=15),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_registrar_recebimento_total_quita_cobranca(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        registrar_recebimento(
            c,
            valor=Decimal("5000.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCobranca.PAID)
        self.assertEqual(c.saldo, Decimal("0"))
        self.assertTrue(c.historico.filter(acao="recebimento_registrado").exists())

    def test_registrar_recebimento_parcial(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        registrar_recebimento(
            c,
            valor=Decimal("2000.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCobranca.PARTIALLY_PAID)
        self.assertEqual(c.saldo, Decimal("3000.00"))

    def test_valor_excede_saldo_rejeitado(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        with self.assertRaises(ValidationError):
            registrar_recebimento(
                c,
                valor=Decimal("5000.01"),
                data_recebimento=self.hoje,
                forma_pagamento="pix",
                autor=self.user_a,
                organization=self.org_a,
            )

    def test_estorno_restaura_saldo_e_historico(self):
        from financeiro.services.cobranca_recebimento import (
            estornar_recebimento,
            registrar_recebimento,
        )

        c = self._criar_cobranca()
        r = registrar_recebimento(
            c,
            valor=Decimal("1500.00"),
            data_recebimento=self.hoje,
            forma_pagamento="transferencia",
            autor=self.user_a,
            organization=self.org_a,
        )
        estornar_recebimento(r, autor=self.user_a, motivo="Lançamento duplicado")
        c.refresh_from_db()
        self.assertEqual(c.saldo, Decimal("5000.00"))
        self.assertEqual(c.status, StatusCobranca.PENDING)
        self.assertTrue(c.historico.filter(acao="recebimento_estornado").exists())

    def test_registrar_recebimento_via_post(self):
        c = self._criar_cobranca()
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_recebimento", args=[c.pk]),
            {
                "valor": "3000,00",
                "data_recebimento": self.hoje.isoformat(),
                "forma_pagamento": "pix",
                "referencia": "NSU123",
                "observacao": "Parcial",
            },
        )
        self.assertEqual(response.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.saldo, Decimal("2000.00"))
        self.assertEqual(c.status, StatusCobranca.PARTIALLY_PAID)

    def test_valor_excede_saldo_rejeitado_no_form(self):
        c = self._criar_cobranca()
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_recebimento", args=[c.pk]),
            {
                "valor": "6000,00",
                "data_recebimento": self.hoje.isoformat(),
                "forma_pagamento": "pix",
            },
        )
        self.assertEqual(response.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.saldo, Decimal("5000.00"))

    def test_estornar_recebimento_via_post(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        r = registrar_recebimento(
            c,
            valor=Decimal("1000.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse(
                "financeiro_cobranca_estornar_recebimento",
                args=[c.pk, r.pk],
            ),
            {"motivo": "Erro"},
        )
        self.assertEqual(response.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.saldo, Decimal("5000.00"))
        r.refresh_from_db()
        self.assertIsNotNone(r.cancelado_em)

    def test_estornar_bloqueia_outro_usuario(self):
        c = Cobranca.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_b,
            descricao="Secreta",
            valor_original=Decimal("1000"),
            data_vencimento=self.hoje,
        )
        r = CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_b,
            valor=Decimal("500"),
            data_recebimento=self.hoje,
            registrado_por=self.user_b,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(
            reverse(
                "financeiro_cobranca_estornar_recebimento",
                args=[c.pk, r.pk],
            ),
        )
        self.assertEqual(response.status_code, 404)


class CobrancaParcelamentoFase5Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_calcular_valores_soma_exata_divisao_exata(self):
        from financeiro.services.cobranca_parcelamento import calcular_valores_parcelas

        valores = calcular_valores_parcelas(Decimal("6000.00"), 3)
        self.assertEqual(sum(valores), Decimal("6000.00"))
        self.assertEqual(valores, [Decimal("2000.00")] * 3)

    def test_calcular_valores_soma_exata_com_centavos(self):
        from financeiro.services.cobranca_parcelamento import calcular_valores_parcelas

        valores = calcular_valores_parcelas(Decimal("1000.01"), 3)
        self.assertEqual(sum(valores), Decimal("1000.01"))
        self.assertEqual(valores[-1], Decimal("333.35"))

    def test_criar_parcelamento_gera_n_cobrancas(self):
        from financeiro.services.cobranca_parcelamento import criar_cobrancas_parceladas

        parcelas = criar_cobrancas_parceladas(
            usuario=self.user_a,
            autor=self.user_a,
            cliente=self.cliente_a,
            descricao="Honorários parcelados",
            valor_total=Decimal("6000.00"),
            primeiro_vencimento=self.hoje,
            num_parcelas=3,
            periodicidade="mensal",
            categoria="honorarios",
            organization=self.org_a,
        )
        self.assertEqual(len(parcelas), 3)
        self.assertEqual(Cobranca.objects.filter(usuario=self.user_a).count(), 3)
        grupo = parcelas[0].grupo_parcelamento_id
        self.assertTrue(all(p.grupo_parcelamento_id == grupo for p in parcelas))
        self.assertEqual(sum(p.valor_original for p in parcelas), Decimal("6000.00"))
        self.assertEqual(parcelas[0].parcela_numero, 1)
        self.assertEqual(parcelas[2].parcela_total, 3)
        self.assertTrue(
            parcelas[0].historico.filter(acao="parcelamento_criado").exists()
        )

    def test_vencimentos_mensal_e_quinzenal(self):
        from financeiro.services.cobranca_parcelamento import calcular_vencimentos_parcelas
        from dateutil.relativedelta import relativedelta

        mensais = calcular_vencimentos_parcelas(self.hoje, 3, "mensal")
        self.assertEqual(mensais[1], self.hoje + relativedelta(months=1))
        quinzenais = calcular_vencimentos_parcelas(self.hoje, 3, "quinzenal")
        self.assertEqual(quinzenais[1], self.hoje + timedelta(days=14))

    def test_parcelamento_via_post(self):
        from django.db.models import Sum

        venc = self.hoje + timedelta(days=10)
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_nova"),
            {
                "tipo_lancamento": "parcelada",
                "cliente": self.cliente_a.pk,
                "contrato_referencia": "",
                "descricao": "Plano mensal",
                "valor": "3000,00",
                "data_vencimento": venc.isoformat(),
                "num_parcelas": "3",
                "periodicidade": "mensal",
                "categoria": "honorarios",
                "responsavel": self.user_a.pk,
                "forma_prevista_pagamento": "",
                "observacoes_internas": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cobranca.objects.filter(descricao="Plano mensal").count(), 3)
        total = Cobranca.objects.filter(descricao="Plano mensal").aggregate(
            total=Sum("valor_original")
        )["total"]
        self.assertEqual(total, Decimal("3000.00"))

    def test_detalhe_exibe_parcelas_do_grupo(self):
        from financeiro.services.cobranca_parcelamento import criar_cobrancas_parceladas

        parcelas = criar_cobrancas_parceladas(
            usuario=self.user_a,
            autor=self.user_a,
            cliente=self.cliente_a,
            descricao="Grupo teste",
            valor_total=Decimal("900.00"),
            primeiro_vencimento=self.hoje,
            num_parcelas=3,
            periodicidade="mensal",
            categoria="honorarios",
            organization=self.org_a,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(
            reverse("financeiro_cobranca_detalhe", args=[parcelas[1].pk])
        )
        self.assertContains(response, "Parcelas do plano")
        self.assertContains(response, "2/3")
        self.assertContains(response, "1/3")


class CobrancaContratoFase6Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.org_b = _provision_tenant(self.user_b, self.cliente_b)
        self.http = Client()
        self.hoje = timezone.localdate()

    def test_criar_contrato(self):
        from financeiro.models import Contrato

        contrato = Contrato.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            referencia="00042",
            descricao="Honorários trabalhistas",
            valor_total=Decimal("12000.00"),
            criado_por=self.user_a,
        )
        self.assertEqual(contrato.referencia, "00042")

    def test_contrato_cliente_outro_usuario_rejeitado(self):
        from financeiro.models import Contrato

        contrato = Contrato(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_b,
            referencia="X",
            descricao="Inválido",
            valor_total=Decimal("100"),
        )
        with self.assertRaises(ValidationError):
            contrato.full_clean()

    def test_gerar_cobrancas_parceladas_do_contrato(self):
        from financeiro.models import Contrato
        from financeiro.services.contrato_cobrancas import gerar_cobrancas_do_contrato

        contrato = Contrato.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            referencia="00099",
            descricao="Plano 6x",
            valor_total=Decimal("12000.00"),
            criado_por=self.user_a,
        )
        cobrancas = gerar_cobrancas_do_contrato(
            contrato,
            autor=self.user_a,
            organization=self.org_a,
            tipo_lancamento="parcelada",
            primeiro_vencimento=self.hoje,
            num_parcelas=6,
            periodicidade="mensal",
            categoria="honorarios",
        )
        self.assertEqual(len(cobrancas), 6)
        self.assertEqual(sum(c.valor_original for c in cobrancas), Decimal("12000.00"))
        self.assertTrue(all(c.contrato_id == contrato.pk for c in cobrancas))
        self.assertEqual(contrato.cobrancas.count(), 6)

    def test_gerar_cobrancas_unica_do_contrato(self):
        from financeiro.models import Contrato
        from financeiro.services.contrato_cobrancas import gerar_cobrancas_do_contrato

        contrato = Contrato.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            referencia="00100",
            descricao="À vista",
            valor_total=Decimal("5000.00"),
            criado_por=self.user_a,
        )
        cobrancas = gerar_cobrancas_do_contrato(
            contrato,
            autor=self.user_a,
            organization=self.org_a,
            tipo_lancamento="unica",
            primeiro_vencimento=self.hoje,
            categoria="honorarios",
        )
        self.assertEqual(len(cobrancas), 1)
        self.assertEqual(cobrancas[0].contrato_referencia, "00100")

    def test_contrato_outro_usuario_bloqueado(self):
        from financeiro.models import Contrato

        contrato = Contrato.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_b,
            referencia="SEC",
            descricao="Secreta",
            valor_total=Decimal("1000"),
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_contrato_detalhe", args=[contrato.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cobranca_form_rejeita_contrato_outro_cliente(self):
        from financeiro.models import Contrato

        contrato = Contrato.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            referencia="REF1",
            descricao="Contrato A",
            valor_total=Decimal("1000"),
        )
        outro = Cliente.objects.create(
            user=self.user_a, nome="Pedro", email="pedro@test.com"
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_nova"),
            {
                "tipo_lancamento": "unica",
                "cliente": outro.pk,
                "contrato": contrato.pk,
                "descricao": "Inválida",
                "valor": "500,00",
                "data_vencimento": self.hoje.isoformat(),
                "categoria": "honorarios",
                "responsavel": self.user_a.pk,
                "forma_prevista_pagamento": "",
                "observacoes_internas": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cobranca.objects.filter(descricao="Inválida").exists())

    def test_gerar_cobrancas_via_post(self):
        from financeiro.models import Contrato

        contrato = Contrato.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            referencia="WEB01",
            descricao="Via web",
            valor_total=Decimal("3000.00"),
            criado_por=self.user_a,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_contrato_gerar_cobrancas", args=[contrato.pk]),
            {
                "tipo_lancamento": "parcelada",
                "num_parcelas": "3",
                "periodicidade": "mensal",
                "primeiro_vencimento": (self.hoje + timedelta(days=5)).isoformat(),
                "descricao": "Parcelas contrato",
                "categoria": "honorarios",
                "forma_prevista_pagamento": "",
                "confirmar": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(contrato.cobrancas.count(), 3)


class CobrancaAgendaFase7Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.http = Client()
        self.hoje = timezone.localdate()

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários",
            "valor_original": Decimal("2000.00"),
            "data_vencimento": self.hoje + timedelta(days=5),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_sincronizar_lembrete_cria_compromisso_automatico(self):
        from financeiro.services.cobranca_agenda import (
            compromisso_lembrete_automatico,
            sincronizar_lembrete_cobranca,
        )
        from usuarios.models import Compromisso

        c = self._criar_cobranca()
        sincronizar_lembrete_cobranca(c)
        comp = compromisso_lembrete_automatico(c)
        self.assertIsNotNone(comp)
        self.assertEqual(comp.tipo, "cobranca")
        self.assertEqual(Compromisso.objects.filter(metadados__cobranca_id=c.pk).count(), 1)

    def test_criar_tarefa_cobranca(self):
        from financeiro.services.cobranca_agenda import criar_tarefa_cobranca
        from usuarios.models import Tarefa

        c = self._criar_cobranca()
        t = criar_tarefa_cobranca(c, autor=self.user_a)
        self.assertEqual(t.metadados["cobranca_id"], c.pk)
        self.assertEqual(Tarefa.objects.filter(metadados__cobranca_id=c.pk).count(), 1)
        self.assertTrue(c.historico.filter(acao="agenda_vinculada").exists())

    def test_agendar_via_post(self):
        c = self._criar_cobranca()
        self.http.login(username="adv_a", password="senha123")
        response = self.http.post(
            reverse("financeiro_cobranca_agendar", args=[c.pk]),
            {"tipo": "tarefa"},
        )
        self.assertEqual(response.status_code, 302)
        from usuarios.models import Tarefa

        self.assertEqual(Tarefa.objects.filter(metadados__cobranca_id=c.pk).count(), 1)

    def test_quitar_cobranca_cancela_lembrete_automatico(self):
        from financeiro.services.cobranca_agenda import (
            compromisso_lembrete_automatico,
            sincronizar_lembrete_cobranca,
        )
        from financeiro.services.cobranca_crud import criar_cobranca
        from financeiro.services.cobranca_recebimento import registrar_recebimento
        from usuarios.choices import StatusCompromisso

        c = self._criar_cobranca()
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        sincronizar_lembrete_cobranca(c)
        registrar_recebimento(
            c,
            valor=Decimal("2000.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        comp = compromisso_lembrete_automatico(c)
        self.assertIsNone(comp)
        from usuarios.models import Compromisso

        self.assertTrue(
            Compromisso.objects.filter(
                metadados__cobranca_id=c.pk,
                status=StatusCompromisso.CANCELADO,
            ).exists()
        )

    def test_itens_atencao_inclui_cobranca_vencida(self):
        from usuarios.services.agenda import itens_atencao

        self._criar_cobranca(
            data_vencimento=self.hoje - timedelta(days=3),
            status=StatusCobranca.OVERDUE,
        )
        itens = itens_atencao(self.org_a, ref=self.hoje)
        self.assertTrue(any(i.item_tipo == "cobranca" for i in itens))

    def test_compromisso_cobranca_aparece_na_agenda(self):
        from financeiro.services.cobranca_crud import criar_cobranca

        c = self._criar_cobranca(data_vencimento=self.hoje + timedelta(days=10))
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        from usuarios.tests_helpers import grant_agenda_permissions

        grant_agenda_permissions(self.user_a)
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertContains(response, c.descricao)
        self.assertContains(response, "Cobrança")


class CobrancaInadimplenciaPrevisaoFase8Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_a, nome="Pedro", email="pedro@test.com"
        )
        self.cliente_outro = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a, self.cliente_b)
        self.org_b = _provision_tenant(self.user_b, self.cliente_outro)
        self.http = Client()
        self.hoje = timezone.localdate()

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários",
            "valor_original": Decimal("1000.00"),
            "data_vencimento": self.hoje - timedelta(days=10),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_inadimplencia_faixas_somam_total_vencido(self):
        from financeiro.services.cobranca_inadimplencia import calcular_inadimplencia

        self._criar_cobranca(
            valor_original=Decimal("100.00"),
            data_vencimento=self.hoje - timedelta(days=5),
        )
        self._criar_cobranca(
            cliente=self.cliente_b,
            descricao="Parcela B",
            valor_original=Decimal("200.00"),
            data_vencimento=self.hoje - timedelta(days=45),
        )
        self._criar_cobranca(
            descricao="Antiga",
            valor_original=Decimal("300.00"),
            data_vencimento=self.hoje - timedelta(days=120),
        )
        # Não vencida — não entra
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Futura",
            valor_original=Decimal("999.00"),
            data_vencimento=self.hoje + timedelta(days=10),
            criado_por=self.user_a,
        )
        # Outro tenant — não entra
        Cobranca.objects.create(
            usuario=self.user_b,
            cliente=self.cliente_outro,
            descricao="Outro adv",
            valor_original=Decimal("5000.00"),
            data_vencimento=self.hoje - timedelta(days=20),
            criado_por=self.user_b,
        )

        resumo = calcular_inadimplencia(self.org_a, hoje=self.hoje)
        soma_faixas = sum(f.total for f in resumo.faixas)
        self.assertEqual(soma_faixas, resumo.total_vencido)
        self.assertEqual(resumo.total_vencido, Decimal("600.00"))
        self.assertEqual(resumo.quantidade, 3)
        self.assertEqual(resumo.faixas[0].total, Decimal("100.00"))
        self.assertEqual(resumo.faixas[1].total, Decimal("200.00"))
        self.assertEqual(resumo.faixas[3].total, Decimal("300.00"))
        self.assertEqual(len(resumo.clientes), 2)
        self.assertEqual(resumo.clientes[0].cliente_nome, "João")

    def test_previsao_cumulativos_30_60_90(self):
        from financeiro.services.cobranca_previsao import calcular_previsao

        self._criar_cobranca(
            descricao="Janela 30",
            valor_original=Decimal("100.00"),
            data_vencimento=self.hoje + timedelta(days=10),
        )
        self._criar_cobranca(
            descricao="Janela 60",
            valor_original=Decimal("200.00"),
            data_vencimento=self.hoje + timedelta(days=45),
        )
        self._criar_cobranca(
            descricao="Janela 90",
            valor_original=Decimal("300.00"),
            data_vencimento=self.hoje + timedelta(days=80),
        )
        # Vencida — não entra na previsão
        self._criar_cobranca(
            descricao="Vencida",
            valor_original=Decimal("50.00"),
            data_vencimento=self.hoje - timedelta(days=1),
        )
        # Além de 90 dias — não entra
        Cobranca.objects.create(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_a,
            descricao="Longe",
            valor_original=Decimal("999.00"),
            data_vencimento=self.hoje + timedelta(days=120),
            criado_por=self.user_a,
        )

        resumo = calcular_previsao(self.org_a, hoje=self.hoje)
        self.assertEqual(resumo.cumulativo_30, Decimal("100.00"))
        self.assertEqual(resumo.cumulativo_60, Decimal("300.00"))
        self.assertEqual(resumo.cumulativo_90, Decimal("600.00"))
        self.assertEqual(resumo.total_previsto, Decimal("600.00"))
        self.assertEqual(resumo.quantidade, 3)

    def test_paginas_inadimplencia_e_previsao_renderizam(self):
        self._criar_cobranca(
            data_vencimento=self.hoje - timedelta(days=15),
            descricao="Vencida teste",
        )
        self._criar_cobranca(
            data_vencimento=self.hoje + timedelta(days=20),
            descricao="Previsão teste",
        )
        self.http.login(username="adv_a", password="senha123")

        resp_inad = self.http.get(reverse("financeiro_cobranca_inadimplencia"))
        self.assertEqual(resp_inad.status_code, 200)
        self.assertContains(resp_inad, "Inadimplência")
        self.assertContains(resp_inad, "Vencida teste")
        self.assertContains(resp_inad, "Faixas de atraso")

        resp_prev = self.http.get(reverse("financeiro_cobranca_previsao"))
        self.assertEqual(resp_prev.status_code, 200)
        self.assertContains(resp_prev, "Previsão de recebimentos")
        self.assertContains(resp_prev, "Previsão teste")
        self.assertContains(resp_prev, "Janelas de vencimento")

    def test_listagem_inclui_subnav_abas(self):
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_listar"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inadimplência")
        self.assertContains(response, "Previsão 30/60/90")


class CobrancaDashboardFase9Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_outro = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.org_b = _provision_tenant(self.user_b, self.cliente_outro)
        self.http = Client()
        self.hoje = timezone.localdate()
        grant_billing_permissions(self.user_a)
        grant_billing_permissions(self.user_b)

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários",
            "valor_original": Decimal("1000.00"),
            "data_vencimento": self.hoje + timedelta(days=10),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_dashboard_indicadores_respeitam_tenant(self):
        from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas

        self._criar_cobranca(
            valor_original=Decimal("500.00"),
            data_vencimento=self.hoje - timedelta(days=5),
        )
        self._criar_cobranca(
            descricao="Futura",
            valor_original=Decimal("300.00"),
            data_vencimento=self.hoje + timedelta(days=15),
        )
        Cobranca.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_outro,
            descricao="Outro tenant",
            valor_original=Decimal("9000.00"),
            data_vencimento=self.hoje - timedelta(days=10),
            criado_por=self.user_b,
        )

        resumo = calcular_dashboard_cobrancas(self.org_a, hoje=self.hoje)
        self.assertEqual(resumo.a_receber, Decimal("800.00"))
        self.assertEqual(resumo.vencido, Decimal("500.00"))
        self.assertEqual(resumo.previsao_30, Decimal("300.00"))
        self.assertEqual(resumo.taxa_inadimplencia, Decimal("62.5"))

    def test_taxa_inadimplencia_zero_sem_saldo_aberto(self):
        from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas

        resumo = calcular_dashboard_cobrancas(self.org_a, hoje=self.hoje)
        self.assertEqual(resumo.taxa_inadimplencia, Decimal("0"))

    def test_prazo_medio_recebimento(self):
        from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas

        c = self._criar_cobranca(data_vencimento=self.hoje - timedelta(days=10))
        CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_a,
            organization=self.org_a,
            valor=Decimal("1000.00"),
            data_recebimento=self.hoje - timedelta(days=5),
            registrado_por=self.user_a,
        )
        resumo = calcular_dashboard_cobrancas(self.org_a, hoje=self.hoje)
        self.assertEqual(resumo.prazo_medio_recebimento, Decimal("5.0"))

    def test_dashboard_renderiza_bloco_cobrancas(self):
        self._criar_cobranca(
            data_vencimento=self.hoje - timedelta(days=3),
            descricao="Dashboard vencida",
        )
        self._criar_cobranca(
            data_vencimento=self.hoje + timedelta(days=7),
            descricao="Dashboard futura",
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Honorários e cobranças")
        self.assertContains(response, "Taxa inadimplência")
        self.assertContains(response, "Previsão 30 dias")
        self.assertContains(response, "João")
        self.assertContains(response, "Dashboard futura")

    def test_dashboard_outro_usuario_nao_ve_cobrancas_alheias(self):
        self._criar_cobranca(descricao="Privada A")
        Cobranca.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_outro,
            descricao="Privada B",
            valor_original=Decimal("5000.00"),
            data_vencimento=self.hoje + timedelta(days=5),
            criado_por=self.user_b,
        )
        self.http.login(username="adv_b", password="senha123")
        response = self.http.get(reverse("financeiro_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Privada A")
        self.assertContains(response, "Privada B")


class CobrancaMensagemFase10Tests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a,
            nome="João Silva",
            email="joao@test.com",
            telefone="11999998888",
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.org_b = _provision_tenant(self.user_b, self.cliente_b)
        self.http = Client()
        self.hoje = timezone.localdate()

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários trabalhistas",
            "valor_original": Decimal("5000.00"),
            "data_vencimento": self.hoje - timedelta(days=10),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_mensagem_padrao_inclui_dados_cliente(self):
        from financeiro.services.cobranca_mensagem import (
            gerar_mensagem_padrao,
            montar_contexto_mensagem,
        )

        c = self._criar_cobranca()
        ctx = montar_contexto_mensagem(c, hoje=self.hoje)
        msg = gerar_mensagem_padrao(ctx)
        self.assertIn("João Silva", msg)
        self.assertIn("Honorários trabalhistas", msg)
        self.assertIn("R$ 5.000,00", msg)
        self.assertEqual(ctx.dias_atraso, 10)

    def test_tons_geram_textos_diferentes(self):
        from financeiro.choices import TomMensagemCobranca
        from financeiro.services.cobranca_mensagem import (
            gerar_mensagem_padrao,
            montar_contexto_mensagem,
        )

        c = self._criar_cobranca()
        ctx = montar_contexto_mensagem(c, hoje=self.hoje)
        cordial = gerar_mensagem_padrao(ctx, tom=TomMensagemCobranca.CORDIAL)
        formal = gerar_mensagem_padrao(ctx, tom=TomMensagemCobranca.FORMAL)
        self.assertNotEqual(cordial, formal)
        self.assertIn("Prezado(a)", formal)

    def test_pagina_cobrar_renderiza(self):
        c = self._criar_cobranca()
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_cobrar", args=[c.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cobrar cliente")
        self.assertContains(response, "João Silva")
        self.assertContains(response, "11999998888")
        self.assertContains(response, "Copiar mensagem")

    def test_cobrar_bloqueia_cobranca_quitada(self):
        c = self._criar_cobranca()
        CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_a,
            valor=Decimal("5000.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user_a,
        )
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_cobrar", args=[c.pk]))
        self.assertEqual(response.status_code, 302)

    def test_cobrar_bloqueia_outro_usuario(self):
        c = self._criar_cobranca()
        self.http.login(username="adv_b", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_cobrar", args=[c.pk]))
        self.assertEqual(response.status_code, 404)

    def test_detalhe_exibe_botao_cobrar_cliente(self):
        c = self._criar_cobranca()
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_detalhe", args=[c.pk]))
        self.assertContains(response, "Cobrar cliente")

    def test_gerar_ia_sem_api_key_retorna_erro(self):
        from unittest.mock import patch

        from financeiro.services.cobranca_mensagem import CobrancaMensagemError

        c = self._criar_cobranca()
        self.http.login(username="adv_a", password="senha123")
        with patch(
            "financeiro.views_cobrancas.gerar_mensagem_ia",
            side_effect=CobrancaMensagemError("OPENAI_API_KEY não configurada."),
        ):
            response = self.http.post(
                reverse("financeiro_cobranca_cobrar", args=[c.pk]),
                {"acao": "gerar_ia", "tom": "cordial"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OPENAI_API_KEY")

    def test_gerar_ia_registra_historico(self):
        from unittest.mock import MagicMock, patch

        from financeiro.services.cobranca_mensagem import gerar_mensagem_ia

        c = self._criar_cobranca()
        mock_response = MagicMock()
        mock_response.content.mensagem = "Mensagem IA de teste para João."
        with patch("financeiro.services.cobranca_mensagem.ia_disponivel", return_value=True):
            with patch("financeiro.services.cobranca_mensagem.Agent") as agent_cls:
                agent_cls.return_value.run.return_value = mock_response
                msg = gerar_mensagem_ia(c, tom="cordial", autor=self.user_a)
        self.assertIn("Mensagem IA de teste", msg)
        self.assertTrue(c.historico.filter(acao="mensagem_cobranca").exists())


class CobrancaRbacFase11Tests(TestCase):
    GRUPO_RESTRITO = "Assistente — sem financeiro"
    GRUPO_COMPLETO = "Financeiro — acesso completo"

    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_restrito = User.objects.create_user(username="assistente", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.http = Client()
        self.hoje = timezone.localdate()

        ct_c = ContentType.objects.get(app_label="financeiro", model="cobranca")
        ct_r = ContentType.objects.get(app_label="financeiro", model="cobrancarecebimento")
        perms_completas = Permission.objects.filter(
            content_type__in=[ct_c, ct_r],
            codename__in=[
                "view_cobrancas",
                "create_cobrancas",
                "edit_cobrancas",
                "cancel_cobrancas",
                "view_relatorios_cobrancas",
                "view_recebimentos",
                "create_recebimentos",
            ],
        )
        self.grupo_completo, _ = Group.objects.get_or_create(name=self.GRUPO_COMPLETO)
        self.grupo_completo.permissions.set(perms_completas)
        self.grupo_restrito, _ = Group.objects.get_or_create(name=self.GRUPO_RESTRITO)
        self.grupo_restrito.permissions.clear()
        self.user_restrito.groups.add(self.grupo_restrito)

    def _criar_cobranca(self, **kwargs):
        defaults = {
            "usuario": self.user_a,
            "cliente": self.cliente_a,
            "descricao": "Honorários",
            "valor_original": Decimal("1000.00"),
            "data_vencimento": self.hoje - timedelta(days=5),
            "criado_por": self.user_a,
            "organization": getattr(self, "org_a", None),
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    def test_usuario_sem_grupo_sem_perm_e_negado(self):
        user = User.objects.create_user(username="sem_grupo", password="senha123")
        self.http.login(username="sem_grupo", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_listar"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_usuario_grupo_sem_perm_bloqueado(self):
        self.http.login(username="assistente", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_listar"))
        self.assertEqual(response.status_code, 302)

    def test_usuario_grupo_com_perm_acessa(self):
        self.user_restrito.groups.remove(self.grupo_restrito)
        self.user_restrito.groups.add(self.grupo_completo)
        self._criar_cobranca(usuario=self.user_restrito, criado_por=self.user_restrito)
        cliente = Cliente.objects.create(
            user=self.user_restrito, nome="Maria", email="m@test.com"
        )
        Cobranca.objects.filter(usuario=self.user_restrito).update(cliente=cliente)
        self.http.login(username="assistente", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_listar"))
        self.assertEqual(response.status_code, 200)

    def test_auditoria_registra_vencimento_automatico(self):
        from financeiro.services.cobranca_listagem import sincronizar_statuses
        from financeiro.services.cobranca_listagem import queryset_anotado

        c = self._criar_cobranca(data_vencimento=self.hoje - timedelta(days=2))
        qs = list(queryset_anotado(self.user_a).filter(pk=c.pk))
        sincronizar_statuses(qs, hoje=self.hoje)
        self.assertTrue(c.historico.filter(acao="vencida").exists())
        # Não duplica ao sincronizar novamente
        sincronizar_statuses(qs, hoje=self.hoje)
        self.assertEqual(c.historico.filter(acao="vencida").count(), 1)

    def test_pagina_auditoria_renderiza(self):
        from financeiro.services.cobranca_crud import criar_cobranca

        c = self._criar_cobranca()
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        self.http.login(username="adv_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_auditoria"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cobrança criada")

    def test_ficha_cliente_oculta_financeiro_sem_perm(self):
        from django.contrib.auth.models import Group

        user_dono = User.objects.create_user(username="dono", password="senha123")
        user_assist = User.objects.create_user(username="assist2", password="senha123")
        org = Organization.objects.create(name="Org Assist Fin")
        Membership.objects.create(
            user=user_assist,
            organization=org,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        cliente = Cliente.objects.create(user=user_dono, nome="Cliente X", email="x@test.com")
        g, _ = Group.objects.get_or_create(name="Equipe restrita")
        g.permissions.clear()
        user_assist.groups.add(g)
        # Assistente não acessa cliente de outro user anyway - test dono vs assist on same... 
        # Test: user in restricted group viewing own client - create client for assist
        cliente_assist = Cliente.objects.create(
            user=user_assist, nome="Y", email="y@test.com", organization=org
        )
        self.http.login(username="assist2", password="senha123")
        response = self.http.get(reverse("cliente", kwargs={"id": cliente_assist.id}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Financeiro")


class CobrancaSpecFase12Tests(TestCase):
    """
    Suite dos 18 testes de aceitação do módulo de Cobranças (spec §39).
    Cada método corresponde a um TESTE numerado do prompt original.
    """

    def setUp(self):
        from django.contrib.auth.models import Group

        self.user_a = User.objects.create_user(username="spec_a", password="senha123")
        self.user_b = User.objects.create_user(username="spec_b", password="senha123")
        self.user_sem_perm = User.objects.create_user(username="spec_restrito", password="senha123")
        self.cliente_a = Cliente.objects.create(
            user=self.user_a, nome="João", email="joao@test.com"
        )
        self.cliente_b = Cliente.objects.create(
            user=self.user_b, nome="Maria", email="maria@test.com"
        )
        self.org_a = _provision_tenant(self.user_a, self.cliente_a)
        self.org_b = _provision_tenant(self.user_b, self.cliente_b)
        self.http = Client()
        self.hoje = timezone.localdate()

        grupo_restrito, _ = Group.objects.get_or_create(name="Spec — sem financeiro")
        grupo_restrito.permissions.clear()
        self.user_sem_perm.groups.add(grupo_restrito)

    def _criar_cobranca(self, usuario=None, cliente=None, **kwargs):
        defaults = {
            "usuario": usuario or self.user_a,
            "cliente": cliente or self.cliente_a,
            "descricao": "Honorários spec",
            "valor_original": Decimal("5000.00"),
            "data_vencimento": self.hoje + timedelta(days=15),
            "criado_por": usuario or self.user_a,
            "organization": self.org_a if (usuario or self.user_a) == self.user_a else self.org_b,
        }
        defaults.update(kwargs)
        return Cobranca.objects.create(**defaults)

    # TESTE 01 — Criar cobrança.
    def test_spec_01_criar_cobranca(self):
        from financeiro.services.cobranca_crud import criar_cobranca

        c = self._criar_cobranca()
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        self.assertTrue(Cobranca.objects.filter(pk=c.pk, usuario=self.user_a).exists())
        self.assertEqual(c.status, StatusCobranca.PENDING)

    # TESTE 02 — Valor zero ou negativo rejeitado.
    def test_spec_02_valor_zero_ou_negativo_rejeitado(self):
        for valor in (Decimal("0"), Decimal("-1")):
            with self.subTest(valor=valor):
                c = Cobranca(
                    usuario=self.user_a,
                    cliente=self.cliente_a,
                    descricao="X",
                    valor_original=valor,
                    data_vencimento=self.hoje,
                )
                with self.assertRaises(ValidationError):
                    c.full_clean()

    # TESTE 03 — Cliente de outro tenant rejeitado.
    def test_spec_03_cliente_outro_tenant_rejeitado(self):
        c = Cobranca(
            usuario=self.user_a,
            organization=self.org_a,
            cliente=self.cliente_b,
            descricao="X",
            valor_original=Decimal("100"),
            data_vencimento=self.hoje,
        )
        with self.assertRaises(ValidationError):
            c.full_clean()

    # TESTE 04 — Contrato de outro tenant rejeitado.
    def test_spec_04_contrato_outro_tenant_rejeitado(self):
        from financeiro.forms import CobrancaForm
        from financeiro.models import Contrato

        contrato_b = Contrato.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_b,
            referencia="OUT99",
            descricao="Contrato B",
            valor_total=Decimal("3000"),
            criado_por=self.user_b,
        )
        form = CobrancaForm(
            data={
                "cliente": self.cliente_a.pk,
                "contrato": contrato_b.pk,
                "descricao": "Teste",
                "valor": "1000,00",
                "data_vencimento": self.hoje.isoformat(),
                "categoria": "honorarios",
                "tipo_lancamento": "unica",
            },
            usuario=self.user_a,
            organization=self.org_a,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("contrato", form.errors)

    # TESTE 05 — Usuário sem permissão não acessa cobrança.
    def test_spec_05_usuario_sem_permissao_nao_acessa(self):
        c = self._criar_cobranca()
        self.http.login(username="spec_restrito", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_detalhe", args=[c.pk]))
        self.assertEqual(response.status_code, 302)

    # TESTE 06 — Pagamento total muda status para recebido.
    def test_spec_06_pagamento_total_quita_cobranca(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        registrar_recebimento(
            c,
            valor=Decimal("5000.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCobranca.PAID)
        self.assertEqual(saldo_cobranca(c), Decimal("0"))

    # TESTE 07 — Pagamento parcial muda status para parcialmente recebido.
    def test_spec_07_pagamento_parcial_status_parcial(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        registrar_recebimento(
            c,
            valor=Decimal("2000.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        c.refresh_from_db()
        self.assertEqual(c.status, StatusCobranca.PARTIALLY_PAID)

    # TESTE 08 — Saldo é calculado corretamente.
    def test_spec_08_saldo_calculado_corretamente(self):
        c = self._criar_cobranca(valor_original=Decimal("5000.00"))
        CobrancaRecebimento.objects.create(
            cobranca=c,
            usuario=self.user_a,
            valor=Decimal("1500.00"),
            data_recebimento=self.hoje,
            registrado_por=self.user_a,
        )
        self.assertEqual(saldo_cobranca(c), Decimal("3500.00"))

    # TESTE 09 — Cobrança vencida muda para vencido.
    def test_spec_09_cobranca_vencida_status_overdue(self):
        c = self._criar_cobranca(data_vencimento=self.hoje - timedelta(days=1))
        self.assertEqual(
            resolver_status_cobranca(c, hoje=self.hoje),
            StatusCobranca.OVERDUE,
        )

    # TESTE 10 — Cobrança cancelada não entra em total a receber.
    def test_spec_10_cancelada_fora_do_total_a_receber(self):
        from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization

        self._criar_cobranca(status=StatusCobranca.CANCELED)
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=self.hoje)
        self.assertEqual(kpis.a_receber, Decimal("0"))

    # TESTE 11 — Pagamento não pode exceder saldo.
    def test_spec_11_pagamento_excede_saldo_rejeitado(self):
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        with self.assertRaises(ValidationError):
            registrar_recebimento(
                c,
                valor=Decimal("5000.01"),
                data_recebimento=self.hoje,
                forma_pagamento="pix",
                autor=self.user_a,
                organization=self.org_a,
            )

    # TESTE 12 — Parcelamento soma exatamente o valor total.
    def test_spec_12_parcelamento_soma_valor_total(self):
        from financeiro.services.cobranca_parcelamento import calcular_valores_parcelas

        valores = calcular_valores_parcelas(Decimal("1000.00"), 3)
        self.assertEqual(sum(valores), Decimal("1000.00"))
        self.assertEqual(len(valores), 3)

    # TESTE 13 — Tenant A não acessa cobrança B pela URL.
    def test_spec_13_tenant_a_nao_acessa_cobranca_b(self):
        c = self._criar_cobranca(usuario=self.user_b, cliente=self.cliente_b, criado_por=self.user_b)
        self.http.login(username="spec_a", password="senha123")
        response = self.http.get(reverse("financeiro_cobranca_detalhe", args=[c.pk]))
        self.assertEqual(response.status_code, 404)

    # TESTE 14 — Dashboard usa apenas dados do tenant atual.
    def test_spec_14_dashboard_apenas_tenant_atual(self):
        from financeiro.services.cobranca_dashboard import calcular_dashboard_cobrancas

        self._criar_cobranca(valor_original=Decimal("100.00"))
        Cobranca.objects.create(
            usuario=self.user_b,
            organization=self.org_b,
            cliente=self.cliente_b,
            descricao="Outro tenant",
            valor_original=Decimal("9999.00"),
            data_vencimento=self.hoje + timedelta(days=5),
            criado_por=self.user_b,
        )
        resumo = calcular_dashboard_cobrancas(self.org_a, hoje=self.hoje)
        self.assertEqual(resumo.a_receber, Decimal("100.00"))

    # TESTE 15 — Recebimentos alimentam indicadores corretamente.
    def test_spec_15_recebimentos_alimentam_indicadores(self):
        from financeiro.services.cobranca_listagem import calcular_kpis_cobrancas_organization
        from financeiro.services.cobranca_recebimento import registrar_recebimento

        c = self._criar_cobranca()
        registrar_recebimento(
            c,
            valor=Decimal("2500.00"),
            data_recebimento=self.hoje,
            forma_pagamento="pix",
            autor=self.user_a,
            organization=self.org_a,
        )
        kpis = calcular_kpis_cobrancas_organization(self.org_a, hoje=self.hoje)
        self.assertEqual(kpis.recebido_mes, Decimal("2500.00"))

    # TESTE 16 — Histórico registra alterações.
    def test_spec_16_historico_registra_alteracoes(self):
        from financeiro.services.cobranca_crud import atualizar_cobranca, criar_cobranca

        c = self._criar_cobranca()
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        venc_anterior = c.data_vencimento
        c.data_vencimento = self.hoje + timedelta(days=30)
        c.save(update_fields=["data_vencimento"])
        atualizar_cobranca(
            c,
            autor=self.user_a,
            vencimento_anterior=venc_anterior,
            responsavel_anterior_id=c.responsavel_id,
        )
        self.assertTrue(c.historico.filter(acao="vencimento_alterado").exists())
        self.assertTrue(c.historico.filter(acao="editada").exists())

    # TESTE 17 — Cancelamento não apaga histórico.
    def test_spec_17_cancelamento_preserva_historico(self):
        from financeiro.services.cobranca_crud import cancelar_cobranca, criar_cobranca

        c = self._criar_cobranca()
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        qtd_antes = c.historico.count()
        self.assertGreaterEqual(qtd_antes, 1)
        cancelar_cobranca(c, autor=self.user_a, motivo="Acordo")
        self.assertGreater(c.historico.count(), qtd_antes)
        self.assertTrue(c.historico.filter(acao="criada").exists())
        self.assertTrue(c.historico.filter(acao="cancelada").exists())

    # TESTE 18 — Lembretes aparecem na Agenda correta.
    def test_spec_18_lembrete_aparece_na_agenda(self):
        from financeiro.services.cobranca_agenda import sincronizar_lembrete_cobranca
        from financeiro.services.cobranca_crud import criar_cobranca
        from usuarios.models import Compromisso

        c = self._criar_cobranca(data_vencimento=self.hoje + timedelta(days=10))
        criar_cobranca(c, autor=self.user_a, organization=self.org_a)
        sincronizar_lembrete_cobranca(c)
        self.assertTrue(
            Compromisso.objects.filter(
                user=self.user_a,
                metadados__cobranca_id=c.pk,
            ).exists()
        )
        from usuarios.tests_helpers import grant_agenda_permissions

        grant_agenda_permissions(self.user_a)
        self.http.login(username="spec_a", password="senha123")
        response = self.http.get(reverse("agenda"), {"view": "lista"})
        self.assertContains(response, c.descricao)
