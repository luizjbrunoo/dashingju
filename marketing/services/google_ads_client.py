"""Cliente Google Ads em memória. Sem persistir access_token. Sem developer_token."""

from __future__ import annotations

import logging

from django.conf import settings

from organizacoes.provider_credentials import CredentialAccessError, get

logger = logging.getLogger(__name__)


class GoogleAdsClientError(Exception):
    """Falha ao montar o cliente. Sem secret."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def google_ads_client_for_organization(*, organization, login_customer_id: str = ""):
    """Constrói GoogleAdsClient só em memória para a Organization dada."""
    if organization is None or getattr(organization, "pk", None) is None:
        logger.warning("google_ads_client_failed reason=missing_org")
        raise GoogleAdsClientError("missing_org")

    client_id = str(getattr(settings, "GOOGLE_ADS_CLIENT_ID", "") or "").strip()
    client_secret = str(getattr(settings, "GOOGLE_ADS_CLIENT_SECRET", "") or "").strip()
    if not client_id or not client_secret:
        logger.warning("google_ads_client_failed reason=config")
        raise GoogleAdsClientError("config")

    try:
        payload = get(organization=organization, provider="google_ads")
    except CredentialAccessError:
        logger.warning("google_ads_client_failed reason=vault")
        raise GoogleAdsClientError("vault") from None

    refresh = str((payload or {}).get("refresh_token") or "").strip()
    if not refresh:
        logger.warning("google_ads_client_failed reason=missing_refresh")
        raise GoogleAdsClientError("missing_refresh")

    config = {
        "use_proto_plus": True,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
    }
    login_id = str(login_customer_id or "").strip()
    if login_id:
        config["login_customer_id"] = login_id

    try:
        from google.ads.googleads.client import GoogleAdsClient

        return GoogleAdsClient.load_from_dict(config)
    except Exception:
        logger.warning("google_ads_client_failed reason=client_init")
        raise GoogleAdsClientError("client_init") from None
