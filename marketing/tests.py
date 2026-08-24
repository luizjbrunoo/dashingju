from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from marketing.choices import StatusConteudo, StatusIdeia, StatusIntegracao
from marketing.models import ContentIdea, ContentItem, ContentPerformance, MarketingIntegracao
from marketing.services.compliance import ComplianceService

User = get_user_model()


class ConteudoIsolamentoTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="adv_a", password="senha123")
        self.user_b = User.objects.create_user(username="adv_b", password="senha123")
        self.item_a = ContentItem.objects.create(
            usuario=self.user_a,
            titulo="Conteúdo A",
            canal="blog",
            corpo="Texto original do artigo sobre direitos trabalhistas.",
        )
        self.ideia_a = ContentIdea.objects.create(
            usuario=self.user_a,
            titulo="Ideia A",
            canal_sugerido="instagram",
        )
        self.client = Client()

    def test_editar_outro_usuario_retorna_404(self):
        self.client.login(username="adv_b", password="senha123")
        url = reverse("marketing_conteudo_editar", args=[self.item_a.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_biblioteca_nao_lista_conteudo_de_outro_usuario(self):
        self.client.login(username="adv_b", password="senha123")
        url = reverse("marketing_conteudo_biblioteca")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Conteúdo A")

    def test_dashboard_requer_login(self):
        url = reverse("marketing_conteudo_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/usuarios/login/", response.url)

    def test_perfil_cria_registro_unico(self):
        self.client.login(username="adv_a", password="senha123")
        url = reverse("marketing_conteudo_perfil")
        response = self.client.post(
            url,
            {
                "nome_escritorio": "Escritório A",
                "descricao": "",
                "areas_atuacao": "Trabalhista",
                "publico": "",
                "regiao": "",
                "tom_voz": "profissional",
                "diferenciais": "",
                "palavras_preferidas": "",
                "palavras_evitar": "",
                "ctas_permitidas": "",
                "observacoes_institucionais": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.client.get(url)
        self.assertEqual(self.user_a.contentprofile_marketing.count(), 1)

    def test_enviar_revisao_altera_status(self):
        self.client.login(username="adv_a", password="senha123")
        url = reverse("marketing_conteudo_editar", args=[self.item_a.pk])
        data = self._payload_item()
        data["acao"] = "enviar_revisao"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.status, StatusConteudo.EM_REVISAO)

    def test_ideia_outro_usuario_404(self):
        self.client.login(username="adv_b", password="senha123")
        url = reverse("marketing_conteudo_ideia_criar", args=[self.ideia_a.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_ideias_nao_lista_de_outro_usuario(self):
        self.client.login(username="adv_b", password="senha123")
        response = self.client.get(reverse("marketing_conteudo_ideias"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Ideia A")

    def test_ideia_descartar(self):
        self.client.login(username="adv_a", password="senha123")
        url = reverse("marketing_conteudo_ideia_descartar", args=[self.ideia_a.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.ideia_a.refresh_from_db()
        self.assertEqual(self.ideia_a.status, StatusIdeia.DESCARTADA)

    def test_reaproveitar_outro_usuario_404(self):
        self.client.login(username="adv_b", password="senha123")
        url = reverse("marketing_conteudo_reaproveitar", args=[self.item_a.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    @patch("marketing.views_conteudo.ReaproveitamentoService.reaproveitar")
    def test_reaproveitar_cria_derivados(self, mock_reaproveitar):
        derivado = ContentItem.objects.create(
            usuario=self.user_a,
            titulo="Derivado LinkedIn",
            canal="linkedin",
            origem=self.item_a,
        )
        mock_reaproveitar.return_value = [derivado]
        self.client.login(username="adv_a", password="senha123")
        url = reverse("marketing_conteudo_reaproveitar", args=[self.item_a.pk])
        response = self.client.post(url, {"formatos": ["linkedin_post"]})
        self.assertEqual(response.status_code, 302)
        mock_reaproveitar.assert_called_once()

    def test_reaproveitar_sem_texto_mostra_aviso(self):
        item_vazio = ContentItem.objects.create(
            usuario=self.user_a,
            titulo="Vazio",
            canal="blog",
        )
        self.client.login(username="adv_a", password="senha123")
        url = reverse("marketing_conteudo_reaproveitar", args=[item_vazio.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "não possui texto")

    def test_verificar_conteudo_cria_checklist(self):
        self.client.login(username="adv_a", password="senha123")
        url = reverse("marketing_conteudo_editar", args=[self.item_a.pk])
        data = self._payload_item()
        data["acao"] = "verificar_conteudo"
        data["corpo"] = "Informações educativas sobre direitos do trabalhador."
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.item_a.aprovacoes.count(), 1)
        checklist = self.item_a.aprovacoes.first().checklist
        self.assertTrue(any(c["rotulo"] for c in checklist))

    def test_verificar_detecta_promessa(self):
        service = ComplianceService()
        item = ContentItem.objects.create(
            usuario=self.user_a,
            titulo="Teste",
            canal="instagram",
            corpo="Garantimos vitória no seu processo trabalhista.",
        )
        approval = service.verificar(item, user=self.user_a)
        promessa = next(c for c in approval.checklist if "promessa" in c["rotulo"].lower())
        self.assertEqual(promessa["status"], "revisar")

    def test_analytics_estado_vazio_sem_dados(self):
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("marketing_conteudo_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conecte suas contas")

    def test_analytics_exibe_metricas_reais(self):
        item = ContentItem.objects.create(
            usuario=self.user_a,
            titulo="Publicado",
            canal="blog",
            status=StatusConteudo.PUBLICADO,
        )
        ContentPerformance.objects.create(
            content_item=item,
            visualizacoes=100,
            engajamento=10,
        )
        self.client.login(username="adv_a", password="senha123")
        response = self.client.get(reverse("marketing_conteudo_analytics"))
        self.assertContains(response, "100")
        self.assertContains(response, "Top conteúdos")

    def test_integracao_isolamento(self):
        MarketingIntegracao.objects.create(
            usuario=self.user_a,
            plataforma="instagram",
            status=StatusIntegracao.PENDENTE,
        )
        self.client.login(username="adv_b", password="senha123")
        response = self.client.get(reverse("marketing_conteudo_integracoes"))
        self.assertNotContains(response, "Solicitação registrada")

    def _payload_item(self):
        return {
            "titulo": "Conteúdo A",
            "tema": "",
            "area_juridica": "",
            "canal": "blog",
            "formato": "",
            "objetivo": "",
            "publico": "",
            "palavra_chave": "",
            "tom": "profissional",
            "cta": "",
            "status": StatusConteudo.RASCUNHO,
            "data_planejada": "",
            "corpo": "Texto teste",
            "duracao_estimada": "",
            "tamanho_aproximado": "",
            "num_slides": "",
            "conteudo_origem": "",
        }
