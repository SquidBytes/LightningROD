"""FordConnect poll loop: endpoint schedule, cadence, 429 and consent handling."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.test_fcon.payloads import telemetry
from web.services.ingestion.base import IngestionRuntime
from web.services.ingestion.fcon_poller import (
    AWAKE_INTERVAL,
    PARKED_INTERVAL,
    FconPollingRuntime,
    vehicle_is_awake,
)
from web.services.sources.fcon.client import FordClient
from web.services.sources.fcon.config import FconConfig
from web.services.sources.fcon.tokens import TokenManager

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 8, 24, 17, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


def _config(**kw) -> FconConfig:
    base = {
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uri": "http://localhost:8000/auth/ford/callback",
        "access_token": "acc",
        "refresh_token": "ref",
        "expires_at": datetime(2026, 8, 24, 18, 0, tzinfo=UTC),
    }
    return FconConfig(**{**base, **kw})


class Ford:
    """Mock Ford: per-endpoint responses, records the order of calls."""

    def __init__(self, telemetry_payload=None) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, httpx.Response] = {}
        self.telemetry = telemetry_payload or telemetry()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/fcon-query/v1")
        if "token" in path:
            self.calls.append("token")
            return httpx.Response(
                200, json={"access_token": "acc-new", "expires_in": 1200}
            )
        name = {
            "/garage": "garage",
            "/telemetry": "telemetry",
            "/electric/charge-schedules": "charge-schedules",
            "/wallbox": "wallbox",
            "/vehicle-health/alerts": "vehicle-health-alerts",
        }[path]
        self.calls.append(name)
        if name in self.responses:
            return self.responses[name]
        body = self.telemetry if name == "telemetry" else {"ok": name}
        return httpx.Response(200, json=body)

    def client(self) -> FordClient:
        return FordClient(httpx.AsyncClient(transport=httpx.MockTransport(self)))


async def _noop_persist(config_id, fields):
    return None


def _runtime(clock=None, config=None, sink=None):
    samples: list[tuple[str, object]] = []

    async def record(config_id, endpoint, payload):
        samples.append((endpoint, payload))

    rt = FconPollingRuntime(
        7,
        config or _config(),
        sink or record,
        persist=_noop_persist,
        now=clock or Clock(),
    )
    return rt, samples


def _tokens(ford: Ford, clock) -> TokenManager:
    async def persist(fields):
        return None

    return TokenManager(_config(), ford.client(), persist, now=clock)


def test_runtime_satisfies_protocol():
    rt, _ = _runtime()
    assert isinstance(rt, IngestionRuntime)


def test_awake_rules():
    assert not vehicle_is_awake(telemetry())
    assert vehicle_is_awake(telemetry(ignitionStatus="ON"))
    assert vehicle_is_awake(telemetry(xevPlugChargerStatus="CONNECTED"))
    assert vehicle_is_awake(telemetry(xevBatteryChargeDisplayStatus="IN_PROGRESS"))


async def test_first_tick_polls_everything_then_only_telemetry():
    clock = Clock()
    ford = Ford()
    rt, samples = _runtime(clock)
    client = ford.client()
    tokens = _tokens(ford, clock)
    assert await rt.tick(client, tokens) == PARKED_INTERVAL
    assert ford.calls == [
        "garage", "charge-schedules", "telemetry", "wallbox", "vehicle-health-alerts",
    ]
    ford.calls.clear()
    await rt.tick(client, tokens)
    assert ford.calls == ["telemetry"]
    assert [e for e, _ in samples].count("telemetry") == 2
    health = rt.health
    assert health["connection_state"] == "polling"
    assert health["newest_reading_at"].startswith("2026-08-24T16:59:31")
    assert health["newest_reading_age"] == "29s ago"


async def test_awake_vehicle_polls_every_minute():
    clock = Clock()
    ford = Ford(telemetry(ignitionStatus="ON"))
    rt, _ = _runtime(clock)
    assert await rt.tick(ford.client(), _tokens(ford, clock)) == AWAKE_INTERVAL


async def test_extras_every_30th_tick_and_schedules_daily():
    clock = Clock()
    ford = Ford()
    rt, _ = _runtime(clock)
    client, tokens = ford.client(), _tokens(ford, clock)
    for _ in range(30):
        await rt.tick(client, tokens)
    ford.calls.clear()
    await rt.tick(client, tokens)
    assert ford.calls == ["telemetry", "wallbox", "vehicle-health-alerts"]
    ford.calls.clear()
    clock.at += timedelta(days=1)
    await rt.tick(client, tokens)
    assert [c for c in ford.calls if c != "token"] == ["charge-schedules", "telemetry"]


async def test_429_skips_rest_of_tick_and_backs_off():
    clock = Clock()
    ford = Ford()
    ford.responses["charge-schedules"] = httpx.Response(429, headers={"Retry-After": "90"})
    rt, samples = _runtime(clock)
    delay = await rt.tick(ford.client(), _tokens(ford, clock))
    assert delay == 90
    assert ford.calls == ["garage", "charge-schedules"]
    assert rt.health["connection_state"] == "rate_limited"
    # Missing Retry-After still waits at least a minute.
    ford.responses["charge-schedules"] = httpx.Response(429)
    assert await rt.tick(ford.client(), _tokens(ford, clock)) == 62


async def test_endpoint_error_does_not_stop_the_tick():
    clock = Clock()
    ford = Ford()
    ford.responses["garage"] = httpx.Response(500, json={"error": "x"})
    rt, samples = _runtime(clock)
    await rt.tick(ford.client(), _tokens(ford, clock))
    assert "telemetry" in [e for e, _ in samples]
    assert rt.health["errors"] == 1
    assert rt.health["last_error"].startswith("garage:")


async def test_dead_access_token_refreshes_next_tick():
    clock = Clock()
    ford = Ford()
    ford.responses["garage"] = httpx.Response(401, json={"error": "expired"})
    rt, _ = _runtime(clock)
    client, tokens = ford.client(), _tokens(ford, clock)
    assert await rt.tick(client, tokens) == AWAKE_INTERVAL
    del ford.responses["garage"]
    ford.calls.clear()
    await rt.tick(client, tokens)
    assert ford.calls[0] == "token"


async def test_rejected_refresh_stops_polling():
    clock = Clock()
    ford = Ford()
    rt, _ = _runtime(clock, config=_config())
    tokens = TokenManager(
        _config(expires_at=clock.at), FordClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(400, json={"error": "invalid_grant"})
                )
            )
        ), lambda f: _noop_persist(0, f), now=clock
    )
    assert await rt.tick(ford.client(), tokens) is None
    assert rt.health["connection_state"] == "needs_consent"


async def test_start_without_consent_returns_immediately():
    cfg = FconConfig(client_id="c", client_secret="s", redirect_uri="http://localhost/x")
    rt, _ = _runtime(config=cfg)
    await asyncio.wait_for(rt.start(), timeout=1)
    assert rt.health["connection_state"] == "needs_consent"


async def test_start_and_stop():
    ford = Ford()
    rt, samples = _runtime()
    rt._client_factory = ford.client
    task = asyncio.create_task(rt.start())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if samples:
            break
    await rt.stop()
    await asyncio.wait_for(task, timeout=1)
    assert samples
    assert rt.health["connection_state"] == "stopped"


def test_source_builds_poller_from_decrypted_config():
    from db.models.data_source_config import DataSourceConfig
    from web.services.sources.fcon import DATA_SOURCE

    row = DataSourceConfig(id=3, source_name="fcon", instance_label="default", config_json={})
    assert DATA_SOURCE.build_runtime is not None
    rt = DATA_SOURCE.build_runtime(row, _config().model_dump(mode="json"))
    assert isinstance(rt, FconPollingRuntime)
    assert (rt.config_id, rt.instance_label) == (3, "default")
