"""ha-fordpass source adapter.

Maps home-assistant-fordpass entity attributes to DB columns via DATA_MAP,
routing every conversion through web.services.units.to_metric.

Quirks of the upstream integration that this adapter exists to absorb:

- The metrics and events entities ship with
  `entity_registry_enabled_default=False`, so a fresh install ingests nothing
  from them until the user enables them.
- Every attribute on the metrics entity is `{"updateTime": ..., "value": x}`.
  Read them through `_metric_value` — a raw `attrs.get` yields the wrapper
  dict, which every numeric conversion silently turns into None.
- ha-fordpass builds the elveh trip attributes with `localize_distance` /
  `localize_temperature`, so they arrive in HA's unit system, not raw metric.
  The elveh STATE's `unit_of_measurement` tracks the *vehicle's* display
  system rather than HA's, so it must never resolve those attributes' units —
  that double-converted distances for imperial-display vehicles on metric HA.
- The outsidetemp entity's `ambientTemp` attribute is NOT the outside
  temperature: it mirrors a Ford metric that sat frozen for weeks on a live
  vehicle. Only the entity state is trustworthy.
- `energytransferlogentry.plugDetails.totalDistanceAdded` is the only source
  of charging distance_added; no metrics or events attribute carries it.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from web.services.units import detection
from web.services.units.data_map import DataMapping, SourceKind, SourceRef
from web.services.units.to_metric import UnknownSourceUnit, to_metric

logger = logging.getLogger("lightningrod.sources.ha_fordpass")


# ---------------------------------------------------------------------------
# Trip-id namespace for cross-source dedup
# ---------------------------------------------------------------------------
# DO NOT CHANGE — invalidating this UUID invalidates every existing
# deterministic trip_id and breaks the dedup invariant. Mirrored verbatim
# in db/migrations/versions/p34_battery_trips_overhaul.py.
LIGHTNINGROD_TRIP_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-4a5b-9c8d-7e6f5a4b3c20")


def compute_trip_id(device_id: str | None, trip_update_time: str | None) -> uuid.UUID | None:
    """Deterministic trip_id for cross-source dedup.

    Returns None when either input is missing; callers fall back to the
    `_find_matching_trip` predicate match in that case.
    """
    if not device_id or not trip_update_time:
        return None
    canonical = f"{device_id}|{trip_update_time}"
    return uuid.uuid5(LIGHTNINGROD_TRIP_NAMESPACE, canonical)

# Separate from the module logger so per-field conversion traces can be
# switched on alone: getLogger("lightningrod.units.trace").setLevel(INFO).
_trace = logging.getLogger("lightningrod.units.trace")


# ---------------------------------------------------------------------------
# DATA_MAP registry
# ---------------------------------------------------------------------------
# Every unit-ful DB column handled by this adapter appears here;
# tests/test_unit/test_data_map_coverage.py locks that invariant.
# `source_unit` strings must be ones web.services.units.to_metric recognizes.

DATA_MAP: list[DataMapping] = [
    # --- ev_battery_status (from sensor.{vin}_metrics) -------------------
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_metrics", SourceKind.HA_ENTITY),
        source_field="xevBatteryRange",
        source_unit="km",
        db_table="ev_battery_status",
        db_column="hv_battery_range",
        db_unit="km",
        notes="Canonical metric source; replaces elveh state reading",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_metrics", SourceKind.HA_ENTITY),
        source_field="xevBatteryMaximumRange",
        source_unit="km",
        db_table="ev_battery_status",
        db_column="hv_battery_max_range",
        db_unit="km",
        notes="Canonical metric source; replaces elveh.maximumBatteryRange",
    ),
    # ha-fordpass installs disagree on pack-capacity scale — some report raw Wh
    # (131000), others already-kWh (141.2). A blind Wh/1000 corrupted the
    # kWh-reporting installs, so wh_autoscale disambiguates by magnitude.
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_metrics", SourceKind.HA_ENTITY),
        source_field="xevBatteryCapacity",
        source_unit="Wh",
        wh_autoscale=True,
        db_table="ev_battery_status",
        db_column="hv_battery_capacity",
        db_unit="kWh",
        notes="Mixed Wh/kWh across ha-fordpass versions; magnitude-autoscaled to kWh",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="maximumBatteryCapacity",
        source_unit="Wh",
        wh_autoscale=True,
        db_table="ev_battery_status",
        db_column="hv_battery_capacity",
        db_unit="kWh",
        notes="Elveh mirror of xevBatteryCapacity; same mixed Wh/kWh autoscale",
    ),

    # --- ev_battery_status: hv_battery_temperature --------------------------
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elvehcharging", SourceKind.HA_ENTITY),
        source_field="batteryTemperature",
        source_unit="degC",
        ha_unit_system_converted=True,
        db_table="ev_battery_status",
        db_column="hv_battery_temperature",
        db_unit="degC",
        notes="Live charging battery temp; ha-fordpass localizes per unit_system",
    ),

    # --- ev_trip_metrics (from sensor.{vin}_events xev-key-off-trip-segment-data) ---
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_events", SourceKind.HA_ENTITY),
        source_field="xev-key-off-trip-segment-data.distance_traveled",
        source_unit="km",
        db_table="ev_trip_metrics",
        db_column="distance",
        db_unit="km",
        notes="Canonical source; replaces elveh.tripDistanceTraveled",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_events", SourceKind.HA_ENTITY),
        source_field="xev-key-off-trip-segment-data.energy_consumed",
        source_unit="Wh",
        db_table="ev_trip_metrics",
        db_column="energy_consumed",
        db_unit="kWh",
        notes="Canonical source; Wh -> kWh via to_metric",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_events", SourceKind.HA_ENTITY),
        source_field="xev-key-off-trip-segment-data.ambient_temperature",
        source_unit="degC",
        db_table="ev_trip_metrics",
        db_column="ambient_temp",
        db_unit="degC",
        notes="Canonical metric source; Ford API key is ambient_temperature (raw °C)",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_events", SourceKind.HA_ENTITY),
        source_field="xev-key-off-trip-segment-data.cabin_temperature",
        source_unit="degC",
        db_table="ev_trip_metrics",
        db_column="cabin_temp",
        db_unit="degC",
        notes="Canonical metric source; Ford API key is cabin_temperature (raw °C)",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_events", SourceKind.HA_ENTITY),
        source_field="xev-key-off-trip-segment-data.outside_air_ambient_temperature",
        source_unit="degC",
        db_table="ev_trip_metrics",
        db_column="outside_air_temp",
        db_unit="degC",
        notes="Canonical metric source; Ford API key is outside_air_ambient_temperature (raw °C)",
    ),
    # Elveh trip attributes: never resolve these from the elveh state's uom.
    # It tracks the vehicle display system, which is where the duplicate-trip
    # double-conversion bug came from (imperial vehicle on metric HA).
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="tripDistanceTraveled",
        source_unit="km",
        ha_unit_system_converted=True,
        db_table="ev_trip_metrics",
        db_column="distance",
        db_unit="km",
        notes="Legacy-fallback trip distance; ha-fordpass localizes per HA unit_system",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="tripEfficiency",
        source_unit="km",
        ha_unit_system_converted=True,
        db_table="ev_trip_metrics",
        db_column="efficiency",
        db_unit="km",
        notes=(
            "tripDistanceTraveled / tripEnergyConsumed as computed by "
            "ha-fordpass, so km/kWh on metric HA and mi/kWh on imperial HA; "
            "the km<->mi factor converts it to canonical km/kWh."
        ),
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="tripRangeRegenerated",
        source_unit="km",
        ha_unit_system_converted=True,
        db_table="ev_trip_metrics",
        db_column="range_regenerated",
        db_unit="km",
        notes="From metrics tripXevBatteryRangeRegenerated via localize_distance",
    ),
    # Primary regen source; the elveh mapping above is the localized fallback
    # for installs whose metrics entity lacks the attribute.
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_metrics", SourceKind.HA_ENTITY),
        source_field="tripXevBatteryRangeRegenerated",
        source_unit="km",
        db_table="ev_trip_metrics",
        db_column="range_regenerated",
        db_unit="km",
        notes="Raw API passthrough (km); backfills the newest trip row",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="maximumBatteryRange",
        source_unit="km",
        ha_unit_system_converted=True,
        db_table="ev_battery_status",
        db_column="hv_battery_max_range",
        db_unit="km",
        notes="Elveh fallback for metrics xevBatteryMaximumRange; localize_distance",
    ),
    # The elveh entity's primary state is a range, so its unit_of_measurement
    # is a distance unit and is useless for these temperature attributes.
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="tripCabinTemp",
        source_unit="degC",
        ha_unit_system_converted=True,
        db_table="ev_trip_metrics",
        db_column="cabin_temp",
        db_unit="degC",
        notes="elveh temp attr: HA localizes per unit_system (imperial->degF, metric->degC)",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="tripAmbientTemp",
        source_unit="degC",
        ha_unit_system_converted=True,
        db_table="ev_trip_metrics",
        db_column="ambient_temp",
        db_unit="degC",
        notes="elveh temp attr: HA localizes per unit_system",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elveh", SourceKind.HA_ENTITY),
        source_field="tripOutsideAirAmbientTemp",
        source_unit="degC",
        ha_unit_system_converted=True,
        db_table="ev_trip_metrics",
        db_column="outside_air_temp",
        db_unit="degC",
        notes="elveh temp attr: HA localizes per unit_system",
    ),

    # --- ev_charging_session (from sensor.{vin}_energytransferlogentry) ----------
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_energytransferlogentry", SourceKind.HA_ENTITY),
        source_field="plugDetails.totalDistanceAdded",
        source_unit="km",
        db_table="ev_charging_session",
        db_column="distance_added",
        db_unit="km",
        ha_unit_system_converted=True,
        notes=(
            "HA-converted per ha-fordpass source "
            "(fordpass_handler.py:get_energy_transfer_log_attrs calls "
            "localize_distance on this field). Imperial HA -> miles, metric HA "
            "-> km. The adapter resolves the source unit per-event from "
            "ha_config.unit_system; to_metric handles the mi->km conversion "
            "back to canonical."
        ),
    ),
    # Cached from the live elvehcharging reading and applied when the
    # energytransferlogentry session record is written.
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elvehcharging", SourceKind.HA_ENTITY),
        source_field="batteryTemperature",
        source_unit="degC",
        ha_unit_system_converted=True,
        db_table="ev_charging_session",
        db_column="battery_temp_start",
        db_unit="degC",
        notes="Cached from elvehcharging; applied at session-write time",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_elvehcharging", SourceKind.HA_ENTITY),
        source_field="batteryTemperature",
        source_unit="degC",
        ha_unit_system_converted=True,
        db_table="ev_charging_session",
        db_column="battery_temp_end",
        db_unit="degC",
        notes="Cached from elvehcharging; mirrored to start/end (single snapshot)",
    ),
    # The entity state, never its stale `ambientTemp` attribute.
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_outsidetemp", SourceKind.HA_ENTITY),
        source_field="state",
        source_unit="degC",
        read_time_uom=True,
        db_table="ev_charging_session",
        db_column="ambient_temp_start",
        db_unit="degC",
        notes="Cached from the outsidetemp state; applied at session-write time",
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_outsidetemp", SourceKind.HA_ENTITY),
        source_field="state",
        source_unit="degC",
        read_time_uom=True,
        db_table="ev_charging_session",
        db_column="ambient_temp_end",
        db_unit="degC",
        notes="Cached from the outsidetemp state; mirrored to start/end",
    ),
    # --- ev_vehicle_status (from the per-slug temperature sensors) ----------
    # The rest of vehicle telemetry (odometer, speed, yaw, acceleration) has no
    # mapping: it arrives on per-slug entities that handlers write directly.
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_outsidetemp", SourceKind.HA_ENTITY),
        source_field="state",
        source_unit="degC",
        read_time_uom=True,
        db_table="ev_vehicle_status",
        db_column="outside_temperature",
        db_unit="degC",
        notes=(
            "Time-series ambient temp from the outsidetemp entity state. "
            "Shares its source with ev_charging_session.ambient_temp_start/end "
            "(different cache discipline — vehicle_status writes per snapshot)."
        ),
    ),
    DataMapping(
        source=SourceRef("sensor.fordpass_{vin}_cabintemperature", SourceKind.HA_ENTITY),
        source_field="state",
        source_unit="degC",
        read_time_uom=True,
        db_table="ev_vehicle_status",
        db_column="cabin_temperature",
        db_unit="degC",
        notes=(
            "Time-series cabin temp from the cabintemperature entity state; "
            "the entity carries no cabinTemperature attribute."
        ),
    ),
]


# ---------------------------------------------------------------------------
# _last_seen_raw cache
# ---------------------------------------------------------------------------
# Keyed by f"{source.pattern}|{source_field}" — /admin/data-sources rebuilds
# the same key, so the two must stay in step.

_last_seen_raw: dict[str, dict[str, Any]] = {}
_last_charging_battery_temp: dict[str, float | None] = {}
_last_outsidetemp: dict[str, float | None] = {}


def _record_last_seen(
    mapping: DataMapping,
    raw_value: Any,
    converted: Any,
    effective_unit: str | None = None,
    method: str | None = None,
) -> None:
    """Record the last-seen raw value for diagnostic display.

    `effective_unit` overrides `mapping.source_unit` when per-event resolution
    produced a different one; `method` names which path resolved it.
    """
    key = f"{mapping.source.pattern}|{mapping.source_field}"
    _last_seen_raw[key] = {
        "value": raw_value,
        "unit": effective_unit or mapping.source_unit,
        "declared_unit": mapping.source_unit,
        "method": method or "declared",
        "seen_at": datetime.now(UTC).isoformat(),
        "converted": converted,
    }


# ---------------------------------------------------------------------------
# Lookup + conversion helpers (consumed by ha_fordpass.handlers slug handlers)
# ---------------------------------------------------------------------------

INGEST_SCHEMA_VERSION = 2  # mark every new adapter-driven row

_ENTITY_SUFFIX_PREFIX = "sensor.fordpass_"


def _entity_suffix(entity_id: str | None) -> str | None:
    """Return the trailing slug of a fordpass entity_id, or None.

    sensor.fordpass_ABC123_elveh -> "elveh". Splits from the right because
    VINs can contain underscores; fordpass slugs never do.
    """
    if not entity_id or not isinstance(entity_id, str):
        return None
    if not entity_id.startswith(_ENTITY_SUFFIX_PREFIX):
        return None
    remainder = entity_id[len(_ENTITY_SUFFIX_PREFIX):]
    parts = remainder.rsplit("_", 1)
    if len(parts) < 2:
        return None
    return parts[1]


def _entity_pattern(entity_id: str) -> str:
    """Convert a concrete entity_id into its DATA_MAP pattern form.

    sensor.fordpass_ABC123_metrics -> sensor.fordpass_{vin}_metrics
    """
    suffix = _entity_suffix(entity_id)
    if suffix is None:
        return entity_id
    return f"sensor.fordpass_{{vin}}_{suffix}"


def lookup_mapping(
    entity_pattern: str, source_field: str
) -> DataMapping | None:
    """Look up the DATA_MAP entry for a given (pattern, attribute).

    Returns the FIRST matching mapping; callers needing all matches (e.g.
    energytransferlogentry.batteryTemperature maps to both start + end)
    should iterate DATA_MAP directly.
    """
    for mapping in DATA_MAP:
        if (
            mapping.source.pattern == entity_pattern
            and mapping.source_field == source_field
        ):
            return mapping
    return None


def _field_type_for_mapping(mapping: DataMapping) -> str | None:
    """Classify a mapping's unit as distance/speed/temperature, else None."""
    # Same semantics as web.services.units.detection._UNIT_FIELD_TYPES,
    # duplicated locally to avoid widening the import graph.
    unit = mapping.db_unit or mapping.source_unit
    if unit in ("km", "mi"):
        return "distance"
    if unit in ("kmh", "mph"):
        return "speed"
    if unit in ("degC", "degF", "F"):
        return "temperature"
    return None


