"""Comparisons query layer validation tests.

Tests gas comparison and network rate comparison calculations.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete

from db.models.charging_session import EVChargingSession
from db.models.ice_vehicle import IceVehicle
from db.models.reference import (
    EVChargingNetwork,
    EVNetworkSubscription,
    GasPriceHistory,
)
from db.models.vehicle import EVVehicle
from web.queries.comparisons import query_gas_comparison

pytestmark = [pytest.mark.query, pytest.mark.db]


async def _setup_comparison_data(db):
    """Create vehicle, ICE comparison row, network, gas prices, and sessions.

    Storage is metric: distance in km, fuel_efficiency_l_per_100km, tank_capacity_l.
    Test values are chosen so the internal imperial-equivalent math (25 MPG,
    15 gal tank, 360 mi total) remains clean.
    """
    # Clear ice_vehicles to keep partial unique index on is_default predictable
    # across tests when SQLite + savepoint isolation leaks committed rows.
    await db.execute(delete(IceVehicle))
    await db.flush()
    # 25 MPG -> L/100km: 235.215 / 25 = 9.4086
    # 15 gal -> liters: 15 * 3.78541 = 56.78115
    vehicle = EVVehicle(
        device_id="COMP_VIN",
        display_name="Comparison Vehicle",
        year=2024,
        make="Ford",
        model="Mustang Mach-E",
        trim_level="Premium",
        battery_option="Extended Range",
        battery_capacity_kwh=91.0,
    )
    db.add(vehicle)
    await db.flush()

    ice = IceVehicle(
        label="2024 Ford Explorer 25 MPG",
        fuel_efficiency_l_per_100km=9.4086,
        tank_capacity_l=56.78115,
        is_default=True,
    )
    db.add(ice)
    await db.flush()

    net = EVChargingNetwork(
        network_name="Comparison Net",
        cost_per_kwh=0.35,
        is_free=False,
        is_verified=True,
    )
    db.add(net)
    await db.flush()

    # Gas price for June 2025 — station $4.00/gal, average $4.20/gal.
    # Storage is metric ($/L); divide by LITER_PER_GAL to invert the read-side
    # multiplication in comparisons._find_gas_price.
    LITER_PER_GAL = 3.78541
    gas_price = GasPriceHistory(
        year=2025,
        month=6,
        station_price=4.00 / LITER_PER_GAL,
        average_price=4.20 / LITER_PER_GAL,
        source="manual",
    )
    db.add(gas_price)
    await db.flush()

    # 3 sessions with known energy and distance.
    # Distance stored in km (metric). 120 mi * 1.60934 = 193.1208 km, etc.
    sessions = []
    KM_PER_MI = 1.60934
    for i, (kwh, miles) in enumerate([(40.0, 120.0), (30.0, 90.0), (50.0, 150.0)]):
        s = EVChargingSession(
            device_id="COMP_VIN",
            energy_kwh=kwh,
            distance_added=miles * KM_PER_MI,
            network_id=net.id,
            location_name="Comparison Net",
            session_start_utc=datetime(2025, 6, 1, tzinfo=UTC) + timedelta(days=i),
            is_complete=True,
            source_system="test",
        )
        sessions.append(s)

    db.add_all(sessions)
    await db.flush()

    return {
        "vehicle": vehicle,
        "ice": ice,
        "network": net,
        "sessions": sessions,
        "total_kwh": 120.0,
        "total_distance_km": 360.0 * KM_PER_MI,  # 579.36 km
    }


async def test_gas_comparison(db_session):
    """Verify gas comparison calculates EV vs gas costs correctly."""
    db = db_session
    data = await _setup_comparison_data(db)
    vehicle = data["vehicle"]
    ice = data["ice"]

    result = await query_gas_comparison(
        db, vehicle=vehicle, ice_vehicle=ice, time_range="all"
    )

    # EV costs: each session = kwh * 0.35 -> 14.00 + 10.50 + 17.50 = 42.00.
    # No subscription configured, so ev_total == ev_energy and ev_fees == 0.
    assert result["ev_energy"] == pytest.approx(42.00, abs=0.01)
    assert result["ev_fees"] == pytest.approx(0.00, abs=0.01)
    assert result["ev_total"] == pytest.approx(42.00, abs=0.01)
    assert result["session_count"] == 3
    # total_distance is in km (metric base)
    assert result["total_distance"] == pytest.approx(data["total_distance_km"], abs=0.01)

    # Gas costs using miles-based path: 360 miles / 25 mpg = 14.4 gallons
    # Station track: 14.4 * $4.00 = $57.60
    # Average track: 14.4 * $4.20 = $60.48
    assert result["gas_total_low"] == pytest.approx(57.60, abs=0.01)
    assert result["gas_total_high"] == pytest.approx(60.48, abs=0.01)

    # Savings: gas - ev
    assert result["savings_low"] == pytest.approx(57.60 - 42.00, abs=0.01)
    assert result["savings_high"] == pytest.approx(60.48 - 42.00, abs=0.01)
    assert result["has_range"] is True
    assert result["ice_label"] == "2024 Ford Explorer 25 MPG"


async def test_gas_comparison_ev_total_includes_subscription_fees(db_session):
    """EV total in the savings comparison is all-in: energy + subscription fees."""
    db = db_session
    data = await _setup_comparison_data(db)
    vehicle = data["vehicle"]
    ice = data["ice"]
    net = data["network"]

    # One subscription period covering June 2025 (the session month) at $7/mo.
    # calculate_monthly_fees_in_range counts any touched calendar month as one,
    # so the June 1-3 session range yields exactly 1 month -> $7.00 in fees.
    sub = EVNetworkSubscription(
        network_id=net.id,
        member_rate=0.30,
        monthly_fee=7.00,
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 30),
    )
    db.add(sub)
    await db.flush()

    result = await query_gas_comparison(
        db, vehicle=vehicle, ice_vehicle=ice, time_range="all"
    )

    # Energy-only stays at 42.00; fees add $7.00; all-in total is 49.00.
    assert result["ev_energy"] == pytest.approx(42.00, abs=0.01)
    assert result["ev_fees"] == pytest.approx(7.00, abs=0.01)
    assert result["ev_total"] == pytest.approx(49.00, abs=0.01)

    # Savings = gas - all-in EV: 57.60 - 49.00 and 60.48 - 49.00.
    assert result["savings_low"] == pytest.approx(57.60 - 49.00, abs=0.01)
    assert result["savings_high"] == pytest.approx(60.48 - 49.00, abs=0.01)


async def test_gas_comparison_no_ice_vehicle(db_session):
    """No ICE vehicle -> returns zeros gracefully."""
    result = await query_gas_comparison(
        db_session, vehicle=None, ice_vehicle=None, time_range="all"
    )

    assert result["ev_total"] == 0.0
    assert result["gas_total_low"] == 0.0
    assert result["gas_total_high"] == 0.0
    assert result["savings_low"] == 0.0
    assert result["session_count"] == 0


async def test_gas_comparison_empty(db_session):
    """ICE vehicle configured but no sessions -> returns zeros."""
    await db_session.execute(delete(IceVehicle))
    await db_session.flush()
    vehicle = EVVehicle(
        device_id="EMPTY_VIN",
        display_name="Empty Vehicle",
        year=2024,
        make="Ford",
        model="Mustang Mach-E",
        battery_capacity_kwh=91.0,
    )
    db_session.add(vehicle)
    await db_session.flush()

    ice = IceVehicle(
        label="Empty ICE",
        fuel_efficiency_l_per_100km=9.4086,
        tank_capacity_l=56.78115,
        is_default=True,
    )
    db_session.add(ice)
    await db_session.flush()

    result = await query_gas_comparison(
        db_session, vehicle=vehicle, ice_vehicle=ice, time_range="all"
    )

    assert result["ev_total"] == 0.0
    assert result["gas_total_low"] == 0.0
    assert result["session_count"] == 0


# ---------------------------------------------------------------------------
# query_distance_gas_comparison — mile-for-mile model
# ---------------------------------------------------------------------------

from web.queries.comparisons import query_distance_gas_comparison  # noqa: E402
from web.unit_system import LITER_PER_GAL, MI_PER_KM  # noqa: E402


async def _setup_distance_data(db):
    """ICE at 25 MPG + month-specific gas prices ($3/gal Jan, $4/gal Feb 2026)."""
    await db.execute(delete(IceVehicle))
    await db.flush()
    ice = IceVehicle(
        label="25 MPG Wagon",
        fuel_efficiency_l_per_100km=9.4086,  # 25 MPG
        tank_capacity_l=56.78115,
        is_default=True,
    )
    db.add(ice)
    for month, price in ((1, 3.00), (2, 4.00)):
        db.add(GasPriceHistory(
            year=2026, month=month,
            station_price=price / LITER_PER_GAL,
            average_price=price / LITER_PER_GAL,
            source="manual",
        ))
    await db.flush()
    return ice


async def test_distance_comparison_prices_each_month_from_odometer(db_session):
    """100 km driven in Jan @ $3/gal + 100 km in Feb @ $4/gal, 25 MPG."""
    from tests.factories.vehicle_status import VehicleStatusFactory

    ice = await _setup_distance_data(db_session)
    device = "DISTVIN"
    for day, odo in ((datetime(2026, 1, 5, tzinfo=UTC), 1000.0),
                     (datetime(2026, 1, 25, tzinfo=UTC), 1100.0),
                     (datetime(2026, 2, 20, tzinfo=UTC), 1200.0)):
        await VehicleStatusFactory.create(
            db_session, device_id=device, recorded_at=day, odometer=odo,
        )

    result = await query_distance_gas_comparison(
        db_session, device_id=device, ice_vehicle=ice, time_range="all",
    )

    assert result["has_data"] is True
    assert result["distance_source"] == "odometer"
    assert result["total_distance"] == pytest.approx(200.0)
    gallons_per_100km = 100 * MI_PER_KM / 25.0
    expected = gallons_per_100km * 3.00 + gallons_per_100km * 4.00
    assert result["gas_total_station"] == pytest.approx(expected, rel=1e-3)


async def test_distance_comparison_skips_odometer_resets(db_session):
    from tests.factories.vehicle_status import VehicleStatusFactory

    ice = await _setup_distance_data(db_session)
    device = "RESETVIN"
    for day, odo in ((datetime(2026, 1, 5, tzinfo=UTC), 1000.0),
                     (datetime(2026, 1, 15, tzinfo=UTC), 1100.0),
                     (datetime(2026, 1, 20, tzinfo=UTC), 50.0),   # reset
                     (datetime(2026, 1, 25, tzinfo=UTC), 100.0)):
        await VehicleStatusFactory.create(
            db_session, device_id=device, recorded_at=day, odometer=odo,
        )

    result = await query_distance_gas_comparison(
        db_session, device_id=device, ice_vehicle=ice, time_range="all",
    )
    # 100 (pre-reset) + 50 (post-reset); the negative jump is ignored.
    assert result["total_distance"] == pytest.approx(150.0)


async def test_distance_comparison_falls_back_to_trips(db_session):
    from tests.factories.trips import TripFactory
    from tests.factories.vehicles import VehicleFactory

    ice = await _setup_distance_data(db_session)
    vehicle = await VehicleFactory.create(db_session, device_id="TRIPVIN")
    await TripFactory.create(
        db_session, device_id=vehicle.device_id,
        end_time=datetime(2026, 1, 10, tzinfo=UTC), distance=80.0,
    )

    result = await query_distance_gas_comparison(
        db_session, device_id=vehicle.device_id, ice_vehicle=ice, time_range="all",
    )
    assert result["has_data"] is True
    assert result["distance_source"] == "trips"
    assert result["total_distance"] == pytest.approx(80.0)


async def test_distance_comparison_empty_without_distance_data(db_session):
    ice = await _setup_distance_data(db_session)
    result = await query_distance_gas_comparison(
        db_session, device_id="NODATAVIN", ice_vehicle=ice, time_range="all",
    )
    assert result["has_data"] is False
    assert result["distance_source"] is None


async def test_distance_comparison_empty_without_ice_vehicle(db_session):
    result = await query_distance_gas_comparison(
        db_session, device_id="X", ice_vehicle=None, time_range="all",
    )
    assert result["has_data"] is False
