"""PREPROD-PRIVATE-STORAGE-01 — download autorizado e acesso direto fail-closed."""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ia.services.document_knowledge import (
    InMemoryTenantStore,
    index_document,
    search_knowledge,
    set_store_factory,
    table_name_for_organization,
)
from ia.tasks import ocr_and_markdown_file, rag_documentos
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.signals import post_save_documentos
from usuarios.tests_helpers import grant_documentos_permissions

MARKER_A = "STORPRIV_ORG_A_7F3K"
MARKER_B = "STORPRIV_ORG_B_9Q2M"
BYTES_A = f"conteudo-a {MARKER_A}".encode("utf-8")
BYTES_B = f"conteudo-b {MARKER_B}".encode("utf-8")


class PreprodPrivateStorageTests(TestCase):
    def setUp(self):
        post_save.disconnect(post_save_documentos, sender=Documentos)
        InMemoryTenantStore.reset()
        set_store_factory(InMemoryTenantStore)
        self.org_a = Organization.objects.create(name="Stor Org A")
        self.org_b = Organization.objects.create(name="Stor Org B")
        self.a1 = User.objects.create_user("stor_a1", password="senha123")
        self.a2 = User.objects.create_user("stor_a2", password="senha123")
        self.a2_sem = User.objects.create_user("stor_a2_sem", password="senha123")
        self.b1 = User.objects.create_user("stor_b1", password="senha123")
        self.zero = User.objects.create_user("stor_zero", password="senha123")
        self.amb = User.objects.create_user("stor_amb", password="senha123")
        for user, org in (
            (self.a1, self.org_a),
            (self.a2, self.org_a),
            (self.a2_sem, self.org_a),
            (self.b1, self.org_b),
        ):
            Membership.objects.create(
                user=user,
                organization=org,
                role=Membership.Role.MEMBER,
                status=Membership.Status.ACTIVE,
            )
        Membership.objects.create(
            user=self.amb,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.amb,
            organization=self.org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        grant_documentos_permissions(self.a1)
        grant_documentos_permissions(self.a2)
        grant_documentos_permissions(self.b1)
        grant_documentos_permissions(self.amb)
        grant_documentos_permissions(self.zero)
        self.ca = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="Cliente Stor A",
            email="stor.a@ex.test",
        )
        self.cb = Cliente.objects.create(
            user=self.b1,
            organization=self.org_b,
            nome="Cliente Stor B",
            email="stor.b@ex.test",
        )
        self.dx = self._doc(self.ca, "peca-a.txt", BYTES_A, MARKER_A)
        self.dy = self._doc(self.cb, "peca-b.txt", BYTES_B, MARKER_B)

    def tearDown(self):
        set_store_factory(None)
        InMemoryTenantStore.reset()
        post_save.connect(post_save_documentos, sender=Documentos)

    def _doc(self, cliente, name, payload, content):
        return Documentos.objects.create(
            cliente=cliente,
            tipo="P",
            arquivo=SimpleUploadedFile(name, payload),
            data_upload=timezone.now(),
            content=content,
        )

    def _download(self, user, documento):
        self.client.force_login(user)
        return self.client.get(
            reverse("documento_download", args=[documento.pk])
        )

    def _payload(self, resp):
        if getattr(resp, "streaming", False):
            return b"".join(resp.streaming_content)
        return resp.content

    def _body(self, resp):
        return self._payload(resp).decode(errors="ignore")

    def _assert_no_disclosure(self, resp, arquivo=None):
        text = self._body(resp)
        self.assertNotIn(str(settings.MEDIA_ROOT), text)
        self.assertNotIn(settings.SECRET_KEY, text)
        self.assertNotIn("AWS_", text)
        self.assertNotIn("SECRET_ACCESS", text)
        if arquivo is not None:
            try:
                path = arquivo.path
            except Exception:
                path = ""
            if path:
                self.assertNotIn(path, text)

    def test_01_a1_download_autorizado(self):
        resp = self._download(self.a1, self.dx)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._payload(resp), BYTES_A)
        self.assertIn("attachment", resp.get("Content-Disposition", "").lower())

    def test_02_a2_same_org_com_capability(self):
        resp = self._download(self.a2, self.dx)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._payload(resp), BYTES_A)

    def test_03_a2_same_org_sem_capability(self):
        resp = self._download(self.a2_sem, self.dx)
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(MARKER_A, self._body(resp))
        self._assert_no_disclosure(resp, self.dx.arquivo)

    def test_04_b1_por_id_fail_closed(self):
        resp = self._download(self.b1, self.dx)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(MARKER_A, self._body(resp))
        self._assert_no_disclosure(resp, self.dx.arquivo)

    def test_05_b1_url_direta_media(self):
        self.client.force_login(self.b1)
        media_url = f"/media/{self.dx.arquivo.name}"
        resp = self.client.get(media_url)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(MARKER_A, self._body(resp))
        self.assertNotEqual(self._payload(resp), BYTES_A)

    def test_06_sem_tenant_context(self):
        resp = self._download(self.zero, self.dx)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(MARKER_A, self._body(resp))

    def test_07_tenant_ambiguo(self):
        resp = self._download(self.amb, self.dx)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(MARKER_A, self._body(resp))

    def test_08_null_ownership(self):
        cli = Cliente.objects.create(
            user=self.a1,
            organization=self.org_a,
            nome="NULL Stor",
            email="null.stor@ex.test",
        )
        nulo = self._doc(cli, "nulo.txt", b"segredo-nulo", "segredo-nulo")
        cli.organization = None
        cli.save(update_fields=["organization"])
        resp = self._download(self.a1, nulo)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("segredo-nulo", self._body(resp))

    def test_09_documento_inexistente(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("documento_download", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_10_arquivo_fisico_ausente(self):
        path = self.dx.arquivo.path
        self.dx.arquivo.storage.delete(self.dx.arquivo.name)
        resp = self._download(self.a1, self.dx)
        self.assertEqual(resp.status_code, 404)
        self._assert_no_disclosure(resp)
        self.assertNotIn(path, self._body(resp))
        self.assertNotIn(MARKER_A, self._body(resp))

    def test_11_path_traversal_bloqueado(self):
        self.client.force_login(self.a1)
        for url in (
            "/media/documentos/../settings.py",
            "/media/documentos/../../core/settings.py",
            "/media/documentos/%2e%2e/%2e%2e/core/settings.py",
            "/media/documentos/..%2f..%2fcore%2fsettings.py",
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 404, url)
            self.assertNotIn("SECRET_KEY", self._body(resp))
            self.assertNotIn(settings.SECRET_KEY, self._body(resp))
        resp = self.client.get(
            reverse("documento_download", args=[self.dx.pk]) + "?path=../../etc/passwd"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._payload(resp), BYTES_A)

    def test_12_filename_igual_sem_colisao(self):
        da = self._doc(self.ca, "contrato.pdf", b"AAA-ORG-A", "AAA-ORG-A")
        db = self._doc(self.cb, "contrato.pdf", b"BBB-ORG-B", "BBB-ORG-B")
        resp_a = self._download(self.a1, da)
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(self._payload(resp_a), b"AAA-ORG-A")
        resp_cross = self._download(self.b1, da)
        self.assertEqual(resp_cross.status_code, 404)
        self.assertNotIn("AAA-ORG-A", self._body(resp_cross))
        resp_b = self._download(self.b1, db)
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(self._payload(resp_b), b"BBB-ORG-B")

    def test_13_upload_cross_org_bloqueado(self):
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

    def test_13b_upload_same_org(self):
        self.client.force_login(self.a2)
        antes = Documentos.objects.filter(cliente=self.ca).count()
        resp = self.client.post(
            reverse("cliente", kwargs={"id": self.ca.pk}),
            {
                "tipo": "C",
                "data": timezone.localdate().isoformat(),
                "documento": SimpleUploadedFile("ok.txt", b"upload-a2"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Documentos.objects.filter(cliente=self.ca).count(), antes + 1)

    def test_14_delete_cross_org_sem_rota(self):
        self.client.force_login(self.b1)
        resp = self.client.post(
            f"/usuarios/documentos/{self.dx.pk}/delete/"
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Documentos.objects.filter(pk=self.dx.pk).exists())
        resp_cli = self.client.post(
            reverse("cliente", kwargs={"id": self.ca.pk}),
            {"action": "delete_documento", "documento_id": str(self.dx.pk)},
        )
        self.assertEqual(resp_cli.status_code, 404)
        self.assertTrue(Documentos.objects.filter(pk=self.dx.pk).exists())

    def test_15_endpoint_nao_revela_path(self):
        resp = self._download(self.b1, self.dx)
        self.assertEqual(resp.status_code, 404)
        self._assert_no_disclosure(resp, self.dx.arquivo)

    def test_16_endpoint_nao_revela_credentials(self):
        resp = self._download(self.b1, self.dx)
        self.assertNotIn("DJANGO_DEFAULT_FILE_STORAGE", self._body(resp))
        self.assertNotIn("AKIA", self._body(resp))

    def test_17_template_nao_usa_url_publica(self):
        self.client.force_login(self.a1)
        resp = self.client.get(reverse("cliente", kwargs={"id": self.ca.pk}))
        self.assertEqual(resp.status_code, 200)
        html = self._body(resp)
        self.assertNotIn("/media/documentos/", html)
        self.assertNotIn(self.dx.arquivo.url, html)
        self.assertIn(reverse("documento_download", args=[self.dx.pk]), html)
        self.client.force_login(self.a2_sem)
        resp_sem = self.client.get(reverse("cliente", kwargs={"id": self.ca.pk}))
        self.assertEqual(resp_sem.status_code, 200)
        self.assertNotIn(
            reverse("documento_download", args=[self.dx.pk]),
            self._body(resp_sem),
        )

    def test_18_ocr_backend_sem_url_publica(self):
        from unittest.mock import patch

        from usuarios.services.document_storage import local_path_for_backend_read

        path, cleanup = local_path_for_backend_read(self.dx.arquivo)
        try:
            self.assertTrue(path)
            self.assertFalse(str(path).startswith("http"))
            self.assertFalse(str(path).startswith("/media/"))
        finally:
            cleanup()
        with patch("ia.tasks.importlib.import_module", side_effect=ImportError("no-ocr")):
            result = ocr_and_markdown_file(self.dx.pk)
        self.assertEqual(result, "ocr_failed")
        self.dx.refresh_from_db()
        self.assertEqual(self.dx.content, MARKER_A)
        self.assertNotIn("http://", self.dx.content)

    def test_19_rag_isolamento(self):
        self.assertEqual(index_document(self.org_a, self.dx), "ok")
        self.assertEqual(index_document(self.org_b, self.dy), "ok")
        hits_a = search_knowledge(self.org_a, MARKER_A)
        hits_b = search_knowledge(self.org_a, MARKER_B)
        self.assertTrue(any(h.metadata.get("documento_id") == self.dx.pk for h in hits_a))
        self.assertFalse(any(h.metadata.get("documento_id") == self.dy.pk for h in hits_b))
        self.assertTrue(
            all(h.table_name == table_name_for_organization(self.org_a) for h in hits_a)
        )
        self.assertEqual(rag_documentos(self.dy.pk), "ok")
        hits_a2 = search_knowledge(self.org_a, MARKER_B)
        self.assertFalse(any(h.metadata.get("documento_id") == self.dy.pk for h in hits_a2))

    def test_20_anonimo_fail_closed(self):
        resp = self.client.get(reverse("documento_download", args=[self.dx.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/usuarios/login/", resp["Location"])

    def test_21_direct_media_anonimo_e_debug(self):
        media_url = f"/media/{self.dx.arquivo.name}"
        resp = self.client.get(media_url)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(MARKER_A, self._body(resp))
        with override_settings(DEBUG=True, SERVE_TENANT_MEDIA=False):
            resp_dbg = self.client.get(media_url)
            self.assertEqual(resp_dbg.status_code, 404)
            self.assertNotIn(MARKER_A, self._body(resp_dbg))
        with override_settings(DEBUG=False):
            resp_prod = self.client.get(media_url)
            self.assertEqual(resp_prod.status_code, 404)
            self.assertNotIn(MARKER_A, self._body(resp_prod))
            self._assert_no_disclosure(resp_prod, self.dx.arquivo)

    def test_22_delete_same_org_rag_purge_e_blob(self):
        self.assertEqual(index_document(self.org_a, self.dx), "ok")
        stored_name = self.dx.arquivo.name
        storage = self.dx.arquivo.storage
        pk = self.dx.pk
        self.assertTrue(storage.exists(stored_name))
        self.dx.delete()
        hits = search_knowledge(self.org_a, MARKER_A)
        self.assertFalse(any(h.metadata.get("documento_id") == pk for h in hits))
        self.assertFalse(storage.exists(stored_name))