def _unit_for_ha_system(
    field_type: str, ha_config: dict | None
) -> str | None:
    """Return the source-unit string HA would emit for `field_type`.

    None when `ha_config.unit_system` cannot be resolved.
    """
    # Delegate to detection._coerce_unit_system_family so the two resolvers
    # stay in sync and both accept the same unit_system shapes.
    from web.services.units.detection import _coerce_unit_system_family

    if not isinstance(ha_config, dict):
        return None
    unit_system = ha_config.get("unit_system")
    family = _coerce_unit_system_family(unit_system, field_type)
    if family is None:
        return None
    if field_type == "distance":
        return "km" if family == "metric" else "mi"
    if field_type == "temperature":
        return "degC" if family == "metric" else "degF"
    if field_type == "speed":
        return "kmh" if family == "metric" else "mph"
    return None


def _resolve_source_unit(
    mapping: DataMapping,
    new_state: dict | None,
    ha_config: dict | None = None,
) -> tuple[str | None, str]:
    """Resolve the effective source unit for one mapping + event.

    Returns (unit, method); /admin/data-sources displays `method`, which is
    one of declared, read_time_uom, ha_unit_system_converted,
    declared_fallback, or unresolved_uom. On unresolved_uom the unit is None
    and the caller must drop the field — never guess a unit for an event that
    did not stamp one.
    """
    # --- 1. Read-time unit_of_measurement path (state-sourced fields) ---
    if mapping.read_time_uom:
        attrs = (new_state or {}).get("attributes") or {}
        resolved = _normalize_uom_string(attrs.get("unit_of_measurement"))
        if resolved:
            return resolved, "read_time_uom"
        logger.warning(
            "read_time_uom mapping %s.%s received no usable "
            "unit_of_measurement; dropping the field for this event",
            mapping.source.pattern,
            mapping.source_field,
        )
        return None, "unresolved_uom"

    # --- 2. HA-unit-system conversion path ---
    if mapping.ha_unit_system_converted:
        field_type = _field_type_for_mapping(mapping)
        if field_type is not None:
            resolved = _unit_for_ha_system(field_type, ha_config)
            if resolved:
                return resolved, "ha_unit_system_converted"
        # Signal requested but we couldn't resolve ha_config.unit_system.
        # Fall back to declared so we at least produce a value; the
        # detection layer will flag this as degraded via unknown_reason.
        logger.warning(
            "ha_unit_system_converted mapping %s.%s received no usable "
            "ha_config.unit_system; falling back to declared %r",
            mapping.source.pattern,
            mapping.source_field,
            mapping.source_unit,
        )
        return mapping.source_unit, "declared_fallback"

    # --- 3. Declared (mapping literal) ---
    return mapping.source_unit, "declared"


