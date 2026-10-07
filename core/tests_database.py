"""PREPROD-POSTGRES-READINESS-01 — parser de DATABASE_URL sem conectar."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.database import databases_from_env

SQLITE = Path("db.sqlite3")


class DatabaseEnvConfigTests(SimpleTestCase):
    def test_vazio_usa_sqlite_dev(self):
        db = databases_from_env(database_url="", sqlite_path=SQLITE)
        self.assertEqual(db["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(db["default"]["NAME"], SQLITE)

    def test_postgres_url_mapeia_engine_sem_conectar(self):
        db = databases_from_env(
            database_url="postgres://adv:s3cret@db.example:5432/advgrowth?sslmode=require",
            sqlite_path=SQLITE,
            conn_max_age=60,
        )
        cfg = db["default"]
        self.assertEqual(cfg["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(cfg["NAME"], "advgrowth")
        self.assertEqual(cfg["USER"], "adv")
        self.assertEqual(cfg["HOST"], "db.example")
        self.assertEqual(cfg["PORT"], "5432")
        self.assertEqual(cfg["OPTIONS"]["sslmode"], "require")
        self.assertEqual(cfg["CONN_MAX_AGE"], 60)
        self.assertTrue(cfg["CONN_HEALTH_CHECKS"])
        self.assertIn("PASSWORD", cfg)

    def test_sqlite_url_windows_path(self):
        db = databases_from_env(
            database_url="sqlite:///C:/tmp/empty.sqlite3",
            sqlite_path=SQLITE,
        )
        self.assertEqual(db["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(db["default"]["NAME"], "C:/tmp/empty.sqlite3")

    def test_esquema_invalido(self):
        with self.assertRaises(ImproperlyConfigured):
            databases_from_env(database_url="mysql://localhost/x", sqlite_path=SQLITE)

    def test_postgres_sem_nome_do_banco(self):
        with self.assertRaises(ImproperlyConfigured):
            databases_from_env(database_url="postgres://adv@db.example:5432", sqlite_path=SQLITE)
