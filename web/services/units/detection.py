"""Unit detection layer.

Records and resolves the unit-of-measurement for every (entity, attribute)
data source we ingest from Home Assistant.

Four detection methods, priority order when resolving a unit:
  1. declared         — source appears in FIELD_CONTRACTS (events/metrics/
                        energytransferlogentry fields)
  2. read_time_uom    — event carried new_state.attributes.unit_of_measurement
  3. cross_reference  — ratio-matched against a known-metric canonical value
                        from events/metrics for the same device + semantic
                        field in a recent time window
  4. unknown          — none of the above; value recorded with flag for
                        later user override (future work)

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
_METHOD_CROSS_REF = "cross_reference"
_METHOD_UNKNOWN = "unknown"

_METHOD_PRIORITY = {
    _METHOD_DECLARED: 0,
    _METHOD_READ_TIME: 1,
    _METHOD_CROSS_REF: 2,
    _METHOD_UNKNOWN: 3,
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

    Priority: declared < read_time_uom < cross_reference < unknown
    (lower number wins). Same priority -> keep latest (overwrite).
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