def _normalize_uom_string(raw: Any) -> str | None:
    """Normalize an HA `unit_of_measurement` string to a to_metric key.

    None for spellings to_metric would not recognize, so callers can fall back.
    """
    if not isinstance(raw, str):
        return None
    u = raw.strip()
    lowered = u.lower()
    if lowered in ("mi", "mile", "miles"):
        return "mi"
    if lowered in ("km", "kilometer", "kilometers"):
        return "km"
    if lowered in ("mph", "mi/h"):
        return "mph"
    if lowered in ("kmh", "km/h"):
        return "kmh"
    if u in ("°C", "degC") or lowered in ("c", "°c"):
        return "degC"
    if u in ("°F", "degF") or lowered in ("f", "°f"):
        return "degF"
    if u == "kWh" or lowered == "kwh":
        return "kWh"
    if u == "Wh" or lowered == "wh":
        return "Wh"
    if lowered in ("s", "sec", "seconds"):
        return "s"
    return None


def convert(
    mapping: DataMapping,
    raw_value: Any,
    new_state: dict | None = None,
    ha_config: dict | None = None,
) -> float | None:
    """Convert `raw_value` to metric via `mapping` + per-event resolution.

    Returns None on an unknown source unit so the adapter boundary absorbs
    unit failures instead of propagating them to the caller.
    """
    if raw_value is None:
        return None
    source_unit, method = _resolve_source_unit(mapping, new_state, ha_config)
    if source_unit is None:
        # The event carried no readable unit; the adapter never guesses.
        return None
    if mapping.wh_autoscale and source_unit == "Wh":
        # Real packs are ~10-300 kWh, so |value| < 1000 can only be kWh already.
        numeric = _safe_float(raw_value)
        if numeric is not None and abs(numeric) < 1000:
            source_unit, method = "kWh", "wh_autoscale"
    try:
        converted = to_metric(raw_value, source_unit)
    except UnknownSourceUnit as exc:
        logger.warning(
            "UnknownSourceUnit on %s.%s: value=%r source_unit=%r method=%s (%s); "
            "skipping field, continuing event",
            mapping.source.pattern,
            mapping.source_field,
            raw_value,
            source_unit,
            method,
            exc,
        )
        return None
    _record_last_seen(
        mapping, raw_value, converted, effective_unit=source_unit, method=method
    )
    detection.record_declared(
        mapping.source.pattern,
        mapping.source_field,
        source_unit,
        raw_value,
    )
    if _trace.isEnabledFor(logging.INFO):
        _trace.info(
            "convert %s.%s | raw=%r | method=%s | source_unit=%s -> db_unit=%s | "
            "converted=%r | -> %s.%s",
            mapping.source.pattern,
            mapping.source_field,
            raw_value,
            method,
            source_unit,
            mapping.db_unit,
            converted,
            mapping.db_table,
            mapping.db_column,
        )
    return converted


