"""ha-fordpass source adapter.

Ingestion adapter for the home-assistant-fordpass (ha-fordpass) HA integration.
Owns the FIELD_CONTRACTS registry mapping HA entity attributes to DB columns,
and routes every conversion through web.services.units.to_metric so unit
handling is explicit and testable.

### No runtime unit auto-detection
This module does NOT import or reference the legacy FordPass preferred-unit
flags (previously carried on ha_config and the hass_processor value-normalizer
helper). Every conversion here flows
through a FIELD_CONTRACTS entry with a declared `source_unit`.

### Prefer documented-metric entities
Reads from:
  - sensor.fordpass_{vin}_metrics  (attributes always metric per integration author)
  - sensor.fordpass_{vin}_events   (xev-key-off-trip-segment-data always metric)

### Read-time UoM fallback for main-state-only fields
When a field exists only on an elveh-shaped main sensor (e.g. the elveh state
value itself), the adapter reads
`new_state["attributes"]["unit_of_measurement"]` at event-processing time and
uses it as the declared source_unit for THAT SINGLE EVENT. Never a
process-global flag. Unknown/missing UoM short-circuits the field with a
warning log. The adapter never silently assumes metric.

### Elveh attributes are not read for unit-bearing trip data
Trip fields (distance, energy_consumed, ambient/cabin/outside_air temps, etc)
come from `sensor.fordpass_{vin}_events.xev-key-off-trip-segment-data`, not
from `sensor.fordpass_{vin}_elveh` attributes. The elveh attribute UoM
semantics are unreliable (2026-03-21 bug, commit abd736b). Battery-related
attributes (voltage/amperage/kW/capacity) ARE read from elveh because they are
SI-already (V, A, kW) and need no conversion — they carry no FIELD_CONTRACTS
entry for that reason (see `tests/test_unit/test_contract_coverage.py`
`_EXEMPTIONS`).

### Canonical source for ev_charging_session.distance_added
Evidence (from `tests/fixtures/ha_payloads/*.json` — matches real ha-fordpass
behavior):

  | Fixture                            | plugDetails.totalDistanceAdded |
  |------------------------------------|--------------------------------|
  | metric_ha_metric_vehicle.json      | 103 (km)                       |
  | metric_ha_imperial_vehicle.json    | 103 (km)                       |
  | imperial_ha_metric_vehicle.json    | 64 (mi)                        |
  | imperial_ha_imperial_vehicle.json  | 64 (mi)                        |

ha-fordpass calls `localize_distance` on this field inside
`fordpass_handler.get_energy_transfer_log_attrs`, so the emitted value
reflects HA's configured unit system: metric HA -> km, imperial HA -> mi.
The contract below carries `ha_unit_system_converted=True`; `_resolve_source_unit`
derives the per-event source unit from `ha_config.unit_system` and routes
through `to_metric` to convert mi -> km back to canonical storage.

There is NO distance-added-shaped attribute on sensor.fordpass_{vin}_metrics
or sensor.fordpass_{vin}_events. The elveh.totalDistanceAdded attribute was a
fixture fabrication (ha-fordpass does not emit it on elveh) — not used.

**Decision: source = sensor.fordpass_{vin}_energytransferlogentry /
plugDetails.totalDistanceAdded, source_unit resolved per-event from
ha_config.unit_system.** This kills the 2026-03-21 double-conversion bug
which assumed the field was always km and produced 165.8 on imperial-HA events.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from web.services.units import detection
from web.services.units.contracts import FieldContract
from web.services.units.to_metric import to_metric, UnknownSourceUnit

logger = logging.getLogger("lightningrod.sources.ha_fordpass")

# Dedicated logger for per-field unit-conversion traces. Separate from the
# module logger so it can be toggled independently at runtime:
#   logging.getLogger("lightningrod.units.trace").setLevel(logging.INFO)
# Default level stays WARNING so normal deployments are quiet; flip to INFO
# (or DEBUG) to see every (field, raw, method, effective_unit, converted).
_trace = logging.getLogger("lightningrod.units.trace")


# ---------------------------------------------------------------------------
# FIELD_CONTRACTS registry
# ---------------------------------------------------------------------------
# Every unit-ful DB column handled by this adapter appears here. The
# tests/test_unit/test_contract_coverage.py invariant test asserts that every
# (table, column) in its _UNIT_FUL_COLUMNS set appears in a FIELD_CONTRACTS
# entry below (with exemptions for dimensionless / SI-already fields).
#
# Source-unit strings MUST be recognized by web.services.units.to_metric.
# Today that means: km, mi, kmh, mph, degC, degF, F, kWh, Wh, s, seconds.

FIELD_CONTRACTS: list[FieldContract] = [
    # --- ev_battery_status (from sensor.{vin}_metrics) -------------------
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="xevBatteryRange",
        source_unit="km",
        target_db_table="ev_battery_status",
        target_db_column="hv_battery_range",
        target_unit="km",
        notes="Canonical metric source; replaces elveh state reading",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="xevBatteryMaximumRange",
        source_unit="km",
        target_db_table="ev_battery_status",
        target_db_column="hv_battery_max_range",
        target_unit="km",
        notes="Canonical metric source; replaces elveh.maximumBatteryRange",
    ),

    # --- ev_battery_status: hv_battery_temperature --------------------------
    # Source exists only on the energytransferlogentry payload
    # (attrs.batteryTemperature) and is documented °C per integration author.
    # Contract lives here because metrics entity does not expose this
    # attribute and this adapter avoids elveh unit-bearing trip reads.
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="batteryTemperature",
        source_unit="degC",
        target_db_table="ev_battery_status",
        target_db_column="hv_battery_temperature",
        target_unit="degC",
        notes="Only exposed on energytransferlogentry payload; integration emits °C",
    ),

    # --- ev_trip_metrics (from sensor.{vin}_events xev-key-off-trip-segment-data) ---
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_events",
        source_attribute="xev-key-off-trip-segment-data.distance_traveled",
        source_unit="km",
        target_db_table="ev_trip_metrics",
        target_db_column="distance",
        target_unit="km",
        notes="Canonical source; replaces elveh.tripDistanceTraveled",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_events",
        source_attribute="xev-key-off-trip-segment-data.energy_consumed",
        source_unit="Wh",
        target_db_table="ev_trip_metrics",
        target_db_column="energy_consumed",
        target_unit="kWh",
        notes="Canonical source; Wh -> kWh via to_metric",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_events",
        source_attribute="xev-key-off-trip-segment-data.ambient_temp",
        source_unit="degC",
        target_db_table="ev_trip_metrics",
        target_db_column="ambient_temp",
        target_unit="degC",
        notes="Canonical metric source; replaces elveh.tripAmbientTemp",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_events",
        source_attribute="xev-key-off-trip-segment-data.cabin_temp",
        source_unit="degC",
        target_db_table="ev_trip_metrics",
        target_db_column="cabin_temp",
        target_unit="degC",
        notes="Canonical metric source; replaces elveh.tripCabinTemp",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_events",
        source_attribute="xev-key-off-trip-segment-data.outside_air_temp",
        source_unit="degC",
        target_db_table="ev_trip_metrics",
        target_db_column="outside_air_temp",
        target_unit="degC",
        notes="Canonical metric source; replaces elveh.tripOutsideAirAmbientTemp",
    ),
    # efficiency and range_regenerated are not currently exposed on the events
    # entity (absent from every 29-00 fixture). They remain sourced from elveh
    # state-level attributes for now; the adapter reads them with the
    # elveh state's read-time unit_of_measurement, NOT from the attribute UoM.
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_elveh",
        source_attribute="tripEfficiency",
        source_unit="km",  # placeholder: actual source_unit resolved read-time from state uom
        target_db_table="ev_trip_metrics",
        target_db_column="efficiency",
        target_unit="km",
        notes=(
            "Read-time fallback — elveh attribute in HA-preferred distance "
            "unit (km/kWh or mi/kWh). Adapter derives the per-event source_unit "
            "from new_state.attributes.unit_of_measurement at read-time. NOT from "
            "a process-global flag. This contract's declared source_unit is the "
            "DEFAULT when the event carries no uom; concrete conversion routes "
            "through adapter._resolve_source_unit()."
        ),
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_elveh",
        source_attribute="tripRangeRegenerated",
        source_unit="km",
        target_db_table="ev_trip_metrics",
        target_db_column="range_regenerated",
        target_unit="km",
        notes="Read-time fallback — elveh attribute; see tripEfficiency contract",
    ),

    # --- ev_charging_session (from sensor.{vin}_energytransferlogentry) ----------
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="plugDetails.totalDistanceAdded",
        source_unit="km",
        target_db_table="ev_charging_session",
        target_db_column="distance_added",
        target_unit="km",
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
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="batteryTemperature",
        source_unit="degC",
        target_db_table="ev_charging_session",
        target_db_column="battery_temp_start",
        target_unit="degC",
        notes="ha-fordpass emits °C on the energytransferlogentry payload",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="batteryTemperature",
        source_unit="degC",
        target_db_table="ev_charging_session",
        target_db_column="battery_temp_end",
        target_unit="degC",
        notes=(
            "ha-fordpass exposes a single snapshot value; start/end mirror until "
            "HA emits discrete timeseries snapshots"
        ),
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="outsidetemp",
        source_unit="degC",
        target_db_table="ev_charging_session",
        target_db_column="ambient_temp_start",
        target_unit="degC",
        notes="ha-fordpass emits °C on the energytransferlogentry payload",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="outsidetemp",
        source_unit="degC",
        target_db_table="ev_charging_session",
        target_db_column="ambient_temp_end",
        target_unit="degC",
        notes="snapshot mirrored to start/end like battery_temp",
    ),
]


# ---------------------------------------------------------------------------
# _last_seen_raw cache
# ---------------------------------------------------------------------------
# Keyed by f"{source_entity_pattern}|{source_attribute}". Stores a dict of
# {"value": <raw>, "unit": <source_unit>, "seen_at": <iso8601 UTC>, "converted": <metric>}.
# Consumed by /admin/data-sources diagnostic page.

_last_seen_raw: dict[str, dict[str, Any]] = {}


def _record_last_seen(
    contract: FieldContract,
    raw_value: Any,
    converted: Any,
    effective_unit: Optional[str] = None,
    method: Optional[str] = None,
) -> None:
    """Record the last-seen raw value for diagnostic display.

    `effective_unit` overrides `contract.source_unit` when a read-time UoM
    fallback was used. `method` captures how the unit was resolved
    (declared / ha_unit_system_converted / read_time_uom / declared_fallback)
    so the data-sources page can show the reason per field.
    """
    key = f"{contract.source_entity_pattern}|{contract.source_attribute}"
    _last_seen_raw[key] = {
        "value": raw_value,
        "unit": effective_unit or contract.source_unit,
        "declared_unit": contract.source_unit,
        "method": method or "declared",
        "seen_at": datetime.now(timezone.utc).isoformat(),
        "converted": converted,
    }


# ---------------------------------------------------------------------------
# Lookup + conversion helpers (consumed by hass_processor.py in Task 2)
# ---------------------------------------------------------------------------

INGEST_SCHEMA_VERSION = 2  # mark every new adapter-driven row

_ENTITY_SUFFIX_PREFIX = "sensor.fordpass_"


def _entity_suffix(entity_id: Optional[str]) -> Optional[str]:
    """Return the trailing slug of a fordpass entity_id, or None.

    Example: sensor.fordpass_ABC123_elveh -> "elveh"
    Example: sensor.fordpass_YOUR_VIN_metrics -> "metrics"

    Uses rsplit so VINs containing underscores (e.g., the PII-free fixture
    placeholder `YOUR_VIN`) still parse correctly. Fordpass slugs are always
    single tokens (elveh, metrics, events, energytransferlogentry, outsidetemp,
    soc, odometer, etc.) so the trailing segment is unambiguous.
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
    """Convert a concrete entity_id into its FIELD_CONTRACTS pattern form.

    sensor.fordpass_ABC123_metrics -> sensor.fordpass_{vin}_metrics
    """
    suffix = _entity_suffix(entity_id)
    if suffix is None:
        return entity_id
    return f"sensor.fordpass_{{vin}}_{suffix}"


