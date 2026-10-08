"""OBJECT-STORAGE-P1 — object key tenant-aware, OCR remoto, delete de blob."""

from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ia.tasks import ocr_and_markdown_file
from organizacoes.models import Membership, Organization
from usuarios.models import Cliente, Documentos
from usuarios.services.document_storage import (
    TENANT_UPLOAD_PREFIX,
    documento_upload_to,
    local_path_for_backend_read,
)
from usuarios.signals import post_save_documentos
from django.db.models.signals import post_save


class MemoryBlobStorage(Storage):
    """Backend remoto simulado: open/save/delete, sem .path."""

    blobs: dict = {}

    def __init__(self, **kwargs):
        super().__init__()

    def _open(self, name, mode="rb"):
        if name not in self.blobs:
            raise FileNotFoundError(name)
        return File(BytesIO(self.blobs[name]), name)

    def _save(self, name, content):
        self.blobs[name] = content.read()
        return name

    def exists(self, name):
        return name in self.blobs

    def delete(self, name):
        self.blobs.pop(name, None)

    def size(self, name):
        return len(self.blobs[name])

    def path(self, name):
        raise NotImplementedError("This backend does not support absolute paths.")

    def url(self, name):
        raise ValueError("no public url")


def _storages():
    return {
        "default": {"BACKEND": "usuarios.tests_object_storage.MemoryBlobStorage"},
        "staticfiles": settings.STORAGES["staticfiles"],
    }


