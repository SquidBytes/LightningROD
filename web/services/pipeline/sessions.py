"""One charging-session row per charge, whichever sources record it.

HA's energy-transfer log entry is Ford's own record of the whole charge, so
it wins everything it measures. FordConnect adds what only per-minute
telemetry sees. Columns a user curates, or that come from an approved
location, are fill-only: a merge never overwrites them. Manual and imported
sessions are never changed.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.queries.locations import get_location_defaults, resolve_location
from web.queries.sessions import find_matching_sessions
from web.queries.trips import nearest_gps
from web.services.pipeline.observation import INGEST_SCHEMA_VERSION, SessionObservation
from web.services.source_merge import (
    Rule,
    is_km_mile_twin,
    merge_values,
)
from web.services.source_system import INGESTED, SourceSystem

logger = logging.getLogger("lightningrod.pipeline.sessions")


def checked_distance_added(current: dict, incoming: dict, incoming_source: str) -> Any:
    """HA's range added, unless it is FordConnect's converted between km and miles.

    HA localizes totalDistanceAdded, the class of bug that double-converted
    trip distances. Range added and a range-estimate change measure slightly
    different things, so only the x1.609 signature overrules HA.
    """
    fcon_side, ha_side = (incoming, current) if incoming_source == SourceSystem.FCON else (current, incoming)
    ha_value, fcon_value = float(ha_side["distance_added"]), float(fcon_side["distance_added"])
    if not is_km_mile_twin(ha_value, fcon_value):
        return ha_side["distance_added"]
    logger.warning(
        "Charge distance_added looks unit-converted for %s starting %s: Home Assistant "
        "%.3f km, FordConnect range gain %.3f km; storing FordConnect's",
        current.get("device_id") or incoming.get("device_id"),
        current.get("session_start_utc") or incoming.get("session_start_utc"),
        ha_value,
        fcon_value,
    )
    return fcon_side["distance_added"]


# Winner name for columns neither source may overwrite once set.
KEEP = "keep"
_CURATED = "curated or from the approved location; fill-only"

SESSION_PRECEDENCE: dict[str, Rule] = {
    "session_start_utc": Rule(SourceSystem.HA_FORDPASS, "Ford's log timestamps; FCON's are per-minute samples"),
    "session_end_utc": Rule(SourceSystem.HA_FORDPASS, "Ford's log timestamps; FCON's are per-minute samples"),
    "charge_duration_seconds": Rule(SourceSystem.HA_FORDPASS, "measured by the vehicle for the whole charge"),
    "plugged_in_duration_seconds": Rule(SourceSystem.HA_FORDPASS, "measured plug time; FCON starts at first charging sample"),
    "energy_kwh": Rule(SourceSystem.HA_FORDPASS, "energy the vehicle logged; FCON's is a pack-estimate delta"),
    "start_soc": Rule(SourceSystem.HA_FORDPASS, "log's first/last SoC"),
    "end_soc": Rule(SourceSystem.HA_FORDPASS, "log's first/last SoC"),
    "max_power": Rule(SourceSystem.HA_FORDPASS, "log covers every second; FCON samples once a minute"),
    "min_power": Rule(SourceSystem.HA_FORDPASS, "log covers every second; FCON samples once a minute"),
    "charging_kw": Rule(SourceSystem.HA_FORDPASS, "log's weighted average; FCON's is the last live sample"),
    "distance_added": Rule(
        SourceSystem.HA_FORDPASS, "log's range added, unless it is FCON's x1.609", resolve=checked_distance_added
    ),
    "charge_type": Rule(SourceSystem.HA_FORDPASS, "the log names the charger type directly"),
    "address": Rule(SourceSystem.HA_FORDPASS, "only the log carries a street address"),
    "latitude": Rule(SourceSystem.HA_FORDPASS, "the log's location is where Ford recorded the charge"),
    "longitude": Rule(SourceSystem.HA_FORDPASS, "the log's location is where Ford recorded the charge"),
    "plug_status": Rule(SourceSystem.FCON, "live plug state at the unplug sample"),
    "charging_status": Rule(SourceSystem.FCON, "live charge display state"),
    "station_status": Rule(SourceSystem.FCON, "only telemetry reports the station link"),
    "charging_voltage": Rule(SourceSystem.FCON, "only telemetry reports charger volts"),
    "charging_amperage": Rule(SourceSystem.FCON, "only telemetry reports charger amps"),
    "estimated_end_utc": Rule(SourceSystem.FCON, "only telemetry reports time-to-full"),
    "battery_temp_start": Rule(SourceSystem.FCON, "read at the start; HA reuses one cached value"),
    "battery_temp_end": Rule(SourceSystem.FCON, "read at the end; HA reuses one cached value"),
    "ambient_temp_start": Rule(SourceSystem.FCON, "read at the start; HA reuses one cached value"),
    "ambient_temp_end": Rule(SourceSystem.FCON, "read at the end; HA reuses one cached value"),
    "location_id": Rule(KEEP, _CURATED),
    "location_name": Rule(KEEP, _CURATED),
    "location_type": Rule(KEEP, _CURATED),
    "network_id": Rule(KEEP, _CURATED),
    "stall_id": Rule(KEEP, _CURATED),
    "evse_voltage": Rule(KEEP, _CURATED),
    "evse_amperage": Rule(KEEP, _CURATED),
    "evse_kw": Rule(KEEP, _CURATED),
    "evse_energy_kwh": Rule(KEEP, _CURATED),
    "evse_max_power_kw": Rule(KEEP, _CURATED),
    "charger_rated_kw": Rule(KEEP, _CURATED),
    "evse_source": Rule(KEEP, _CURATED),
    "is_free": Rule(KEEP, _CURATED),
    "cost": Rule(KEEP, _CURATED),
    "cost_without_overrides": Rule(KEEP, _CURATED),
    "cost_source": Rule(KEEP, _CURATED),
    "estimated_cost": Rule(KEEP, _CURATED),
    "source_system": Rule(SourceSystem.HA_FORDPASS, "one label whichever source arrived first"),
}


def is_mergeable(row: EVChargingSession) -> bool:
    return row.source_system in INGESTED


def session_values(row: EVChargingSession) -> dict[str, Any]:
    return {f: getattr(row, f) for f in (*SESSION_PRECEDENCE, "device_id")}


def apply_session_merge(
    row: EVChargingSession, incoming: dict, incoming_source: str
) -> dict[str, Any]:
    """Merge `incoming` into `row` in place; returns what changed."""
    changes = merge_values(SESSION_PRECEDENCE, session_values(row), incoming, incoming_source)
    for field, value in changes.items():
        setattr(row, field, value)
    return changes


def refresh_own_row(row: EVChargingSession, derived: dict) -> None:
    """The same source reporting its own charge again: measured columns refresh, curated ones only fill."""
    for field, value in derived.items():
        rule = SESSION_PRECEDENCE.get(field)
        if rule is not None and rule.winner == KEEP and getattr(row, field) is not None:
            continue
        setattr(row, field, value)


async def _measured_position(
    db: AsyncSession, obs: SessionObservation
) -> tuple[float | None, float | None]:
    """The session's coordinates; 0,0 is a broken reading, so use the nearest stored fix."""
    lat, lon = obs.values.get("latitude"), obs.values.get("longitude")
    if lat is None or lon is None:
        return None, None
    if (float(lat), float(lon)) != (0.0, 0.0):
        return lat, lon
    return await nearest_gps(db, obs.device_id, obs.values["session_start_utc"]) or (None, None)


