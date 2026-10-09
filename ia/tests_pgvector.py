"""ADV-GROWTH-SAAS-PGVECTOR-P2B — isolamento A1/A2/B1 no PostgreSQL/pgvector."""

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone

from ia.models import VectorChunk
from ia.services.document_knowledge import (
    LanceTenantStore,
    PgVectorTenantStore,
    get_store,
    index_document,
    search_knowledge,
    set_store_factory,
)
from ia.services.embeddings import (
    RAG_EMBEDDING_DIM,
    RAG_EMBEDDING_MODEL,
    deterministic_embedding,
    reset_embedder,
    set_embedder,
)
from ia.tasks import rag_documentos
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.signals import post_save_documentos

MARKER_A = "PGV_ORG_A_7F3K"
MARKER_B = "PGV_ORG_B_9Q2M"


class PgVectorTenantStoreTests(TestCase):
    def setUp(self):
        post_save.disconnect(post_save_documentos, sender=Documentos)
        set_store_factory(None)
        set_embedder(deterministic_embedding)
        self.org_a = Organization.objects.create(name="PgV Org A")
        self.org_b = Organization.objects.create(name="PgV Org B")
        self.ua = User.objects.create_user("pgv_a", password="senha123")
        self.ub = User.objects.create_user("pgv_b", password="senha123")
        Membership.objects.create(
            user=self.ua,
            organization=self.org_a,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.ub,
            organization=self.org_b,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        self.ca = Cliente.objects.create(
            user=self.ua,
            organization=self.org_a,
            nome="Cliente PgV A",
            email="pgv.a@ex.test",
        )
        self.cb = Cliente.objects.create(
            user=self.ub,
            organization=self.org_b,
            nome="Cliente PgV B",
            email="pgv.b@ex.test",
        )
        self.a1 = self._doc(self.ca, "a1.txt", f"contrato honorarios {MARKER_A} A1")
        self.a2 = self._doc(self.ca, "a2.txt", f"peticao inicial {MARKER_A} A2")
        self.b1 = self._doc(self.cb, "b1.txt", f"segredo {MARKER_B} B1")
        self.assertEqual(index_document(self.org_a, self.a1), "ok")
        self.assertEqual(index_document(self.org_a, self.a2), "ok")
        self.assertEqual(index_document(self.org_b, self.b1), "ok")

    def tearDown(self):
        reset_embedder()
        set_store_factory(None)
        post_save.connect(post_save_documentos, sender=Documentos)

    def _doc(self, cliente, name, content):
        return Documentos.objects.create(
            cliente=cliente,
            tipo="O",
            arquivo=SimpleUploadedFile(name, content.encode("utf-8")),
            data_upload=timezone.now(),
            content=content,
        )

    def _ids(self, hits):
        return {h.metadata.get("documento_id") for h in hits}

    def test_runtime_store_is_pgvector(self):
        store = get_store(self.org_a)
        self.assertIsInstance(store, PgVectorTenantStore)

    def test_lance_store_blocked(self):
        with self.assertRaises(ImproperlyConfigured):
            LanceTenantStore("empresa")

    def test_search_a_returns_a1_a2_never_b1(self):
        hits = search_knowledge(self.org_a, MARKER_A)
        ids = self._ids(hits)
        self.assertIn(self.a1.pk, ids)
        self.assertIn(self.a2.pk, ids)
        self.assertNotIn(self.b1.pk, ids)
        blob = " ".join(h.text for h in hits)
        self.assertNotIn(MARKER_B, blob)

    def test_search_b_returns_b1_never_a(self):
        hits = search_knowledge(self.org_b, MARKER_B)
        ids = self._ids(hits)
        self.assertIn(self.b1.pk, ids)
        self.assertNotIn(self.a1.pk, ids)
        self.assertNotIn(self.a2.pk, ids)
        blob = " ".join(h.text for h in hits)
        self.assertNotIn(MARKER_A, blob)

    def test_delete_a1_preserves_a2_and_b1(self):
        store = PgVectorTenantStore(self.org_a)
        deleted = store.delete_documento(self.a1.pk)
        self.assertGreaterEqual(deleted, 1)
        ids = self._ids(search_knowledge(self.org_a, MARKER_A))
        self.assertNotIn(self.a1.pk, ids)
        self.assertIn(self.a2.pk, ids)
        self.assertTrue(
            VectorChunk.objects.filter(
                organization=self.org_b, documento=self.b1
            ).exists()
        )

    def test_reindex_a2_idempotent(self):
        self.assertEqual(index_document(self.org_a, self.a2), "ok")
        self.assertEqual(index_document(self.org_a, self.a2), "ok")
        n = VectorChunk.objects.filter(
            organization=self.org_a,
            documento=self.a2,
            embedding_model=RAG_EMBEDDING_MODEL,
        ).count()
        self.assertEqual(n, 1)
        self.assertEqual(
            VectorChunk.objects.filter(organization=self.org_b, documento=self.b1).count(),
            1,
        )

    def test_organization_none_fail_closed(self):
        self.assertEqual(search_knowledge(None, MARKER_A), [])
        self.assertEqual(index_document(None, self.a1), "MISSING_ORGANIZATION")
        with self.assertRaises(ValueError):
            get_store(None)

    def test_metadata_spoof_org_b_on_store_a_recusado(self):
        store = PgVectorTenantStore(self.org_a)
        store.upsert_chunks(
            [
                {
                    "id": f"org{self.org_a.pk}-doc{self.b1.pk}-c0",
                    "text": f"spoof {MARKER_B}",
                    "meta": {
                        "organization_id": self.org_b.pk,
                        "documento_id": self.b1.pk,
                        "cliente_id": self.cb.pk,
                        "name": "spoof.txt",
                    },
                }
            ]
        )
        self.assertFalse(
            VectorChunk.objects.filter(
                organization=self.org_a, documento=self.b1
            ).exists()
        )
        hits = search_knowledge(self.org_a, MARKER_B)
        self.assertNotIn(self.b1.pk, self._ids(hits))
        self.assertTrue(
            VectorChunk.objects.filter(
                organization=self.org_b, documento=self.b1
            ).exists()
        )

    def test_embedding_model_and_dim_persisted(self):
        row = VectorChunk.objects.get(organization=self.org_a, documento=self.a1)
        self.assertEqual(row.embedding_model, RAG_EMBEDDING_MODEL)
        self.assertEqual(row.embedding_dim, RAG_EMBEDDING_DIM)
        self.assertEqual(len(row.embedding), RAG_EMBEDDING_DIM)
        self.assertIsNotNone(row.organization_id)

    def test_worker_index_visible_to_new_store_instance(self):
        extra = self._doc(self.ca, "a3.txt", f"job worker {MARKER_A} A3")
        self.assertEqual(rag_documentos(extra.pk), "ok")
        other = PgVectorTenantStore(self.org_a)
        hits = other.search("job worker A3", filters={"organization_id": self.org_a.pk})
        self.assertTrue(any(h.metadata.get("documento_id") == extra.pk for h in hits))
        hits_web = search_knowledge(self.org_a, "job worker A3")
        self.assertIn(extra.pk, self._ids(hits_web))
        self.assertNotIn(self.b1.pk, self._ids(hits_web))

    def test_search_sql_filters_organization_before_rank(self):
        hits = search_knowledge(
            self.org_a,
            MARKER_B,
            knowledge_filters={"organization_id": self.org_b.pk},
        )
        self.assertNotIn(self.b1.pk, self._ids(hits))
        self.assertNotIn(MARKER_B, " ".join(h.text for h in hits))
