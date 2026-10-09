"""P3B: Secretaria sem SqliteDb, histórico tenant-scoped no PostgreSQL."""

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ia.agents import JuriAI, SecretariaAI
from ia.models import Pergunta, SecretariaConversationState
from ia.services.secretaria_state import (
    HISTORY_LIMIT,
    append_secretaria_turns,
    channel_key_chat,
    channel_key_whatsapp,
    load_secretaria_history,
)
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente


class AgnoSqliteRemovedTests(SimpleTestCase):
    def test_agents_nao_usa_sqlitedb(self):
        import ia.agents as agents

        source = inspect.getsource(agents)
        self.assertNotIn("SqliteDb", source)
        self.assertNotIn("db.sqlite3", source)
        self.assertNotIn("MEMORY_DB_FILE", source)
        self.assertNotIn("MEMORY_TABLE", source)
        self.assertNotIn("update_memory_on_run", source)
        self.assertNotIn("add_history_to_context", source)

    def test_secretaria_build_sem_db_e_sem_sqlite_file(self):
        before = set(Path(".").glob("db.sqlite3"))
        with patch("ia.agents.Agent") as mock_agent:
            with patch("ia.agents.OpenAIChat"):
                SecretariaAI.build_agent(user_id=7, organization=None)
        kwargs = mock_agent.call_args.kwargs
        self.assertIsNone(kwargs.get("db"))
        self.assertNotIn("update_memory_on_run", kwargs)
        self.assertNotIn("add_history_to_context", kwargs)
        self.assertFalse(kwargs.get("search_knowledge"))
        self.assertEqual(kwargs.get("session_id"), "org-none-usr-7")
        after = set(Path(".").glob("db.sqlite3"))
        self.assertEqual(after, before)


class SecretariaStateIsolationTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Agno Org A")
        self.org_b = Organization.objects.create(name="Agno Org B")
        self.user_a = User.objects.create_user("agno_a", password="senha123")
        self.user_b = User.objects.create_user("agno_b", password="senha123")
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
            user=self.user_a,
            organization=self.org_a,
            nome="Agno Cliente A",
            email="agno.a@ex.test",
        )
        self.cli_b = Cliente.objects.create(
            user=self.user_b,
            organization=self.org_b,
            nome="Agno Cliente B",
            email="agno.b@ex.test",
        )

    def test_sim_confirm_history_tenant_scoped(self):
        key_a = channel_key_chat(self.cli_a.pk)
        append_secretaria_turns(
            organization=self.org_a,
            user_id=self.user_a.pk,
            channel_key=key_a,
            user_text="quero amanha 14h",
            assistant_text="Posso confirmar este horario? Responda: SIM para confirmar.",
            cliente=self.cli_a,
        )
        hist_a = load_secretaria_history(
            organization=self.org_a,
            user_id=self.user_a.pk,
            channel_key=key_a,
        )
        hist_b = load_secretaria_history(
            organization=self.org_b,
            user_id=self.user_b.pk,
            channel_key=channel_key_chat(self.cli_b.pk),
        )
        self.assertIn("quero amanha 14h", hist_a)
        self.assertIn("SIM para confirmar", hist_a)
        self.assertNotIn("quero amanha 14h", hist_b)
        self.assertNotIn("SIM para confirmar", hist_b)
        spoof = load_secretaria_history(
            organization=self.org_b,
            user_id=self.user_a.pk,
            channel_key=key_a,
        )
        self.assertEqual(spoof, "")

    def test_organization_none_nao_persiste_nem_le(self):
        append_secretaria_turns(
            organization=None,
            user_id=self.user_a.pk,
            channel_key=channel_key_chat(self.cli_a.pk),
            user_text="segredo",
            assistant_text="ok",
        )
        self.assertEqual(SecretariaConversationState.objects.count(), 0)
        self.assertEqual(
            load_secretaria_history(
                organization=None,
                user_id=self.user_a.pk,
                channel_key=channel_key_chat(self.cli_a.pk),
            ),
            "",
        )

    def test_whatsapp_channel_key_nao_e_telefone(self):
        key = channel_key_whatsapp("5511999999999")
        self.assertTrue(key.startswith("wa:"))
        self.assertNotIn("5511999999999", key)
        self.assertNotIn("phone", key)

    def test_history_limit(self):
        key = channel_key_chat(self.cli_a.pk)
        for i in range(HISTORY_LIMIT + 3):
            append_secretaria_turns(
                organization=self.org_a,
                user_id=self.user_a.pk,
                channel_key=key,
                user_text=f"msg-{i}",
                assistant_text=f"resp-{i}",
                cliente=self.cli_a,
            )
        state = SecretariaConversationState.objects.get(
            organization=self.org_a, user=self.user_a, channel_key=key
        )
        self.assertLessEqual(len(state.turns), HISTORY_LIMIT)

    def test_chat_stream_grava_historico_org(self):
        self.client.force_login(self.user_a)
        perg = Pergunta.objects.create(pergunta="agendar 14h", cliente=self.cli_a)
        mock_agent = MagicMock()
        mock_agent.run.side_effect = [
            [],
            MagicMock(
                content="Posso confirmar este horario? Responda: SIM para confirmar."
            ),
        ]
        with patch("ia.views.retrieve_tenant_context", return_value=""):
            with patch(
                "ia.views.SecretariaAI.build_agent", return_value=mock_agent
            ) as mock_build:
                resp = self.client.post(
                    reverse("stream_resposta"), {"id_pergunta": perg.id}
                )
                corpo = b"".join(resp.streaming_content).decode()
        self.assertIn("SIM para confirmar", corpo)
        self.assertNotIn("session_id", mock_build.call_args.kwargs)
        self.assertEqual(mock_build.call_args.kwargs["organization"], self.org_a)
        self.assertEqual(mock_build.call_args.kwargs["cliente_id"], self.cli_a.pk)
        hist = load_secretaria_history(
            organization=self.org_a,
            user_id=self.user_a.pk,
            channel_key=channel_key_chat(self.cli_a.pk),
        )
        self.assertIn("agendar 14h", hist)
        self.assertIn("SIM para confirmar", hist)

    def test_session_id_tenant_aware_sem_telefone(self):
        with patch("ia.agents.Agent") as mock_agent:
            with patch("ia.agents.OpenAIChat"):
                SecretariaAI.build_agent(
                    user_id=self.user_a.pk,
                    organization=self.org_a,
                    cliente_id=self.cli_a.pk,
                    conversation_history="HISTÓRICO: SIM para confirmar 14h",
                )
        kwargs = mock_agent.call_args.kwargs
        self.assertEqual(
            kwargs["session_id"],
            f"org-{self.org_a.pk}-cli-{self.cli_a.pk}-usr-{self.user_a.pk}",
        )
        self.assertNotIn("5511", kwargs["session_id"])
        self.assertIn("SIM para confirmar 14h", kwargs["instructions"])

    def test_whatsapp_sem_org_nao_persiste(self):
        user_zero = User.objects.create_user("agno_zero", password="senha123")
        env = {
            "IA_WEBHOOK_SECRET": "segredo-teste",
            "IA_WHATSAPP_USER_ID": str(user_zero.pk),
        }
        payload = {
            "phone": "5511977777777",
            "data": {
                "key": {"remoteJid": "5511977777777@s.whatsapp.net"},
                "message": {"conversation": "SIM"},
            },
        }
        mock_agent = MagicMock()
        mock_agent.run.return_value = MagicMock(content="ok")
        with patch.dict("os.environ", env, clear=False):
            with patch("ia.views.retrieve_tenant_context") as mock_ctx:
                with patch(
                    "ia.views.SecretariaAI.build_agent", return_value=mock_agent
                ) as mock_build:
                    resp = self.client.post(
                        reverse("webhook_whatsapp"),
                        data=__import__("json").dumps(payload),
                        content_type="application/json",
                        HTTP_X_WEBHOOK_SECRET="segredo-teste",
                    )
        self.assertEqual(resp.status_code, 200)
        mock_ctx.assert_not_called()
        self.assertIsNone(mock_build.call_args.kwargs["organization"])
        self.assertEqual(SecretariaConversationState.objects.count(), 0)

    def test_juri_build_sem_sqlite(self):
        with patch("ia.agents.Agent") as mock_agent:
            JuriAI.build_agent(self.org_a)
        kwargs = mock_agent.call_args.kwargs
        self.assertNotIn("db", kwargs)
        self.assertNotIn("update_memory_on_run", kwargs)
