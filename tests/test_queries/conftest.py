"""Scenario fixtures for query layer tests.

Each fixture creates KNOWN data with EXACT values for deterministic assertions.
All fixtures return a dict with created objects and pre-computed expected values.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest_asyncio

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.reference import (
    EVChargingNetwork,
    EVLocationLookup,
    EVNetworkSubscription,
)
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEVICE_ID = "TEST_VIN_QUERY"
BASE_DATE = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


async def _create_vehicle(db, device_id=DEVICE_ID):
    v = EVVehicle(
        device_id=device_id,
        display_name="Query Test Vehicle",
        year=2024,
        make="Ford",
        model="Mustang Mach-E",
        battery_capacity_kwh=91.0,
        vin=device_id,
        source_system="test_fixture",
    )
    db.add(v)
    await db.flush()
    return v


# ---------------------------------------------------------------------------
# Cost scenario
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cost_scenario(db_session):
    """Create known cost scenario with exact values.

    Network A: $0.35/kWh (no subscription)
    Network B: $0.45/kWh, subscription at $0.25/kWh, $12.99/month fee
    Location X: linked to Network A, has cost override $0.30/kWh

    Sessions:
    1. 50.0 kWh on Network A (no location override) -> display_cost = 50*0.35 = 17.50
    2. 30.0 kWh on Network A (no location override) -> display_cost = 30*0.35 = 10.50
    3. 40.0 kWh on Network B WITH subscription     -> display_cost = 40*0.25 = 10.00
    4. 20.0 kWh on Network B WITHOUT subscription   -> display_cost = 20*0.45 = 9.00
    5. 25.0 kWh on Network A at Location X (override) -> display_cost = 25*0.30 = 7.50
    6. 10.0 kWh FREE session (is_free=True)          -> display_cost = 0.00
    """
    db = db_session
    vehicle = await _create_vehicle(db)

    # Networks
    net_a = EVChargingNetwork(
        network_name="Network A",
        cost_per_kwh=0.35,
        is_free=False,
        is_verified=True,
        source_system="test_fixture",
    )
    net_b = EVChargingNetwork(
        network_name="Network B",
        cost_per_kwh=0.45,
        is_free=False,
        is_verified=True,
        source_system="test_fixture",
    )
    db.add_all([net_a, net_b])
    await db.flush()

    # Subscription for Network B: active from 2025-05-01 to 2025-08-01
    sub = EVNetworkSubscription(
        network_id=net_b.id,
        member_rate=0.25,
        monthly_fee=12.99,
        start_date=date(2025, 5, 1),
        end_date=date(2025, 8, 1),
    )
    db.add(sub)
    await db.flush()

    # Location X with cost override, linked to Network A
    loc_x = EVLocationLookup(
        location_name="Station X",
        network_id=net_a.id,
        cost_per_kwh=0.30,
        location_type="public",
        is_verified=True,
        source_system="test_fixture",
    )
    db.add(loc_x)
    await db.flush()

    # Sessions with EXACT known values
    sessions = []

    # Session 1: Network A, 50 kWh
    s1 = EVChargingSession(
        device_id=DEVICE_ID,
        energy_kwh=50.0,
        network_id=net_a.id,
        session_start_utc=BASE_DATE - timedelta(days=10),
        is_complete=True,
        source_system="test_fixture",
    )
    sessions.append(s1)

    # Session 2: Network A, 30 kWh
    s2 = EVChargingSession(
        device_id=DEVICE_ID,
        energy_kwh=30.0,
        network_id=net_a.id,
        session_start_utc=BASE_DATE - timedelta(days=8),
        is_complete=True,
        source_system="test_fixture",
    )
    sessions.append(s2)

    # Session 3: Network B, 40 kWh, WITH subscription (date inside sub range)
    s3 = EVChargingSession(
        device_id=DEVICE_ID,
        energy_kwh=40.0,
        network_id=net_b.id,
        session_start_utc=datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC),
        is_complete=True,
        source_system="test_fixture",
    )
    sessions.append(s3)

    # Session 4: Network B, 20 kWh, WITHOUT subscription (date before sub range)
    s4 = EVChargingSession(
        device_id=DEVICE_ID,
        energy_kwh=20.0,
        network_id=net_b.id,
        session_start_utc=datetime(2025, 4, 15, 10, 0, 0, tzinfo=UTC),
        is_complete=True,
        source_system="test_fixture",
    )
    sessions.append(s4)

    # Session 5: Network A at Location X (location override $0.30/kWh)
    s5 = EVChargingSession(
        device_id=DEVICE_ID,
        energy_kwh=25.0,
        network_id=net_a.id,
        location_id=loc_x.id,
        session_start_utc=BASE_DATE - timedelta(days=5),
        is_complete=True,
        source_system="test_fixture",
    )
    sessions.append(s5)

    # Session 6: Free session
    s6 = EVChargingSession(
        device_id=DEVICE_ID,
        energy_kwh=10.0,
        is_free=True,
        session_start_utc=BASE_DATE - timedelta(days=3),
        is_complete=True,
        source_system="test_fixture",
    )
    sessions.append(s6)

    db.add_all(sessions)
    await db.flush()

    return {
        "vehicle": vehicle,
        "net_a": net_a,
        "net_b": net_b,
        "subscription": sub,
        "loc_x": loc_x,
        "sessions": sessions,
        "db": db,
        # Pre-computed expected values
        "expected": {
            # s1: 17.50, s2: 10.50, s3: 10.00, s4: 9.00, s5: 7.50, s6: 0.00
            "total_cost": 17.50 + 10.50 + 10.00 + 9.00 + 7.50 + 0.00,  # 54.50
            "total_kwh": 50.0 + 30.0 + 40.0 + 20.0 + 25.0 + 10.0,  # 175.0
            "total_sessions": 6,
            "free_kwh": 10.0,
            "free_count": 1,
            "net_a_cost": 17.50 + 10.50 + 7.50,  # 35.50
            "net_a_sessions": 3,
            "net_b_cost": 10.00 + 9.00,  # 19.00
            "net_b_sessions": 2,
            # Subscription savings: s3 would cost 40*0.45=18.00 at non-member,
            # paid 40*0.25=10.00, savings = 8.00
            "subscription_savings": 8.00,
        },
    }


# ---------------------------------------------------------------------------
# Battery scenario
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def battery_scenario(db_session):
    """Create known battery scenario with SOC progression over 7 days.

    20 records with known SOC values at known timestamps.
    Includes a charging region (negative kW = charging) and idle regions.
    """
    db = db_session
    vehicle = await _create_vehicle(db)

    records = []
    start = datetime(2025, 6, 10, 0, 0, 0, tzinfo=UTC)

    # SOC progression: idle -> charge -> idle -> discharge
    soc_values = [
        30.0, 28.0, 25.0, 22.0, 20.0,  # discharging/idle (0-4)
        25.0, 35.0, 50.0, 65.0, 80.0,  # charging (5-9) -- negative kW
        82.0, 80.0, 78.0, 75.0, 72.0,  # discharging/idle (10-14)
        70.0, 68.0, 65.0, 60.0, 55.0,  # discharging (15-19)
    ]
    kw_values = [
        0.0, -0.1, -0.2, 0.0, 0.0,       # idle (below threshold)
        -5.0, -50.0, -75.0, -60.0, -30.0, # charging (negative = power into battery)
        0.0, 0.1, 0.0, -0.1, 0.0,         # idle
        0.0, 0.0, 0.1, 0.0, 0.0,          # idle
    ]
    range_values = [
        90.0, 84.0, 75.0, 66.0, 60.0,
        75.0, 105.0, 150.0, 195.0, 240.0,
        246.0, 240.0, 234.0, 225.0, 216.0,
        210.0, 204.0, 195.0, 180.0, 165.0,
    ]

    for i in range(20):
        rec = EVBatteryStatus(
            device_id=DEVICE_ID,
            recorded_at=start + timedelta(hours=i * 8),
            hv_battery_soc=soc_values[i],
            hv_battery_kw=kw_values[i],
            hv_battery_range=range_values[i],
            hv_battery_capacity=88.5 if i < 10 else 88.3,  # slight degradation
            source_system="test_fixture",
        )
        records.append(rec)

    db.add_all(records)
    await db.flush()

    return {
        "vehicle": vehicle,
        "records": records,
        "db": db,
        "device_id": DEVICE_ID,
        "expected": {
            "record_count": 20,
            "soc_values": soc_values,
            "first_soc": 30.0,
            "last_soc": 55.0,
            # Charging region: indices 5-9 (kw < -0.5)
            "charging_regions": [(5, 9)],
            "max_capacity_day1": 88.5,
        },
    }


# ---------------------------------------------------------------------------
# Energy scenario
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def energy_scenario(db_session):
    """Create known energy scenario with 10 sessions for aggregation tests.

    5 AC sessions + 5 DC sessions with known energy values.
    """
    db = db_session
    vehicle = await _create_vehicle(db)

    sessions = []
    ac_energy = [15.0, 20.0, 12.0, 18.0, 25.0]  # total = 90.0
    dc_energy = [45.0, 55.0, 40.0, 50.0, 60.0]   # total = 250.0
    # Distance values are km (metric canonical). Efficiency: 3.0 km/kWh for AC, 2.5 km/kWh for DC.
    ac_distance = [45.0, 60.0, 36.0, 54.0, 75.0]
    dc_distance = [112.5, 137.5, 100.0, 125.0, 150.0]

    for i, (kwh, dist) in enumerate(zip(ac_energy, ac_distance, strict=True)):
        s = EVChargingSession(
            device_id=DEVICE_ID,
            energy_kwh=kwh,
            charge_type="AC",
            distance_added=dist,
            session_start_utc=BASE_DATE - timedelta(days=20 - i),
            is_complete=True,
            source_system="test_fixture",
        )
        sessions.append(s)

    for i, (kwh, dist) in enumerate(zip(dc_energy, dc_distance, strict=True)):
        s = EVChargingSession(
            device_id=DEVICE_ID,
            energy_kwh=kwh,
            charge_type="DC",
            distance_added=dist,
            session_start_utc=BASE_DATE - timedelta(days=15 - i),
            is_complete=True,
            source_system="test_fixture",
        )
        sessions.append(s)

    db.add_all(sessions)
    await db.flush()

    return {
        "vehicle": vehicle,
        "sessions": sessions,
        "db": db,
        "expected": {
            "total_kwh": 340.0,  # 90 + 250
            "total_sessions": 10,
            "ac_kwh": 90.0,
            "dc_kwh": 250.0,
            "ac_count": 5,
            "dc_count": 5,
            # efficiency: AC sessions = 3.0, DC sessions = 2.5
            # avg = (5*3.0 + 5*2.5) / 10 = 2.75
            "avg_efficiency": 2.75,
            "best_efficiency": 3.0,
            "worst_efficiency": 2.5,
        },
    }


# ---------------------------------------------------------------------------
# Trip scenario
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def trip_scenario(db_session):
    """Create known trip scenario with 8 trips.

    All trips have deterministic values for distance, duration, efficiency.
    """
    db = db_session
    vehicle = await _create_vehicle(db)

    trips = []
    # 8 trips with known values
    trip_data = [
        {"distance": 25.0, "duration": 30.0, "efficiency": 3.2, "energy_consumed": 7.81},
        {"distance": 50.0, "duration": 55.0, "efficiency": 2.8, "energy_consumed": 17.86},
        {"distance": 10.0, "duration": 15.0, "efficiency": 3.5, "energy_consumed": 2.86},
        {"distance": 80.0, "duration": 90.0, "efficiency": 2.5, "energy_consumed": 32.0},
        {"distance": 35.0, "duration": 40.0, "efficiency": 3.0, "energy_consumed": 11.67},
        {"distance": 15.0, "duration": 20.0, "efficiency": 3.3, "energy_consumed": 4.55},
        {"distance": 60.0, "duration": 70.0, "efficiency": 2.7, "energy_consumed": 22.22},
        {"distance": 40.0, "duration": 45.0, "efficiency": 3.1, "energy_consumed": 12.90},
    ]

    for i, td in enumerate(trip_data):
        end_time = BASE_DATE - timedelta(days=14 - i * 2)
        t = EVTripMetrics(
            trip_id=uuid.uuid4(),
            device_id=DEVICE_ID,
            distance=td["distance"],
            duration=td["duration"],
            efficiency=td["efficiency"],
            energy_consumed=td["energy_consumed"],
            end_time=end_time,
            is_complete=True,
            source_system="test_fixture",
        )
        trips.append(t)

    db.add_all(trips)
    await db.flush()

    distances = [td["distance"] for td in trip_data]
    efficiencies = [td["efficiency"] for td in trip_data]

    return {
        "vehicle": vehicle,
        "trips": trips,
        "db": db,
        "expected": {
            "count": 8,
            "total_distance": sum(distances),  # 315.0
            "avg_efficiency": sum(efficiencies) / len(efficiencies),  # 3.0125
        },
    }


# ---------------------------------------------------------------------------
# Charge curve fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sessions_with_battery_status(db_session):
    """DC session with >=3 battery_status points.
    Creates:
    1 vehicle (device_id 'charge_p25_real_vin')
    1 DC session spanning 1 hour
    5 EVBatteryStatus rows with recorded_at inside the session window
    has_real_charge_curve_data should return True against this.
    """
    device_id = "charge_p25_real_vin"
    vehicle = await _create_vehicle(db_session, device_id=device_id)

    start = BASE_DATE - timedelta(days=2)
    end = start + timedelta(hours=1)
    session = EVChargingSession(
        device_id=device_id,
        charge_type="DC",
        energy_kwh=45.0,
        max_power=120.0,
        evse_max_power_kw=150.0,
        session_start_utc=start,
        session_end_utc=end,
        is_complete=True,
        source_system="test_fixture",
    )
    db_session.add(session)
    await db_session.flush()

    # 5 battery_status points spread across the session window
    for i in range(5):
        rec = EVBatteryStatus(
            device_id=device_id,
            recorded_at=start + timedelta(minutes=10 * i + 5),
            hv_battery_soc=20.0 + i * 15.0,
            hv_battery_kw=-100.0 + i * 10.0,
            source_system="test_fixture",
        )
        db_session.add(rec)
    await db_session.flush()

    return {
        "vehicle": vehicle,
        "session": session,
        "device_id": device_id,
        "db": db_session,
    }


@pytest_asyncio.fixture
async def sessions_with_valueless_battery_status(db_session):
    """DC session with >=3 battery_status rows that carry no telemetry.
    Same shape as `sessions_with_battery_status`, except every value column is
    NULL — the row pattern a metrics event used to insert on every poll.
    has_real_charge_curve_data must return False: neither axis of the real
    charge curve can be plotted from these.
    """
    device_id = "charge_p25_empty_vin"
    vehicle = await _create_vehicle(db_session, device_id=device_id)

    start = BASE_DATE - timedelta(days=2)
    end = start + timedelta(hours=1)
    session = EVChargingSession(
        device_id=device_id,
        charge_type="DC",
        energy_kwh=45.0,
        max_power=120.0,
        evse_max_power_kw=150.0,
        session_start_utc=start,
        session_end_utc=end,
        is_complete=True,
        source_system="test_fixture",
    )
    db_session.add(session)
    await db_session.flush()

    for i in range(5):
        db_session.add(
            EVBatteryStatus(
                device_id=device_id,
                recorded_at=start + timedelta(minutes=10 * i + 5),
                source_system="test_fixture",
            )
        )
    await db_session.flush()

    return {
        "vehicle": vehicle,
        "session": session,
        "device_id": device_id,
        "db": db_session,
    }


@pytest_asyncio.fixture
async def sessions_with_soc_only_battery_status(db_session):
    """DC session whose battery_status rows carry SOC but no pack power.
    The charge curve plots kW against SOC; without kW there is no curve, only
    a flat line pinned to zero.
    """
    device_id = "charge_p25_soconly_vin"
    vehicle = await _create_vehicle(db_session, device_id=device_id)

    start = BASE_DATE - timedelta(days=2)
    end = start + timedelta(hours=1)
    session = EVChargingSession(
        device_id=device_id,
        charge_type="DC",
        energy_kwh=45.0,
        max_power=120.0,
        session_start_utc=start,
        session_end_utc=end,
        is_complete=True,
        source_system="test_fixture",
    )
    db_session.add(session)
    await db_session.flush()

    for i in range(5):
        db_session.add(
            EVBatteryStatus(
                device_id=device_id,
                recorded_at=start + timedelta(minutes=10 * i + 5),
                hv_battery_soc=20.0 + i * 15.0,
                source_system="test_fixture",
            )
        )
    await db_session.flush()

    return {
        "vehicle": vehicle,
        "session": session,
        "device_id": device_id,
        "db": db_session,
    }


@pytest_asyncio.fixture
async def sessions_without_battery_status(db_session):
    """DC-only sessions with no battery_status rows.
    Creates:
    1 vehicle (device_id 'charge_p25_synth_vin')
    3 DC sessions with peak kW [100.0, 150.0, 200.0] → median 150.0
    0 battery_status rows (synthetic fallback path)
    Returned dict includes expected.median_peak_kw / dc_session_count for
    deterministic assertions.
    """
    device_id = "charge_p25_synth_vin"
    vehicle = await _create_vehicle(db_session, device_id=device_id)

    peaks = [100.0, 150.0, 200.0]
    sessions = []
    for i, peak in enumerate(peaks):
        start = BASE_DATE - timedelta(days=2 + i)
        session = EVChargingSession(
            device_id=device_id,
            charge_type="DC",
            energy_kwh=40.0 + i,
            max_power=peak,
            evse_max_power_kw=250.0,
            session_start_utc=start,
            session_end_utc=start + timedelta(minutes=30),
            is_complete=True,
            source_system="test_fixture",
        )
        sessions.append(session)
    db_session.add_all(sessions)
    await db_session.flush()

    return {
        "vehicle": vehicle,
        "sessions": sessions,
        "device_id": device_id,
        "db": db_session,
        "expected": {
            "dc_session_count": len(peaks),
            "median_peak_kw": 150.0,
            "peaks": peaks,
        },
    }


@pytest_asyncio.fixture
async def trips_with_ambient_temp(db_session):
    """Trips with populated ambient_temp + distance + energy_consumed (temperature scatter path).

    Creates 10 trips with ambient_temp spanning -10 °C → +30 °C (in 5 °C steps),
    varied distance and energy_consumed so the scatter has spread. All end_times
    are within the last 30 days so both "7d" and "all" range filters return
    non-empty results (but "7d" returns a strict subset of "all").
    """
    db = db_session
    await _create_vehicle(db)

    now = datetime.now(UTC)
    temps = [-10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0, 22.0, 25.0, 30.0]
    trips = []
    for i, ambient_c in enumerate(temps):
        # Spread end_times from ~35d ago to ~1d ago so 7d window filters to
        # the last 2-3 trips. distance and energy_consumed vary so efficiency
        # (distance/energy) isn't constant across the scatter.
        end_time = now - timedelta(days=(35 - i * 4))
        distance = 40.0 + i * 8.0  # 40..112 km
        energy = 10.0 + i * 2.0  # 10..28 kWh
        t = EVTripMetrics(
            trip_id=uuid.uuid4(),
            device_id=DEVICE_ID,
            distance=distance,
            duration=45.0,
            energy_consumed=energy,
            efficiency=distance / energy,
            ambient_temp=ambient_c,
            start_time=end_time - timedelta(minutes=45),
            end_time=end_time,
            is_complete=True,
            source_system="test_fixture",
        )
        trips.append(t)

    db.add_all(trips)
    await db.flush()
    return trips


@pytest_asyncio.fixture
async def trips_minimal_count(db_session):
    """Fewer than 5 trips with ambient_temp — exercises empty-state branch.

    Creates exactly 3 trips with ambient_temp set. All below the 7d/30d
    min_points=5 threshold so chart builder should return "".
    """
    db = db_session
    await _create_vehicle(db)

    now = datetime.now(UTC)
    trips = []
    for i in range(3):
        end_time = now - timedelta(days=(3 - i))
        t = EVTripMetrics(
            trip_id=uuid.uuid4(),
            device_id=DEVICE_ID,
            distance=50.0 + i * 10.0,
            duration=40.0,
            energy_consumed=12.0 + i * 2.0,
            efficiency=(50.0 + i * 10.0) / (12.0 + i * 2.0),
            ambient_temp=15.0 + i * 2.0,
            start_time=end_time - timedelta(minutes=30),
            end_time=end_time,
            is_complete=True,
            source_system="test_fixture",
        )
        trips.append(t)

    db.add_all(trips)
    await db.flush()
    return trips


@pytest_asyncio.fixture
async def trips_with_regen(db_session):
    """Trips with populated range_regenerated + distance + energy_consumed.

    Three trips with deterministic derivation for regen_kwh and regen_pct:
      - Trip A: distance=100, energy=20, range_regenerated=10 → regen_kwh=2.0, pct=10.0
      - Trip B: distance=50,  energy=10, range_regenerated=5  → regen_kwh=1.0, pct=10.0
      - Trip C: distance=200, energy=40, range_regenerated=20 → regen_kwh=4.0, pct=10.0
    """
    db = db_session
    await _create_vehicle(db)

    now = datetime.now(UTC)
    specs = [
        {"distance": 100.0, "energy": 20.0, "regen": 10.0},
        {"distance": 50.0, "energy": 10.0, "regen": 5.0},
        {"distance": 200.0, "energy": 40.0, "regen": 20.0},
    ]
    trips = []
    for i, spec in enumerate(specs):
        end_time = now - timedelta(days=(3 - i))
        t = EVTripMetrics(
            trip_id=uuid.uuid4(),
            device_id=DEVICE_ID,
            distance=spec["distance"],
            duration=60.0,
            energy_consumed=spec["energy"],
            efficiency=spec["distance"] / spec["energy"],
            range_regenerated=spec["regen"],
            start_time=end_time - timedelta(hours=1),
            end_time=end_time,
            is_complete=True,
            source_system="test_fixture",
        )
        trips.append(t)

    db.add_all(trips)
    await db.flush()
    return trips
