"""Seed review-queue rows by replaying synthesized HASS events.

Runs after seed_sample.py. Fires six fake FordPass energytransferlogentry
payloads through the real hass_processor pipeline to populate every review
surface the UI exposes: unverified networks, unverified locations, and
sessions flagged needs_review (duplicate + auto_association).

Also backfills unverified ev_location_lookup rows for the public charging
locations that seed_sample.py leaves dangling (session rows with
location_name/lat/lon but no lookup entry), so the Locations review tab
shows the full set of public locations as if they had been ingested via HA.

Usage:
    uv run python scripts/seed_review_queue.py
    uv run python scripts/seed_review_queue.py --dry-run
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import func, select, text

from db.engine import AsyncSessionLocal
from db.models.charging_session import EVChargingSession
from db.models.reference import EVLocationLookup
from web.services.hass_processor import process_state_change

SEED_SOURCE = "review_queue_seed"
SAMPLE_SOURCE = "sample_generator"
# Default matches generate_sample_data.py:36. seed_sample.py can override via
# --device-id (its default is the VIN), so we auto-detect at runtime from the
# sample rows actually present in the DB.
DEFAULT_TARGET_DEVICE_ID = "demo_lightning_showcase"
TARGET_DEVICE_ID: str = DEFAULT_TARGET_DEVICE_ID

# Minimal ha_config: only unit_system / FordPass unit keys are read by the
# processor, and metric defaults mean no conversion happens (we supply metric
# values directly in the payloads below).
HA_CONFIG: dict = {
    "unit_system": {
        "length": "km",
        "mass": "kg",
        "temperature": "\u00b0C",
        "volume": "L",
    },
}


# ---------------------------------------------------------------------------
# Scenarios — six HA events spanning the review UI states
# ---------------------------------------------------------------------------
# Each scenario drives one process_state_change call. The overlap_filter
# chooses a real sample_generator session to align with (for duplicate
# detection); non-overlapping scenarios pass None and use a fixed timestamp.

SCENARIOS: list[dict] = [
    {
        "id": "A",
        "network": "Electrify America Corp",
        "location_name": "Downtown EA",
        "latitude": 39.2907,
        "longitude": -76.6125,
        "address": "North Charles St, Baltimore MD",
        "charge_type": "DC_FAST",
        "energy_kwh": None,          # derived from overlap target * 1.02
        "duration_seconds": 2400.0,
        "start_soc": 30.0,
        "end_soc": 80.0,
        "max_power_w": 150000.0,
        "overlap_filter": {"location_name_ilike": "Home"},
        "promote_to": "auto_association",
    },
    {
        "id": "B",
        "network": "EVGo Charging",
        "location_name": "Pittsburgh Fast Stop",
        "latitude": 40.4406,
        "longitude": -79.9959,
        "address": "Liberty Ave, Pittsburgh PA",
        "charge_type": "DC_FAST",
        "energy_kwh": 34.5,
        "duration_seconds": 1500.0,
        "start_soc": 25.0,
        "end_soc": 70.0,
        "max_power_w": 100000.0,
        "overlap_filter": None,
        "fixed_start_utc": "2024-11-03T14:15:00+00:00",
    },
    {
        "id": "C",
        "network": "Neighborhood Free L2",
        "location_name": "Community Center",
        "latitude": 39.3001,
        "longitude": -76.6022,
        "address": "Community Center, Baltimore MD",
        "charge_type": "AC",
        "energy_kwh": None,
        "duration_seconds": 9000.0,
        "start_soc": 40.0,
        "end_soc": 75.0,
        "max_power_w": 7200.0,
        "overlap_filter": {"location_name_ilike": "Work"},
    },
    {
        "id": "D",
        "network": "Electrify America",  # verified, already exists
        "location_name": "EA Harbor Annex",
        "latitude": 39.2870,
        "longitude": -76.5998,
        "address": "Harbor East Annex, Baltimore MD",
        "charge_type": "DC_FAST",
        "energy_kwh": 41.2,
        "duration_seconds": 1800.0,
        "start_soc": 22.0,
        "end_soc": 78.0,
        "max_power_w": 175000.0,
        "overlap_filter": None,
        "fixed_start_utc": "2024-10-22T16:40:00+00:00",
        "skip_network_retag": True,  # network is pre-existing + verified
    },
    {
        "id": "E",
        "network": "Tesla Superchargers",  # plural — variant of verified Tesla Supercharger
        "location_name": "Reston Annex",
        "latitude": 38.9590,
        "longitude": -77.3568,
        "address": "Reston Town Center Annex, Reston VA",
        "charge_type": "DC_FAST",
        "energy_kwh": None,
        "duration_seconds": 1800.0,
        "start_soc": 20.0,
        "end_soc": 75.0,
        "max_power_w": 140000.0,
        "overlap_filter": {"charge_type": "DC"},
    },
    {
        "id": "F",
        "network": "Home",  # verified, already exists
        "location_name": "Garage",
        "latitude": 39.2905,
        "longitude": -76.6123,
        "address": "Home Garage, Baltimore MD",
        "charge_type": "AC",
        "energy_kwh": 15.5,
        "duration_seconds": 7200.0,
        "start_soc": 55.0,
        "end_soc": 85.0,
        "max_power_w": 9600.0,
        "overlap_filter": None,
        "fixed_start_utc": "2024-12-05T03:30:00+00:00",
        "skip_network_retag": True,  # Home is pre-existing + verified
    },
]


# ---------------------------------------------------------------------------
# Payload builder — matches tests/test_ha_sim/simulator.py:make_charging_session_event
# ---------------------------------------------------------------------------

def build_energy_transfer_payload(
    device_id: str,
    session_start_utc: datetime,
    energy_kwh: float,
    charge_type: str,
    network_name: str,
    location_name: str,
    latitude: float,
    longitude: float,
    address: str,
    start_soc: float,
    end_soc: float,
    duration_seconds: float,
    max_power_w: float,
) -> tuple[str, dict]:
    entity_id = f"sensor.fordpass_{device_id}_energytransferlogentry"
    start_iso = session_start_utc.isoformat()
    end_dt = session_start_utc + timedelta(seconds=duration_seconds)
    end_iso = end_dt.isoformat()

    attrs = {
        "energyConsumed": energy_kwh,
        "chargerType": charge_type,
        "energyTransferDuration": {
            "begin": start_iso,
            "end": end_iso,
            "totalTime": duration_seconds,
        },
        "plugDetails": {
            "totalPluggedInTime": duration_seconds + 120,
            "totalDistanceAdded": 80.0,
        },
        "stateOfCharge": {
            "firstSOC": start_soc,
            "lastSOC": end_soc,
        },
        "power": {
            "max": max_power_w,
            "min": 5000.0,
            "weightedAverage": max_power_w * 0.7,
        },
        "location": {
            "name": location_name,
            "network": network_name,
            "latitude": latitude,
            "longitude": longitude,
            "address": {
                "address1": address,
                "city": None,
                "state": None,
            },
        },
        "timeStamp": end_iso,
    }
    new_state = {
        "state": "complete",
        "last_changed": end_iso,
        "last_updated": end_iso,
        "attributes": attrs,
    }
    return entity_id, new_state


# ---------------------------------------------------------------------------
# Overlap timestamp picker — aligns to a real sample session for dedup match
# ---------------------------------------------------------------------------

async def pick_overlap_target(db, overlap_filter: dict) -> Optional[tuple[datetime, float]]:
    """Return (session_start_utc, energy_kwh) of a sample session matching filter."""
    query = select(
        EVChargingSession.session_start_utc,
        EVChargingSession.energy_kwh,
    ).where(
        EVChargingSession.source_system == SAMPLE_SOURCE,
        EVChargingSession.device_id == TARGET_DEVICE_ID,
        EVChargingSession.session_start_utc.isnot(None),
        EVChargingSession.energy_kwh.isnot(None),
    )

    if "location_name_ilike" in overlap_filter:
        query = query.where(
            EVChargingSession.location_name.ilike(overlap_filter["location_name_ilike"])
        )
    if "charge_type" in overlap_filter:
        query = query.where(EVChargingSession.charge_type == overlap_filter["charge_type"])

    query = query.order_by(EVChargingSession.session_start_utc.asc()).limit(1)
    row = (await db.execute(query)).first()
    if row is None:
        return None
    start, energy = row
    return start, float(energy)


# ---------------------------------------------------------------------------
# Wipe + retag — makes the script re-runnable
# ---------------------------------------------------------------------------

async def wipe_previous_seed(db) -> None:
    """Delete rows tagged SEED_SOURCE so a re-run starts clean."""
    # Sessions first (FK on network_id is ON DELETE SET NULL but sessions
    # reference the networks/locations, so removing children first is cleaner).
    res = await db.execute(
        text("DELETE FROM ev_charging_session WHERE source_system = :s"),
        {"s": SEED_SOURCE},
    )
    sessions_deleted = res.rowcount
    res = await db.execute(
        text("DELETE FROM ev_charging_networks WHERE source_system = :s"),
        {"s": SEED_SOURCE},
    )
    networks_deleted = res.rowcount
    res = await db.execute(
        text("DELETE FROM ev_location_lookup WHERE source_system = :s"),
        {"s": SEED_SOURCE},
    )
    locations_deleted = res.rowcount
    await db.commit()
    print(
        f"  Wiped previous seed: {sessions_deleted} sessions, "
        f"{networks_deleted} networks, {locations_deleted} locations"
    )


async def retag_after_scenario(scenario: dict, session_start_utc: datetime) -> None:
    """Re-tag rows just inserted by process_state_change from 'home_assistant' → SEED_SOURCE.

    Sessions are fingerprinted by (device_id, session_start_utc). Networks
    and locations are fingerprinted by name + is_verified=False + HA/NULL
    source. This lets wipe_previous_seed cleanly remove them on re-run.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                UPDATE ev_charging_session
                   SET source_system = :seed
                 WHERE device_id = :did
                   AND session_start_utc = :start
                   AND source_system = 'home_assistant'
                """
            ),
            {"seed": SEED_SOURCE, "did": TARGET_DEVICE_ID, "start": session_start_utc},
        )
        if not scenario.get("skip_network_retag"):
            await db.execute(
                text(
                    """
                    UPDATE ev_charging_networks
                       SET source_system = :seed
                     WHERE network_name = :name
                       AND is_verified = FALSE
                       AND (source_system = 'home_assistant' OR source_system IS NULL)
                    """
                ),
                {"seed": SEED_SOURCE, "name": scenario["network"]},
            )
        await db.execute(
            text(
                """
                UPDATE ev_location_lookup
                   SET source_system = :seed
                 WHERE location_name = :name
                   AND is_verified = FALSE
                   AND (source_system = 'home_assistant' OR source_system IS NULL)
                """
            ),
            {"seed": SEED_SOURCE, "name": scenario["location_name"]},
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Sample-location backfill — surfaces the public charging locations that
# seed_sample.py leaves as session-only rows
# ---------------------------------------------------------------------------

async def backfill_sample_locations(db) -> int:
    """Insert unverified ev_location_lookup rows for distinct sample session locations.
    seed_sample.py seeds only Home and Work into ev_location_lookup but the
    sample sessions reference ~13 other location_names (public chargers). This
    mirrors what resolve_location would have produced during live HA
    ingestion, so the Locations review tab is populated.
    Idempotent: skips location names already present (case-insensitive).
    Never touches Home/Work — those are verified and owned by seed_sample.py.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT DISTINCT
                    location_name,
                    location_type,
                    FIRST_VALUE(latitude) OVER w AS latitude,
                    FIRST_VALUE(longitude) OVER w AS longitude,
                    FIRST_VALUE(address) OVER w AS address
                FROM ev_charging_session
                WHERE source_system = :src
                  AND location_name IS NOT NULL
                  AND location_name NOT IN ('Home', 'Work')
                WINDOW w AS (
                    PARTITION BY location_name
                    ORDER BY session_start_utc
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                )
                """
            ),
            {"src": SAMPLE_SOURCE},
        )
    ).all()

    existing = {
        n.lower()
        for n in (
            await db.execute(select(func.lower(EVLocationLookup.location_name)))
        )
        .scalars()
        .all()
    }

    inserted = 0
    name_to_id: dict[str, int] = {}
    for loc_name, loc_type, lat, lon, addr in rows:
        if loc_name.lower() in existing:
            continue
        inferred_type = loc_type if loc_type in ("home", "work") else "public"
        new_loc = EVLocationLookup(
            location_name=loc_name,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            address=addr,
            location_type=inferred_type,
            is_verified=False,
            source_system=SAMPLE_SOURCE,
        )
        db.add(new_loc)
        await db.flush()
        name_to_id[loc_name] = new_loc.id
        inserted += 1

    # Point matching sessions at the new location ids
    for loc_name, loc_id in name_to_id.items():
        await db.execute(
            text(
                """
                UPDATE ev_charging_session
                   SET location_id = :lid
                 WHERE source_system = :src
                   AND location_name = :name
                   AND (location_id IS NULL OR location_id NOT IN (
                        SELECT id FROM ev_location_lookup))
                """
            ),
            {"lid": loc_id, "src": SAMPLE_SOURCE, "name": loc_name},
        )

    await db.commit()
    print(f"  Backfilled {inserted} unverified locations from sample data")
    return inserted


# ---------------------------------------------------------------------------
# Post-commit patch: flip one session to review_type='auto_association'
# ---------------------------------------------------------------------------

async def patch_auto_association(db, scenario_a_start: Optional[datetime]) -> None:
    """Flip scenario A's session to review_type='auto_association' for UI coverage.

    No live code path produces this state today (only the schema and template
    reference it). We force one row into it so the "Confirm" badge renders
    against real data.
    """
    if scenario_a_start is None:
        logging.warning("patch_auto_association: no scenario A start timestamp, skipping")
        return
    logging.warning(
        "patch_auto_association: forcing review_type='auto_association' on seeded row — "
        "this state is not set by any live code path today."
    )
    await db.execute(
        text(
            """
            UPDATE ev_charging_session
               SET review_type = 'auto_association',
                   needs_review = TRUE
             WHERE device_id = :did
               AND session_start_utc = :start
               AND source_system = :seed
            """
        ),
        {"did": TARGET_DEVICE_ID, "start": scenario_a_start, "seed": SEED_SOURCE},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Verification output
# ---------------------------------------------------------------------------

async def verify_review_queue(db) -> None:
    print(f"\n{'=' * 60}")
    print("  REVIEW QUEUE VERIFICATION")
    print(f"{'=' * 60}")

    # Networks
    row = (
        await db.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_verified = FALSE) AS unverified_total,
                    COUNT(*) FILTER (WHERE is_verified = FALSE AND source_system = :seed) AS unverified_seed
                FROM ev_charging_networks
                """
            ),
            {"seed": SEED_SOURCE},
        )
    ).first()
    print(
        f"\n  Unverified networks: total={row.unverified_total}  "
        f"(seeded-by-this-script={row.unverified_seed})"
    )

    # Locations
    row = (
        await db.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_verified = FALSE) AS unverified_total,
                    COUNT(*) FILTER (WHERE is_verified = FALSE AND source_system = :seed) AS unverified_seed,
                    COUNT(*) FILTER (WHERE is_verified = FALSE AND source_system = :sample) AS unverified_sample
                FROM ev_location_lookup
                """
            ),
            {"seed": SEED_SOURCE, "sample": SAMPLE_SOURCE},
        )
    ).first()
    print(
        f"  Unverified locations: total={row.unverified_total}  "
        f"(seeded-by-this-script={row.unverified_seed}, "
        f"backfilled-from-sample={row.unverified_sample})"
    )

    # Session review types
    rows = (
        await db.execute(
            text(
                """
                SELECT COALESCE(review_type, '(none)') AS rt, COUNT(*) AS cnt
                FROM ev_charging_session
                WHERE needs_review = TRUE
                GROUP BY review_type
                ORDER BY rt
                """
            )
        )
    ).all()
    print("\n  Sessions needs_review=TRUE by review_type:")
    if not rows:
        print("    (none)")
    for r in rows:
        print(f"    {r.rt}: {r.cnt}")

    # Duplicate pairs — seeded vs originals
    rows = (
        await db.execute(
            text(
                """
                SELECT s.id AS seed_id,
                       s.duplicate_of_id AS orig_id,
                       s.session_start_utc AS start,
                       s.energy_kwh AS kwh
                FROM ev_charging_session s
                WHERE s.source_system = :seed
                  AND s.duplicate_of_id IS NOT NULL
                ORDER BY s.session_start_utc
                LIMIT 5
                """
            ),
            {"seed": SEED_SOURCE},
        )
    ).all()
    print("\n  Duplicate pairs (seed_id → original_id):")
    if not rows:
        print("    (none)")
    for r in rows:
        kwh = float(r.kwh) if r.kwh is not None else None
        kwh_str = f"{kwh:.3f}" if kwh is not None else "—"
        print(f"    {r.seed_id} → {r.orig_id}  start={r.start}  kWh={kwh_str}")

    print("\n  Open http://localhost:8000/review to triage.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _detect_sample_device_id() -> str:
    """Detect the device_id used by sample_generator rows in the DB.

    seed_sample.py defaults to the VIN (1FT8W3ED5LFB0D19) but generate_sample_data.py
    uses 'demo_lightning_showcase'. Either could be live — pick whichever owns the
    most sample rows so duplicate-detection fuzzy matching finds real targets.
    """
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT device_id, COUNT(*) AS cnt
                    FROM ev_charging_session
                    WHERE source_system = :src
                    GROUP BY device_id
                    ORDER BY cnt DESC
                    LIMIT 1
                    """
                ),
                {"src": SAMPLE_SOURCE},
            )
        ).first()
    if row is None:
        return DEFAULT_TARGET_DEVICE_ID
    return row.device_id


