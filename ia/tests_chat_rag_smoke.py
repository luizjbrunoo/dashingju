"""Gates e isolamento do smoke de chat RAG. Sem OpenAI."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.signals import post_save
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ia.agents import SecretariaAI
from ia.chat_rag_smoke import (
    MARKER_A,
    MARKER_B,
    ChatRagSmokeError,
    assert_chat_rag_tenant_retrieval,
    run_staging_chat_rag_smoke,
)
from ia.models import VectorChunk
from ia.services.document_knowledge import (
    PgVectorTenantStore,
    get_store,
    index_document,
    retrieve_tenant_context as knowledge_retrieve,
    set_store_factory,
)
from ia.services.embeddings import deterministic_embedding, reset_embedder, set_embedder
from ia.views import retrieve_tenant_context as views_retrieve
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.signals import post_save_documentos


class ChatRagSmokeGateTests(SimpleTestCase):
    def test_production_bloqueia(self):
        with self.assertRaises(ChatRagSmokeError) as ctx:
            run_staging_chat_rag_smoke(environ={"APP_ENV": "production"})
        self.assertIn("production", str(ctx.exception))

    def test_comando_production_exit_nao_zero(self):
        with patch(
            "ia.chat_rag_smoke.is_production_environment", return_value=True
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("staging_chat_rag_smoke", stdout=StringIO())
        self.assertIn("production", str(ctx.exception))

    def test_sqlite_bloqueia_antes_de_escrever(self):
        with patch(
            "ia.chat_rag_smoke.is_production_environment", return_value=False
        ):
            with self.assertRaises(ChatRagSmokeError) as ctx:
                run_staging_chat_rag_smoke(environ={"APP_ENV": "staging"})
        self.assertIn("not postgresql", str(ctx.exception))

    def test_comando_imprime_saida_esperada(self):
        with patch(
            "ia.management.commands.staging_chat_rag_smoke.run_staging_chat_rag_smoke"
        ):
            out = StringIO()
            call_command("staging_chat_rag_smoke", stdout=out)
        text = out.getvalue()
        for line in (
            "PGVECTOR_BACKEND=OK",
            "CHAT_ORG_A_CONTEXT=OK",
            "CHAT_ORG_A_NO_B=OK",
            "CHAT_ORG_B_CONTEXT=OK",
            "CHAT_ORG_B_NO_A=OK",
            "CHAT_NO_ORG_FAIL_CLOSED=OK",
            "NO_GLOBAL_RETRIEVAL=OK",
            "NO_LANCEDB_RUNTIME=OK",
            "CLEANUP=OK",
            "CHAT_RAG_SMOKE=OK",
        ):
            self.assertIn(line, text)


class ChatRagSmokeRetrievalTests(TestCase):
    def setUp(self):
        post_save.disconnect(post_save_documentos, sender=Documentos)
        set_store_factory(None)
        set_embedder(deterministic_embedding)

    def tearDown(self):
        reset_embedder()
        set_store_factory(None)
        post_save.connect(post_save_documentos, sender=Documentos)

    def _org_doc(self, label, marker):
        org = Organization.objects.create(name=f"ChatSmoke {label}")
        user = User.objects.create_user(f"chatrag_{label}", password="senha123")
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        cliente = Cliente.objects.create(
            user=user,
            organization=org,
            nome=f"ChatSmoke Cliente {label}",
            email=f"chatrag.{label}@ex.test",
        )
        doc = Documentos.objects.create(
            cliente=cliente,
            tipo="O",
            arquivo=SimpleUploadedFile(f"{label}.txt", marker.encode("utf-8")),
            data_upload=timezone.now(),
            content=f"contexto interno {marker}",
        )
        self.assertEqual(index_document(org, doc), "ok")
        return org, doc

    def test_views_helper_is_the_chat_path(self):
        self.assertIs(views_retrieve, knowledge_retrieve)

    def test_org_a_context_excludes_b(self):
        org_a, _doc_a = self._org_doc("a", MARKER_A)
        org_b, _doc_b = self._org_doc("b", MARKER_B)
        ctx_a = views_retrieve(org_a, MARKER_A)
        self.assertIn(MARKER_A, ctx_a)
        self.assertNotIn(MARKER_B, ctx_a)
        ctx_b = views_retrieve(org_b, MARKER_B)
        self.assertIn(MARKER_B, ctx_b)
        self.assertNotIn(MARKER_A, ctx_b)

    def test_no_organization_fail_closed_sem_query_global(self):
        self._org_doc("a", MARKER_A)
        self._org_doc("b", MARKER_B)
        with patch(
            "ia.services.document_knowledge.search_knowledge"
        ) as mock_search:
            self.assertEqual(views_retrieve(None, MARKER_A), "")
            self.assertEqual(views_retrieve(None, MARKER_B), "")
            mock_search.assert_not_called()

    def test_runtime_store_is_pgvector(self):
        org_a, _doc = self._org_doc("a", MARKER_A)
        self.assertIsInstance(get_store(org_a), PgVectorTenantStore)

    def test_secretaria_nao_tem_lancedb(self):
        self.assertIsNone(getattr(SecretariaAI, "knowledge", None))

    def test_assert_helper_cleanup(self):
        before_orgs = Organization.objects.filter(
            name__startswith="_SMOKE_CHAT_RAG_"
        ).count()
        before_chunks = VectorChunk.objects.count()
        backend = assert_chat_rag_tenant_retrieval()
        self.assertEqual(backend, "PgVectorTenantStore")
        self.assertEqual(
            Organization.objects.filter(name__startswith="_SMOKE_CHAT_RAG_").count(),
            before_orgs,
        )
        self.assertEqual(VectorChunk.objects.count(), before_chunks)
