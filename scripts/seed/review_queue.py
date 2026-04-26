"""Seed module: mark a few sessions for the review queue with dedup pairs.

Picks 3 of the sessions inserted by charging_sessions.seed() (source_system='seed'),
flags them needs_review=True with review_type='duplicate', then inserts a
near-duplicate for each (source_system='seed_duplicate', also needs_review=True)
shifted ±30 min in start time and ±10% in energy.

Returns the number of dedup PAIRS created (0 if already seeded).
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.vehicle import EVVehicle

logger = logging.getLogger(__name__)

_DEMO_VIN = "1FT6W1EV0NWG00000"
_RNG = random.Random(42)
_PAIR_COUNT = 3


async def seed(db: AsyncSession) -> int:
    """Flag 3 seed sessions as needs_review and insert near-duplicate pairs.

    Idempotent: if any session for the demo vehicle already has needs_review=True,
    the module returns 0 immediately.

    Returns the number of dedup pairs created (0 on skip).
    """
    # Resolve demo vehicle device_id
    device_id = (
        await db.execute(select(EVVehicle.device_id).where(EVVehicle.vin == _DEMO_VIN))
    ).scalar_one_or_none()
    if device_id is None:
        logger.warning("Demo vehicle missing — skipping review_queue seed")
        return 0

    # Idempotency: skip if any session already flagged
    already_flagged = (
        await db.execute(
            select(EVChargingSession.id)
            .where(
                EVChargingSession.device_id == device_id,
                EVChargingSession.needs_review.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if already_flagged is not None:
        logger.debug("review_queue: already seeded — skipping")
        return 0

    # Pick the 3 most-recent seed sessions
    originals = (
        (
            await db.execute(
                select(EVChargingSession)
                .where(
                    EVChargingSession.device_id == device_id,
                    EVChargingSession.source_system == "seed",
                )
                .order_by(EVChargingSession.session_start_utc.desc())
                .limit(_PAIR_COUNT)
            )
        )
        .scalars()
        .all()
    )

    if not originals:
        logger.warning(
            "review_queue: no seed sessions found — run charging_sessions.seed() first"
        )
        return 0

    pairs_created = 0
    for orig in originals:
        # Flag the original
        orig.needs_review = True
        orig.review_type = "duplicate"

        # Time shift: ±30 min (never zero — ensure the pair is slightly off)
        start_shift_minutes = _RNG.choice([m for m in range(-30, 31) if m != 0])
        end_shift_minutes = _RNG.choice([m for m in range(-30, 31) if m != 0])

        dup_energy = float(orig.energy_kwh) * (1.0 + _RNG.uniform(-0.10, 0.10))
        dup_energy = round(dup_energy, 2)

        dup_cost = float(orig.cost) if orig.cost is not None else None
        dup_session_start = orig.session_start_utc + timedelta(
            minutes=start_shift_minutes
        )
        dup_session_end = orig.session_end_utc + timedelta(minutes=end_shift_minutes)
        dup_duration = (dup_session_end - dup_session_start).total_seconds()

        dup = EVChargingSession(
            # identifiers
            session_id=uuid.UUID(int=_RNG.getrandbits(128)),
            device_id=orig.device_id,
            # session classification
            charge_type=orig.charge_type,
            location_name=orig.location_name,
            location_type=orig.location_type,
            location_id=orig.location_id,
            network_id=orig.network_id,
            is_free=orig.is_free,
            plug_status=orig.plug_status,
            charging_status=orig.charging_status,
            station_status=orig.station_status,
            # power metrics — inherited from original (cross-source, same charger)
            charging_voltage=orig.charging_voltage,
            charging_amperage=orig.charging_amperage,
            charging_kw=orig.charging_kw,
            # timestamps
            session_start_utc=dup_session_start,
            session_end_utc=dup_session_end,
            estimated_end_utc=dup_session_end,
            recorded_at=dup_session_end,
            # duration
            charge_duration_seconds=round(max(dup_duration, 0), 0),
            plugged_in_duration_seconds=round(max(dup_duration + 120, 0), 0),
            # SoC / energy
            start_soc=orig.start_soc,
            end_soc=orig.end_soc,
            energy_kwh=dup_energy,
            # cost — keep identical (cross-source billing can match)
            cost=dup_cost,
            cost_without_overrides=dup_cost,
            cost_source=orig.cost_source,
            estimated_cost=orig.estimated_cost,
            # completion flag — NOT NULL, must be set
            is_complete=orig.is_complete,
            # geo
            address=orig.address,
            latitude=orig.latitude,
            longitude=orig.longitude,
            max_power=orig.max_power,
            min_power=orig.min_power,
            distance_added=orig.distance_added,
            # EVSE metrics
            evse_voltage=orig.evse_voltage,
            evse_amperage=orig.evse_amperage,
            evse_kw=orig.evse_kw,
            evse_energy_kwh=dup_energy,
            evse_max_power_kw=orig.evse_max_power_kw,
            charger_rated_kw=orig.charger_rated_kw,
            evse_source=orig.evse_source,
            # thermal
            battery_temp_start=orig.battery_temp_start,
            battery_temp_end=orig.battery_temp_end,
            ambient_temp_start=orig.ambient_temp_start,
            ambient_temp_end=orig.ambient_temp_end,
            # review flags
            needs_review=True,
            review_type="duplicate",
            duplicate_of_id=orig.id,
            # pipeline metadata
            source_system="seed_duplicate",
            original_timestamp=dup_session_start,
            ingest_schema_version=2,
        )
        db.add(dup)
        pairs_created += 1

    await db.flush()
    logger.info(
        "review_queue: flagged %d originals, inserted %d duplicates",
        pairs_created,
        pairs_created,
    )
    return pairs_created
