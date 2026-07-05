"""query_charging_efficiency — loss and utilization have independent inputs."""

import pytest

from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicles import VehicleFactory
from web.queries.dashboard import query_charging_efficiency

pytestmark = pytest.mark.db


async def test_utilization_counts_without_metered_energy(db_session):
    """A stall-mapped session (rated power, no wall meter) feeds utilization only."""
    vehicle = await VehicleFactory.create(db_session)
    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        charger_rated_kw=11.5,
        max_power=9.0,
        evse_energy_kwh=None,
    )

    result = await query_charging_efficiency(db_session)
    assert result["sessions_with_evse"] == 0
    assert result["sessions_with_util"] == 1
    assert result["avg_loss_pct"] is None
    assert result["avg_utilization_pct"] == pytest.approx(9.0 / 11.5 * 100)


async def test_loss_counts_with_metered_energy(db_session):
    vehicle = await VehicleFactory.create(db_session)
    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        energy_kwh=20.0,
        evse_energy_kwh=22.0,
        charger_rated_kw=None,
    )

    result = await query_charging_efficiency(db_session)
    assert result["sessions_with_evse"] == 1
    assert result["sessions_with_util"] == 0
    assert result["total_loss_kwh"] == pytest.approx(2.0)
    assert result["avg_loss_pct"] == pytest.approx(2.0 / 22.0 * 100)
    assert result["avg_utilization_pct"] is None
