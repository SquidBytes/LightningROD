"""Async client for Ford's FordConnect Query API — GET-only, plus the token endpoint."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

import httpx

FORD_BASE_URL = "https://api.vehicle.ford.com"
AUTHORIZE_URL = f"{FORD_BASE_URL}/fcon-public/v1/auth/init"
TOKEN_URL = (
    f"{FORD_BASE_URL}/dah2vb2cprod.onmicrosoft.com/oauth2/v2.0/token"
    "?p=B2C_1A_FCON_AUTHORIZE"
)
QUERY_BASE = f"{FORD_BASE_URL}/fcon-query/v1"

ENDPOINTS = {
    "garage": "/garage",
    "telemetry": "/telemetry",
    "charge-schedules": "/electric/charge-schedules",
    "wallbox": "/wallbox",
    "vehicle-health-alerts": "/vehicle-health/alerts",
}

# Ford omitted Retry-After on every 429 captured; one refill tick plus margin.
DEFAULT_RETRY_AFTER = 62
TIMEOUT = httpx.Timeout(45.0)


class FordError(RuntimeError):
    """Ford returned an error, or was unreachable."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class RateLimited(FordError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s", status=429)
        self.retry_after = retry_after


class AuthExpired(FordError):
    """401/403: the token needs refreshing, or consent needs re-running."""


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _retry_after(headers: httpx.Headers) -> int:
    raw = (headers.get("retry-after") or "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_RETRY_AFTER


def decode_response(response: httpx.Response) -> Any:
    """Parse a Ford response, tolerating the HTML pages auth errors return."""
    status = response.status_code
    if status == 429:
        raise RateLimited(_retry_after(response.headers))
    text = response.text
    ctype = response.headers.get("content-type", "").lower()
    if "json" not in ctype:
        # B2C answers a dead token with a login page instead of JSON.
        snippet = re.sub(r"\s+", " ", text)[:200]
        if status in (401, 403):
            raise AuthExpired(f"{status} (html body)", status=status, body=snippet)
        raise FordError(f"{status} non-JSON response", status=status, body=snippet)
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError as exc:
        raise FordError(f"{status} unparseable JSON", status=status, body=text[:200]) from exc
    if status in (401, 403):
        raise AuthExpired(f"{status} unauthorized", status=status, body=text[:500])
    if status >= 400:
        raise FordError(f"{status} error", status=status, body=text[:500])
    return payload


class FordClient:
    """Stateless calls against Ford; token lifecycle lives in `TokenManager`."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> FordClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def token_request(self, form: dict[str, str]) -> dict[str, Any]:
        """POST to the token endpoint; returns Ford's token response."""
        try:
            response = await self._http.post(
                TOKEN_URL,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise FordError(f"token endpoint unreachable: {type(exc).__name__}") from exc
        token = decode_response(response)
        if not isinstance(token, dict) or "access_token" not in token:
            raise FordError("token endpoint returned no access_token")
        return token

    async def get(self, endpoint: str, access_token: str) -> Any:
        """GET one named endpoint's JSON body."""
        try:
            response = await self._http.get(
                QUERY_BASE + ENDPOINTS[endpoint],
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
        except httpx.HTTPError as exc:
            raise FordError(f"{endpoint} unreachable: {type(exc).__name__}") from exc
        return decode_response(response)