def lookup_contract(
    entity_pattern: str, source_attribute: str
) -> Optional[FieldContract]:
    """Look up the FIELD_CONTRACTS entry for a given (pattern, attribute).

    Returns the FIRST matching contract; callers needing all matches (e.g.
    energytransferlogentry.batteryTemperature maps to both start + end)
    should iterate FIELD_CONTRACTS directly.
    """
    for contract in FIELD_CONTRACTS:
        if (
            contract.source_entity_pattern == entity_pattern
            and contract.source_attribute == source_attribute
        ):
            return contract
    return None


def _field_type_for_contract(contract: FieldContract) -> Optional[str]:
    """Classify a contract's source/target unit as distance/temperature/etc.

    Returns None when the contract's units are dimensionless or unsupported
    for HA-unit-system resolution. Used by _resolve_source_unit when the
    contract carries `ha_unit_system_converted=True`.
    """
    # Same semantics as web.services.units.detection._UNIT_FIELD_TYPES
    # duplicated locally to avoid widening the import graph.
    unit = contract.target_unit or contract.source_unit
    if unit in ("km", "mi"):
        return "distance"
    if unit in ("kmh", "mph"):
        return "speed"
    if unit in ("degC", "degF", "F"):
        return "temperature"
    return None


def _unit_for_ha_system(
    field_type: str, ha_config: Optional[dict]
) -> Optional[str]:
    """Return the source-unit string HA would emit for `field_type`.

    Reads `ha_config.unit_system` (either the flat-string or nested-dict
    shape) and returns the matching to_metric unit key for `field_type`:
      metric + distance -> km ; imperial + distance -> mi
      metric + temperature -> degC ; imperial + temperature -> degF
      metric + speed -> kmh ; imperial + speed -> mph
    Returns None when the unit system cannot be resolved.
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
    contract: FieldContract,
    new_state: Optional[dict],
    ha_config: Optional[dict] = None,
) -> tuple[str, str]:
    """Resolve the effective source unit for one contract + event.

    Returns (unit, method). `method` is one of:
      - "declared"                    — contract.source_unit used verbatim
      - "ha_unit_system_converted"    — derived from ha_config.unit_system
      - "read_time_uom"               — elveh-state attribute UoM
      - "declared_fallback"           — signal requested but not available;
                                        fell back to contract.source_unit

    The HA-unit-system path (method="ha_unit_system_converted") fires when
    `contract.ha_unit_system_converted=True` — i.e. ha-fordpass calls
    `localize_distance` / `localize_temperature` on this field. The elveh
    read-time path stays for legacy elveh-state contracts.

    This function never raises; on any problem it returns the contract's
    declared source_unit with method="declared_fallback" so the caller can
    still convert.
    """
    # --- 1. HA-unit-system conversion path ---
    if contract.ha_unit_system_converted:
        field_type = _field_type_for_contract(contract)
        if field_type is not None:
            resolved = _unit_for_ha_system(field_type, ha_config)
            if resolved:
                return resolved, "ha_unit_system_converted"
        # Signal requested but we couldn't resolve ha_config.unit_system.
        # Fall back to declared so we at least produce a value; the
        # detection layer will flag this as degraded via unknown_reason.
        logger.warning(
            "ha_unit_system_converted contract %s.%s received no usable "
            "ha_config.unit_system; falling back to declared %r",
            contract.source_entity_pattern,
            contract.source_attribute,
            contract.source_unit,
        )
        return contract.source_unit, "declared_fallback"

    # --- 2. Elveh-state read-time UoM path (legacy behavior preserved) ---
    if contract.source_entity_pattern.endswith("_elveh") and new_state is not None:
        attrs = new_state.get("attributes") or {}
        raw_uom = attrs.get("unit_of_measurement")
        if raw_uom:
            normalized = _normalize_uom_string(raw_uom)
            if normalized:
                return normalized, "read_time_uom"

    # --- 3. Declared (contract literal) ---
    return contract.source_unit, "declared"


def _normalize_uom_string(raw: str) -> Optional[str]:
    """Normalize an HA `unit_of_measurement` string to a to_metric key.
    Maps the common HA UoM spellings (with/without degree sign, case
    variations) to the canonical strings recognized by to_metric.
    Returns None for unrecognized inputs so callers can fall back.
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
    contract: FieldContract,
    raw_value: Any,
    new_state: Optional[dict] = None,
    ha_config: Optional[dict] = None,
) -> Optional[float]:
    """Convert `raw_value` to metric via `contract` + per-event resolution.

    Resolution order (inside `_resolve_source_unit`):
      1. `ha_unit_system_converted=True` -> ha_config.unit_system
      2. elveh-state read-time UoM
      3. contract.source_unit (declared)

    Logs and returns None on UnknownSourceUnit so the adapter boundary absorbs
    unit failures rather than propagating them to the caller. Records the
    conversion in `_last_seen_raw` AND in the unit-detection layer so the
    /admin/data-sources diagnostic page surfaces the resolution path.

    Also emits a per-field trace on `lightningrod.units.trace` logger (INFO
    level) so you can follow: field, raw, method, source_unit, converted,
    target column.
    """
    if raw_value is None:
        return None
    source_unit, method = _resolve_source_unit(contract, new_state, ha_config)
    try:
        converted = to_metric(raw_value, source_unit)
    except UnknownSourceUnit as exc:
        logger.warning(
            "UnknownSourceUnit on %s.%s: value=%r source_unit=%r method=%s (%s); "
            "skipping field, continuing event",
            contract.source_entity_pattern,
            contract.source_attribute,
            raw_value,
            source_unit,
            method,
            exc,
        )
        return None
    _record_last_seen(
        contract, raw_value, converted, effective_unit=source_unit, method=method
    )
    detection.record_declared(
        contract.source_entity_pattern,
        contract.source_attribute,
        source_unit,
        raw_value,
    )
    if _trace.isEnabledFor(logging.INFO):
        _trace.info(
            "convert %s.%s | raw=%r | method=%s | source_unit=%s -> target_unit=%s | "
            "converted=%r | -> %s.%s",
            contract.source_entity_pattern,
            contract.source_attribute,
            raw_value,
            method,
            source_unit,
            contract.target_unit,
            converted,
            contract.target_db_table,
            contract.target_db_column,
        )
    return converted


