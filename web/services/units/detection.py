"""Unit detection layer.

Records and resolves the unit-of-measurement for every (entity, attribute)
data source we ingest from Home Assistant.

Five detection methods, priority order when resolving a unit:
  1. declared                — source appears in FIELD_CONTRACTS (events/
                               metrics/energytransferlogentry fields)
  2. read_time_uom           — event carried
                               new_state.attributes.unit_of_measurement
  3. device_class_ha_config  — inferred from
                               new_state.attributes.device_class combined
                               with the HA instance's unit_system preference
  4. cross_reference         — ratio-matched against a known-metric canonical
                               value from events/metrics for the same device +
                               semantic field in a recent time window
  5. unknown                 — no signal resolved; value recorded with flag
                               for later user override (future work)

Storage: module-level in-memory cache. No DB persistence in this pass.
Survives for the life of the process.

Consumed by /admin/data-sources for diagnostic display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("lightningrod.units.detection")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_METHOD_DECLARED = "declared"
_METHOD_READ_TIME = "read_time_uom"
_METHOD_DEVICE_CLASS = "device_class_ha_config"
_METHOD_CROSS_REF = "cross_reference"
_METHOD_UNKNOWN = "unknown"

_METHOD_PRIORITY = {
    _METHOD_DECLARED: 0,
    _METHOD_READ_TIME: 1,
    _METHOD_DEVICE_CLASS: 2,
    _METHOD_CROSS_REF: 3,
    _METHOD_UNKNOWN: 4,
}


@dataclass(frozen=True)
class DetectionRecord:
    """Snapshot of what we know about one (entity_pattern, attribute) source.

    attribute is "" for a sensor's state value (no nested attribute).
    detected_unit is None when method == unknown.
    """

    entity_pattern: str
    attribute: str
    detected_unit: Optional[str]
    method: str
    confidence: str
    sample_raw: Optional[float]
    sample_canonical: Optional[float]
    ratio: Optional[float]
    last_seen: datetime
    unknown_reason: Optional[str] = None
    cross_ref_observations: int = 0


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Keyed by f"{entity_pattern}|{attribute}"
_records: dict[str, DetectionRecord] = {}

# Recent-reads buffer keyed by device_id, value is a list of tuples:
# (entity_pattern, attribute, raw_value, timestamp)
_recent_reads: dict[str, list[tuple[str, str, float, datetime]]] = {}

# Keep this many reads per device, and expire anything older than this window.
_RECENT_READS_MAX_PER_DEVICE = 50
_RECENT_READS_TTL = timedelta(minutes=5)

# Ratio tolerance for distance cross-ref (±2% around each target factor).
_DISTANCE_RATIO_TOLERANCE = 0.02
# Factor for km canonical -> mi target (canonical_km * 0.621371 = miles_value).
_KM_PER_MILE = 1.609344
_MI_FACTOR = 1.0 / _KM_PER_MILE  # ~0.621371


def _key(entity_pattern: str, attribute: str) -> str:
    return f"{entity_pattern}|{attribute}"


# ---------------------------------------------------------------------------
# Cross-reference pairings
# ---------------------------------------------------------------------------
# (canonical_entity_pattern, canonical_attribute,
#  target_entity_pattern, target_attribute, field_type)
#
# For each pairing, when the canonical source is observed with a known-metric
# value, the detection layer checks the recent-reads buffer for matching
# target reads from the same device and tries to derive the target's unit
# from the observed ratio / delta.
CROSS_REF_PAIRINGS: list[tuple[str, str, str, str, str]] = [
    # events.distance_traveled (km) pairs with elveh.tripDistanceTraveled
    (
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.distance_traveled",
        "sensor.fordpass_{vin}_elveh",
        "tripDistanceTraveled",
        "distance",
    ),
    # events trip temperatures (°C) pair with elveh trip temp attributes
    (
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.ambient_temp",
        "sensor.fordpass_{vin}_elveh",
        "tripAmbientTemp",
        "temperature",
    ),
    (
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.cabin_temp",
        "sensor.fordpass_{vin}_elveh",
        "tripCabinTemp",
        "temperature",
    ),
    (
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.outside_air_temp",
        "sensor.fordpass_{vin}_elveh",
        "tripOutsideAirAmbientTemp",
        "temperature",
    ),
    # metrics.xevBatteryRange (km) pairs with elveh state (range) and
    # soc.batteryRange attribute. elveh state has attribute="".
    (
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        "sensor.fordpass_{vin}_elveh",
        "",
        "distance",
    ),
    (
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        "sensor.fordpass_{vin}_soc",
        "batteryRange",
        "distance",
    ),
    # metrics.xevBatteryMaximumRange (km) pairs with elveh.maximumBatteryRange
    (
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryMaximumRange",
        "sensor.fordpass_{vin}_elveh",
        "maximumBatteryRange",
        "distance",
    ),
]


def _pairings_for_canonical(
    canonical_entity_pattern: str, canonical_attribute: str
) -> list[tuple[str, str, str]]:
    """Return (target_entity_pattern, target_attribute, field_type) for a canonical source."""
    out: list[tuple[str, str, str]] = []
    for c_ent, c_attr, t_ent, t_attr, ftype in CROSS_REF_PAIRINGS:
        if c_ent == canonical_entity_pattern and c_attr == canonical_attribute:
            out.append((t_ent, t_attr, ftype))
    return out


# ---------------------------------------------------------------------------
# Recent-reads buffer helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prune_buffer(device_id: str) -> None:
    """Drop expired + oldest reads for a device."""
    reads = _recent_reads.get(device_id)
    if not reads:
        return
    cutoff = _now() - _RECENT_READS_TTL
    fresh = [r for r in reads if r[3] >= cutoff]
    if len(fresh) > _RECENT_READS_MAX_PER_DEVICE:
        fresh = fresh[-_RECENT_READS_MAX_PER_DEVICE:]
    if fresh:
        _recent_reads[device_id] = fresh
    else:
        _recent_reads.pop(device_id, None)


def _buffer_add(
    device_id: str,
    entity_pattern: str,
    attribute: str,
    raw_value: Any,
) -> None:
    """Append a raw read to the recent-reads buffer for cross-ref."""
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        return
    _prune_buffer(device_id)
    _recent_reads.setdefault(device_id, []).append(
        (entity_pattern, attribute, val, _now())
    )
    _prune_buffer(device_id)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _confidence_for_declared() -> str:
    return "high"


def _confidence_for_read_time() -> str:
    return "high"


def _confidence_for_device_class() -> str:
    return "medium"


def _confidence_for_cross_ref(obs: int) -> str:
    if obs >= 3:
        return "high"
    if obs >= 1:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Record insert helpers
# ---------------------------------------------------------------------------


def _insert_or_update(new: DetectionRecord) -> None:
    """Insert `new` unless an existing higher-priority record is in place.

    Priority (lower number wins):
      declared < read_time_uom < device_class_ha_config < cross_reference
      < unknown
    Same priority -> keep latest (overwrite).
    """
    key = _key(new.entity_pattern, new.attribute)
    existing = _records.get(key)
    if existing is None:
        _records[key] = new
        return
    existing_rank = _METHOD_PRIORITY.get(existing.method, 99)
    new_rank = _METHOD_PRIORITY.get(new.method, 99)
    if new_rank < existing_rank:
        _records[key] = new
        return
    if new_rank > existing_rank:
        # Existing is higher priority; keep it.
        return
    # Same priority: overwrite with the most recent observation.
    _records[key] = new


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_declared(
    entity_pattern: str,
    attribute: str,
    unit: str,
    raw_value: Any,
) -> None:
    """Record a source whose unit is declared in FIELD_CONTRACTS."""
    rec = DetectionRecord(
        entity_pattern=entity_pattern,
        attribute=attribute,
        detected_unit=unit,
        method=_METHOD_DECLARED,
        confidence=_confidence_for_declared(),
        sample_raw=_safe_float(raw_value),
        sample_canonical=None,
        ratio=None,
        last_seen=_now(),
        unknown_reason=None,
    )
    _insert_or_update(rec)


def record_read_time(
    entity_pattern: str,
    attribute: str,
    unit: str,
    raw_value: Any,
    device_id: Optional[str] = None,
) -> None:
    """Record a source whose unit came from new_state.attributes.unit_of_measurement.

    Populates the recent-reads buffer so later canonical observations from
    `try_cross_reference` can corroborate / refine the detected unit.
    """
    rec = DetectionRecord(
        entity_pattern=entity_pattern,
        attribute=attribute,
        detected_unit=unit,
        method=_METHOD_READ_TIME,
        confidence=_confidence_for_read_time(),
        sample_raw=_safe_float(raw_value),
        sample_canonical=None,
        ratio=None,
        last_seen=_now(),
        unknown_reason=None,
    )
    _insert_or_update(rec)
    if device_id:
        _buffer_add(device_id, entity_pattern, attribute, raw_value)


def record_unknown(
    entity_pattern: str,
    attribute: str,
    raw_value: Any,
    reason: str,
    device_id: Optional[str] = None,
) -> None:
    """Record a source with no resolvable unit.

    Populates the recent-reads buffer so a future canonical observation
    can upgrade this record via cross-reference.

    `reason` should be the bitstring of failed signals (e.g.
    "no_uom|no_device_class|no_unit_system|no_cross_ref") so the diagnostic
    page can surface each missing signal distinctly. Free-form strings are
    still accepted for backwards compatibility.
    """
    rec = DetectionRecord(
        entity_pattern=entity_pattern,
        attribute=attribute,
        detected_unit=None,
        method=_METHOD_UNKNOWN,
        confidence="low",
        sample_raw=_safe_float(raw_value),
        sample_canonical=None,
        ratio=None,
        last_seen=_now(),
        unknown_reason=reason,
    )
    _insert_or_update(rec)
    if device_id:
        _buffer_add(device_id, entity_pattern, attribute, raw_value)


def record_device_class_inference(
    entity_pattern: str,
    attribute: str,
    unit: str,
    device_class: str,
    ha_unit_system: Any,
    raw_value: Any = None,
    device_id: Optional[str] = None,
) -> None:
    """Record a source whose unit was inferred from device_class + unit_system.

    `device_class` and `ha_unit_system` are recorded via `unknown_reason`
    (repurposed as a provenance string for this method) so the diagnostic
    page can show the user exactly how the inference was made.
    """
    provenance = f"device_class={device_class!r}, ha_unit_system={ha_unit_system!r}"
    rec = DetectionRecord(
        entity_pattern=entity_pattern,
        attribute=attribute,
        detected_unit=unit,
        method=_METHOD_DEVICE_CLASS,
        confidence=_confidence_for_device_class(),
        sample_raw=_safe_float(raw_value),
        sample_canonical=None,
        ratio=None,
        last_seen=_now(),
        unknown_reason=provenance,
    )
    _insert_or_update(rec)
    if device_id and raw_value is not None:
        _buffer_add(device_id, entity_pattern, attribute, raw_value)


def try_cross_reference(
    device_id: str,
    canonical_entity_pattern: str,
    canonical_attribute: str,
    canonical_value: Any,
    canonical_unit: str,
) -> list[DetectionRecord]:
    """Cross-reference a canonical reading against recent reads for this device.

    Looks up registered pairings whose canonical side matches
    (canonical_entity_pattern, canonical_attribute). For each pairing it
    searches the recent-reads buffer for a matching target read from the
    same device and — for supported field types — derives the target unit
    from the observed ratio / delta.

    Returns the list of DetectionRecords updated (useful for testing).
    Never raises.
    """
    updated: list[DetectionRecord] = []
    canonical_f = _safe_float(canonical_value)
    if canonical_f is None or canonical_f == 0:
        return updated

    pairings = _pairings_for_canonical(canonical_entity_pattern, canonical_attribute)
    if not pairings:
        return updated

    _prune_buffer(device_id)
    reads = _recent_reads.get(device_id, [])
    if not reads:
        return updated

    for target_ent, target_attr, field_type in pairings:
        # Find the most recent matching target read.
        match = None
        for rec_ent, rec_attr, rec_val, rec_ts in reversed(reads):
            if rec_ent == target_ent and rec_attr == target_attr:
                match = (rec_val, rec_ts)
                break
        if match is None:
            continue
        target_raw, _ts = match

        detected, ratio, reason = _detect_from_observation(
            field_type, target_raw, canonical_f, canonical_unit
        )

        key = _key(target_ent, target_attr)
        existing = _records.get(key)
        obs = 1
        if existing is not None and existing.method == _METHOD_CROSS_REF:
            # If the detected unit matches the prior detection, bump obs count.
            if existing.detected_unit == detected:
                obs = existing.cross_ref_observations + 1

        if detected is None:
            rec = DetectionRecord(
                entity_pattern=target_ent,
                attribute=target_attr,
                detected_unit=None,
                method=_METHOD_UNKNOWN,
                confidence="low",
                sample_raw=target_raw,
                sample_canonical=canonical_f,
                ratio=ratio,
                last_seen=_now(),
                unknown_reason=reason,
                cross_ref_observations=0,
            )
        else:
            rec = DetectionRecord(
                entity_pattern=target_ent,
                attribute=target_attr,
                detected_unit=detected,
                method=_METHOD_CROSS_REF,
                confidence=_confidence_for_cross_ref(obs),
                sample_raw=target_raw,
                sample_canonical=canonical_f,
                ratio=ratio,
                last_seen=_now(),
                unknown_reason=None,
                cross_ref_observations=obs,
            )

        _insert_or_update(rec)
        # _insert_or_update may retain an existing higher-priority record.
        # Return the record that is *now* stored — not our candidate — so
        # tests + callers see the effective state.
        updated.append(_records.get(key, rec))
    return updated


def resolve_unit(entity_pattern: str, attribute: str) -> Optional[str]:
    """Return the currently-known unit for a source, or None."""
    rec = _records.get(_key(entity_pattern, attribute))
    if rec is None:
        return None
    return rec.detected_unit


# ---------------------------------------------------------------------------
# HA unit_system + device_class inference
# ---------------------------------------------------------------------------

_METRIC_LENGTH_TOKENS = {"km", "m", "metric", "si"}
_IMPERIAL_LENGTH_TOKENS = {"mi", "ft", "imperial", "us", "us_customary"}
_METRIC_TEMP_TOKENS = {"°c", "c", "degc", "celsius", "metric"}
_IMPERIAL_TEMP_TOKENS = {"°f", "f", "degf", "fahrenheit", "imperial", "us", "us_customary"}


def _coerce_unit_system_family(ha_unit_system: Any, field_type: str) -> Optional[str]:
    """Normalize HA unit_system to 'metric' or 'imperial' for a given field_type.

    Accepts:
      * a dict such as {"length": "km", "temperature": "°C", ...}
      * a flat string "metric" or "imperial" (older HA shape)
      * None / anything else -> returns None (caller falls back to unknown)
    """
    if ha_unit_system is None:
        return None
    # Flat string form.
    if isinstance(ha_unit_system, str):
        s = ha_unit_system.strip().lower()
        if s in ("metric", "si"):
            return "metric"
        if s in ("imperial", "us", "us_customary"):
            return "imperial"
        return None
    if not isinstance(ha_unit_system, dict):
        return None
    # Dict form. Pick the key that disambiguates this field_type, then
    # normalize the value via token sets.
    length_key = None
    for k in ("length", "distance"):
        if k in ha_unit_system:
            length_key = str(ha_unit_system[k]).strip().lower()
            break
    temp_key = (
        str(ha_unit_system["temperature"]).strip().lower()
        if "temperature" in ha_unit_system
        else None
    )
    if field_type in ("distance", "speed"):
        if length_key in _METRIC_LENGTH_TOKENS:
            return "metric"
        if length_key in _IMPERIAL_LENGTH_TOKENS:
            return "imperial"
    if field_type == "temperature":
        if temp_key in _METRIC_TEMP_TOKENS:
            return "metric"
        if temp_key in _IMPERIAL_TEMP_TOKENS:
            return "imperial"
    # Nothing relevant to this field_type; fall back to any global indicator.
    if length_key in _METRIC_LENGTH_TOKENS or temp_key in _METRIC_TEMP_TOKENS:
        return "metric"
    if length_key in _IMPERIAL_LENGTH_TOKENS or temp_key in _IMPERIAL_TEMP_TOKENS:
        return "imperial"
    return None


# Map normalized unit strings -> semantic field_type, so read-time UoM
# applied to one field (e.g. the state's "mi") isn't blindly reused when a
# handler is asking about a temperature attribute.
_UNIT_FIELD_TYPES: dict[str, str] = {
    "km": "distance",
    "mi": "distance",
    "kmh": "speed",
    "mph": "speed",
    "degC": "temperature",
    "degF": "temperature",
    "F": "temperature",
    "kWh": "energy",
    "Wh": "energy",
    "kW": "power",
    "s": "duration",
    "seconds": "duration",
}


def _unit_matches_field_type(unit: Optional[str], field_type: str) -> bool:
    """Return True when `unit` is the right kind of unit for `field_type`.

    A resolver answer like ("mi", read_time_uom) is only valid when the
    handler asked for a distance — applying mi -> km conversion math to a
    temperature value would silently corrupt data.

    Unknown / unmapped field_type is treated as permissive (no check).
    """
    if unit is None:
        return False
    expected = _UNIT_FIELD_TYPES.get(unit)
    if expected is None:
        # Unit we don't know how to classify — trust the caller.
        return True
    return expected == field_type


def _unit_from_device_class(
    device_class: Optional[str],
    ha_unit_system: Any,
    field_type: str,
) -> Optional[str]:
    """Derive a source unit from HA's device_class + unit_system.

    Returns None when the combination is ambiguous or not supported.

    Supported:
      device_class="temperature" -> degC (metric) / degF (imperial)
      device_class="distance"    -> km   (metric) / mi   (imperial)
      device_class="speed"       -> kmh  (metric) / mph  (imperial)
      device_class="energy"      -> kWh  (always, HA standardizes)
      device_class="power"       -> kW   (always, HA standardizes)
      device_class="pressure"    -> None (hPa/psi/kPa ambiguous; out of scope)
    """
    if not device_class:
        return None
    dc = device_class.strip().lower()

    if dc == "energy":
        return "kWh"
    if dc == "power":
        return "kW"
    if dc == "pressure":
        return None

    family = _coerce_unit_system_family(ha_unit_system, field_type)
    if family is None:
        return None

    if dc == "temperature":
        return "degC" if family == "metric" else "degF"
    if dc == "distance":
        return "km" if family == "metric" else "mi"
    if dc == "speed":
        return "kmh" if family == "metric" else "mph"
    return None


def _compose_unknown_reason(
    raw_uom: Any,
    device_class: Optional[str],
    unit_system: Any,
    existing: Optional[DetectionRecord],
) -> str:
    """Build the pipe-separated bitstring reason for an unknown resolution."""
    bits: list[str] = []
    if not raw_uom:
        bits.append("no_uom")
    if not device_class:
        bits.append("no_device_class")
    if unit_system is None or unit_system == {}:
        bits.append("no_unit_system")
    if existing is None or existing.method == _METHOD_UNKNOWN:
        bits.append("no_cross_ref")
    return "|".join(bits) if bits else "no_signals"


def resolve_source_unit(
    *,
    entity_id: str,
    attribute: str,
    new_state: dict,
    ha_config: dict,
    field_type: str,
    record: bool = True,
    raw_value: Any = None,
    device_id: Optional[str] = None,
) -> tuple[Optional[str], str, str]:
    """Resolve the source unit for an HA event, using only HA signals.

    Returns (unit, method, confidence).

    Priority chain:
      1. declared              — (entity_pattern, attribute) in FIELD_CONTRACTS
      2. read_time_uom         — normalized new_state.attributes.unit_of_measurement
      3. device_class_ha_config — new_state.attributes.device_class + ha_config.unit_system
      4. cross_reference       — prior resolved entry in detection cache
      5. unknown               — no signal; return (None, "unknown", "low")

    Never returns a hardcoded default unit. Callers are expected to handle
    a None unit by skipping conversion.

    When `record=True` (default), this function also records the resolution
    into the detection layer so /admin/data-sources surfaces it. Set
    `record=False` when the caller will do its own recording.

    `raw_value` and `device_id` are forwarded into the detection records
    (used by cross-reference buffer population).
    """
    # Late imports to avoid a circular dependency: ha_fordpass.adapter imports
    # this module.
    from web.services.sources.ha_fordpass import adapter as ha_fordpass

    entity_pattern = ha_fordpass._entity_pattern(entity_id) if entity_id else ""
    attrs = (new_state or {}).get("attributes") or {} if isinstance(new_state, dict) else {}

    # --- 1. declared via FIELD_CONTRACTS ---------------------------------
    contract = ha_fordpass.lookup_contract(entity_pattern, attribute)
    if contract is not None:
        try:
            # adapter._resolve_source_unit returns (unit, method). The method
            # returned from the adapter (declared / ha_unit_system_converted /
            # read_time_uom / declared_fallback) is richer than the detection
            # layer's "declared" bucket — we still label the detection record
            # "declared" here because the contract existed, but the adapter's
            # observability path captures the nuance per-event.
            resolved, _adapter_method = ha_fordpass._resolve_source_unit(
                contract, new_state, ha_config
            )
        except Exception:
            resolved = contract.source_unit
        if record and resolved:
            record_declared(entity_pattern, attribute, resolved, raw_value)
        return resolved, _METHOD_DECLARED, _confidence_for_declared()

    # --- 2. read_time_uom ------------------------------------------------
    raw_uom = attrs.get("unit_of_measurement")
    normalized = ha_fordpass._normalize_uom_string(raw_uom) if raw_uom else None
    # Only accept the event's UoM when it matches the field_type. The event
    # attribute `unit_of_measurement` is the entity's STATE unit; nested
    # attributes (e.g. elveh.tripAmbientTemp on a `mi`-unit state) have
    # independent semantics that the state UoM cannot describe.
    if normalized and _unit_matches_field_type(normalized, field_type):
        if record:
            record_read_time(
                entity_pattern, attribute, normalized, raw_value, device_id=device_id
            )
        return normalized, _METHOD_READ_TIME, _confidence_for_read_time()

    # --- 3. device_class + ha_config.unit_system ------------------------
    device_class = attrs.get("device_class")
    unit_system = (ha_config or {}).get("unit_system") if isinstance(ha_config, dict) else None
    inferred = _unit_from_device_class(device_class, unit_system, field_type)
    if inferred is not None:
        if record:
            record_device_class_inference(
                entity_pattern,
                attribute,
                inferred,
                device_class=device_class or "",
                ha_unit_system=unit_system,
                raw_value=raw_value,
                device_id=device_id,
            )
        return inferred, _METHOD_DEVICE_CLASS, _confidence_for_device_class()

    # --- 4. cross_reference / prior detection cache ---------------------
    existing = _records.get(_key(entity_pattern, attribute))
    if (
        existing is not None
        and existing.detected_unit
        and _unit_matches_field_type(existing.detected_unit, field_type)
    ):
        # Any higher-priority prior record wins.
        if existing.method == _METHOD_CROSS_REF:
            if record:
                # Don't overwrite the cross_reference record's math; just
                # push the raw_value into the device buffer so subsequent
                # canonical events can refresh it.
                if device_id and raw_value is not None:
                    _buffer_add(device_id, entity_pattern, attribute, raw_value)
            return existing.detected_unit, _METHOD_CROSS_REF, existing.confidence
        if existing.method in (_METHOD_READ_TIME, _METHOD_DEVICE_CLASS, _METHOD_DECLARED):
            # Previously we saw a higher-confidence signal for this source;
            # honour it even though this event lacks signals of its own.
            return existing.detected_unit, existing.method, existing.confidence

    # --- 5. unknown ------------------------------------------------------
    reason = _compose_unknown_reason(raw_uom, device_class, unit_system, existing)
    if record:
        record_unknown(
            entity_pattern, attribute, raw_value, reason, device_id=device_id
        )
    return None, _METHOD_UNKNOWN, "low"


def convert_with_resolved_unit(
    *,
    raw_value: Any,
    resolved_unit: Optional[str],
    method: str,
    confidence: str,
    entity_id: Optional[str],
    attribute: str,
) -> Optional[float]:
    """Thin wrapper over to_metric for handlers that already called resolve_source_unit.

    When `resolved_unit` is None (method='unknown') we return None — the caller
    must skip the field. resolve_source_unit already recorded the unknown
    reason into the detection layer.
    """
    # Late import to avoid a circular dep with web.services.units.to_metric,
    # which is permitted here but keeps module import graph clean.
    from web.services.units.to_metric import to_metric, UnknownSourceUnit

    if raw_value is None:
        return None
    if resolved_unit is None:
        return None
    try:
        return to_metric(raw_value, resolved_unit)
    except UnknownSourceUnit:
        logger.warning(
            "UnknownSourceUnit on %s.%s (value=%r, resolved=%r, method=%s); skipping",
            entity_id or "<unknown>",
            attribute,
            raw_value,
            resolved_unit,
            method,
        )
        return None


def snapshot() -> list[DetectionRecord]:
    """Return all known detection records (copy)."""
    return [replace(r) for r in _records.values()]


def clear() -> None:
    """Reset all detection state. Tests only."""
    _records.clear()
    _recent_reads.clear()


# ---------------------------------------------------------------------------
# Field-type-specific observation math
# ---------------------------------------------------------------------------


def _detect_from_observation(
    field_type: str,
    target_raw: float,
    canonical_value: float,
    canonical_unit: str,
) -> tuple[Optional[str], Optional[float], Optional[str]]:
    """Given canonical + target reads, derive (detected_unit, ratio, reason).

    Returns (unit, ratio, None) when detection succeeded.
    Returns (None, ratio_or_None, reason_string) on failure.
    """
    if field_type == "distance":
        return _detect_distance(target_raw, canonical_value, canonical_unit)
    if field_type == "temperature":
        return _detect_temperature(target_raw, canonical_value, canonical_unit)
    # Extensible: pressure / speed / other field types can be added here.
    return (None, None, f"unsupported field_type={field_type!r}")


def _detect_distance(
    target_raw: float,
    canonical_value: float,
    canonical_unit: str,
) -> tuple[Optional[str], Optional[float], Optional[str]]:
    """Distance cross-ref: compare target / canonical ratio.

    canonical_unit is expected to be 'km'. Anomaly-flag anything else since
    our canonical sources are always metric.
    """
    if canonical_value == 0:
        return (None, None, "canonical value is zero; cannot compute ratio")
    ratio = target_raw / canonical_value

    if abs(ratio - 1.0) <= _DISTANCE_RATIO_TOLERANCE:
        # Target equals canonical value.
        if canonical_unit == "km":
            return ("km", ratio, None)
        return (canonical_unit, ratio, None)

    if abs(ratio - _MI_FACTOR) <= _DISTANCE_RATIO_TOLERANCE:
        # Target is canonical_km * 0.621 — i.e. the same distance expressed in mi.
        return ("mi", ratio, None)

    if abs(ratio - _KM_PER_MILE) <= _DISTANCE_RATIO_TOLERANCE:
        # Anomaly: target is 1.609x canonical — shouldn't happen since
        # canonical is always metric. Flag for review.
        return (
            None,
            ratio,
            (
                f"ratio={ratio:.3f} matches km/mi factor in the reverse "
                "direction; canonical was expected to be metric"
            ),
        )

    return (
        None,
        ratio,
        f"ratio={ratio:.3f} did not match known distance conversion factor",
    )


def _detect_temperature(
    target_raw: float,
    canonical_value: float,
    canonical_unit: str,
) -> tuple[Optional[str], Optional[float], Optional[str]]:
    """Temperature cross-ref: test both C and F interpretations directly.

    Ratio analysis doesn't work (F<->C is linear-with-offset, not
    multiplicative). Instead, compute the F->C conversion of the raw read
    and compare against the canonical; if it matches within ±1°, target is
    degF. If the raw read already matches the canonical within ±1°, target
    is degC. Otherwise unknown.
    """
    if canonical_unit not in ("degC", "°C"):
        return (None, None, f"unsupported canonical temperature unit {canonical_unit!r}")

    expected_c = (target_raw - 32.0) * 5.0 / 9.0
    if abs(expected_c - canonical_value) < 1.0:
        return ("degF", None, None)
    if abs(target_raw - canonical_value) < 1.0:
        return ("degC", None, None)
    return (
        None,
        None,
        (
            f"neither raw={target_raw} nor F->C={expected_c:.2f} matched "
            f"canonical={canonical_value}; cannot determine unit"
        ),
    )