# ---------------------------------------------------------------------------
# Safe-float helper (kept here for parity with the ingestion._helpers copy)
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _metric_value(attrs: dict, key: str) -> Any:
    """Unwrap one `{"updateTime": ..., "value": x}` metrics attribute.

    Unwraps exactly one level, so a metric whose `value` is itself structured
    (Ford sends a few) comes back as that dict rather than a number. Bare
    scalars pass through unchanged.
    """
    raw = attrs.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


# ---------------------------------------------------------------------------
# Device-id extraction
# ---------------------------------------------------------------------------

def _device_id_from_entity(entity_id: str) -> str | None:
    """Extract the VIN segment from sensor.fordpass_{vin}_{slug}.

    Splits from the right so VINs containing underscores survive intact.
    """
    if not entity_id or not entity_id.startswith(_ENTITY_SUFFIX_PREFIX):
        return None
    remainder = entity_id[len(_ENTITY_SUFFIX_PREFIX):]
    parts = remainder.rsplit("_", 1)
    if not parts or not parts[0]:
        return None
    return parts[0]


# ---------------------------------------------------------------------------
# process_sample — main entry point (SourceAdapter Protocol)
# ---------------------------------------------------------------------------

async def process_sample(
    source_ref: str,
    payload: dict,
    db: AsyncSession,
    source_config: dict | None = None,
) -> None:
    """Route an HA state_changed event to the appropriate write path.

    Never raises: the caller is the HA WebSocket event loop. `source_config`
    is HA's own config, forwarded so per-event unit resolution can read its
    unit_system.
    """
    # This source's reading of the transport-neutral SourceAdapter Protocol:
    # the source_ref is an HA entity_id and the payload is the new_state.
    entity_id, new_state, ha_config = source_ref, payload, source_config

    if not entity_id or not isinstance(new_state, dict):
        return

    suffix = _entity_suffix(entity_id)
    if suffix is None:
        return

    device_id = _device_id_from_entity(entity_id) or "unknown"

    try:
        if suffix == "metrics":
            await _handle_metrics_entity(entity_id, new_state, device_id, db, ha_config)
        elif suffix == "events":
            await _handle_events_entity(entity_id, new_state, device_id, db, ha_config)
        elif suffix == "energytransferlogentry":
            await _handle_energy_transfer_entity(
                entity_id, new_state, device_id, db, ha_config
            )
        elif suffix == "elvehcharging":
            await _handle_elvehcharging_entity(entity_id, new_state, device_id, db, ha_config)
        elif suffix == "outsidetemp":
            await _handle_outsidetemp_entity(entity_id, new_state, device_id, db, ha_config)
        else:
            # Not an adapter-owned entity. Silent return — the ha_fordpass
            # slug handlers cover per-slug routing for vehicle status, GPS, etc.
            return
    except UnknownSourceUnit as exc:
        # convert() already absorbs this; belt-and-braces in case a caller
        # bypasses convert() and calls to_metric directly.
        logger.warning(
            "UnknownSourceUnit escaped convert() for %s: %s", entity_id, exc
        )
    except Exception:
        # The adapter must never raise into the WebSocket loop.
        logger.exception("ha_fordpass.process_sample failed for %s", entity_id)