class ObjectStorageP1Tests(TestCase):
    def setUp(self):
        post_save.disconnect(post_save_documentos, sender=Documentos)
        MemoryBlobStorage.blobs = {}
        self.org_a = Organization.objects.create(name="Obj Org A")
        self.org_b = Organization.objects.create(name="Obj Org B")
        self.user_a = User.objects.create_user("obja", password="senha123")
        self.user_b = User.objects.create_user("objb", password="senha123")
        Membership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        Membership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            role=Membership.Role.MEMBER,
            status=Membership.Status.ACTIVE,
        )
        self.ca = Cliente.objects.create(
            user=self.user_a,
            organization=self.org_a,
            nome="Cli A",
            email="obja@ex.test",
        )
        self.cb = Cliente.objects.create(
            user=self.user_b,
            organization=self.org_b,
            nome="Cli B",
            email="objb@ex.test",
        )

    def tearDown(self):
        MemoryBlobStorage.blobs = {}
        post_save.connect(post_save_documentos, sender=Documentos)

    def _doc(self, cliente, name, payload, content="texto"):
        return Documentos.objects.create(
            cliente=cliente,
            tipo="O",
            arquivo=SimpleUploadedFile(name, payload),
            data_upload=timezone.now(),
            content=content,
        )

    def test_object_key_contem_organization_id(self):
        doc = self._doc(self.ca, "contrato.pdf", b"%PDF-A")
        name = doc.arquivo.name
        self.assertTrue(name.startswith(f"{TENANT_UPLOAD_PREFIX}org_{self.org_a.pk}/"))
        self.assertRegex(name, rf"documentos/org_{self.org_a.pk}/[0-9a-f]{{32}}\.pdf$")
        self.assertNotIn("contrato", name)
        self.assertNotIn(self.ca.nome, name)
        self.assertNotIn(self.ca.email, name)

    def test_mesmo_filename_namespaces_diferentes(self):
        da = self._doc(self.ca, "contrato.pdf", b"AAA")
        db = self._doc(self.cb, "contrato.pdf", b"BBB")
        self.assertIn(f"org_{self.org_a.pk}/", da.arquivo.name)
        self.assertIn(f"org_{self.org_b.pk}/", db.arquivo.name)
        self.assertNotEqual(da.arquivo.name, db.arquivo.name)
        self.assertFalse(da.arquivo.name.startswith(f"{TENANT_UPLOAD_PREFIX}org_{self.org_b.pk}/"))

    def test_upload_to_rejeita_sem_organization(self):
        cli = Cliente.objects.create(
            user=self.user_a,
            organization=None,
            nome="Sem Org",
            email="semorg@ex.test",
        )
        dummy = Documentos(cliente=cli)
        with self.assertRaises(ValueError) as ctx:
            documento_upload_to(dummy, "x.txt")
        self.assertEqual(str(ctx.exception), "MISSING_ORGANIZATION")

    def test_traversal_nao_entra_no_key(self):
        key = documento_upload_to(Documentos(cliente=self.ca), "../../etc/passwd")
        self.assertNotIn("..", key)
        self.assertNotIn("etc", key)
        self.assertNotIn("passwd", key)
        self.assertTrue(key.startswith(f"{TENANT_UPLOAD_PREFIX}org_{self.org_a.pk}/"))

    def test_upload_local_dev_funciona(self):
        doc = self._doc(self.ca, "ok.txt", b"hello-local")
        self.assertTrue(doc.arquivo.storage.exists(doc.arquivo.name))
        with doc.arquivo.open("rb") as fh:
            self.assertEqual(fh.read(), b"hello-local")

    @override_settings(STORAGES=_storages())
    def test_remote_open_tempfile_sem_path(self):
        MemoryBlobStorage.blobs = {}
        doc = self._doc(self.ca, "remoto.bin", b"bytes-remotos", content="PREVIO")
        with self.assertRaises(NotImplementedError):
            doc.arquivo.path
        path, cleanup = local_path_for_backend_read(doc.arquivo)
        try:
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"bytes-remotos")
            self.assertFalse(str(path).startswith("http"))
        finally:
            cleanup()

    @override_settings(STORAGES=_storages())
    def test_worker_ocr_remote_storage_nao_usa_path(self):
        MemoryBlobStorage.blobs = {}
        doc = self._doc(self.ca, "scan.txt", b"payload", content="MANTER")

        class FakeDoc:
            def export_to_markdown(self):
                return "# markdown extraido"

        class FakeResult:
            document = FakeDoc()

        class FakeConverter:
            def convert(self, path):
                with open(path, "rb") as fh:
                    self.read = fh.read()
                return FakeResult()

        converter = FakeConverter()

        def fake_import(name):
            self.assertEqual(name, "docling.document_converter")

            class Mod:
                DocumentConverter = lambda inner_self=None: converter

            return Mod()

        with patch("ia.tasks.importlib.import_module", side_effect=fake_import):
            result = ocr_and_markdown_file(doc.pk)
        self.assertEqual(result, "ok")
        doc.refresh_from_db()
        self.assertEqual(doc.content, "# markdown extraido")
        self.assertEqual(converter.read, b"payload")

    def test_falha_abrir_nao_sobrescreve_content(self):
        doc = self._doc(self.ca, "keep.txt", b"x", content="CONTEUDO-VALIDO")
        doc.arquivo.storage.delete(doc.arquivo.name)
        result = ocr_and_markdown_file(doc.pk)
        self.assertEqual(result, "storage_unreadable")
        doc.refresh_from_db()
        self.assertEqual(doc.content, "CONTEUDO-VALIDO")

    def test_ocr_failed_nao_sobrescreve_content(self):
        doc = self._doc(self.ca, "keep2.txt", b"x", content="JA-EXTRAIDO")
        with patch("ia.tasks.importlib.import_module", side_effect=ImportError("no-ocr")):
            result = ocr_and_markdown_file(doc.pk)
        self.assertEqual(result, "ocr_failed")
        doc.refresh_from_db()
        self.assertEqual(doc.content, "JA-EXTRAIDO")

    def test_delete_remove_blob_do_proprio_tenant(self):
        doc = self._doc(self.ca, "apagar.txt", b"blob-a")
        stored = doc.arquivo.name
        storage = doc.arquivo.storage
        self.assertTrue(storage.exists(stored))
        pk = doc.pk
        doc.delete()
        self.assertFalse(Documentos.objects.filter(pk=pk).exists())
        self.assertFalse(storage.exists(stored))

    def test_delete_idempotente_blob_ausente(self):
        doc = self._doc(self.ca, "ghost.txt", b"g")
        stored = doc.arquivo.name
        doc.arquivo.storage.delete(stored)
        doc.delete()

    def test_tenant_b_nao_deleta_blob_de_a(self):
        da = self._doc(self.ca, "segredo.txt", b"AAA")
        db = self._doc(self.cb, "segredo.txt", b"BBB")
        name_a = da.arquivo.name
        name_b = db.arquivo.name
        storage = da.arquivo.storage
        db.delete()
        self.assertTrue(storage.exists(name_a))
        self.assertFalse(storage.exists(name_b))
        self.assertTrue(Documentos.objects.filter(pk=da.pk).exists())

    def test_media_direto_continua_404(self):
        doc = self._doc(self.ca, "visivel.txt", b"nope")
        resp = self.client.get(f"/media/{doc.arquivo.name}")
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("nope", resp.content.decode(errors="ignore"))
        self.assertNotIn("AWS_", resp.content.decode(errors="ignore"))
