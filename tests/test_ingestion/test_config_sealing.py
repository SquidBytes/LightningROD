"""Startup sealing of plaintext secrets, and the supervisor's decrypt boundary."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from db.models.data_source_config import DataSourceConfig
from web.services.secrets import SecretBox, is_encrypted
from web.services.sources.config_secrets import open_config, seal_stored_configs

pytestmark = [pytest.mark.db]


async def _only_row(db_session, config_json: dict) -> DataSourceConfig:
    await db_session.execute(delete(DataSourceConfig))
    row = DataSourceConfig(
        source_name="ha_fordpass",
        instance_label="default",
        config_json=config_json,
        enabled=True,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_startup_seals_plaintext_once(db_session):
    row = await _only_row(db_session, {"ha_url": "http://x", "ha_token": "plain-token"})
    assert await seal_stored_configs(db_session) == 1
    await db_session.refresh(row)
    assert is_encrypted(row.config_json["ha_token"])
    assert open_config("ha_fordpass", row.config_json).values["ha_token"] == "plain-token"
    # Second boot is a no-op.
    assert await seal_stored_configs(db_session) == 0


async def test_supervisor_hands_builder_plaintext(db_session, monkeypatch):
    from web.services.ingestion.supervisor import IngestionSupervisor
    from web.services.sources.config_secrets import seal_config

    row = await _only_row(
        db_session, seal_config("ha_fordpass", {"ha_url": "http://x", "ha_token": "tok"})
    )
    seen: dict = {}

    class _Rt:
        config_id = row.id
        source_name = "ha_fordpass"
        instance_label = "default"
        health: dict = {}

        async def start(self): ...
        async def stop(self): ...

    def _builder(r, config):
        seen.update(config)
        return _Rt()

    import importlib

    sup_module = importlib.import_module("web.services.ingestion.supervisor")
    sup = IngestionSupervisor()
    monkeypatch.setattr(sup_module, "_builders", lambda: {"ha_fordpass": _builder})
    await sup._start_one(row)
    assert seen["ha_token"] == "tok"
    await sup.stop_all()


async def test_supervisor_skips_unreadable_row_and_reports_it(db_session):
    from web.services.ingestion.supervisor import IngestionSupervisor

    foreign = SecretBox("lost-key").encrypt("tok")
    row = await _only_row(db_session, {"ha_url": "http://x", "ha_token": foreign})
    sup = IngestionSupervisor()
    await sup._start_one(row)
    assert sup.get_runtime_by_config_id(row.id) is None
    assert sup.needs_reauth(row.id)
    [entry] = sup.health()
    assert entry["connection_state"] == "needs_reauth"