# ---------------------------------------------------------------------------
# Per-entity handlers
# ---------------------------------------------------------------------------

async def _handle_metrics_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: dict | None = None,
) -> None:
    """sensor.fordpass_{vin}_metrics -> ev_battery_status row.

    Writes a row with hv_battery_range + hv_battery_max_range (+ any other
    battery fields available on the metrics entity) and
    ingest_schema_version = INGEST_SCHEMA_VERSION.
    """
    from db.models.battery_status import EVBatteryStatus

    attrs = new_state.get("attributes") or {}
    pattern = _entity_pattern(entity_id)

    range_mapping = lookup_mapping(pattern, "xevBatteryRange")
    max_range_mapping = lookup_mapping(pattern, "xevBatteryMaximumRange")

    raw_range = _metric_value(attrs, "xevBatteryRange")
    raw_max_range = _metric_value(attrs, "xevBatteryMaximumRange")
    hv_range = (
        convert(range_mapping, raw_range, new_state, ha_config)
        if range_mapping
        else None
    )
    hv_max_range = (
        convert(max_range_mapping, raw_max_range, new_state, ha_config)
        if max_range_mapping
        else None
    )

    # Cross-reference: metrics is canonical (km). If any target entity has
    # recent reads for the paired attributes, let the detection layer derive
    # the target unit from the observed ratio.
    if hv_range is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_metrics",
            "xevBatteryRange",
            hv_range,
            "km",
        )
    if hv_max_range is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_metrics",
            "xevBatteryMaximumRange",
            hv_max_range,
            "km",
        )

    # SI-already scalar attributes pass through without conversion
    hv_soc = _safe_float(_metric_value(attrs, "xevBatteryStateOfCharge"))
    hv_actual_soc = _safe_float(_metric_value(attrs, "xevBatteryActualStateOfCharge"))
    cap_mapping = lookup_mapping(pattern, "xevBatteryCapacity")
    hv_capacity = (
        convert(cap_mapping, _metric_value(attrs, "xevBatteryCapacity"), new_state, ha_config)
        if cap_mapping
        else None
    )
    hv_voltage = _safe_float(_metric_value(attrs, "xevBatteryVoltage"))
    hv_amperage = _safe_float(_metric_value(attrs, "xevBatteryIoCurrent"))
    # Ford publishes no pack-power metric. ha-fordpass derives its elveh
    # batterykW attribute from voltage x current, so deriving it the same way
    # here keeps the two ingestion paths numerically identical.
    hv_kw = (
        round(hv_voltage * hv_amperage / 1000.0, 2)
        if hv_voltage is not None and hv_amperage is not None
        else None
    )

    recorded_at = _parse_event_ts(new_state) or datetime.now(UTC)

    values = {
        "hv_battery_range": hv_range,
        "hv_battery_max_range": hv_max_range,
        "hv_battery_soc": hv_soc,
        "hv_battery_actual_soc": hv_actual_soc,
        "hv_battery_capacity": hv_capacity,
        "hv_battery_voltage": hv_voltage,
        "hv_battery_amperage": hv_amperage,
        "hv_battery_kw": hv_kw,
    }
    # A row whose every value column is NULL carries no telemetry; every
    # consumer filters those out anyway, so skip the insert.
    if any(v is not None for v in values.values()):
        db.add(EVBatteryStatus(
            device_id=device_id,
            recorded_at=recorded_at,
            source_system="ha_fordpass",
            original_timestamp=recorded_at,
            ingest_schema_version=INGEST_SCHEMA_VERSION,
            **values,
        ))
        logger.debug(
            "ha_fordpass: wrote ev_battery_status for %s range=%s max=%s",
            device_id,
            hv_range,
            hv_max_range,
        )
    else:
        logger.debug(
            "ha_fordpass: metrics event for %s carried no battery values; "
            "no ev_battery_status row written",
            device_id,
        )

    await _backfill_trip_regen(entity_id, attrs, device_id, db, recorded_at, ha_config)


