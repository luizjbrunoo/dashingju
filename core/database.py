"""DATABASES a partir de env: SQLite DEV; PostgreSQL via DATABASE_URL.

Não conecta a nenhum banco. Não contém credenciais.
Staging/prod devem injetar DATABASE_URL externamente.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

_PG_SCHEMES = {"postgres", "postgresql"}
_SQLITE_SCHEMES = {"", "sqlite", "sqlite3"}


def _sqlite_name_from_url_path(path: str, sqlite_path):
    cleaned = unquote(path or "")
    if cleaned.startswith("/") and len(cleaned) >= 3 and cleaned[2] == ":":
        cleaned = cleaned[1:]
    if not cleaned or cleaned == "/":
        return sqlite_path
    return cleaned


def databases_from_env(*, database_url: str, sqlite_path, conn_max_age: int = 0) -> dict:
    raw = (database_url or "").strip()
    if not raw:
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": sqlite_path,
            }
        }

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower().split("+")[0]

    if scheme in _SQLITE_SCHEMES:
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": _sqlite_name_from_url_path(parsed.path, sqlite_path),
            }
        }

    if scheme not in _PG_SCHEMES:
        raise ImproperlyConfigured(
            "DATABASE_URL usa um esquema não suportado. Use sqlite, postgres ou postgresql."
        )

    name = unquote((parsed.path or "").lstrip("/"))
    if not name:
        raise ImproperlyConfigured(
            "DATABASE_URL PostgreSQL precisa incluir o nome do banco."
        )

    options = {}
    query = parse_qs(parsed.query)
    sslmode = (query.get("sslmode") or [None])[0]
    if sslmode:
        options["sslmode"] = sslmode

    cfg = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": conn_max_age,
        "CONN_HEALTH_CHECKS": True,
    }
    if options:
        cfg["OPTIONS"] = options
    return {"default": cfg}