# ---------------------------------------------------------------------------
# Safe-float helper (re-exported from hass_processor for parity)
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Device-id extraction
# ---------------------------------------------------------------------------

def _device_id_from_entity(entity_id: str) -> Optional[str]:
    """Extract the VIN segment from sensor.fordpass_{vin}_{slug}.

    Uses rsplit so that VIN placeholders containing underscores (e.g. the
    PII-free fixture placeholder `YOUR_VIN`) return as the full VIN rather
    than being truncated at the first underscore.
    """
    if not entity_id or not entity_id.startswith(_ENTITY_SUFFIX_PREFIX):
        return None
    remainder = entity_id[len(_ENTITY_SUFFIX_PREFIX):]
    parts = remainder.rsplit("_", 1)
    if not parts or not parts[0]:
        return None
    return parts[0]


# ---------------------------------------------------------------------------
# process_event — main entry point
# ---------------------------------------------------------------------------

async def process_event(
    entity_id: str,
    new_state: dict,
    db: AsyncSession,
    ha_config: Optional[dict] = None,
) -> None:
    """Route an HA state_changed event to the appropriate write path.
    Guarantee: zero runtime unit auto-detection; every conversion goes
    through a FIELD_CONTRACTS entry via `convert`.
    Dispatches by entity suffix:
    _metrics -> writes ev_battery_status row (range / max_range)
    _events -> writes ev_trip_metrics row (from xev-key-off-trip-segment-data)
    _energytransferlogentry -> writes ev_charging_session row
    everything else -> no-op (handled by hass_processor's legacy handlers,
    or explicitly ignored)
    `ha_config` is forwarded to `convert()` so contracts flagged
    `ha_unit_system_converted` can resolve the effective source unit
    per-event from HA's configured unit_system.
    Unknown entity or missing attribute: log debug + return. No exception
    propagates to the caller (the HA WebSocket event loop).
    """
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
        else:
            # Not an adapter-owned entity. Silent return — hass_processor
            # handles legacy per-slug routing for vehicle status, GPS, etc.
            return
    except UnknownSourceUnit as exc:
        # convert() already absorbs this; belt-and-braces in case a caller
        # bypasses convert() and calls to_metric directly.
        logger.warning(
            "UnknownSourceUnit escaped convert() for %s: %s", entity_id, exc
        )
    except Exception:
        # The adapter must never raise into the WebSocket loop.
        logger.exception("ha_fordpass.process_event failed for %s", entity_id)


