"""Parsers de environment para staging/produção. Sem I/O de rede."""

from __future__ import annotations

import base64
from dataclasses import dataclass

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


KEY_SOURCE_ENV = "env"
KEY_SOURCE_DEV_DERIVED = "dev-derived"
_DEV_FERNET_SALT = b"dashingju.integration-credentials.v1"
_DEV_FERNET_INFO = b"dev-derived"


def is_valid_fernet_key(raw: str | None) -> bool:
    value = (raw or "").strip()
    if not value:
        return False
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode("utf-8"))
    except (ValueError, TypeError, Exception):
        return False
    return True


def derive_dev_fernet_key(secret_key: str) -> str:
    """Fernet derivada da SECRET_KEY. Somente DEBUG/teste. Nunca usar em DEBUG=False."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_DEV_FERNET_SALT,
        info=_DEV_FERNET_INFO,
    ).derive(str(secret_key).encode("utf-8"))
    return base64.urlsafe_b64encode(material).decode("ascii")


@dataclass(frozen=True)
class IntegrationCredentialsKey:
    value: str
    source: str

    def __repr__(self) -> str:
        return f"IntegrationCredentialsKey(source={self.source!r})"

    def __str__(self) -> str:
        return f"IntegrationCredentialsKey(source={self.source})"


def resolve_integration_credentials_key(
    *,
    debug: bool,
    raw: str | None,
    secret_key: str,
) -> IntegrationCredentialsKey:
    value = (raw or "").strip()
    if debug:
        if value:
            if not is_valid_fernet_key(value):
                raise ImproperlyConfigured(
                    "INTEGRATION_CREDENTIALS_KEY inválida."
                )
            return IntegrationCredentialsKey(value=value, source=KEY_SOURCE_ENV)
        derived = derive_dev_fernet_key(secret_key)
        return IntegrationCredentialsKey(
            value=derived, source=KEY_SOURCE_DEV_DERIVED
        )
    if not value:
        raise ImproperlyConfigured(
            "INTEGRATION_CREDENTIALS_KEY é obrigatória quando DEBUG=False."
        )
    if not is_valid_fernet_key(value):
        raise ImproperlyConfigured("INTEGRATION_CREDENTIALS_KEY inválida.")
    if value == derive_dev_fernet_key(DEV_SECRET_KEY):
        raise ImproperlyConfigured(
            "INTEGRATION_CREDENTIALS_KEY de DEV não pode ser usada com DEBUG=False."
        )
    return IntegrationCredentialsKey(value=value, source=KEY_SOURCE_ENV)


def resolve_google_ads_oauth_redirect_uri(*, debug: bool, raw: str | None) -> str:
    """Redirect URI exclusiva de configuração. Sem inferência pelo request."""
    value = (raw or "").strip()
    if not value:
        raise ImproperlyConfigured("GOOGLE_ADS_OAUTH_REDIRECT_URI é obrigatória.")
    if "*" in value:
        raise ImproperlyConfigured("GOOGLE_ADS_OAUTH_REDIRECT_URI inválida.")
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path:
        raise ImproperlyConfigured("GOOGLE_ADS_OAUTH_REDIRECT_URI inválida.")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ImproperlyConfigured("GOOGLE_ADS_OAUTH_REDIRECT_URI inválida.")
    if not debug and parsed.scheme != "https":
        raise ImproperlyConfigured(
            "GOOGLE_ADS_OAUTH_REDIRECT_URI exige HTTPS quando DEBUG=False."
        )
    return value


def resolve_google_ads_oauth_config(
    *,
    debug: bool,
    client_id: str | None,
    client_secret: str | None,
    redirect_uri: str | None,
) -> tuple[str, str, str]:
    cid = (client_id or "").strip()
    secret = (client_secret or "").strip()
    if not cid or not secret:
        raise ImproperlyConfigured("OAuth Google Ads indisponível.")
    redirect = resolve_google_ads_oauth_redirect_uri(debug=debug, raw=redirect_uri)
    return cid, secret, redirect


def resolve_allowed_hosts(*, debug: bool, raw: str | None) -> list[str]:
    hosts = parse_allowed_hosts(raw)
    if debug:
        return hosts
    if not hosts:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS é obrigatório quando DEBUG=False."
        )
    return hosts
