"""One charging-session row per charge when Home Assistant and FordConnect both record it.

HA's energy-transfer log entry is Ford's own record of the whole charge, so
it wins everything it measures. FordConnect adds what only per-minute
telemetry sees. Columns a user curates, or that come from an approved
location, are fill-only: a merge never overwrites them.
"""

from __future__ import annotations

from typing import Any

from db.models.charging_session import EVChargingSession
from web.services.source_merge import FCON, HA, MERGEABLE_SOURCES, Rule, merge_values

# Winner name for columns neither source may overwrite once set.
KEEP = "keep"
_CURATED = "curated or from the approved location; fill-only"

SESSION_PRECEDENCE: dict[str, Rule] = {
    "session_start_utc": Rule(HA, "Ford's log timestamps; FCON's are per-minute samples"),
    "session_end_utc": Rule(HA, "Ford's log timestamps; FCON's are per-minute samples"),
    "charge_duration_seconds": Rule(HA, "measured by the vehicle for the whole charge"),
    "plugged_in_duration_seconds": Rule(HA, "measured plug time; FCON starts at first charging sample"),
    "energy_kwh": Rule(HA, "energy the vehicle logged; FCON's is a pack-estimate delta"),
    "start_soc": Rule(HA, "log's first/last SoC"),
    "end_soc": Rule(HA, "log's first/last SoC"),
    "max_power": Rule(HA, "log covers every second; FCON samples once a minute"),
    "min_power": Rule(HA, "log covers every second; FCON samples once a minute"),
    "charging_kw": Rule(HA, "log's weighted average; FCON's is the last live sample"),
    "distance_added": Rule(HA, "log's range added; FCON's is a range-estimate delta"),
    "charge_type": Rule(HA, "the log names the charger type directly"),
    "address": Rule(HA, "only the log carries a street address"),
    "latitude": Rule(HA, "the log's location is where Ford recorded the charge"),
    "longitude": Rule(HA, "the log's location is where Ford recorded the charge"),
    "plug_status": Rule(FCON, "live plug state at the unplug sample"),
    "charging_status": Rule(FCON, "live charge display state"),
    "station_status": Rule(FCON, "only telemetry reports the station link"),
    "charging_voltage": Rule(FCON, "only telemetry reports charger volts"),
    "charging_amperage": Rule(FCON, "only telemetry reports charger amps"),
    "estimated_end_utc": Rule(FCON, "only telemetry reports time-to-full"),
    "battery_temp_start": Rule(FCON, "read at the start; HA reuses one cached value"),
    "battery_temp_end": Rule(FCON, "read at the end; HA reuses one cached value"),
    "ambient_temp_start": Rule(FCON, "read at the start; HA reuses one cached value"),
    "ambient_temp_end": Rule(FCON, "read at the end; HA reuses one cached value"),
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
    "source_system": Rule(HA, "one label whichever source arrived first"),
}


def is_mergeable(row: EVChargingSession) -> bool:
    return row.source_system in MERGEABLE_SOURCES


def session_values(row: EVChargingSession) -> dict[str, Any]:
    return {f: getattr(row, f) for f in SESSION_PRECEDENCE}


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
