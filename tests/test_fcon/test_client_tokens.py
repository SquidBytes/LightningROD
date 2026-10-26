"""Ford client decoding and the token refresh loop, against a mock transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from web.services.sources.fcon.client import (
    AuthExpired,
    FordClient,
    FordError,
    RateLimited,
)
from web.services.sources.fcon.config import FconConfig
from web.services.sources.fcon.tokens import (
    ConsentRequired,
    TokenManager,
    exchange_code,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
TOKEN = {
    "access_token": "acc-2",
    "refresh_token": "ref-2",
    "token_type": "Bearer",
    "expires_in": 1200,
    "refresh_token_expires_in": 7776000,
}


def _client(handler) -> FordClient:
    return FordClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _json(status: int, body, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def _config(**kw) -> FconConfig:
    base = {
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uri": "http://localhost:8000/auth/ford/callback",
    }
    return FconConfig(**{**base, **kw})


async def test_429_uses_retry_after_or_62():
    c = _client(lambda r: _json(429, {}, {"Retry-After": "30"}))
    with pytest.raises(RateLimited) as exc:
        await c.get("telemetry", "t")
    assert exc.value.retry_after == 30
    c = _client(lambda r: httpx.Response(429, text=""))
    with pytest.raises(RateLimited) as exc:
        await c.get("telemetry", "t")
    assert exc.value.retry_after == 62


async def test_html_401_is_auth_expired():
    c = _client(lambda r: httpx.Response(401, text="<html>login</html>", headers={"content-type": "text/html"}))
    with pytest.raises(AuthExpired):
        await c.get("telemetry", "t")


async def test_get_sends_bearer_and_parses():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["path"] = request.url.path
        return _json(200, {"metrics": {}})

    assert await _client(handler).get("telemetry", "tok") == {"metrics": {}}
    assert seen == {"auth": "Bearer tok", "path": "/fcon-query/v1/telemetry"}


async def test_network_failure_is_ford_error_without_status():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(FordError) as exc:
        await _client(handler).get("telemetry", "t")
    assert exc.value.status is None


async def test_exchange_code_replays_redirect_uri():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return _json(200, TOKEN)

    cfg = await exchange_code(_client(handler), _config(oauth_state="s"), "the-code")
    assert seen["grant_type"] == "authorization_code"
    assert seen["redirect_uri"] == "http://localhost:8000/auth/ford/callback"
    assert cfg.refresh_token == "ref-2"
    assert cfg.oauth_state is None
    assert cfg.connected


async def test_live_token_is_reused_without_a_call():
    cfg = _config(access_token="acc", refresh_token="ref", expires_at=NOW + timedelta(minutes=10))

    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("refreshed a live token")

    tm = TokenManager(cfg, _client(handler), persist=_noop, now=lambda: NOW)
    assert await tm.access_token() == "acc"


async def test_refreshes_inside_90s_and_persists_tokens():
    cfg = _config(access_token="acc", refresh_token="ref", expires_at=NOW + timedelta(seconds=60))
    persisted: list[dict] = []

    async def persist(fields):
        persisted.append(fields)

    tm = TokenManager(cfg, _client(lambda r: _json(200, TOKEN)), persist, now=lambda: NOW)
    assert await tm.access_token() == "acc-2"
    [fields] = persisted
    assert fields["refresh_token"] == "ref-2"
    assert "client_secret" not in fields


async def test_refresh_keeps_old_refresh_token_when_not_rotated():
    cfg = _config(access_token="a", refresh_token="keep", expires_at=NOW)
    token = {k: v for k, v in TOKEN.items() if k != "refresh_token"}
    tm = TokenManager(cfg, _client(lambda r: _json(200, token)), _noop, now=lambda: NOW)
    await tm.access_token()
    assert tm.config.refresh_token == "keep"


async def test_rejected_refresh_requires_consent():
    cfg = _config(access_token="a", refresh_token="dead", expires_at=NOW)
    persisted: list[dict] = []

    async def persist(fields):
        persisted.append(fields)

    tm = TokenManager(
        cfg, _client(lambda r: _json(400, {"error": "invalid_grant"})), persist, now=lambda: NOW
    )
    with pytest.raises(ConsentRequired):
        await tm.access_token()
    assert persisted[-1]["needs_consent"] is True
    with pytest.raises(ConsentRequired):
        await tm.access_token()


async def test_server_error_on_refresh_is_not_consent():
    cfg = _config(access_token="a", refresh_token="r", expires_at=NOW)
    tm = TokenManager(cfg, _client(lambda r: _json(503, {})), _noop, now=lambda: NOW)
    with pytest.raises(FordError) as exc:
        await tm.access_token()
    assert not isinstance(exc.value, ConsentRequired)
    assert not tm.config.needs_consent


async def _noop(fields):
    return None
