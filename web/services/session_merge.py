"""One charging-session row per charge when Home Assistant and FordConnect both record it.

HA's energy-transfer log entry is Ford's own record of the whole charge, so
it wins everything it measures. FordConnect adds what only per-minute
telemetry sees. Columns a user curates, or that come from an approved
location, are fill-only: a merge never overwrites them.
"""

from __future__ import annotations

import logging
from typing import Any

from db.models.charging_session import EVChargingSession
from web.services.source_merge import (
    Rule,
    is_km_mile_twin,
    merge_values,
)
from web.services.source_system import INGESTED, SourceSystem

logger = logging.getLogger("lightningrod.session_merge")

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
    """Re-derive an unmerged FordConnect row: measured columns refresh, curated ones only fill."""
    for field, value in derived.items():
        rule = SESSION_PRECEDENCE.get(field)
        if rule is not None and rule.winner == KEEP and getattr(row, field) is not None:
            continue
        setattr(row, field, value)
