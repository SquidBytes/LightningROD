"""Home Assistant's `metrics` entity and FordConnect telemetry carry the same Ford dict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle_status import EVVehicleStatus
from web.services.sources.fcon import adapter as fcon
from web.services.sources.ha_fordpass import adapter as ha

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parent.parent / "fixtures"
ENTITY = "sensor.fordpass_YOUR_VIN_metrics"
# What differs by design: identity, the source label and bookkeeping.
NOT_COMPARED = {"id", "device_id", "source_system", "ingested_at", "created_at", "updated_at"}


def _columns(row) -> dict:
    return {
        c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in NOT_COMPARED
    }


async def _only(db, model, source: str):
    rows = (await db.execute(select(model).where(model.source_system == source))).scalars().all()
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("model", [EVBatteryStatus, EVVehicleStatus])
async def test_same_metrics_give_the_same_row_from_either_source(db_session, model):
    telemetry = json.loads((FIXTURES / "fcon" / "telemetry_parked.json").read_text())
    new_state = json.loads((FIXTURES / "ha_metrics" / "metrics_full.json").read_text())[ENTITY]
    assert {k: new_state["attributes"][k] for k in telemetry["metrics"]} == telemetry["metrics"]

    await fcon.process_sample("telemetry", telemetry, db_session)
    await ha.process_sample(ENTITY, new_state, db_session, {"unit_system": "imperial"})
    await db_session.flush()

    from_fcon = _columns(await _only(db_session, model, "fcon"))
    from_ha = _columns(await _only(db_session, model, "ha_fordpass"))
    assert from_ha == from_fcon
    assert sum(v is not None for v in from_ha.values()) > 8
