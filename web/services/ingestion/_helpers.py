"""Shared HA-generic helpers used by ingestion runtime + source adapters.

Both `web/services/sources/ha_fordpass/handlers.py` and
`web/services/sources/ha_gas_price/handlers.py` import from here so the
two adapters stay thin.

Functions in this module are HA-event-shape generic (no FordPass / gas-specific
logic). The single edge case: `_read_time_uom` and `_convert_with_uom` import
`_normalize_uom_string` from `web.services.sources.ha_fordpass.adapter` — that
edge is preserved (FordPass owns the canonical UOM normalization table). Future
adapters that need their own UOM normalizer can swap the import or pass the
normalizer in.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from web.services.sources.ha_fordpass import adapter as ha_fordpass
from web.services.units import detection
from web.services.units.to_metric import UnknownSourceUnit, to_metric

logger = logging.getLogger("lightningrod.ingestion.helpers")


# ---------------------------------------------------------------------------
# Read-time unit_of_measurement helpers
# ---------------------------------------------------------------------------

def _read_time_uom(new_state: dict) -> str | None:
    """Return the normalized source_unit from new_state.attributes.unit_of_measurement.

    Returns None when the event carries no UoM attribute. Callers use this
    per-event value, never a process-global flag. When the result
    is None the caller must decide whether to skip (preferred for unit-ful
    fields) or passthrough (only for fields it knows are already metric).
    """
    if not isinstance(new_state, dict):
        return None
    attrs = new_state.get("attributes") or {}
    raw = attrs.get("unit_of_measurement")
    if raw is None:
        return None
    return ha_fordpass._normalize_uom_string(raw)


def _convert_with_uom(
    raw_value: Any,
    source_unit: str | None,
    field_name: str,
    entity_id: str | None = None,
    *,
    entity_pattern: str | None = None,
    attribute: str | None = None,
    device_id: str | None = None,
) -> float | None:
    """Convert raw_value from source_unit to metric; log + return None on failure.

    Used by handlers for fields not represented in FIELD_CONTRACTS today.
    When `source_unit` is None, the event carries no UoM attribute. In that
    case we first consult the unit-detection layer (which may have
    cross-referenced a unit from a sibling entity), and if that also has no
    answer we warn-log and return None.

    `entity_pattern` / `attribute` are passed through to the detection layer
    so unit-of-measurement observations for this source get recorded. If a
    caller omits them we derive `entity_pattern` from `entity_id`, and fall
    back to using `field_name` as the attribute key.
    """
    if raw_value is None:
        return None

    # Derive detection keys. Callers may pass entity_pattern/attribute
    # explicitly; if not, derive them from entity_id + field_name.
    if entity_pattern is None and entity_id is not None:
        entity_pattern = ha_fordpass._entity_pattern(entity_id)
    if attribute is None:
        attribute = field_name or ""

    if source_unit is None:
        # Try the detection layer as a last-resort fallback.
        resolved = None
        if entity_pattern is not None:
            resolved = detection.resolve_unit(entity_pattern, attribute or "")
        if resolved is not None:
            try:
                converted = to_metric(raw_value, resolved)
            except UnknownSourceUnit:
                logger.warning(
                    "detection.resolve_unit returned %r but to_metric rejected it "
                    "on %s.%s (value=%r); skipping",
                    resolved,
                    entity_id or "<unknown>",
                    field_name,
                    raw_value,
                )
                return None
            logger.debug(
                "read-time UoM absent on %s.%s; detection layer resolved %r",
                entity_id or "<unknown>",
                field_name,
                resolved,
            )
            return converted
        logger.warning(
            "read-time UoM missing on %s for field %s; skipping value %r "
            "(adapter will not assume metric)",
            entity_id or "<unknown>",
            field_name,
            raw_value,
        )
        if entity_pattern is not None:
            detection.record_unknown(
                entity_pattern,
                attribute or "",
                raw_value,
                reason="no unit_of_measurement on event",
                device_id=device_id,
            )
        return None

    try:
        converted = to_metric(raw_value, source_unit)
    except UnknownSourceUnit:
        logger.warning(
            "UnknownSourceUnit on %s.%s: value=%r source_unit=%r; skipping",
            entity_id or "<unknown>",
            field_name,
            raw_value,
            source_unit,
        )
        return None

    if entity_pattern is not None:
        detection.record_read_time(
            entity_pattern,
            attribute or "",
            source_unit,
            raw_value,
            device_id=device_id,
        )
    return converted


# ---------------------------------------------------------------------------
# HA state object helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _get_state_value(new_state: dict) -> str | None:
    """Extract state value from HA state object."""
    if not new_state:
        return None
    return new_state.get("state")


def _get_attributes(new_state: dict) -> dict:
    """Extract attributes dict from HA state object."""
    if not new_state:
        return {}
    return new_state.get("attributes", {})


def _get_unit_system(ha_config: dict) -> dict:
    """Extract HA unit system dict from config.

    Returns a dict shaped {"length": ..., "temperature": ..., "volume": ...,
    "mass": ...} when HA provides one, or an empty dict otherwise.

    Consumers: `detection.resolve_source_unit()` uses this dict (plus
    device_class) to infer a source unit when the event itself carries no
    unit_of_measurement attribute.
    """
    if not isinstance(ha_config, dict):
        return {}
    us = ha_config.get("unit_system")
    if isinstance(us, dict):
        return dict(us)
    # Older HA ships a flat "metric"/"imperial" string. Keep it addressable
    # as a dict with a synthetic marker so the resolver can still see it.
    if isinstance(us, str):
        return {"_flat": us}
    return {}


def _resolve_and_convert(
    *,
    raw_value: Any,
    entity_id: str,
    attribute: str,
    new_state: dict,
    ha_config: dict,
    field_type: str,
    device_id: str | None,
) -> float | None:
    """Resolve the source unit via HA signals, then convert via to_metric.

    Convenience wrapper for vehicle-status and battery-status handlers. Hides
    the resolve -> convert -> record chain so handler code stays readable.
    """
    if raw_value is None:
        return None
    resolved_unit, method, confidence = detection.resolve_source_unit(
        entity_id=entity_id,
        attribute=attribute,
        new_state=new_state,
        ha_config=ha_config,
        field_type=field_type,
        raw_value=raw_value,
        device_id=device_id,
    )
    return detection.convert_with_resolved_unit(
        raw_value=raw_value,
        resolved_unit=resolved_unit,
        method=method,
        confidence=confidence,
        entity_id=entity_id,
        attribute=attribute,
    )


def _get_event_timestamp(
    new_state: dict, keys: tuple[str, ...] = ("last_changed", "last_updated")
) -> datetime | None:
    """Extract event timestamp from HA state object.

    Tries each key of `keys` in order, parsing ISO format with timezone.
    Returns None if no valid timestamp found. Callers that must distinguish
    every update pass ("last_updated", "last_changed") instead: HA freezes
    last_changed while only attributes change.
    """
    for key in keys:
        val = new_state.get(key) if new_state else None
        if val:
            try:
                if isinstance(val, str):
                    if val.endswith("Z"):
                        val = val[:-1] + "+00:00"
                    return datetime.fromisoformat(val)
                if isinstance(val, datetime):
                    return val
            except (ValueError, TypeError):
                continue
    return None
