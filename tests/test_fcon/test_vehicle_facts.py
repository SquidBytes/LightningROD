"""Vehicle details from FordConnect's garage and telemetry, filling only empty columns."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from db.models.vehicle import EVVehicle
from tests.test_fcon.payloads import telemetry
from web.services.sources.fcon import adapter
from web.services.sources.fcon.garage import garage_facts
from web.services.vehicles.auto_create import ensure_vehicle
from web.services.vehicles.battery_match import match_battery, presets_for

VIN = "YOURVIN0000000003"
DEVICE = VIN.lower()
LIGHTNING = "F-150 Lightning"


def _garage(**over) -> dict:
    base = {
        "vin": VIN, "nickName": "Lightning", "vehicleType": "2023 F-150", "modelName": "F-150",
        "modelYear": "2023", "make": "F", "engineType": "BEV",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _telemetry(capacity: float = 106.75) -> dict:
    p = telemetry(xevBatteryCapacity=capacity)
    p["vin"] = VIN
    return p


@pytest.mark.unit
def test_garage_maps_make_model_year_and_nickname():
    g = garage_facts(_garage())
    assert g.facts == {"make": "Ford", "model": LIGHTNING, "year": 2023}
    assert g.display_name == "Lightning"
    assert "2023 F-150" in g.auto_names


@pytest.mark.unit
def test_nickname_lookup_is_tolerant_and_falls_back_to_year_model():
    assert garage_facts(_garage(nickName=None, nickname="Blue")).display_name == "Blue"
    assert garage_facts(_garage(nickName="  ")).display_name == "2023 F-150"


@pytest.mark.unit
def test_only_an_electric_partial_name_maps_to_a_preset_model():
    assert garage_facts(_garage(engineType="ICE")).facts["model"] == "F-150"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("year", "capacity", "option", "usable", "trim"),
    [
        (2023, 106.75, "Standard Range", 98.0, None),  # three trims share the pack
        (2023, 140.0, "Extended Range", 131.0, None),
        (2025, 134.0, "Extended Range", 123.0, "Flash"),  # only Flash has ER-123 in 2025
        (2026, 133.0, "Extended Range", 123.0, None),
    ],
)
def test_capacity_picks_the_battery(year, capacity, option, usable, trim):
    m = match_battery(presets_for("Ford", LIGHTNING, year), capacity)
    assert (m.battery_option, m.usable_kwh, m.trim_level) == (option, usable, trim)


@pytest.mark.unit
@pytest.mark.parametrize("capacity", [80.0, 150.0, 0.0])
def test_capacity_outside_tolerance_matches_nothing(capacity):
    assert match_battery(presets_for("Ford", LIGHTNING, 2023), capacity) is None


async def _vehicle(db) -> EVVehicle:
    v = await db.scalar(select(EVVehicle).where(EVVehicle.device_id == DEVICE))
    await db.refresh(v)
    return v


async def _connect(db, garage: dict | None = None, capacity: float = 106.75) -> None:
    await adapter.process_sample("garage", garage or _garage(), db)
    await adapter.process_sample("telemetry", _telemetry(capacity), db)
    await db.flush()


@pytest.mark.db
async def test_vehicle_home_assistant_created_gets_filled(db_session):
    await ensure_vehicle(db_session, DEVICE, source_system="ha_fordpass", vin=DEVICE)
    await _connect(db_session)
    v = await _vehicle(db_session)
    assert (v.display_name, v.make, v.model, v.year) == ("Lightning", "Ford", LIGHTNING, 2023)
    assert (v.battery_option, float(v.battery_capacity_kwh), float(v.battery_gross_capacity_kwh)) == (
        "Standard Range", 98.0, 108.0,
    )
    assert v.trim_level is None  # ambiguous: left for the user
    assert v.vin == DEVICE  # already set by Home Assistant; not replaced


@pytest.mark.db
async def test_user_edits_are_never_overwritten(db_session):
    await ensure_vehicle(db_session, DEVICE, source_system="fcon")
    v = await _vehicle(db_session)
    v.display_name, v.year, v.battery_option, v.trim_level = "Work truck", 2024, "Extended Range", "XLT"
    await db_session.flush()
    await _connect(db_session)
    v = await _vehicle(db_session)
    assert (v.display_name, v.year, v.battery_option, v.trim_level) == (
        "Work truck", 2024, "Extended Range", "XLT",
    )
    # The measured pack (Standard Range) disagrees with the user's option, so no
    # capacity is filled in beside it.
    assert v.battery_capacity_kwh is None
    assert (v.make, v.model) == ("Ford", LIGHTNING)


@pytest.mark.db
async def test_nickname_replaces_only_an_automatic_name(db_session):
    await _connect(db_session, _garage(nickName=None))
    assert (await _vehicle(db_session)).display_name == "2023 F-150"
    await _connect(db_session, _garage(nickName="Lightning"))
    assert (await _vehicle(db_session)).display_name == "Lightning"
    v = await _vehicle(db_session)
    v.display_name = "Mine"
    await db_session.flush()
    await _connect(db_session, _garage(nickName="Something else"))
    assert (await _vehicle(db_session)).display_name == "Mine"


@pytest.mark.db
async def test_rerunning_changes_nothing(db_session):
    await _connect(db_session)
    before = {c.key: getattr(await _vehicle(db_session), c.key) for c in EVVehicle.__table__.columns}
    await _connect(db_session)
    after = {c.key: getattr(await _vehicle(db_session), c.key) for c in EVVehicle.__table__.columns}
    assert after == before