async def run(dry_run: bool) -> None:
    global TARGET_DEVICE_ID

    if not dry_run:
        TARGET_DEVICE_ID = await _detect_sample_device_id()

    print(f"\n  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  Device ID: {TARGET_DEVICE_ID}")
    print(f"  Seed source tag: {SEED_SOURCE}")

    print(f"\n{'=' * 60}")
    print("  SCENARIOS")
    print(f"{'=' * 60}")
    for sc in SCENARIOS:
        overlap = "overlap" if sc.get("overlap_filter") else "fixed"
        print(f"  [{sc['id']}] {sc['network']} @ {sc['location_name']} ({overlap})")

    if dry_run:
        print("\n  [DRY RUN] No DB writes.")
        return

    print(f"\n{'=' * 60}")
    print("  WIPE PREVIOUS SEED")
    print(f"{'=' * 60}")
    async with AsyncSessionLocal() as db:
        await wipe_previous_seed(db)

    print(f"\n{'=' * 60}")
    print("  BACKFILL SAMPLE LOCATIONS")
    print(f"{'=' * 60}")
    async with AsyncSessionLocal() as db:
        await backfill_sample_locations(db)

    print(f"\n{'=' * 60}")
    print("  REPLAY HASS EVENTS")
    print(f"{'=' * 60}")

    scenario_a_start: Optional[datetime] = None

    for sc in SCENARIOS:
        # Resolve timestamp + energy
        if sc.get("overlap_filter"):
            async with AsyncSessionLocal() as db:
                target = await pick_overlap_target(db, sc["overlap_filter"])
            if target is None:
                print(
                    f"  [{sc['id']}] SKIP — no overlap target found "
                    f"for filter {sc['overlap_filter']}"
                )
                continue
            target_start, target_energy = target
            start_utc = target_start + timedelta(minutes=10)
            energy_kwh = target_energy * 1.02
        else:
            start_utc = datetime.fromisoformat(sc["fixed_start_utc"])
            if start_utc.tzinfo is None:
                start_utc = start_utc.replace(tzinfo=timezone.utc)
            energy_kwh = sc["energy_kwh"]

        entity_id, new_state = build_energy_transfer_payload(
            device_id=TARGET_DEVICE_ID,
            session_start_utc=start_utc,
            energy_kwh=energy_kwh,
            charge_type=sc["charge_type"],
            network_name=sc["network"],
            location_name=sc["location_name"],
            latitude=sc["latitude"],
            longitude=sc["longitude"],
            address=sc["address"],
            start_soc=sc["start_soc"],
            end_soc=sc["end_soc"],
            duration_seconds=sc["duration_seconds"],
            max_power_w=sc["max_power_w"],
        )

        print(
            f"  [{sc['id']}] fire event — {sc['network']} @ {sc['location_name']}, "
            f"start={start_utc.isoformat()}, kWh={energy_kwh:.3f}"
        )
        await process_state_change(entity_id, {}, new_state, HA_CONFIG)
        await retag_after_scenario(sc, start_utc)

        if sc["id"] == "A":
            scenario_a_start = start_utc

    # Promote scenario A's row from duplicate → auto_association
    print(f"\n{'=' * 60}")
    print("  PROMOTE AUTO-ASSOCIATION ROW")
    print(f"{'=' * 60}")
    async with AsyncSessionLocal() as db:
        await patch_auto_association(db, scenario_a_start)

    async with AsyncSessionLocal() as db:
        await verify_review_queue(db)

    print("\n  Done.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed review-queue rows by replaying fake HASS energy-transfer events",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print scenarios, no DB writes")
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
