"""Seed module: 90 days of realistic demo charging sessions (~40–50 rows).

Distribution:
  ~40% Home L2         — slow AC, private
  ~25% Work L2         — slow AC, private
  ~20% Tesla SC DCFC   — fast DC, public
  ~15% EA Costco DCFC  — fast DC, public

Thermal fields (battery_temp_*, ambient_temp_*) are populated via
ContractDrivenSeeder.value_for() using the FIELD_CONTRACTS declared in the
HA Fordpass adapter.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork, EVLocationLookup
from db.models.vehicle import EVVehicle
from scripts.seed.base import ContractDrivenSeeder, load_declared_contracts

_DEMO_VIN = "1FT6W1EV0NWG00000"
_TABLE = "ev_charging_session"

# ---------------------------------------------------------------------------
# Charger-type profiles
# ---------------------------------------------------------------------------

_PROFILES = [
    # (location_name, charge_type, location_type, network_name,
    #  weight, kwh_range, dur_hours_range, peak_kw, soc_start_range,
    #  soc_end_range, rate_per_kwh, charge_current_type)
    (
        "Home",
        "AC",
        "private",
        "Home",
        40,
        (8.0, 25.0),
        (2.0, 6.0),
        7.0,
        (20.0, 50.0),
        (70.0, 95.0),
        0.12,
        "AC",
    ),
    (
        "Work",
        "AC",
        "private",
        "Work",  # employer-provided network (free)
        25,
        (5.0, 18.0),
        (1.5, 4.0),
        7.0,
        (20.0, 55.0),
        (65.0, 92.0),
        0.0,  # free at work
        "AC",
    ),
    (
        "Tesla Supercharger Downtown",
        "DC",
        "public",
        "Tesla Supercharger",
        20,
        (30.0, 60.0),
        (20 / 60, 45 / 60),
        150.0,
        (15.0, 45.0),
        (75.0, 95.0),
        0.36,
        "DC",
    ),
    (
        "Electrify America Costco",
        "DC",
        "public",
        "Electrify America",
        15,
        (35.0, 70.0),
        (15 / 60, 30 / 60),
        250.0,
        (15.0, 40.0),
        (80.0, 95.0),
        0.36,
        "DC",
    ),
]

# Unpack weights for random.choices
_WEIGHTS = [p[4] for p in _PROFILES]

_TARGET_SESSIONS = 50
_MIN_SESSIONS = 40
_DAYS_BACK = 90


async def seed(db: AsyncSession) -> int:
    """Insert up to 50 charging sessions spanning 90 days.

    Idempotent: if ≥40 sessions already exist for the demo vehicle, returns 0.
    Raises RuntimeError if the demo vehicle or any required location/network is absent.
    """
    # --- Lookup demo vehicle ---
    result = await db.execute(select(EVVehicle).where(EVVehicle.vin == _DEMO_VIN))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise RuntimeError(
            f"Demo vehicle with VIN {_DEMO_VIN} not found — run vehicle.seed() first."
        )

    # --- Idempotency check ---
    count_result = await db.execute(
        select(func.count()).where(EVChargingSession.device_id == vehicle.device_id)
    )
    existing_count = count_result.scalar_one()
    if existing_count >= _MIN_SESSIONS:
        return 0

    # --- Build lookup caches ---
    loc_names = [p[0] for p in _PROFILES]
    locs_result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.location_name.in_(loc_names))
    )
    loc_map: dict[str, EVLocationLookup] = {
        loc.location_name: loc for loc in locs_result.scalars().all()
    }
    for name in loc_names:
        if name not in loc_map:
            raise RuntimeError(
                f"Location '{name}' not found in ev_location_lookup — run locations.seed() first."
            )

    net_names = [p[3] for p in _PROFILES if p[3] is not None]
    nets_result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.network_name.in_(net_names))
    )
    net_map: dict[str, EVChargingNetwork] = {
        net.network_name: net for net in nets_result.scalars().all()
    }
    for name in net_names:
        if name not in net_map:
            raise RuntimeError(
                f"Network '{name}' not found in ev_charging_networks — run networks.seed() first."
            )

    # --- Set up seeder and RNG ---
    seeder = ContractDrivenSeeder(
        declared=load_declared_contracts(),
        expected=[],
        rng=random.Random(42),
    )
    rng = random.Random(42)

    # --- Generate timestamps (1–3 day spacing, 90 days back) ---
    # Anchor the most recent session within the last ~18 hours so the
    # default 7-day dashboard window always has fresh rows. Walk
    # backwards from there and reverse to ascending.
    now = datetime.now(UTC)
    earliest = now - timedelta(days=_DAYS_BACK)
    timestamps: list[datetime] = []
    cursor = now - timedelta(hours=rng.uniform(2.0, 18.0))
    while cursor > earliest and len(timestamps) < _TARGET_SESSIONS:
        timestamps.append(cursor)
        gap_days = rng.uniform(1.0, 3.0)
        cursor -= timedelta(days=gap_days)
    timestamps.reverse()

    sessions: list[EVChargingSession] = []
    for ts_start in timestamps:
        # Pick a profile by weight
        profile = rng.choices(_PROFILES, weights=_WEIGHTS, k=1)[0]
        (
            loc_name,
            charge_type,
            loc_type,
            network_name,
            _weight,
            kwh_range,
            dur_hours_range,
            peak_kw,
            soc_start_range,
            soc_end_range,
            rate_per_kwh,
            current_type,
        ) = profile

        loc = loc_map[loc_name]
        network = net_map[network_name] if network_name else None

        # Energy and duration
        energy_kwh = round(rng.uniform(*kwh_range), 2)
        dur_hours = rng.uniform(*dur_hours_range)
        dur_seconds = dur_hours * 3600
        ts_end = ts_start + timedelta(seconds=dur_seconds)

        # Wall-to-battery loss factor (EVSE-side energy is always > vehicle-side)
        if current_type == "AC":
            loss_factor = rng.uniform(0.10, 0.14)
        else:
            loss_factor = rng.uniform(0.04, 0.07)
        evse_energy_kwh = round(energy_kwh * (1 + loss_factor), 2)

        # Max-power utilization factor (real sessions rarely peak the stall)
        if current_type == "AC":
            util_factor = rng.uniform(0.85, 0.98)
        else:
            util_factor = rng.uniform(0.55, 0.92)
        evse_max_power_kw = round(peak_kw * util_factor, 2)

        # SoC
        start_soc = round(rng.uniform(*soc_start_range), 1)
        end_soc = round(rng.uniform(*soc_end_range), 1)

        # Cost
        if rate_per_kwh == 0.0:
            cost = 0.0
            estimated_cost = 0.0
            cost_without_overrides = 0.0
            cost_source = "calculated"
            session_source_system = "seed"
            is_free = True
        else:
            estimated_cost = round(energy_kwh * rate_per_kwh, 2)
            cost_without_overrides = estimated_cost
            roll = rng.random()
            if roll < 0.70:
                cost_source = "calculated"
                session_source_system = "seed"
                cost = estimated_cost
            elif roll < 0.85:
                cost_source = "manual"
                session_source_system = "manual_entry"
                cost = round(estimated_cost * rng.uniform(0.85, 1.20), 2)
            elif roll < 0.95:
                cost_source = "adapter"
                session_source_system = "ha_fordpass"
                cost = round(estimated_cost * rng.uniform(0.95, 1.05), 2)
            else:
                cost_source = "manual"
                session_source_system = "csv_import"
                cost = round(estimated_cost * rng.uniform(0.50, 0.90), 2)
            is_free = False

        # Power metrics — average kW inferred from energy/duration
        avg_kw = round(energy_kwh / dur_hours, 2) if dur_hours > 0 else peak_kw
        voltage = 240.0 if current_type == "AC" else 400.0
        amperage = round(avg_kw * 1000 / voltage, 1)

        # Thermal fields via ContractDrivenSeeder
        battery_temp_start = seeder.value_for(_TABLE, "battery_temp_start")
        battery_temp_end = seeder.value_for(_TABLE, "battery_temp_end")
        ambient_temp_start = seeder.value_for(_TABLE, "ambient_temp_start")
        ambient_temp_end = seeder.value_for(_TABLE, "ambient_temp_end")

        # Distance added — rough estimate: ~6 km/kWh efficiency
        distance_added = round(energy_kwh * 6.0, 1)

        evse_source = rng.choices(
            ["estimated", "stall_default", "manual", "adapter"],
            weights=[50, 30, 10, 10],
            k=1,
        )[0]

        session = EVChargingSession(
            session_id=uuid.UUID(int=rng.getrandbits(128)),
            device_id=vehicle.device_id,
            charge_type=charge_type,
            location_name=loc_name,
            location_type=loc_type,
            location_id=loc.id,
            network_id=network.id if network else None,
            is_free=is_free,
            plug_status="unplugged",
            charging_status="complete",
            station_status="available",
            charging_voltage=voltage,
            charging_amperage=amperage,
            charging_kw=avg_kw,
            session_start_utc=ts_start,
            session_end_utc=ts_end,
            estimated_end_utc=ts_end,
            recorded_at=ts_end,
            charge_duration_seconds=round(dur_seconds, 0),
            plugged_in_duration_seconds=round(dur_seconds + rng.uniform(0, 300), 0),
            start_soc=start_soc,
            end_soc=end_soc,
            energy_kwh=energy_kwh,
            cost=cost,
            cost_without_overrides=cost_without_overrides,
            cost_source=cost_source,
            estimated_cost=estimated_cost,
            is_complete=True,
            address=loc.address,
            latitude=loc.latitude,
            longitude=loc.longitude,
            max_power=peak_kw,
            min_power=round(avg_kw * 0.5, 2),
            distance_added=distance_added,
            evse_voltage=voltage,
            evse_amperage=amperage,
            evse_kw=avg_kw,
            evse_energy_kwh=evse_energy_kwh,
            evse_max_power_kw=evse_max_power_kw,
            charger_rated_kw=peak_kw,
            evse_source=evse_source,
            needs_review=False,
            battery_temp_start=battery_temp_start,
            battery_temp_end=battery_temp_end,
            ambient_temp_start=ambient_temp_start,
            ambient_temp_end=ambient_temp_end,
            source_system=session_source_system,
            original_timestamp=ts_start,
            ingest_schema_version=2,
        )
        sessions.append(session)

    db.add_all(sessions)
    await db.flush()
    return len(sessions)