async def _backfill_trip_regen(
    entity_id: str,
    attrs: dict,
    device_id: str,
    db: AsyncSession,
    recorded_at: datetime,
    ha_config: dict | None,
) -> None:
    """NULL-fill regen + driving score on the newest trip row from metrics attrs.

    tripXevBatteryRangeRegenerated (raw km) and tripXevBatteryChargeRegenerated
    (a regen-vs-brake driving score %) live on the metrics entity, which is
    always enabled — unlike the elveh fallback, whose trip attributes many
    installs never emit. Metrics refreshes every poll cycle, so a trip row
    created by the events handler gets its regen/score within one cycle.
    Only NULL fields are filled; elveh-sourced values are never overwritten.
    """
    raw_regen = _metric_value(attrs, "tripXevBatteryRangeRegenerated")
    raw_score = _safe_float(_metric_value(attrs, "tripXevBatteryChargeRegenerated"))
    # ha-fordpass emits 0 for missing telemetry; treat as unmeasured.
    if raw_score == 0:
        raw_score = None
    regen = None
    if raw_regen is not None:
        regen_mapping = lookup_mapping(
            _entity_pattern(entity_id), "tripXevBatteryRangeRegenerated"
        )
        if regen_mapping is not None:
            regen = convert(regen_mapping, raw_regen, None, ha_config)
    if regen == 0:
        regen = None
    if regen is None and raw_score is None:
        return

    from datetime import timedelta

    from sqlalchemy import select as _select

    from db.models.trip_metrics import EVTripMetrics

    trip = (await db.execute(
        _select(EVTripMetrics)
        .where(EVTripMetrics.device_id == device_id)
        .where(EVTripMetrics.end_time >= recorded_at - timedelta(hours=24))
        .order_by(EVTripMetrics.end_time.desc())
        .limit(1)
    )).scalar_one_or_none()
    if trip is None:
        return

    changed = False
    if regen is not None and trip.range_regenerated is None:
        trip.range_regenerated = regen
        changed = True
    if raw_score is not None and trip.driving_score is None:
        trip.driving_score = raw_score
        changed = True
    if changed:
        logger.debug(
            "ha_fordpass: backfilled trip %s regen=%s score=%s from metrics",
            trip.id,
            regen,
            raw_score,
        )


