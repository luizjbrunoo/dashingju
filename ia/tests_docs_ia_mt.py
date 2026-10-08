"""DOCS-IA-MT-01 — isolamento adversarial Documento/OCR/RAG por Organization."""

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models.signals import post_save
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from io import StringIO

from ia.services.document_knowledge import (
    InMemoryTenantStore,
    LEGACY_GLOBAL_TABLE,
    index_document,
    search_knowledge,
    set_store_factory,
    table_name_for_organization,
)
from ia.tasks import ocr_and_markdown_file, rag_documentos
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.signals import post_save_documentos

MARKER_A = "SEGREDO_ORG_A_7F3K"
MARKER_B = "SEGREDO_ORG_B_9Q2M"
SEMANTIC = "contrato de honorários advocatícios e cláusula de êxito"


class DocsIaMtAdversarialTests(TestCase):
    def setUp(self):
        post_save.disconnect(post_save_documentos, sender=Documentos)
        InMemoryTenantStore.reset()
        set_store_factory(InMemoryTenantStore)
        self.org_a = Organization.objects.create(name="Docs Org A")
        self.org_b = Organization.objects.create(name="Docs Org B")
        self.a1 = User.objects.create_user(username="docs_a1", password="senha123")
        self.a2 = User.objects.create_user(username="docs_a2", password="senha123")
        self.b1 = User.objects.create_user(username="docs_b1", password="senha123")
        self._member(self.a1, self.org_a, Membership.Role.OWNER)
        self._member(self.a2, self.org_a)
        self._member(self.b1, self.org_b, Membership.Role.OWNER)
        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente CA",
            email="ca.docs@ex.test",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente CB",
            email="cb.docs@ex.test",
        )
        self.da = self._doc(
            self.ca,
            "da.txt",
            f"{SEMANTIC} da parte A. {MARKER_A}",
        )
        self.db = self._doc(
            self.cb,
            "db.txt",
            f"{SEMANTIC} da parte B. {MARKER_B}",
        )
        self.assertEqual(index_document(self.org_a, self.da), "ok")
        self.assertEqual(index_document(self.org_b, self.db), "ok")

    def tearDown(self):
        set_store_factory(None)
        InMemoryTenantStore.reset()
        post_save.connect(post_save_documentos, sender=Documentos)

    def _member(self, user, org, role=Membership.Role.MEMBER):
        return Membership.objects.create(
            user=user,
            organization=org,
            role=role,
            status=Membership.Status.ACTIVE,
        )

    def _doc(self, cliente, name, content):
        return Documentos.objects.create(
            cliente=cliente,
            tipo="O",
            arquivo=SimpleUploadedFile(name, content.encode("utf-8")),
            data_upload=timezone.now(),
            content=content,
        )

    def _texts(self, hits):
        return " ".join(hit.text for hit in hits)

    def test_http_idor_documento_b(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("analise_jurisprudencia", args=[self.db.id]))
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(MARKER_B, resp.content.decode(errors="ignore"))
        resp = self.client.post(reverse("processar_analise", args=[self.db.id]))
        self.assertEqual(resp.status_code, 404)

    def test_upload_cross_org_rejeitado(self):
        self.client.force_login(self.a1)
        antes = Documentos.objects.filter(cliente=self.cb).count()
        resp = self.client.post(
            reverse("cliente", kwargs={"id": self.cb.pk}),
            {
                "tipo": "O",
                "data": timezone.localdate().isoformat(),
                "documento": SimpleUploadedFile("hack.txt", b"x"),
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(Documentos.objects.filter(cliente=self.cb).count(), antes)

    def test_job_pk_indexa_somente_org_do_sql(self):
        novo = self._doc(self.cb, "job-b.txt", f"job {MARKER_B} extra")
        result = rag_documentos(novo.pk)
        self.assertEqual(result, "ok")
        hits_a = search_knowledge(self.org_a, MARKER_B)
        self.assertNotIn(MARKER_B, self._texts(hits_a))
        hits_b = search_knowledge(self.org_b, "job extra")
        self.assertTrue(any(h.metadata.get("documento_id") == novo.pk for h in hits_b))
        self.assertTrue(all(h.table_name == table_name_for_organization(self.org_b) for h in hits_b))

    def test_null_nao_indexa(self):
        cli_nulo = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente NULL",
            email="null.docs@ex.test",
        )
        nulo = self._doc(cli_nulo, "nulo.txt", f"conteudo {MARKER_A} nulo")
        cli_nulo.organization = None
        cli_nulo.save(update_fields=["organization"])
        self.assertEqual(ocr_and_markdown_file(nulo.pk), "skip")
        self.assertEqual(rag_documentos(nulo.pk), "skip")
        hits = search_knowledge(self.org_a, "nulo")
        self.assertFalse(any(h.metadata.get("documento_id") == nulo.pk for h in hits))

    def test_vector_metadata_obrigatoria(self):
        hits = search_knowledge(self.org_a, MARKER_A)
        self.assertTrue(hits)
        meta = hits[0].metadata
        self.assertEqual(meta.get("organization_id"), self.org_a.pk)
        self.assertEqual(meta.get("documento_id"), self.da.pk)
        self.assertEqual(meta.get("cliente_id"), self.ca.pk)
        self.assertEqual(hits[0].table_name, table_name_for_organization(self.org_a))

    def test_retrieval_a_marker(self):
        hits = search_knowledge(self.org_a, MARKER_A)
        self.assertIn(MARKER_A, self._texts(hits))
        self.assertNotIn(MARKER_B, self._texts(hits))

    def test_retrieval_cross_explicit_secret(self):
        hits = search_knowledge(self.org_a, MARKER_B)
        self.assertNotIn(MARKER_B, self._texts(hits))
        self.assertFalse(any(h.metadata.get("documento_id") == self.db.pk for h in hits))

    def test_retrieval_semantic_nao_traz_b(self):
        hits = search_knowledge(self.org_a, SEMANTIC)
        self.assertFalse(any(h.metadata.get("documento_id") == self.db.pk for h in hits))
        self.assertNotIn(MARKER_B, self._texts(hits))

    def test_knowledge_filters_maliciosos(self):
        hits = search_knowledge(
            self.org_a,
            MARKER_B,
            knowledge_filters={"organization_id": self.org_b.pk},
        )
        self.assertNotIn(MARKER_B, self._texts(hits))
        hits = search_knowledge(self.org_a, MARKER_B, cliente_id=self.cb.pk)
        self.assertEqual(hits, [])
        hits = search_knowledge(self.org_a, MARKER_B, documento_id=self.db.pk)
        self.assertEqual(hits, [])

    def test_same_org_a1_ve_documento_a2(self):
        doc_a2 = self._doc(self.ca, "a2.txt", f"{SEMANTIC} upload A2 {MARKER_A}")
        self.assertEqual(index_document(self.org_a, doc_a2), "ok")
        hits = search_knowledge(self.org_a, "upload A2")
        self.assertTrue(any(h.metadata.get("documento_id") == doc_a2.pk for h in hits))
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("analise_jurisprudencia", args=[doc_a2.id]))
        self.assertEqual(resp.status_code, 200)

    def test_same_name_filename_nao_colide(self):
        xa = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Empresa XPTO",
            email="xpto.a@ex.test",
        )
        xb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Empresa XPTO",
            email="xpto.b@ex.test",
        )
        da = self._doc(xa, "contrato.pdf", f"contrato honorarios {MARKER_A} xpto-a")
        db = self._doc(xb, "contrato.pdf", f"contrato honorarios {MARKER_B} xpto-b")
        index_document(self.org_a, da)
        index_document(self.org_b, db)
        hits = search_knowledge(self.org_a, "xpto")
        blob = self._texts(hits)
        self.assertIn(MARKER_A, blob)
        self.assertNotIn(MARKER_B, blob)

    def test_metadata_spoof_nao_expoe_sql_b(self):
        store_a = InMemoryTenantStore(table_name_for_organization(self.org_a))
        store_a.rows.append(
            {
                "text": f"spoof {MARKER_B}",
                "meta": {
                    "organization_id": self.org_a.pk,
                    "documento_id": self.db.pk,
                    "cliente_id": self.ca.pk,
                    "name": "spoof.txt",
                },
            }
        )
        hits = search_knowledge(self.org_a, MARKER_B)
        self.assertFalse(any(h.metadata.get("documento_id") == self.db.pk for h in hits))
        self.assertNotIn(MARKER_B, self._texts(hits))

    def test_legacy_vector_invisivel(self):
        InMemoryTenantStore.buckets.setdefault(LEGACY_GLOBAL_TABLE, []).append(
            {
                "text": f"legado {MARKER_B} {MARKER_A}",
                "meta": {"cliente_id": self.cb.pk, "name": "legado.txt"},
            }
        )
        self.assertNotIn(MARKER_B, self._texts(search_knowledge(self.org_a, MARKER_B)))
        self.assertNotIn(
            "legado",
            self._texts(search_knowledge(self.org_b, "legado")),
        )

    def test_delete_stale(self):
        self.da.delete()
        hits = search_knowledge(self.org_a, MARKER_A)
        self.assertFalse(any(h.metadata.get("documento_id") == self.da.pk for h in hits))

    def test_reindex_idempotente(self):
        index_document(self.org_a, self.da)
        index_document(self.org_a, self.da)
        store = InMemoryTenantStore(table_name_for_organization(self.org_a))
        n = sum(
            1
            for row in store.rows
            if row.get("meta", {}).get("documento_id") == self.da.pk
        )
        self.assertEqual(n, 1)

    def test_pre_retrieval_usa_namespace_org(self):
        hits = search_knowledge(self.org_a, MARKER_A)
        self.assertTrue(hits)
        self.assertEqual(hits[0].table_name, f"documentos_org_{self.org_a.pk}")
        self.assertNotEqual(hits[0].table_name, LEGACY_GLOBAL_TABLE)

    def test_ocr_job_null_skip(self):
        cli = Cliente.objects.create(
            user=self.a1, organization=self.org_a, nome="N", email="n2@ex.test"
        )
        doc = self._doc(cli, "x.txt", "abc")
        cli.organization = None
        cli.save(update_fields=["organization"])
        before = doc.content
        self.assertEqual(ocr_and_markdown_file(doc.pk), "skip")
        doc.refresh_from_db()
        self.assertEqual(doc.content, before)

    def test_index_mismatch_sql_fail_closed(self):
        self.assertEqual(index_document(self.org_a, self.db), "ORGANIZATION_CONFLICT")
        hits = search_knowledge(self.org_a, MARKER_B)
        self.assertFalse(any(h.metadata.get("documento_id") == self.db.pk for h in hits))

    def test_reindex_command_dry_run(self):
        out = StringIO()
        call_command("reindex_documentos_rag", "--dry-run", stdout=out)
        text = out.getvalue()
        self.assertIn("DOCUMENTS_TOTAL=", text)
        self.assertIn("RESULT=DRY-RUN", text)