async def _location_fields(
    db: AsyncSession, obs: SessionObservation, lat: float | None, lon: float | None
) -> dict[str, Any]:
    """Place columns from the resolved location; the source's own name is only a fallback."""
    location_id = await resolve_location(
        db,
        latitude=lat,
        longitude=lon,
        location_name=obs.location_name,
        source_system=obs.source_system,
    )
    defaults = await get_location_defaults(db, location_id)
    return {
        "location_id": location_id,
        "location_name": defaults.location_name or obs.location_name,
        "location_type": defaults.location_type,
        "network_id": defaults.network_id,
        "charge_type": obs.values.get("charge_type") or defaults.charge_type,
        "stall_id": defaults.stall_id,
        "evse_voltage": defaults.evse_voltage,
        "evse_amperage": defaults.evse_amperage,
        "charger_rated_kw": defaults.charger_rated_kw,
        "evse_source": "stall_default" if defaults.stall_id else None,
    }


async def upsert_session(db: AsyncSession, obs: SessionObservation) -> EVChargingSession | None:
    """Store one charge; returns its row, or None when a protected session already records it."""
    source = obs.source_system
    start = obs.values["session_start_utc"]
    lat, lon = await _measured_position(db, obs)
    values = {**obs.values, "latitude": lat, "longitude": lon}

    existing = await db.scalar(
        select(EVChargingSession).where(
            EVChargingSession.device_id == obs.device_id,
            EVChargingSession.source_system == source,
            EVChargingSession.session_start_utc == start,
        )
    )
    target = existing
    if target is None:
        others = [
            m
            for m in await find_matching_sessions(db, obs.device_id, start, values.get("energy_kwh"))
            if m[1] != source
        ]
        other_id = next((mid for mid, other in others if other in INGESTED), None)
        if other_id is not None:
            target = await db.get(EVChargingSession, other_id)
        elif others:
            logger.info("%s session matches protected session %s; not written", source, others[0][0])
            return None

    # Resolve a place only when the row does not already have one, so a merge
    # or re-parse never creates a stray unverified location.
    if target is None or target.location_id is None:
        values.update(await _location_fields(db, obs, lat, lon))

    if existing is not None:
        refresh_own_row(existing, values)
        return existing
    if target is not None:
        apply_session_merge(target, {**values, "source_system": source}, source)
        return target

    session = EVChargingSession(
        device_id=obs.device_id,
        source_system=source,
        is_complete=True,
        recorded_at=obs.recorded_at,
        original_timestamp=start,
        ingest_schema_version=INGEST_SCHEMA_VERSION.get(source),
        **values,
    )
    db.add(session)
    await db.flush()
    logger.info(
        "Stored %s charging session: %s kWh, %s -> %s%%",
        source, values.get("energy_kwh"), values.get("start_soc"), values.get("end_soc"),
    )
    return session
