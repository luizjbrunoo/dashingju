import inspect
import json
import os
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from ia.agents import SecretariaAI
from ia.models import Pergunta
from ia.wrapper_evolution_api import EvolutionAPI
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos


WEBHOOK_PAYLOAD = {
    "phone": "5511999999999",
    "data": {
        "key": {"remoteJid": "5511999999999@s.whatsapp.net"},
        "message": {"extendedTextMessage": {"text": "ola"}},
    },
}


class IaSegurancaTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="pass-a-123")
        self.user_b = User.objects.create_user(username="user_b", password="pass-b-123")
        self.org_a = Organization.objects.create(name="IA Org A")
        self.org_b = Organization.objects.create(name="IA Org B")
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
        self.cli_a = Cliente.objects.create(
            nome="Cliente A",
            email="a@example.com",
            user=self.user_a,
            organization=self.org_a,
        )
        self.cli_b = Cliente.objects.create(
            nome="Cliente B",
            email="b@example.com",
            user=self.user_b,
            organization=self.org_b,
        )
        self.doc_a = Documentos.objects.create(
            cliente=self.cli_a,
            tipo="O",
            arquivo=SimpleUploadedFile("a.txt", b"peca-a"),
            data_upload=timezone.now(),
            content="texto peca A",
        )
        self.doc_b = Documentos.objects.create(
            cliente=self.cli_b,
            tipo="O",
            arquivo=SimpleUploadedFile("b.txt", b"peca-b"),
            data_upload=timezone.now(),
            content="texto peca B",
        )
        self.perg_a = Pergunta.objects.create(pergunta="pergunta A", cliente=self.cli_a)
        self.perg_b = Pergunta.objects.create(pergunta="pergunta B", cliente=self.cli_b)

    def _login_a(self):
        self.client.force_login(self.user_a)

    def test_anonimo_nao_acessa_chat(self):
        resp = self.client.get(reverse("chat", args=[self.cli_a.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/usuarios/login/", resp.url)

    def test_anonimo_nao_acessa_stream(self):
        resp = self.client.post(
            reverse("stream_resposta"), {"id_pergunta": self.perg_a.id}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/usuarios/login/", resp.url)

    def test_anonimo_nao_acessa_referencias(self):
        resp = self.client.get(reverse("ver_referencias", args=[self.perg_a.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/usuarios/login/", resp.url)

    def test_anonimo_nao_acessa_analise(self):
        resp = self.client.get(reverse("analise_jurisprudencia", args=[self.doc_a.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/usuarios/login/", resp.url)

    def test_anonimo_nao_processa_analise(self):
        resp = self.client.post(reverse("processar_analise", args=[self.doc_a.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/usuarios/login/", resp.url)

    def test_user_a_acessa_cliente_a(self):
        self._login_a()
        resp = self.client.get(reverse("chat", args=[self.cli_a.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente A")

    def test_user_a_nao_acessa_cliente_b(self):
        self._login_a()
        resp = self.client.get(reverse("chat", args=[self.cli_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_user_a_nao_acessa_pergunta_b(self):
        self._login_a()
        resp = self.client.get(reverse("ver_referencias", args=[self.perg_b.id]))
        self.assertEqual(resp.status_code, 404)
        resp_stream = self.client.post(
            reverse("stream_resposta"), {"id_pergunta": self.perg_b.id}
        )
        self.assertEqual(resp_stream.status_code, 404)

    def test_user_a_nao_acessa_documento_b(self):
        self._login_a()
        resp = self.client.get(reverse("analise_jurisprudencia", args=[self.doc_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_user_a_nao_processa_documento_b(self):
        self._login_a()
        with patch("ia.views.JurisprudenciaAI") as mock_j:
            resp = self.client.post(reverse("processar_analise", args=[self.doc_b.id]))
            self.assertEqual(resp.status_code, 404)
            mock_j.assert_not_called()

    def test_csrf_fluxo_legitimo_chat(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user_a)
        pagina = csrf_client.get(reverse("chat", args=[self.cli_a.id]))
        self.assertEqual(pagina.status_code, 200)
        token = pagina.cookies["csrftoken"].value
        sem_token = csrf_client.post(
            reverse("chat", args=[self.cli_a.id]), {"pergunta": "oi"}
        )
        self.assertEqual(sem_token.status_code, 403)
        com_token = csrf_client.post(
            reverse("chat", args=[self.cli_a.id]),
            {"pergunta": "oi"},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(com_token.status_code, 200)
        self.assertIn("id", com_token.json())

    def test_webhook_sem_secret_403(self):
        env = os.environ.copy()
        env.pop("IA_WEBHOOK_SECRET", None)
        env.pop("IA_WHATSAPP_USER_ID", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("ia.views.SecretariaAI.build_agent") as mock_build:
                resp = self.client.post(
                    reverse("webhook_whatsapp"),
                    data=json.dumps(WEBHOOK_PAYLOAD),
                    content_type="application/json",
                )
                self.assertEqual(resp.status_code, 403)
                mock_build.assert_not_called()

    def test_webhook_secret_incorreto_403(self):
        env = os.environ.copy()
        env["IA_WEBHOOK_SECRET"] = "segredo-teste"
        env["IA_WHATSAPP_USER_ID"] = str(self.user_a.pk)
        with patch.dict(os.environ, env, clear=True):
            with patch("ia.views.SecretariaAI.build_agent") as mock_build:
                resp = self.client.post(
                    reverse("webhook_whatsapp"),
                    data=json.dumps(WEBHOOK_PAYLOAD),
                    content_type="application/json",
                    HTTP_X_WEBHOOK_SECRET="outro",
                )
                self.assertEqual(resp.status_code, 403)
                mock_build.assert_not_called()

    def test_webhook_secret_correto_fluxo_permitido(self):
        env = os.environ.copy()
        env["IA_WEBHOOK_SECRET"] = "segredo-teste"
        env["IA_WHATSAPP_USER_ID"] = str(self.user_a.pk)
        mock_agent = MagicMock()
        mock_agent.run.return_value = MagicMock(content="ok")
        with patch.dict(os.environ, env, clear=True):
            with patch("ia.views.SecretariaAI.build_agent", return_value=mock_agent) as mock_build:
                resp = self.client.post(
                    reverse("webhook_whatsapp"),
                    data=json.dumps(WEBHOOK_PAYLOAD),
                    content_type="application/json",
                    HTTP_X_WEBHOOK_SECRET="segredo-teste",
                )
                self.assertEqual(resp.status_code, 200)
                mock_build.assert_called_once()
                self.assertEqual(mock_build.call_args.kwargs["user_id"], self.user_a.pk)
                mock_agent.run.assert_called_once()

    def test_webhook_sem_user_id_sem_efeitos(self):
        env = os.environ.copy()
        env["IA_WEBHOOK_SECRET"] = "segredo-teste"
        env.pop("IA_WHATSAPP_USER_ID", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("ia.views.SecretariaAI.build_agent") as mock_build:
                resp = self.client.post(
                    reverse("webhook_whatsapp"),
                    data=json.dumps(WEBHOOK_PAYLOAD),
                    content_type="application/json",
                    HTTP_X_WEBHOOK_SECRET="segredo-teste",
                )
                self.assertEqual(resp.status_code, 403)
                mock_build.assert_not_called()

    def test_webhook_user_invalido_sem_efeitos(self):
        env = os.environ.copy()
        env["IA_WEBHOOK_SECRET"] = "segredo-teste"
        env["IA_WHATSAPP_USER_ID"] = "999999"
        with patch.dict(os.environ, env, clear=True):
            with patch("ia.views.SecretariaAI.build_agent") as mock_build:
                resp = self.client.post(
                    reverse("webhook_whatsapp"),
                    data=json.dumps(WEBHOOK_PAYLOAD),
                    content_type="application/json",
                    HTTP_X_WEBHOOK_SECRET="segredo-teste",
                )
                self.assertEqual(resp.status_code, 403)
                mock_build.assert_not_called()

    def test_webhook_json_invalido_400(self):
        env = os.environ.copy()
        env["IA_WEBHOOK_SECRET"] = "segredo-teste"
        env["IA_WHATSAPP_USER_ID"] = str(self.user_a.pk)
        with patch.dict(os.environ, env, clear=True):
            with patch("ia.views.SecretariaAI.build_agent") as mock_build:
                resp = self.client.post(
                    reverse("webhook_whatsapp"),
                    data="nao-json",
                    content_type="application/json",
                    HTTP_X_WEBHOOK_SECRET="segredo-teste",
                )
                self.assertEqual(resp.status_code, 400)
                mock_build.assert_not_called()

    def test_build_agent_nao_tem_fallback_user_1(self):
        param = inspect.signature(SecretariaAI.build_agent).parameters["user_id"]
        self.assertIs(param.default, inspect.Parameter.empty)
        with self.assertRaises(TypeError):
            SecretariaAI.build_agent(session_id="x")

    def test_ausencia_env_nao_usa_fallback_hardcoded(self):
        env = os.environ.copy()
        env.pop("DATAJUD_API_KEY", None)
        env.pop("EVOLUTION_INSTANCE_KEY", None)
        env.pop("EVOLUTION_LOGIN_EMAIL", None)
        env.pop("EVOLUTION_LOGIN_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            api = EvolutionAPI()
            self.assertEqual(api._API_KEY.get("dashing") or "", "")
            with patch("ia.agents.requests.post") as mock_post:
                from ia.agents import search_datajud_api

                resultado = search_datajud_api.entrypoint(
                    tribunal="tjsp", process_number="00008323520184013202"
                )
                mock_post.assert_not_called()
                self.assertIn("consulta_indisponivel", resultado)

    def test_erro_interno_nao_devolve_segredo(self):
        self._login_a()
        with patch(
            "ia.views.SecretariaAI.build_agent",
            side_effect=RuntimeError("super-secret-token"),
        ):
            resp = self.client.post(
                reverse("stream_resposta"), {"id_pergunta": self.perg_a.id}
            )
            corpo = b"".join(resp.streaming_content).decode()
            self.assertNotIn("super-secret-token", corpo)
            self.assertNotIn("Traceback", corpo)
            self.assertIn("Não foi possível gerar a resposta.", corpo)

    def test_erro_analise_nao_devolve_excecao(self):
        self._login_a()
        with patch("ia.views.JurisprudenciaAI") as mock_j:
            mock_j.return_value.run.side_effect = RuntimeError("sk-secret-value")
            resp = self.client.post(
                reverse("processar_analise", args=[self.doc_a.id]), follow=True
            )
            self.assertEqual(resp.status_code, 200)
            self.assertNotContains(resp, "sk-secret-value")
            self.assertContains(resp, "Falha na análise.")
