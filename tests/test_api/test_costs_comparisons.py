"""Costs page renders both Savings Scenarios comparison models."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from db.models.ice_vehicle import IceVehicle
from db.models.reference import GasPriceHistory
from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicle_status import VehicleStatusFactory
from tests.factories.vehicles import VehicleFactory
from web.unit_system import to_metric_price_per_volume

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
        station_price=to_metric_price_per_volume(Decimal("3.50"), "us"),
        average_price=to_metric_price_per_volume(Decimal("3.50"), "us"),
        source="manual",
    ))
    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        cost=Decimal("12.00"),
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
    assert "Charge-for-charge on gas" in response.text
    assert "ledger-grid-3" in response.text
    assert "odometer" in response.text
    # Single EV column: the "Your EV" label appears exactly once.
    assert response.text.count("Your EV — actual spend") == 1


@pytest.mark.db
async def test_custom_comparison_with_an_empty_value_is_not_an_error(client, db_session):
    """Choosing "Custom" submits the form before a value is typed."""
    response = await client.get(
        "/charging/costs?ref_mode=custom&ref_value=&ref_network_id=",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
