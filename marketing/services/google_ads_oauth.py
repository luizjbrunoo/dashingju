"""OAuth Google Ads por Organization. Sem GAQL, sem persistir access_token."""

from __future__ import annotations

import hmac
import logging
import secrets
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from google_auth_oauthlib.flow import Flow

from core.runtime_env import resolve_google_ads_oauth_config
from financeiro.tenancy_write import organization_for_finance_write
from marketing.models import OrganizationGoogleAdsConnection
from organizacoes.provider_credentials import CredentialAccessError, delete, put

logger = logging.getLogger(__name__)

SESSION_KEY = "google_ads_oauth"
OAUTH_SCOPE = "https://www.googleapis.com/auth/adwords"
TTL_SECONDS = 600
FUTURE_SKEW_SECONDS = 30
_DENIED = frozenset({"access_denied", "consent_denied"})

MSG_OAUTH_FALHOU = "Não foi possível concluir a conexão com o Google Ads."
MSG_OAUTH_OK = "Conta Google Ads conectada."
MSG_DISCONNECT_OK = "Conta Google Ads desconectada."


class GoogleAdsOAuthStateError(Exception):
    """State inválido, expirado ou reutilizado. Sem secret."""


class GoogleAdsOAuthDenied(Exception):
    """Usuário recusou o consentimento no Google."""


class GoogleAdsOAuthError(Exception):
    """Falha de OAuth sem detalhe de token."""


def oauth_config():
    return resolve_google_ads_oauth_config(
        debug=bool(settings.DEBUG),
        client_id=getattr(settings, "GOOGLE_ADS_CLIENT_ID", ""),
        client_secret=getattr(settings, "GOOGLE_ADS_CLIENT_SECRET", ""),
        redirect_uri=getattr(settings, "GOOGLE_ADS_OAUTH_REDIRECT_URI", ""),
    )


def _flow(*, state: str | None = None):
    client_id, client_secret, redirect_uri = oauth_config()
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=[OAUTH_SCOPE],
        redirect_uri=redirect_uri,
        state=state,
    )
    return flow


def start_authorization_url(request) -> str:
    organization = organization_for_finance_write(request)
    if organization is None:
        raise GoogleAdsOAuthError("tenant")
    try:
        oauth_config()
    except ImproperlyConfigured:
        logger.warning("google_ads_oauth_failed reason=config")
        raise GoogleAdsOAuthError("config") from None
    nonce = secrets.token_urlsafe(32)
    request.session[SESSION_KEY] = {
        "state": nonce,
        "organization_id": int(organization.pk),
        "user_id": int(request.user.pk),
        "issued_at": int(time.time()),
    }
    request.session.modified = True
    flow = _flow(state=nonce)
    auth_url, _returned = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=nonce,
    )
    return auth_url


def _pending_or_error(request) -> dict:
    pending = request.session.get(SESSION_KEY)
    if not isinstance(pending, dict):
        raise GoogleAdsOAuthStateError("missing_session")
    return pending


def consume_valid_oauth_state(request) -> dict:
    """Valida state; pop() imediato após compare_digest, antes de fetch_token."""
    pending = _pending_or_error(request)
    raw_state = str(request.GET.get("state") or "").strip()
    stored_state = str(pending.get("state") or "").strip()
    if not raw_state or not stored_state:
        raise GoogleAdsOAuthStateError("missing_state")
    if not hmac.compare_digest(stored_state, raw_state):
        raise GoogleAdsOAuthStateError("state_mismatch")

    request.session.pop(SESSION_KEY, None)
    request.session.modified = True

    organization = organization_for_finance_write(request)
    if organization is None:
        raise GoogleAdsOAuthStateError("tenant")
    try:
        stored_org = int(pending.get("organization_id"))
        stored_user = int(pending.get("user_id"))
        issued_at = pending.get("issued_at")
        if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
            raise GoogleAdsOAuthStateError("issued_at")
        issued_at = float(issued_at)
    except (TypeError, ValueError, GoogleAdsOAuthStateError):
        raise GoogleAdsOAuthStateError("issued_at") from None

    now = time.time()
    if issued_at > now + FUTURE_SKEW_SECONDS:
        raise GoogleAdsOAuthStateError("issued_at_future")
    if now - issued_at > TTL_SECONDS:
        raise GoogleAdsOAuthStateError("expired")
    if stored_user != int(request.user.pk):
        raise GoogleAdsOAuthStateError("user_mismatch")
    if stored_org != int(organization.pk):
        raise GoogleAdsOAuthStateError("org_mismatch")
    return pending


def google_denied(request) -> bool:
    error = str(request.GET.get("error") or "").strip().lower()
    return error in _DENIED


def complete_authorization(request) -> None:
    consume_valid_oauth_state(request)
    if google_denied(request):
        logger.warning("google_ads_oauth_failed reason=access_denied")
        raise GoogleAdsOAuthDenied()
    code = str(request.GET.get("code") or "").strip()
    if not code:
        logger.warning("google_ads_oauth_failed reason=missing_code")
        raise GoogleAdsOAuthError("missing_code")

    organization = organization_for_finance_write(request)
    if organization is None:
        raise GoogleAdsOAuthError("tenant")

    try:
        flow = _flow(state=str(request.GET.get("state") or ""))
        flow.fetch_token(code=code)
        refresh = getattr(getattr(flow, "credentials", None), "refresh_token", None)
        refresh = str(refresh or "").strip()
    except GoogleAdsOAuthError:
        raise
    except Exception:
        logger.warning("google_ads_oauth_failed reason=token_exchange")
        raise GoogleAdsOAuthError("token_exchange") from None

    if not refresh:
        logger.warning("google_ads_oauth_failed reason=missing_refresh")
        raise GoogleAdsOAuthError("missing_refresh")

    try:
        with transaction.atomic():
            put(
                organization=organization,
                provider="google_ads",
                secrets={"refresh_token": refresh},
            )
            conn, _created = OrganizationGoogleAdsConnection.objects.get_or_create(
                organization=organization,
                defaults={"status": OrganizationGoogleAdsConnection.Status.AUTHORIZED},
            )
            conn.status = OrganizationGoogleAdsConnection.Status.AUTHORIZED
            conn.connected_at = timezone.now()
            conn.save(
                update_fields=["status", "connected_at", "updated_at"],
            )
    except CredentialAccessError:
        logger.warning("google_ads_oauth_failed reason=vault")
        raise GoogleAdsOAuthError("vault") from None


def disconnect_organization(request) -> None:
    organization = organization_for_finance_write(request)
    if organization is None:
        raise GoogleAdsOAuthError("tenant")
    with transaction.atomic():
        delete(organization=organization, provider="google_ads")
        conn, _created = OrganizationGoogleAdsConnection.objects.get_or_create(
            organization=organization,
            defaults={"status": OrganizationGoogleAdsConnection.Status.DISCONNECTED},
        )
        conn.status = OrganizationGoogleAdsConnection.Status.DISCONNECTED
        conn.customer_id = ""
        conn.login_customer_id = ""
        conn.descriptive_name = ""
        conn.currency_code = ""
        conn.connected_at = None
        conn.save()
