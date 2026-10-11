"""Cliente Asaas — POST/GET /v3/accounts. Transport injetável. Sem Mutate."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from core.runtime_env import (
    ASAAS_BASE_URLS,
    asaas_base_url,
    resolve_asaas_environment,
)

DEFAULT_TIMEOUT_SECONDS = 30
API_TOKEN_HEADER = "access_token"
WEBHOOK_TOKEN_HEADER = "asaas-access-token"
USER_AGENT = "ADV Growth 90"
ACCOUNTS_PATH = "/accounts"
LIST_ACCOUNT_FILTERS = frozenset(
    {"offset", "limit", "cpfCnpj", "email", "name", "walletId"}
)
CONCLUSIVE_CLIENT_STATUSES = frozenset({400, 401, 403, 404, 422})


class AsaasClientError(Exception):
    """Falha sanitizada do client. Sem secret."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"<AsaasClientError reason={self.reason}>"


class AsaasTimeoutError(AsaasClientError):
    def __init__(self):
        super().__init__("timeout")


class AsaasHttpError(AsaasClientError):
    def __init__(self, *, reason: str, status: int | None = None, error_code: str = ""):
        self.status = status
        self.error_code = error_code
        super().__init__(reason)


Transport = Callable[..., "AsaasHttpResponse"]


@dataclass(frozen=True)
class AsaasHttpResponse:
    status: int
    payload: Any


def asaas_api_headers(*, api_key: str) -> dict[str, str]:
    token = str(api_key or "").strip()
    if not token:
        raise AsaasClientError("missing_credential")
    return {
        API_TOKEN_HEADER: token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _sanitize_error_code(raw) -> str:
    text = str(raw or "").strip().upper().replace(" ", "_").replace("-", "_")[:64]
    if not text:
        return ""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if any(ch not in allowed for ch in text):
        return ""
    return text


def _error_code_from_payload(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return _sanitize_error_code(first.get("code"))
    return _sanitize_error_code(payload.get("code"))


def _parse_json_body(raw: bytes | str | None) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AsaasClientError("invalid_json") from exc


def urllib_transport(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: int,
) -> AsaasHttpResponse:
    """HTTP real. Nunca logar headers, body ou resposta. Só via acionamento explícito."""
    request = urllib.request.Request(
        url,
        data=body,
        method=str(method or "GET").upper(),
        headers={k: v for k, v in (headers or {}).items() if k and v is not None},
    )
    try:
        with urllib.request.urlopen(request, timeout=int(timeout)) as response:
            raw = response.read()
            payload = _parse_json_body(raw)
            return AsaasHttpResponse(status=int(response.status), payload=payload)
    except socket.timeout as exc:
        raise AsaasTimeoutError() from exc
    except TimeoutError as exc:
        raise AsaasTimeoutError() from exc
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        try:
            payload = _parse_json_body(raw)
        except AsaasClientError:
            payload = None
        error_code = _error_code_from_payload(payload)
        reason = f"http_{status}" if status else "http_error"
        raise AsaasHttpError(
            reason=reason,
            status=status,
            error_code=error_code,
        ) from None
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", "") or "")
        lowered = reason.lower()
        if "timed out" in lowered or "timeout" in lowered:
            raise AsaasTimeoutError() from None
        raise AsaasClientError("inconclusive") from None


class AsaasClient:
    """Client mockável. api_key explícita. webhook_token não é parâmetro."""

    def __init__(
        self,
        *,
        environment: str,
        api_key: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        transport: Transport | None = None,
    ):
        self.environment = resolve_asaas_environment(environment)
        self.base_url = asaas_base_url(self.environment)
        self.timeout = int(timeout)
        self._api_key = str(api_key or "").strip()
        self._transport = transport
        if not self._api_key:
            raise AsaasClientError("missing_credential")
        if self.base_url not in ASAAS_BASE_URLS.values():
            raise AsaasClientError("invalid_base_url")
        if self.timeout <= 0:
            raise AsaasClientError("invalid_timeout")

    def api_headers(self) -> dict[str, str]:
        return asaas_api_headers(api_key=self._api_key)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        query: dict | None = None,
    ) -> AsaasHttpResponse:
        if self._transport is None:
            raise AsaasClientError("http_disabled")
        verb = str(method or "").strip().upper()
        if verb not in {"GET", "POST"}:
            raise AsaasClientError("invalid_method")
        rel = str(path or "").strip()
        if not rel.startswith("/") or "://" in rel or ".." in rel:
            raise AsaasClientError("invalid_path")
        url = f"{self.base_url.rstrip('/')}{rel}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        body = None
        if json_body is not None:
            if verb != "POST":
                raise AsaasClientError("invalid_method")
            body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        try:
            result = self._transport(
                method=verb,
                url=url,
                headers=self.api_headers(),
                body=body,
                timeout=self.timeout,
            )
        except (AsaasTimeoutError, AsaasHttpError, AsaasClientError):
            raise
        except socket.timeout:
            raise AsaasTimeoutError() from None
        except TimeoutError:
            raise AsaasTimeoutError() from None
        except Exception:
            raise AsaasClientError("inconclusive") from None
        if not isinstance(result, AsaasHttpResponse):
            raise AsaasClientError("invalid_response")
        return result

    def create_account(self, payload: dict) -> AsaasHttpResponse:
        if not isinstance(payload, dict) or not payload:
            raise AsaasClientError("invalid_payload")
        return self.request("POST", ACCOUNTS_PATH, json_body=payload)

    def list_accounts(self, **filters) -> AsaasHttpResponse:
        query: dict[str, str] = {}
        for key, raw in filters.items():
            name = str(key).strip()
            if name not in LIST_ACCOUNT_FILTERS:
                raise AsaasClientError("unsupported_filter")
            if raw is None:
                continue
            text = str(raw).strip()
            if not text:
                continue
            query[name] = text
        return self.request("GET", ACCOUNTS_PATH, query=query or None)

    def __repr__(self) -> str:
        return f"<AsaasClient env={self.environment}>"

    def __str__(self) -> str:
        return f"AsaasClient({self.environment})"
