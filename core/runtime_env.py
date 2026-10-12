"""Parsers de environment para staging/produção. Sem I/O de rede."""

from __future__ import annotations

import base64
import os
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


def resolve_asaas_baas_enabled(raw: str | None) -> bool:
    """Feature flag BaaS. Ausente, vazio ou inválido => False. Só true explícito liga."""
    value = str(raw or "").strip().lower()
    return value in _TRUE


def asaas_baas_enabled() -> bool:
    """Lê ASAAS_BAAS_ENABLED de settings. Ausente => False."""
    from django.conf import settings

    return bool(getattr(settings, "ASAAS_BAAS_ENABLED", False))


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


PROCESS_WEB = "web"
PROCESS_WORKER = "worker"
_PROCESS_ROLES = frozenset({PROCESS_WEB, PROCESS_WORKER})

ASAAS_ENV_SANDBOX = "SANDBOX"
ASAAS_ENV_PRODUCTION = "PRODUCTION"
ASAAS_BASE_URLS = {
    ASAAS_ENV_SANDBOX: "https://api-sandbox.asaas.com/v3",
    ASAAS_ENV_PRODUCTION: "https://api.asaas.com/v3",
}


class AsaasParentKeyError(Exception):
    """Chave-pai indisponível. Mensagem sem secret."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def resolve_django_process(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if not value:
        return ""
    if value not in _PROCESS_ROLES:
        raise ImproperlyConfigured("DJANGO_PROCESS inválido.")
    return value


def resolve_asaas_environment(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if value not in ASAAS_BASE_URLS:
        raise ImproperlyConfigured("ASAAS_ENVIRONMENT inválido.")
    return value


def asaas_base_url(environment: str | None) -> str:
    env = resolve_asaas_environment(environment)
    return ASAAS_BASE_URLS[env]


def resolve_public_base_url(raw: str | None, *, debug: bool = False) -> str:
    """Base canônica da aplicação. Staging/prod exige HTTPS. Sem secret."""
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise ImproperlyConfigured("PUBLIC_BASE_URL é obrigatória.")
    if "*" in value:
        raise ImproperlyConfigured("PUBLIC_BASE_URL inválida.")
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImproperlyConfigured("PUBLIC_BASE_URL inválida.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ImproperlyConfigured("PUBLIC_BASE_URL inválida.")
    if parsed.username or parsed.password:
        raise ImproperlyConfigured("PUBLIC_BASE_URL inválida.")
    if not debug and parsed.scheme != "https":
        raise ImproperlyConfigured("PUBLIC_BASE_URL exige HTTPS quando DEBUG=False.")
    return value


def build_asaas_webhook_ingress_url(*, public_id, base_url: str, debug: bool = False) -> str:
    """Monta URL pública do webhook. UUID na path; nenhum segredo."""
    import uuid as uuid_mod

    try:
        uid = uuid_mod.UUID(str(public_id))
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("webhook_public_id inválido.") from exc
    base = resolve_public_base_url(base_url, debug=debug)
    return f"{base}/financeiro/webhooks/asaas/{uid}/"


def resolve_asaas_webhook_email(raw: str | None) -> str:
    """E-mail operacional do webhook. Não é o e-mail do usuário final. Sem secret."""
    value = (raw or "").strip()
    if not value:
        raise ImproperlyConfigured("ASAAS_WEBHOOK_EMAIL é obrigatório.")
    if " " in value or value.count("@") != 1:
        raise ImproperlyConfigured("ASAAS_WEBHOOK_EMAIL inválido.")
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        raise ImproperlyConfigured("ASAAS_WEBHOOK_EMAIL inválido.")
    if any(ch in value for ch in "<>\"'"):
        raise ImproperlyConfigured("ASAAS_WEBHOOK_EMAIL inválido.")
    if len(value) > 254:
        raise ImproperlyConfigured("ASAAS_WEBHOOK_EMAIL inválido.")
    return value


def resolve_asaas_webhook_url(raw: str | None) -> str:
    """URL HTTPS final do webhook ADV. Fail-closed. Sem secret."""
    value = (raw or "").strip()
    if not value:
        raise ImproperlyConfigured("URL de webhook Asaas é obrigatória.")
    if "*" in value:
        raise ImproperlyConfigured("URL de webhook Asaas inválida.")
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path:
        raise ImproperlyConfigured("URL de webhook Asaas inválida.")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ImproperlyConfigured("URL de webhook Asaas inválida.")
    return value


def get_asaas_parent_api_key(*, process: str | None = None, raw: str | None = None) -> str:
    """Somente processo Web. Nunca logar o valor."""
    from django.conf import settings

    if process is None:
        process = getattr(settings, "DJANGO_PROCESS", "") or ""
    try:
        role = resolve_django_process(process)
    except ImproperlyConfigured as exc:
        raise AsaasParentKeyError("invalid_process") from exc
    if role != PROCESS_WEB:
        raise AsaasParentKeyError("worker" if role == PROCESS_WORKER else "not_web")
    if raw is None:
        raw = os.environ.get("ASAAS_PARENT_API_KEY")
    key = str(raw or "").strip()
    if not key:
        raise AsaasParentKeyError("missing")
    return key


def get_whatsapp_webhook_verify_token(*, raw: str | None = None) -> str:
    """Token de verificação GET da Meta. Vazio = fail-closed. Nunca logar."""
    from django.conf import settings

    if raw is None:
        raw = getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    return str(raw or "").strip()


def get_meta_whatsapp_app_secret(*, raw: str | None = None) -> str:
    """App Secret para X-Hub-Signature-256. Vazio = fail-closed. Nunca logar."""
    from django.conf import settings

    if raw is None:
        raw = getattr(settings, "META_WHATSAPP_APP_SECRET", "")
    return str(raw or "").strip()
