"""IngestionSupervisor lifecycle tests.

Covers ``start_all`` / ``stop_all`` / ``restart_runtime`` / ``stop_runtime``
semantics plus the data_source_configs read invariant — the supervisor
spawns one runtime per enabled row natively (replacement coverage for the
deleted credential-shim test that locked the legacy fallback shim).

These tests use a fake runtime class so the lifecycle assertions don't
require a live WebSocket server. ``AsyncSessionLocal`` is monkeypatched to
yield the test transaction so supervisor reads see the test fixtures.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import delete

from db.models.data_source_config import DataSourceConfig
from web.services.ingestion.base import IngestionRuntime
from web.services.ingestion.supervisor import IngestionSupervisor

pytestmark = [pytest.mark.unit, pytest.mark.db]


class _FakeRuntime:
    """Records start/stop calls; never opens a real connection."""

    def __init__(
        self,
        config_id: int,
        ha_url: str,
        ha_token: str,
        *,
        source_name: str = "ha_fordpass",
        instance_label: str = "default",
    ) -> None:
        self.config_id = config_id
        self.source_name = source_name
        self.instance_label = instance_label
        self._ha_url = ha_url
        self._ha_token = ha_token
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True
        # Return immediately so the asyncio.create_task wrapping in the
        # supervisor completes quickly. Production runtimes block here in
        # their event loop; for the lifecycle assertions we only care that
        # `start` was awaited.

    async def stop(self) -> None:
        self.stopped = True

    @property
    def health(self) -> dict[str, Any]:
        return {
            "connected": self.started and not self.stopped,
            "connection_state": (
                "connected"
                if self.started and not self.stopped
                else "disconnected"
            ),
        }


class _FakeAsyncSessionLocal:
    """Yields the test session inside an async-with that doesn't close it.

    Mimics ``async with AsyncSessionLocal() as db:`` — the production
    sessionmaker returns a fresh session per call; we instead hand back the
    long-lived test session so commits land in the same savepoint that the
    test fixture rolls back.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_):
        return False


@pytest.fixture
def patched_supervisor_module(monkeypatch, db_session):
    """Patch ``_RUNTIME_CLASSES`` and ``AsyncSessionLocal`` on the supervisor module."""
    import importlib

    # ``from web.services.ingestion import supervisor`` resolves to the
    # IngestionSupervisor instance (the package re-exports the singleton),
    # so ``import x.y.z as alias`` no longer yields the module object.
    # importlib.import_module returns the actual module.
    sup_module = importlib.import_module("web.services.ingestion.supervisor")

    monkeypatch.setitem(sup_module._RUNTIME_CLASSES, "ha_fordpass", _FakeRuntime)
    monkeypatch.setattr(
        sup_module, "AsyncSessionLocal", _FakeAsyncSessionLocal(db_session)
    )
    return sup_module




async def _seed_config_row(db, *, enabled: bool = True) -> int:
    """Insert one ha_fordpass:default row; return the assigned id."""
    await db.execute(delete(DataSourceConfig))
    row = DataSourceConfig(
        source_name="ha_fordpass",
        instance_label="default",
        config_json={"ha_url": "http://example.test", "ha_token": "tok"},
        enabled=enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.id


@pytest.mark.asyncio
async def test_start_all_spawns_one_runtime_per_enabled_row(
    db_session, patched_supervisor_module
):
    """data_source_configs read invariant — supervisor spawns one runtime per enabled row."""
    import asyncio

    cid = await _seed_config_row(db_session, enabled=True)
    sup = IngestionSupervisor()
    await sup.start_all()
    try:
        # Yield once so the asyncio.create_task wrapping rt.start() runs.
        await asyncio.sleep(0)
        rt = sup.get_runtime_by_config_id(cid)
        assert rt is not None
        assert isinstance(rt, IngestionRuntime)
        assert sup.get_runtime("ha_fordpass", "default") is rt
        assert getattr(rt, "started", False) is True
    finally:
        await sup.stop_all()


@pytest.mark.asyncio
async def test_start_all_skips_disabled_rows(db_session, patched_supervisor_module):
    cid = await _seed_config_row(db_session, enabled=False)
    sup = IngestionSupervisor()
    await sup.start_all()
    try:
        assert sup.get_runtime_by_config_id(cid) is None
    finally:
        await sup.stop_all()


@pytest.mark.asyncio
async def test_restart_runtime_drops_old_then_spawns_new(
    db_session, patched_supervisor_module
):
    cid = await _seed_config_row(db_session, enabled=True)
    sup = IngestionSupervisor()
    await sup.start_all()
    try:
        rt1 = sup.get_runtime_by_config_id(cid)
        assert rt1 is not None
        await sup.restart_runtime(cid)
        rt2 = sup.get_runtime_by_config_id(cid)
        assert rt1 is not rt2
        assert getattr(rt1, "stopped", False) is True
        assert rt2 is not None
    finally:
        await sup.stop_all()


@pytest.mark.asyncio
async def test_restart_runtime_with_disabled_row_stops_only(
    db_session, patched_supervisor_module
):
    """enabled=true -> false flow: row disabled, runtime stopped, no fresh start."""
    cid = await _seed_config_row(db_session, enabled=True)
    sup = IngestionSupervisor()
    await sup.start_all()
    try:
        # Disable the row in-place, then restart.
        row = await db_session.get(DataSourceConfig, cid)
        row.enabled = False
        await db_session.commit()
        await sup.restart_runtime(cid)
        assert sup.get_runtime_by_config_id(cid) is None
    finally:
        await sup.stop_all()


@pytest.mark.asyncio
async def test_stop_runtime_removes_from_registry(
    db_session, patched_supervisor_module
):
    cid = await _seed_config_row(db_session, enabled=True)
    sup = IngestionSupervisor()
    await sup.start_all()
    await sup.stop_runtime(cid)
    assert sup.get_runtime_by_config_id(cid) is None


@pytest.mark.asyncio
async def test_health_aggregates_all_runtimes(
    db_session, patched_supervisor_module
):
    cid = await _seed_config_row(db_session, enabled=True)
    sup = IngestionSupervisor()
    await sup.start_all()
    try:
        h = sup.health()
        assert isinstance(h, list)
        assert len(h) == 1
        entry = h[0]
        assert entry["config_id"] == cid
        assert entry["source_name"] == "ha_fordpass"
        assert entry["instance_label"] == "default"
        assert "connection_state" in entry  # flattened from runtime.health
    finally:
        await sup.stop_all()


@pytest.mark.asyncio
async def test_start_all_skips_invalid_config_json(
    db_session, monkeypatch, patched_supervisor_module
):
    """A pathological config_json crashes only that row; remaining rows continue."""
    await db_session.execute(delete(DataSourceConfig))
    bad = DataSourceConfig(
        source_name="ha_fordpass",
        instance_label="bad",
        config_json={},  # missing required ha_url / ha_token
        enabled=True,
    )
    db_session.add(bad)
    await db_session.commit()
    await db_session.refresh(bad)

    sup = IngestionSupervisor()
    await sup.start_all()
    try:
        # Bad row didn't spawn a runtime — supervisor logged + skipped.
        assert sup.get_runtime_by_config_id(bad.id) is None
    finally:
        await sup.stop_all()
