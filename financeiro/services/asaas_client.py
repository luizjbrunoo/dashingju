"""Cliente Asaas — fundação. Sem HTTP nesta fase. Sem Mutate."""

from __future__ import annotations

from core.runtime_env import (
    ASAAS_BASE_URLS,
    asaas_base_url,
    resolve_asaas_environment,
)

DEFAULT_TIMEOUT_SECONDS = 30
API_TOKEN_HEADER = "access_token"
WEBHOOK_TOKEN_HEADER = "asaas-access-token"


class AsaasClientError(Exception):
    """Falha sanitizada do client. Sem secret."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def asaas_api_headers(*, api_key: str) -> dict[str, str]:
    token = str(api_key or "").strip()
    if not token:
        raise AsaasClientError("missing_credential")
    return {
        API_TOKEN_HEADER: token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


class AsaasClient:
    """Client mockável. api_key explícita. webhook_token não é parâmetro."""

    def __init__(self, *, environment: str, api_key: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.environment = resolve_asaas_environment(environment)
        self.base_url = asaas_base_url(self.environment)
        self.timeout = int(timeout)
        self._api_key = str(api_key or "").strip()
        if not self._api_key:
            raise AsaasClientError("missing_credential")
        if self.base_url not in ASAAS_BASE_URLS.values():
            raise AsaasClientError("invalid_base_url")

    def api_headers(self) -> dict[str, str]:
        return asaas_api_headers(api_key=self._api_key)

    def request(self, *args, **kwargs):
        raise AsaasClientError("http_disabled")

    def __repr__(self) -> str:
        return f"<AsaasClient env={self.environment}>"

    def __str__(self) -> str:
        return f"AsaasClient({self.environment})"
