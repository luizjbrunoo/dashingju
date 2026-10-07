"""Parsers de environment para staging/produção. Sem secrets e sem I/O de rede."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

DEV_SECRET_KEY = "django-insecure-jq&o389#*bm(#$iw=f@2c86o=1j(+228)uqifq3qxx&hcbe7sj"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_bool(raw: str | None, *, default: bool, name: str) -> bool:
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name} inválido. Use true/false.")


def env_int(raw: str | None, *, default: int, name: str) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} deve ser um inteiro.") from exc


def parse_allowed_hosts(raw: str | None) -> list[str]:
    hosts = []
    for item in (raw or "").split(","):
        host = item.strip()
        if not host:
            continue
        if host == "*":
            raise ImproperlyConfigured("ALLOWED_HOSTS não aceita wildcard.")
        if "://" in host:
            raise ImproperlyConfigured(
                "ALLOWED_HOSTS não deve incluir protocolo (http/https)."
            )
        hosts.append(host)
    return hosts


def parse_csrf_trusted_origins(raw: str | None) -> list[str]:
    origins = []
    for item in (raw or "").split(","):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        if not (origin.startswith("http://") or origin.startswith("https://")):
            raise ImproperlyConfigured(
                "CSRF_TRUSTED_ORIGINS exige esquema http:// ou https://."
            )
        origins.append(origin)
    return origins


def resolve_secret_key(*, debug: bool, raw: str | None) -> str:
    value = (raw or "").strip()
    if debug:
        return value or DEV_SECRET_KEY
    if not value:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY é obrigatória quando DEBUG=False."
        )
    if value == DEV_SECRET_KEY:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY de DEV não pode ser usada com DEBUG=False."
        )
    return value


def resolve_allowed_hosts(*, debug: bool, raw: str | None) -> list[str]:
    hosts = parse_allowed_hosts(raw)
    if debug:
        return hosts
    if not hosts:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS é obrigatório quando DEBUG=False."
        )
    return hosts
