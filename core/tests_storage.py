"""OBJECT-STORAGE-P1 — resolver S3 vs FileSystemStorage."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.storage import FILESYSTEM_BACKEND, S3_BACKEND, resolve_default_storage


class ResolveDefaultStorageTests(SimpleTestCase):
    def test_sem_s3_usa_filesystem(self):
        cfg = resolve_default_storage(environ={})
        self.assertEqual(cfg["BACKEND"], FILESYSTEM_BACKEND)
        self.assertNotIn("OPTIONS", cfg)

    def test_s3_completo_usa_backend_privado(self):
        cfg = resolve_default_storage(
            environ={
                "AWS_STORAGE_BUCKET_NAME": "adv-docs",
                "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
                "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG",
                "AWS_S3_ENDPOINT_URL": "https://tigris.example",
                "AWS_S3_REGION_NAME": "auto",
                "AWS_S3_ADDRESSING_STYLE": "virtual",
            }
        )
        self.assertEqual(cfg["BACKEND"], S3_BACKEND)
        opts = cfg["OPTIONS"]
        self.assertEqual(opts["bucket_name"], "adv-docs")
        self.assertEqual(opts["endpoint_url"], "https://tigris.example")
        self.assertIsNone(opts["default_acl"])
        self.assertFalse(opts["file_overwrite"])
        self.assertIsNone(opts["custom_domain"])
        self.assertTrue(opts["querystring_auth"])

    def test_config_parcial_nao_cai_em_filesystem(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            resolve_default_storage(
                environ={"AWS_STORAGE_BUCKET_NAME": "adv-docs"}
            )
        msg = str(ctx.exception)
        self.assertIn("incompleto", msg)
        self.assertNotIn("adv-docs", msg)

    def test_required_sem_config_falha(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_default_storage(
                environ={"DJANGO_OBJECT_STORAGE_REQUIRED": "true"}
            )

    def test_erro_nao_inclui_secret(self):
        secret = "super-secret-value-xyz"
        with self.assertRaises(ImproperlyConfigured) as ctx:
            resolve_default_storage(
                environ={
                    "AWS_SECRET_ACCESS_KEY": secret,
                    "AWS_ACCESS_KEY_ID": "AKIATEST",
                }
            )
        self.assertNotIn(secret, str(ctx.exception))
        self.assertNotIn("AKIATEST", str(ctx.exception))