async def _handle_events_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: dict | None = None,
) -> None:
    """sensor.fordpass_{vin}_events -> ev_trip_metrics row.

    Reads xev-key-off-trip-segment-data attribute subkey. This is the
    canonical metric source for trip data; elveh.trip* attributes are not read.
    """
    from db.models.trip_metrics import EVTripMetrics

    attrs = new_state.get("attributes") or {}

    # Trip data is in attrs["customEvents"]["xev-key-off-trip-segment-data"]["oemData"]["trip_data"]["stringArrayValue"].
    # Each element of stringArrayValue is a JSON-encoded dict. Take the last valid one.
    # `updateTime` is a SIBLING key to `oemData` inside xev-key-off-trip-segment-data
    # and serves as the canonical input for the deterministic trip_id hash.
    trip: dict | None = None
    update_time_iso: str | None = None
    custom_events = attrs.get("customEvents") or {}
    if isinstance(custom_events, dict):
        xev = custom_events.get("xev-key-off-trip-segment-data") or {}
        if isinstance(xev, dict):
            update_time_iso = xev.get("updateTime")
            raw_array = (
                ((xev.get("oemData") or {}).get("trip_data") or {})
                .get("stringArrayValue") or []
            )
            for raw_item in reversed(raw_array):
                try:
                    parsed = json.loads(raw_item) if isinstance(raw_item, str) else raw_item
                    if isinstance(parsed, dict):
                        trip = parsed
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

    if trip is None:
        return

    pattern = _entity_pattern(entity_id)

    def _lookup(attr_suffix: str) -> DataMapping | None:
        return lookup_mapping(
            pattern, f"xev-key-off-trip-segment-data.{attr_suffix}"
        )

    distance_c = _lookup("distance_traveled")
    energy_c = _lookup("energy_consumed")
    ambient_c = _lookup("ambient_temperature")
    cabin_c = _lookup("cabin_temperature")
    outside_c = _lookup("outside_air_ambient_temperature")

    distance = convert(distance_c, trip.get("distance_traveled"), new_state, ha_config) if distance_c else None
    energy = convert(energy_c, trip.get("energy_consumed"), new_state, ha_config) if energy_c else None
    ambient = convert(ambient_c, trip.get("ambient_temperature"), new_state, ha_config) if ambient_c else None
    cabin = convert(cabin_c, trip.get("cabin_temperature"), new_state, ha_config) if cabin_c else None
    outside_air = convert(outside_c, trip.get("outside_air_ambient_temperature"), new_state, ha_config) if outside_c else None

    # Cross-reference: events trip fields are always metric. Let the detection
    # layer use them as the canonical reference for paired elveh attributes.
    if distance is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_events",
            "xev-key-off-trip-segment-data.distance_traveled",
            distance,
            "km",
        )
    if ambient is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_events",
            "xev-key-off-trip-segment-data.ambient_temperature",
            ambient,
            "degC",
        )
    if cabin is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_events",
            "xev-key-off-trip-segment-data.cabin_temperature",
            cabin,
            "degC",
        )
    if outside_air is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_events",
            "xev-key-off-trip-segment-data.outside_air_ambient_temperature",
            outside_air,
            "degC",
        )

    # duration (s) is SI-passthrough; no mapping conversion needed
    duration = _safe_float(trip.get("trip_duration"))

    recorded_at = _parse_event_ts(new_state) or datetime.now(UTC)

    # Deterministic-id lookup first: if a row exists with the same uuid5(NS,
    # device_id|tripUpdateTime) the events payload is the second delivery for
    # the same physical trip — enrich rather than insert. This bypasses the
    # rounding-drift sensitivity of the legacy predicate match.
    deterministic_id = compute_trip_id(device_id, update_time_iso)
    if deterministic_id is not None:
        from sqlalchemy import select as _select

        existing = (await db.execute(
            _select(EVTripMetrics)
            .where(EVTripMetrics.trip_id == deterministic_id)
            .limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            if ambient is not None:
                existing.ambient_temp = ambient
            if cabin is not None:
                existing.cabin_temp = cabin
            if outside_air is not None:
                existing.outside_air_temp = outside_air
            _fill_trip_duration(existing, duration)
            if existing.odometer_end is None and existing.end_time is not None:
                existing.odometer_end = await _closest_odometer(db, device_id, existing.end_time)
            if existing.odometer_start is None and existing.start_time is not None:
                existing.odometer_start = await _closest_odometer(db, device_id, existing.start_time)
            logger.info(
                "ha_fordpass: enriched trip row %s for %s via deterministic id",
                existing.id,
                device_id,
            )
            return

    # Match-and-enrich fallback: legacy predicate match for trips whose source
    # didn't carry tripUpdateTime (no deterministic id available).
    from web.services.sources.ha_fordpass.handlers import _find_matching_trip

    existing = await _find_matching_trip(db, device_id, distance, energy, around=recorded_at)
    if existing is not None:
        # events values are canonical °C — always overwrite temps.
        if ambient is not None:
            existing.ambient_temp = ambient
        if cabin is not None:
            existing.cabin_temp = cabin
        if outside_air is not None:
            existing.outside_air_temp = outside_air
        _fill_trip_duration(existing, duration)
        logger.info(
            "ha_fordpass: enriched existing trip row %s for %s with events temps",
            existing.id,
            device_id,
        )
        return

    odometer_end = await _closest_odometer(db, device_id, recorded_at)

    start_time = None
    odometer_start = None
    if duration:
        from datetime import timedelta

        start_time = recorded_at - timedelta(seconds=duration)
        odometer_start = await _closest_odometer(db, device_id, start_time)

    record = EVTripMetrics(
        # Deterministic uuid5 when tripUpdateTime is available; uuid4 fallback
        # otherwise. The fallback rows can't be matched across sources but the
        # row-level UNIQUE(trip_id) constraint still guards against double
        # inserts of the same payload.
        trip_id=deterministic_id or uuid.uuid4(),
        device_id=device_id,
        start_time=start_time,
        end_time=recorded_at,
        recorded_at=recorded_at,
        distance=distance,
        duration=duration,
        energy_consumed=energy,
        ambient_temp=ambient,
        cabin_temp=cabin,
        outside_air_temp=outside_air,
        odometer_start=odometer_start,
        odometer_end=odometer_end,
        is_complete=True,
        source_system="ha_fordpass",
        original_timestamp=recorded_at,
        ingest_schema_version=INGEST_SCHEMA_VERSION,
    )
    db.add(record)
    logger.debug(
        "ha_fordpass: wrote ev_trip_metrics for %s distance=%s energy=%s",
        device_id,
        distance,
        energy,
    )


async def _handle_elvehcharging_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: dict | None = None,
) -> None:
    """sensor.fordpass_{vin}_elvehcharging -> cache battery temp + write EVBatteryStatus row."""
    from db.models.battery_status import EVBatteryStatus

    attrs = new_state.get("attributes") or {}
    pattern = _entity_pattern(entity_id)

    batt_c = lookup_mapping(pattern, "batteryTemperature")
    battery_temp = (
        convert(batt_c, attrs.get("batteryTemperature"), new_state, ha_config)
        if batt_c
        else None
    )
    _last_charging_battery_temp[device_id] = battery_temp

    if battery_temp is None:
        return

    record = EVBatteryStatus(
        device_id=device_id,
        recorded_at=_parse_event_ts(new_state) or datetime.now(UTC),
        source_system="ha_fordpass",
        hv_battery_temperature=battery_temp,
        ingest_schema_version=INGEST_SCHEMA_VERSION,
    )
    db.add(record)
    logger.debug(
        "ha_fordpass: wrote ev_battery_status (hv_battery_temperature=%.1f) for %s",
        battery_temp,
        device_id,
    )


async def _handle_outsidetemp_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: dict | None = None,
) -> None:
    """sensor.fordpass_{vin}_outsidetemp -> cache ambient temp (no DB write)."""
    pattern = _entity_pattern(entity_id)

    amb_c = lookup_mapping(pattern, "state")
    ambient_temp = (
        convert(amb_c, new_state.get("state"), new_state, ha_config)
        if amb_c
        else None
    )
    if ambient_temp is not None:
        _last_outsidetemp[device_id] = ambient_temp


