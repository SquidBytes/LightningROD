"""ICE vehicle HTTP route smoke tests + UNIT-01 round-trip assertion.

Wave 0 stubs — bodies filled in by Plan 04 (web/routes/settings.py ICE CRUD block).
Routes scaffolded:
  POST   /settings/ice-vehicles          — create with display-unit input
  GET    /settings/ice-vehicles/{id}/edit — fetch edit modal with display-unit values
  PUT    /settings/ice-vehicles/{id}     — update with display-unit input
  DELETE /settings/ice-vehicles/{id}     — delete (with default-row guard)
  POST   /settings/ice-vehicles/{id}/set-default — promote to default
"""

import pytest

try:
    from db.models.ice_vehicle import IceVehicle  # noqa: F401
    HAS_ICE_VEHICLES = True
except ImportError:
    HAS_ICE_VEHICLES = False

pytestmark = [pytest.mark.api, pytest.mark.db]


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 2: Plan 04 lands ICE CRUD route block")
async def test_post_ice_vehicle_creates_row(client, db_session):
    """POST /settings/ice-vehicles with valid form -> 200, row created."""
    pytest.skip("Wave 2: Plan 04 fills in POST round-trip assertion")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 2: Plan 04 lands ICE CRUD route block")
async def test_unit_01_us_input_writes_metric(client, db_session):
    """UNIT-01: POST fuel_efficiency_display=28 (US units) writes ~8.40 L/100km to DB.

    Conversion: to_metric_fuel_efficiency(28, 'us') = 235.215 / 28 = 8.40 L/100km.
    Asserts the route handler ran the write-time conversion before the query layer.
    """
    pytest.skip("Wave 2: Plan 04 fills in UNIT-01 conversion assertion")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 2: Plan 04 lands ICE CRUD route block")
async def test_unit_01_round_trip_us_units(client, db_session):
    """UNIT-01 round-trip: POST 28 (US), GET edit form, assert rendered fuel_efficiency_display ~= 28.0."""
    pytest.skip("Wave 2: Plan 04 fills in UNIT-01 round-trip assertion")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 2: Plan 04 lands ICE CRUD route block")
async def test_put_ice_vehicle_updates_row(client, db_session):
    """PUT /settings/ice-vehicles/{id} with new label -> row updated, response includes HX-Trigger=closeIceVehicleModal."""
    pytest.skip("Wave 2: Plan 04 fills in PUT round-trip + HX-Trigger header assertion")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 2: Plan 04 lands ICE CRUD route block")
async def test_set_default_promotes_row(client, db_session):
    """POST /settings/ice-vehicles/{id}/set-default flips is_default and demotes others."""
    pytest.skip("Wave 2: Plan 04 fills in set-default route assertion")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 2: Plan 04 lands ICE CRUD route block")
async def test_delete_default_guard_returns_422_alert(client, db_session):
    """DELETE on the default ICE row when others exist returns alert-error (per RESEARCH OQ4)."""
    pytest.skip("Wave 2: Plan 04 fills in delete-guard assertion")
