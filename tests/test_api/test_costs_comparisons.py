"""Costs page renders both Savings Scenarios comparison models."""

from datetime import UTC, datetime

import pytest

from db.models.ice_vehicle import IceVehicle
from db.models.reference import GasPriceHistory
from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicle_status import VehicleStatusFactory
from tests.factories.vehicles import VehicleFactory
from web.unit_system import LITER_PER_GAL

pytestmark = pytest.mark.db


async def test_costs_page_renders_mile_for_mile_section(client, db_session):
    vehicle = await VehicleFactory.create(db_session)
    db_session.add(IceVehicle(
        label="25 MPG Wagon",
        fuel_efficiency_l_per_100km=9.4086,
        is_default=True,
    ))
    db_session.add(GasPriceHistory(
        year=2026, month=1,
        station_price=3.50 / LITER_PER_GAL,
        average_price=3.50 / LITER_PER_GAL,
        source="manual",
    ))
    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        cost=12.00,
        session_start_utc=datetime(2026, 1, 10, tzinfo=UTC),
        distance_added=160.9,  # feeds the energy-equivalent model's primary path
    )
    for day, odo in ((datetime(2026, 1, 5, tzinfo=UTC), 1000.0),
                     (datetime(2026, 1, 25, tzinfo=UTC), 1300.0)):
        await VehicleStatusFactory.create(
            db_session, device_id=vehicle.device_id, recorded_at=day, odometer=odo,
        )
    await db_session.flush()

    response = await client.get("/charging/costs")
    assert response.status_code == 200
    assert "Mile-for-mile in your 25 MPG Wagon" in response.text
    assert "Charge-for-charge" in response.text
    assert "odometer" in response.text