async def _handle_energy_transfer_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: dict | None = None,
) -> None:
    """sensor.fordpass_{vin}_energytransferlogentry -> ev_charging_session row.

    Delegates to the slug handler so duplicate detection, location resolution
    and EVSE defaults have exactly one implementation.
    """
    from web.services.sources.ha_fordpass.handlers import handle_energy_transfer

    await handle_energy_transfer(
        "energytransferlogentry", new_state, ha_config or {}, device_id, db
    )


# ---------------------------------------------------------------------------
# Tiny helpers shared across handlers (no cross-module deps)
# ---------------------------------------------------------------------------

def _fill_trip_duration(existing: Any, duration: float | None) -> None:
    """NULL-fill duration (and a derivable start_time) on an enriched trip row.

    The events payload carries the canonical trip_duration in seconds; rows
    created first by the elveh fallback may lack it (older ha-fordpass emits
    tripDuration as an H:MM:SS string some installs failed to parse).
    """
    if duration is None or existing.duration is not None:
        return
    existing.duration = duration
    if existing.start_time is None and existing.end_time is not None:
        from datetime import timedelta

        existing.start_time = existing.end_time - timedelta(seconds=duration)


def _parse_event_ts(new_state: dict) -> datetime | None:
    for key in ("last_changed", "last_updated"):
        val = new_state.get(key)
        if not val:
            continue
        parsed = _parse_iso(val)
        if parsed is not None:
            return parsed
    return None


def _parse_iso(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if not isinstance(val, str):
        return None
    try:
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


async def _closest_odometer(
    db: AsyncSession, device_id: str, target_time: datetime | None
) -> float | None:
    """Closest ev_vehicle_status.odometer reading near `target_time`.

    Pulls a ±2h window of readings for the device and picks the one whose
    `recorded_at` is nearest to `target_time` by absolute timedelta. Dialect
    portable: no SQL `abs(extract(epoch ...))` math (PG-only) and no
    `julianday` (SQLite-only) — Python does the picking.
    """
    if not device_id or target_time is None:
        return None

    from datetime import timedelta

    from sqlalchemy import select as _select

    from db.models.vehicle_status import EVVehicleStatus

    window_start = target_time - timedelta(hours=2)
    window_end = target_time + timedelta(hours=2)

    stmt = (
        _select(EVVehicleStatus.recorded_at, EVVehicleStatus.odometer)
        .where(EVVehicleStatus.device_id == device_id)
        .where(EVVehicleStatus.odometer.isnot(None))
        .where(EVVehicleStatus.recorded_at >= window_start)
        .where(EVVehicleStatus.recorded_at <= window_end)
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return None

    # SQLite returns naive datetimes via aiosqlite; PG returns tz-aware.
    # Coerce both sides to UTC-aware before subtraction so the comparison
    # works on either backend.
    target_aware = target_time if target_time.tzinfo else target_time.replace(tzinfo=UTC)

    def _delta(r):
        recorded = r.recorded_at
        recorded_aware = recorded if recorded.tzinfo else recorded.replace(tzinfo=UTC)
        return abs((recorded_aware - target_aware).total_seconds())

    closest = min(rows, key=_delta)
    return float(closest.odometer) if closest.odometer is not None else None
