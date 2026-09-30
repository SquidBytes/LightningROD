"""Legacy 'homeassistant' rows are relabelled to 'ha_fordpass'; others are untouched."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from db.migrations.versions.relabel_legacy_ha_source import relabel_legacy_source
from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory

pytestmark = [pytest.mark.unit, pytest.mark.db]


async def test_legacy_label_becomes_ha_fordpass(db_session):
    legacy = await TripFactory.create(db_session, device_id="RELABEL_VIN", source_system="homeassistant")
    manual = await TripFactory.create(db_session, device_id="RELABEL_VIN", source_system="manual_entry")
    session = EVChargingSession(device_id="RELABEL_VIN", source_system="homeassistant")
    db_session.add(session)
    await db_session.flush()
    ids = {"legacy": legacy.id, "manual": manual.id, "session": session.id}

    await db_session.run_sync(lambda s: relabel_legacy_source(s.connection()))
    db_session.expire_all()

    labels = dict(
        (await db_session.execute(
            select(EVTripMetrics.id, EVTripMetrics.source_system)
            .where(EVTripMetrics.device_id == "RELABEL_VIN")
        )).all()
    )
    assert labels == {ids["legacy"]: "ha_fordpass", ids["manual"]: "manual_entry"}
    assert await db_session.scalar(
        select(EVChargingSession.source_system).where(EVChargingSession.id == ids["session"])
    ) == "ha_fordpass"