# ---------------------------------------------------------------------------
# Per-entity handlers
# ---------------------------------------------------------------------------

async def _handle_metrics_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: Optional[dict] = None,
) -> None:
    """sensor.fordpass_{vin}_metrics -> ev_battery_status row.

    Writes a row with hv_battery_range + hv_battery_max_range (+ any other
    battery fields available on the metrics entity) and
    ingest_schema_version = INGEST_SCHEMA_VERSION.
    """
    from db.models.battery_status import EVBatteryStatus

    attrs = new_state.get("attributes") or {}
    pattern = _entity_pattern(entity_id)

    range_contract = lookup_contract(pattern, "xevBatteryRange")
    max_range_contract = lookup_contract(pattern, "xevBatteryMaximumRange")

    raw_range = attrs.get("xevBatteryRange")
    raw_max_range = attrs.get("xevBatteryMaximumRange")
    hv_range = (
        convert(range_contract, raw_range, new_state, ha_config)
        if range_contract
        else None
    )
    hv_max_range = (
        convert(max_range_contract, raw_max_range, new_state, ha_config)
        if max_range_contract
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
    hv_soc = _safe_float(attrs.get("xevBatteryStateOfCharge"))
    hv_actual_soc = _safe_float(attrs.get("xevBatteryActualStateOfCharge"))
    hv_capacity = _safe_float(attrs.get("xevBatteryCapacity"))
    hv_voltage = _safe_float(attrs.get("xevBatteryVoltage"))
    hv_amperage = _safe_float(attrs.get("xevBatteryAmperage"))
    # xevBatteryPower is reported in W by the integration; convert to kW for the hv_battery_kw column
    raw_power_w = _safe_float(attrs.get("xevBatteryPower"))
    hv_kw = raw_power_w / 1000.0 if raw_power_w is not None else None

    recorded_at = _parse_event_ts(new_state) or datetime.now(timezone.utc)

    record = EVBatteryStatus(
        device_id=device_id,
        recorded_at=recorded_at,
        source_system="home_assistant",
        hv_battery_range=hv_range,
        hv_battery_max_range=hv_max_range,
        hv_battery_soc=hv_soc,
        hv_battery_actual_soc=hv_actual_soc,
        hv_battery_capacity=hv_capacity,
        hv_battery_voltage=hv_voltage,
        hv_battery_amperage=hv_amperage,
        hv_battery_kw=hv_kw,
        original_timestamp=recorded_at,
        ingest_schema_version=INGEST_SCHEMA_VERSION,
    )
    db.add(record)
    logger.debug(
        "ha_fordpass: wrote ev_battery_status for %s range=%s max=%s",
        device_id,
        hv_range,
        hv_max_range,
    )


async def _handle_events_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: Optional[dict] = None,
) -> None:
    """sensor.fordpass_{vin}_events -> ev_trip_metrics row.

    Reads xev-key-off-trip-segment-data attribute subkey. This is the
    canonical metric source for trip data; elveh.trip* attributes are not read.
    """
    from db.models.trip_metrics import EVTripMetrics

    attrs = new_state.get("attributes") or {}
    trip = attrs.get("xev-key-off-trip-segment-data")
    if not isinstance(trip, dict):
        return

    pattern = _entity_pattern(entity_id)

    def _lookup(attr_suffix: str) -> Optional[FieldContract]:
        return lookup_contract(
            pattern, f"xev-key-off-trip-segment-data.{attr_suffix}"
        )

    distance_c = _lookup("distance_traveled")
    energy_c = _lookup("energy_consumed")
    ambient_c = _lookup("ambient_temp")
    cabin_c = _lookup("cabin_temp")
    outside_c = _lookup("outside_air_temp")

    distance = convert(distance_c, trip.get("distance_traveled"), new_state, ha_config) if distance_c else None
    energy = convert(energy_c, trip.get("energy_consumed"), new_state, ha_config) if energy_c else None
    ambient = convert(ambient_c, trip.get("ambient_temp"), new_state, ha_config) if ambient_c else None
    cabin = convert(cabin_c, trip.get("cabin_temp"), new_state, ha_config) if cabin_c else None
    outside_air = convert(outside_c, trip.get("outside_air_temp"), new_state, ha_config) if outside_c else None

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
            "xev-key-off-trip-segment-data.ambient_temp",
            ambient,
            "degC",
        )
    if cabin is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_events",
            "xev-key-off-trip-segment-data.cabin_temp",
            cabin,
            "degC",
        )
    if outside_air is not None:
        detection.try_cross_reference(
            device_id,
            "sensor.fordpass_{vin}_events",
            "xev-key-off-trip-segment-data.outside_air_temp",
            outside_air,
            "degC",
        )

    # duration (s) is SI-passthrough; no contract conversion needed
    duration = _safe_float(trip.get("trip_duration"))

    recorded_at = _parse_event_ts(new_state) or datetime.now(timezone.utc)

    record = EVTripMetrics(
        device_id=device_id,
        start_time=None,
        end_time=recorded_at,
        recorded_at=recorded_at,
        distance=distance,
        duration=duration,
        energy_consumed=energy,
        ambient_temp=ambient,
        cabin_temp=cabin,
        outside_air_temp=outside_air,
        is_complete=True,
        source_system="home_assistant",
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


async def _handle_energy_transfer_entity(
    entity_id: str,
    new_state: dict,
    device_id: str,
    db: AsyncSession,
    ha_config: Optional[dict] = None,
) -> None:
    """sensor.fordpass_{vin}_energytransferlogentry -> ev_charging_session row.

    plugDetails.totalDistanceAdded is HA-converted (per ha-fordpass source
    `fordpass_handler.get_energy_transfer_log_attrs` -> `localize_distance`);
    the adapter resolves its effective unit per-event from `ha_config`.
    Battery + ambient temps (batteryTemperature, outsidetemp) pass through
    raw in °C on this payload.
    """
    from db.models.charging_session import EVChargingSession

    attrs = new_state.get("attributes") or {}
    if not attrs:
        return

    pattern = _entity_pattern(entity_id)

    # Core energy + charger metadata (already in canonical units)
    energy_kwh = _safe_float(attrs.get("energyConsumed"))
    charge_type_raw = attrs.get("chargerType")

    # Duration
    duration_data = attrs.get("energyTransferDuration") or {}
    session_start_utc = _parse_iso(duration_data.get("begin"))
    session_end_utc = _parse_iso(duration_data.get("end"))
    charge_duration_seconds = _safe_float(duration_data.get("totalTime"))

    # Plug details + distance_added via FIELD_CONTRACTS.
    plug_data = attrs.get("plugDetails") or {}
    plugged_in_duration_seconds = _safe_float(plug_data.get("totalPluggedInTime"))
    dist_contract = lookup_contract(pattern, "plugDetails.totalDistanceAdded")
    distance_added = (
        convert(dist_contract, plug_data.get("totalDistanceAdded"), new_state, ha_config)
        if dist_contract
        else None
    )

    # SOC
    soc_data = attrs.get("stateOfCharge") or {}
    start_soc = _safe_float(soc_data.get("firstSOC"))
    end_soc = _safe_float(soc_data.get("lastSOC"))

    # Power (W -> kW)
    power_data = attrs.get("power") or {}
    max_power = _safe_float(power_data.get("max"))
    min_power = _safe_float(power_data.get("min"))
    weighted = _safe_float(power_data.get("weightedAverage"))
    if max_power is not None:
        max_power = max_power / 1000.0
    if min_power is not None:
        min_power = min_power / 1000.0
    charging_kw = weighted / 1000.0 if weighted is not None else None

    # Location
    location_data = attrs.get("location") or {}
    addr_dict = location_data.get("address") or {}
    address = _format_address(addr_dict)
    latitude = _safe_float(location_data.get("latitude"))
    longitude = _safe_float(location_data.get("longitude"))
    location_name = location_data.get("name") or (
        addr_dict.get("city") if addr_dict else None
    )

    # Thermal via FIELD_CONTRACTS (both start+end contracts point at the same
    # raw attribute; we convert once and mirror)
    batt_start_c = lookup_contract(pattern, "batteryTemperature")  # first match == battery_temp_start
    battery_temp = (
        convert(batt_start_c, attrs.get("batteryTemperature"), new_state, ha_config)
        if batt_start_c
        else None
    )
    amb_start_c = lookup_contract(pattern, "outsidetemp")
    ambient_temp = (
        convert(amb_start_c, attrs.get("outsidetemp"), new_state, ha_config)
        if amb_start_c
        else None
    )

    original_timestamp = _parse_iso(attrs.get("timeStamp"))

    session = EVChargingSession(
        device_id=device_id,
        source_system="home_assistant",
        charge_type=_normalize_charger_type(charge_type_raw),
        location_name=location_name,
        session_start_utc=session_start_utc,
        session_end_utc=session_end_utc,
        charge_duration_seconds=charge_duration_seconds,
        plugged_in_duration_seconds=plugged_in_duration_seconds,
        start_soc=start_soc,
        end_soc=end_soc,
        energy_kwh=energy_kwh,
        max_power=max_power,
        min_power=min_power,
        charging_kw=charging_kw,
        address=address,
        latitude=latitude,
        longitude=longitude,
        distance_added=distance_added,
        battery_temp_start=battery_temp,
        battery_temp_end=battery_temp,
        ambient_temp_start=ambient_temp,
        ambient_temp_end=ambient_temp,
        original_timestamp=original_timestamp,
        is_complete=True,
        recorded_at=datetime.now(timezone.utc),
        ingest_schema_version=INGEST_SCHEMA_VERSION,
    )
    db.add(session)
    logger.debug(
        "ha_fordpass: wrote ev_charging_session for %s distance_added=%s energy=%s",
        device_id,
        distance_added,
        energy_kwh,
    )


# ---------------------------------------------------------------------------
# Tiny helpers shared across handlers (no cross-module deps)
# ---------------------------------------------------------------------------

def _parse_event_ts(new_state: dict) -> Optional[datetime]:
    for key in ("last_changed", "last_updated"):
        val = new_state.get(key)
        if not val:
            continue
        parsed = _parse_iso(val)
        if parsed is not None:
            return parsed
    return None


def _parse_iso(val: Any) -> Optional[datetime]:
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


def _format_address(addr: Optional[dict]) -> Optional[str]:
    if not addr or not isinstance(addr, dict):
        return None
    parts = []
    if addr.get("address1"):
        parts.append(addr["address1"])
    if addr.get("city"):
        parts.append(addr["city"])
    if addr.get("state"):
        parts.append(addr["state"])
    return ", ".join(parts) if parts else None


# Mirrors hass_processor._CHARGER_TYPE_MAP so the adapter's charging-session
# path is drop-in compatible. Kept local to avoid a back-import.
_CHARGER_TYPE_MAP = {
    "AC": "AC", "AC_BASIC": "AC", "AC_LEVEL_1": "AC", "AC_LEVEL_2": "AC",
    "AC LEVEL 1": "AC", "AC LEVEL 2": "AC", "LEVEL_1": "AC", "LEVEL_2": "AC",
    "LEVEL 1": "AC", "LEVEL 2": "AC", "L1": "AC", "L2": "AC",
    "DC": "DC", "DC_FAST": "DC", "DC_DCFAST": "DC", "DC_COMBO": "DC",
    "DC FAST": "DC", "DCFC": "DC", "L3": "DC", "LEVEL 3": "DC",
}


def _normalize_charger_type(raw: Any) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    key = raw.strip().upper()
    if not key:
        return None
    return _CHARGER_TYPE_MAP.get(key, key)
