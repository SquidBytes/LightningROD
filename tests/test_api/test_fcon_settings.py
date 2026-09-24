"""FordConnect settings card: credentials, Connect Vehicle, paste and callback."""

from __future__ import annotations

import html
import re

import httpx
import pytest
from sqlalchemy import delete

from db.models.data_source_config import DataSourceConfig
from web.routes import fcon as fcon_routes
from web.services.secrets import is_encrypted, mask_secret
from web.services.sources.config_store import find_row, read_row
from web.services.sources.fcon.client import FordClient

pytestmark = pytest.mark.db

SECRET = "client-secret-abcdef123456"
TOKEN = {
    "access_token": "acc-token-value",
    "refresh_token": "ref-token-value",
    "expires_in": 1200,
    "refresh_token_expires_in": 7776000,
}


@pytest.fixture(autouse=True)
async def _clean(db_session, monkeypatch):
    await db_session.execute(delete(DataSourceConfig).where(DataSourceConfig.source_name == "fcon"))
    await db_session.commit()
    restarted: list[int] = []

    async def _restart(config_id):
        restarted.append(config_id)

    async def _stop(config_id):
        return None

    monkeypatch.setattr(fcon_routes.supervisor, "restart_runtime", _restart)
    monkeypatch.setattr(fcon_routes.supervisor, "stop_runtime", _stop)
    return restarted


def _ford(monkeypatch, handler):
    monkeypatch.setattr(
        fcon_routes,
        "FordClient",
        lambda: FordClient(httpx.AsyncClient(transport=httpx.MockTransport(handler))),
    )


async def _save(client, **kw):
    data = {
        "client_id": "my-client-id",
        "client_secret": SECRET,
        "redirect_uri": "http://localhost:8000/auth/ford/callback",
        **kw,
    }
    return await client.post("/settings/fcon/credentials", data=data)


async def _stored(db_session) -> dict:
    row = await find_row(db_session, "fcon")
    assert row is not None
    await db_session.refresh(row)
    return row.config_json


async def test_tab_shows_card_with_guessed_redirect(client):
    body = (await client.get("/settings/data-sources")).text
    assert "FordConnect" in body
    assert "http://localhost:8000/auth/ford/callback" in body
    assert "please confirm this is right" in body
    assert "web (UI) port" in body


async def test_redirect_derived_from_request_port(client):
    body = (await client.get("http://test:8123/settings/fcon")).text
    assert "http://localhost:8123/auth/ford/callback" in body
    assert "please confirm" not in body


async def test_credentials_sealed_and_secret_never_rendered(client, db_session):
    resp = await _save(client)
    assert resp.status_code == 200
    stored = await _stored(db_session)
    assert stored["client_id"] == "my-client-id"
    assert is_encrypted(stored["client_secret"])
    assert SECRET not in resp.text
    assert mask_secret(SECRET) in resp.text


async def test_masked_resave_keeps_secret(client, db_session):
    await _save(client)
    await _save(client, client_secret=mask_secret(SECRET))
    row = await find_row(db_session, "fcon")
    await db_session.refresh(row)
    assert read_row(row).values["client_secret"] == SECRET


async def test_missing_fields_rejected(client):
    resp = await _save(client, client_id="")
    assert resp.status_code == 422
    assert "required" in resp.text


async def _connect(client) -> str:
    resp = await client.post("/settings/fcon/connect")
    assert resp.status_code == 200
    state = re.search(r"state=([0-9a-f]{16})", resp.text)
    assert state
    return state.group(1)


async def test_connect_needs_credentials(client):
    resp = await client.post("/settings/fcon/connect")
    assert resp.status_code == 422


async def test_paste_flow_stores_sealed_tokens(client, db_session, monkeypatch, _clean):
    await _save(client)
    state = await _connect(client)
    seen = {}

    def ford(request: httpx.Request):
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json=TOKEN)

    _ford(monkeypatch, ford)
    pasted = f"  http://localhost:8000/auth/ford/callback?code=the-code&state={state}\n"
    resp = await client.post("/settings/fcon/finish", data={"pasted": pasted})
    assert resp.status_code == 200, resp.text
    assert "Vehicle connected" in resp.text
    assert seen["code"] == "the-code"
    assert seen["redirect_uri"] == "http://localhost:8000/auth/ford/callback"
    stored = await _stored(db_session)
    assert is_encrypted(stored["access_token"])
    assert is_encrypted(stored["refresh_token"])
    assert stored["oauth_state"] is None
    assert "ref-token-value" not in resp.text
    assert _clean  # runtime restarted
    # Credentials survive the token merge.
    assert stored["client_id"] == "my-client-id"


async def test_paste_errors_are_explained(client, monkeypatch):
    await _save(client)
    state = await _connect(client)
    resp = await client.post("/settings/fcon/finish", data={"pasted": "nonsense"})
    assert resp.status_code == 422
    assert "doesn't look like a URL" in html.unescape(resp.text)

    _ford(monkeypatch, lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    resp = await client.post(
        "/settings/fcon/finish",
        data={"pasted": f"http://localhost:8000/auth/ford/callback?code=x&state={state}"},
    )
    assert resp.status_code == 422
    assert "already used" in resp.text


async def test_callback_on_same_machine_finishes(client, db_session, monkeypatch):
    await _save(client)
    state = await _connect(client)
    _ford(monkeypatch, lambda r: httpx.Response(200, json=TOKEN))
    resp = await client.get(f"/auth/ford/callback?code=c&state={state}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings?tab=data_sources"
    assert (await _stored(db_session))["refresh_token"]


async def test_callback_failure_renders_reason(client):
    await _save(client)
    await _connect(client)
    resp = await client.get("/auth/ford/callback?code=c&state=ffffffffffffffff")
    assert resp.status_code == 400
    assert "doesn't match" in html.unescape(resp.text)


async def test_changing_client_id_drops_tokens(client, db_session, monkeypatch):
    await _save(client)
    state = await _connect(client)
    _ford(monkeypatch, lambda r: httpx.Response(200, json=TOKEN))
    await client.get(f"/auth/ford/callback?code=c&state={state}")
    await _save(client, client_id="another-app", client_secret=mask_secret(SECRET))
    stored = await _stored(db_session)
    assert stored["refresh_token"] is None


async def test_disconnect_clears_tokens(client, db_session, monkeypatch):
    await _save(client)
    state = await _connect(client)
    _ford(monkeypatch, lambda r: httpx.Response(200, json=TOKEN))
    await client.get(f"/auth/ford/callback?code=c&state={state}")
    resp = await client.post("/settings/fcon/disconnect")
    assert "History is kept" in resp.text
    stored = await _stored(db_session)
    assert stored["refresh_token"] is None
    assert stored["client_id"] == "my-client-id"


async def test_retention_saved_and_estimated(client):
    body = (await client.get("/settings/fcon")).text
    assert 'name="days"' in body and 'value="30"' in body
    resp = await client.post("/settings/fcon/retention", data={"days": "7"})
    assert 'value="7"' in resp.text
    est = await client.get("/settings/fcon/retention-estimate?days=365")
    assert "MB" in est.text or "GB" in est.text
