"""Chat/WhatsApp tenant-scoped e ausência de LanceDB no import."""

import inspect
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ia.agents import SecretariaAI
from ia.models import Pergunta
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente


class ImportLanceSideEffectTests(SimpleTestCase):
    def test_agents_nao_instancia_lancedb_nem_empresa(self):
        import ia.agents as agents
        import ia.views as views

        source = inspect.getsource(agents)
        self.assertNotIn("LanceDb", source)
        self.assertNotIn("OpenAIEmbedder", source)
        self.assertNotIn("VECTOR_DB_TABLE", source)
        self.assertNotIn("VECTOR_DB_URI", source)
        self.assertIsNone(getattr(SecretariaAI, "knowledge", None))
        views_src = inspect.getsource(views)
        self.assertNotIn("LanceDb", views_src)
        self.assertNotIn("empresa.lance", views_src)

    def test_build_agent_sem_organization_nao_liga_knowledge(self):
        with patch("ia.agents.Agent") as mock_agent:
            with patch("ia.agents.OpenAIChat"):
                SecretariaAI.build_agent(session_id="x", user_id=1)
        kwargs = mock_agent.call_args.kwargs
        self.assertIsNone(kwargs.get("knowledge"))
        self.assertFalse(kwargs.get("search_knowledge"))
        self.assertIn("Não há Organization resolvida", kwargs.get("instructions", ""))


class ChatWhatsappRagTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Chat Org A")
        self.org_b = Organization.objects.create(name="Chat Org B")
        self.user_a = User.objects.create_user("chat_a", password="senha123")
        self.user_b = User.objects.create_user("chat_b", password="senha123")
        self.user_amb = User.objects.create_user("chat_amb", password="senha123")
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
        Membership.objects.create(
            user=self.user_amb,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.user_amb,
            organization=self.org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.cli_a = Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="Chat Cliente A",
            email="chat.a@ex.test",
        )
        self.cli_b = Cliente.objects.create(
            user=self.user_b,
            organization=self.org_b,
            nome="Chat Cliente B",
            email="chat.b@ex.test",
        )
        self.perg_a = Pergunta.objects.create(pergunta="qual contrato A?", cliente=self.cli_a)
        self.perg_b = Pergunta.objects.create(pergunta="qual contrato B?", cliente=self.cli_b)

    def _agent(self):
        mock_agent = MagicMock()
        mock_agent.run.return_value = MagicMock(content="ok")
        return mock_agent

    def test_chat_org_a_so_recebe_contexto_a(self):
        self.client.force_login(self.user_a)
        mock_agent = self._agent()
        with patch("ia.views.retrieve_tenant_context", return_value="CTX_A") as mock_ctx:
            with patch("ia.views.SecretariaAI.build_agent", return_value=mock_agent) as mock_build:
                resp = self.client.post(
                    reverse("stream_resposta"), {"id_pergunta": self.perg_a.id}
                )
                b"".join(resp.streaming_content)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_ctx.call_args.args[0], self.org_a)
        self.assertEqual(mock_ctx.call_args.args[1], self.perg_a.pergunta)
        self.assertEqual(mock_build.call_args.kwargs["organization"], self.org_a)
        self.assertEqual(mock_build.call_args.kwargs["knowledge_context"], "CTX_A")

    def test_chat_org_b_so_recebe_contexto_b(self):
        self.client.force_login(self.user_b)
        mock_agent = self._agent()
        with patch("ia.views.retrieve_tenant_context", return_value="CTX_B") as mock_ctx:
            with patch("ia.views.SecretariaAI.build_agent", return_value=mock_agent) as mock_build:
                resp = self.client.post(
                    reverse("stream_resposta"), {"id_pergunta": self.perg_b.id}
                )
                b"".join(resp.streaming_content)
        self.assertEqual(mock_ctx.call_args.args[0], self.org_b)
        self.assertEqual(mock_build.call_args.kwargs["organization"], self.org_b)
        self.assertEqual(mock_build.call_args.kwargs["knowledge_context"], "CTX_B")

    def test_chat_nao_acessa_pergunta_de_outra_org(self):
        self.client.force_login(self.user_a)
        with patch("ia.views.retrieve_tenant_context") as mock_ctx:
            with patch("ia.views.SecretariaAI.build_agent") as mock_build:
                resp = self.client.post(
                    reverse("stream_resposta"), {"id_pergunta": self.perg_b.id}
                )
        self.assertEqual(resp.status_code, 404)
        mock_ctx.assert_not_called()
        mock_build.assert_not_called()

    def test_whatsapp_tenant_resolved_usa_org(self):
        env = {
            "IA_WEBHOOK_SECRET": "segredo-teste",
            "IA_WHATSAPP_USER_ID": str(self.user_a.pk),
        }
        mock_agent = self._agent()
        payload = {
            "phone": "5511999999999",
            "data": {
                "key": {"remoteJid": "5511999999999@s.whatsapp.net"},
                "message": {"extendedTextMessage": {"text": "ola contrato"}},
            },
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("ia.views.retrieve_tenant_context", return_value="WCTX") as mock_ctx:
                with patch("ia.views.SecretariaAI.build_agent", return_value=mock_agent) as mock_build:
                    resp = self.client.post(
                        reverse("webhook_whatsapp"),
                        data=__import__("json").dumps(payload),
                        content_type="application/json",
                        HTTP_X_WEBHOOK_SECRET="segredo-teste",
                    )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_ctx.call_args.args[0], self.org_a)
        self.assertEqual(mock_build.call_args.kwargs["organization"], self.org_a)
        self.assertEqual(mock_build.call_args.kwargs["knowledge_context"], "WCTX")

    def test_whatsapp_ambiguous_fail_closed_sem_retrieval(self):
        env = {
            "IA_WEBHOOK_SECRET": "segredo-teste",
            "IA_WHATSAPP_USER_ID": str(self.user_amb.pk),
        }
        mock_agent = self._agent()
        payload = {
            "phone": "5511888888888",
            "data": {
                "key": {"remoteJid": "5511888888888@s.whatsapp.net"},
                "message": {"conversation": "ola"},
            },
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("ia.views.retrieve_tenant_context") as mock_ctx:
                with patch("ia.views.SecretariaAI.build_agent", return_value=mock_agent) as mock_build:
                    resp = self.client.post(
                        reverse("webhook_whatsapp"),
                        data=__import__("json").dumps(payload),
                        content_type="application/json",
                        HTTP_X_WEBHOOK_SECRET="segredo-teste",
                    )
        self.assertEqual(resp.status_code, 200)
        mock_ctx.assert_not_called()
        self.assertIsNone(mock_build.call_args.kwargs["organization"])
        self.assertEqual(mock_build.call_args.kwargs["knowledge_context"], "")
