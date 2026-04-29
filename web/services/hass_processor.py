"""Home Assistant sensor event processor.

Dispatches HA state_changed events to registered sensor handlers. Maps
ha-fordpass entities to database records: charging sessions from
energytransferlogentry, vehicle status snapshots, battery status updates,
and trip metrics. Also handles gas price sensor events from arbitrary
entity_ids configured in app_settings.

Unit conversion logic has been removed from this module. Conversion now
routes through `web.services.sources.ha_fordpass.adapter` (FIELD_CONTRACTS
registry) plus `web.services.units.to_metric`.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from web.services.sources.ha_fordpass import adapter as ha_fordpass
from web.services.units import detection
from web.services.units.to_metric import UnknownSourceUnit, to_metric

logger = logging.getLogger("lightningrod.hass.processor")


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
# Slug extractor
# ---------------------------------------------------------------------------


def extract_slug(entity_id: str) -> str | None:
    """Extract sensor slug from entity_id pattern sensor.fordpass_{vin}_{slug}.

    Example: sensor.fordpass_1ftvw1el6pwg05841_soc -> soc
    """
    # entity_id format: sensor.fordpass_{vin}_{slug}
    # Split on "." first, then split the sensor part
    if not entity_id or not entity_id.startswith("sensor.fordpass_"):
        return None
    # Remove "sensor.fordpass_" prefix, then split on "_"
    remainder = entity_id[len("sensor.fordpass_"):]
    # VIN is next, then slug (slug may contain underscores -- unlikely but safe)
    parts = remainder.split("_", 1)
    if len(parts) >= 2:
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# Sensor handler registry
# ---------------------------------------------------------------------------

SENSOR_HANDLERS: dict[str, Callable] = {}


def handles(*slugs):
    """Decorator to register a handler for one or more sensor slugs."""
    def decorator(fn):
        for slug in slugs:
            SENSOR_HANDLERS[slug] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Pending vehicle/battery status batching
# ---------------------------------------------------------------------------

# Accumulates fields until flushed (on 'lastrefresh' or timeout)
_pending_vehicle_status: dict[str, dict[str, Any]] = {}
_pending_vehicle_status_ts: dict[str, float] = {}  # device_id -> last_update epoch

_pending_battery_status: dict[str, dict[str, Any]] = {}
_pending_battery_status_ts: dict[str, float] = {}

# Track last-seen trip values per device to detect new trips
_last_trip_values: dict[str, dict[str, Any]] = {}

_FLUSH_TIMEOUT = 30  # seconds


async def _flush_vehicle_status(device_id: str, db) -> None:
    """Write accumulated vehicle status fields as a single EVVehicleStatus row."""
    fields = _pending_vehicle_status.pop(device_id, None)
    _pending_vehicle_status_ts.pop(device_id, None)
    if not fields:
        return

    from db.models.vehicle_status import EVVehicleStatus

    record = EVVehicleStatus(
        device_id=device_id,
        recorded_at=fields.pop("_recorded_at", datetime.now(UTC)),
        source_system="home_assistant",
        **fields,
    )
    db.add(record)
    logger.debug("Flushed vehicle status for %s (%d fields)", device_id, len(fields))


async def _flush_battery_status(device_id: str, db) -> None:
    """Write accumulated battery status fields as a single EVBatteryStatus row."""
    fields = _pending_battery_status.pop(device_id, None)
    _pending_battery_status_ts.pop(device_id, None)
    if not fields:
        return

    from db.models.battery_status import EVBatteryStatus

    record = EVBatteryStatus(
        device_id=device_id,
        recorded_at=fields.pop("_recorded_at", datetime.now(UTC)),
        source_system="home_assistant",
        ingest_schema_version=ha_fordpass.INGEST_SCHEMA_VERSION,
        **fields,
    )
    db.add(record)
    logger.debug("Flushed battery status for %s (%d fields)", device_id, len(fields))


async def _find_matching_trip(
    db,
    device_id: str,
    distance: float | None,
    energy_consumed: float | None,
):
    """Query for an existing EVTripMetrics row matching this trip.

    Match key: device_id + distance within ±0.01 km + energy_consumed within
    ±0.01 kWh + end_time within the last 24 hours.  Returns the first matching
    row or None.

    Both the _elveh handler (handle_battery_status) and the _events handler
    (adapter._handle_events_entity) call this before inserting so that when
    both fire for the same physical trip only one row is written — the second
    handler enriches the existing row instead of duplicating it.
    """
    if distance is None or energy_consumed is None:
        return None

    from datetime import timedelta

    from sqlalchemy import and_, select

    from db.models.trip_metrics import EVTripMetrics

    cutoff = datetime.now(UTC) - timedelta(hours=24)
    result = await db.execute(
        select(EVTripMetrics)
        .where(
            and_(
                EVTripMetrics.device_id == device_id,
                EVTripMetrics.distance.between(distance - 0.01, distance + 0.01),
                EVTripMetrics.energy_consumed.between(
                    energy_consumed - 0.01, energy_consumed + 0.01
                ),
                EVTripMetrics.end_time >= cutoff,
            )
        )
        .order_by(EVTripMetrics.end_time.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


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

    Convenience wrapper used by the legacy vehicle-status and battery-status
    handlers. Hides the resolve -> convert -> record chain so handler code
    stays readable.
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


def _get_event_timestamp(new_state: dict) -> datetime | None:
    """Extract event timestamp from HA state object.

    Tries last_changed, then last_updated, parsing ISO format with timezone.
    Returns None if no valid timestamp found.
    """
    for key in ("last_changed", "last_updated"):
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


# ---------------------------------------------------------------------------
# Adapter-delegated handler (metrics + events entities)
# ---------------------------------------------------------------------------

@handles("metrics", "events")
async def handle_via_adapter(slug, new_state, ha_config, device_id, db):
    """Delegate _metrics and _events entities to ha_fordpass.process_event.

    These slugs are the metric-canonical sources for battery status
    (xevBatteryRange / xevBatteryMaximumRange) and trip metrics
    (xev-key-off-trip-segment-data). The adapter owns the FIELD_CONTRACTS
    routing; this dispatcher simply hands off.
    """
    entity_id = f"sensor.fordpass_{device_id}_{slug}"
    await ha_fordpass.process_event(entity_id, new_state, db, ha_config)


# ---------------------------------------------------------------------------
# Vehicle status handler
# ---------------------------------------------------------------------------

@handles(
    "odometer", "speed", "acceleratorpedalposition", "brakepedalstatus",
    "braketorque", "gearleverposition", "parkingbrakestatus", "ignitionstatus",
    "yawrate", "torqueattransmission", "wheeltorquestatus", "enginespeed",
    "cabintemperature", "coolanttemp", "outsidetemp", "acceleration",
    "deepsleep", "deviceconnectivity", "evccstatus", "lastrefresh",
)
async def handle_vehicle_status(slug, new_state, ha_config, device_id, db):
    """Handle vehicle telemetry and status sensors.

    Accumulates fields in a pending dict and flushes on 'lastrefresh'
    or after a timeout to produce one EVVehicleStatus row per batch.

    Distance and temperature conversions resolve the source unit per-event
    via `detection.resolve_source_unit` (read-time UoM -> device_class +
    ha_config.unit_system -> cross-reference cache -> unknown). No hardcoded
    default unit is ever assumed; values with no resolvable unit are
    dropped from the pending dict and surfaced on /admin/data-sources.
    """
    state_val = _get_state_value(new_state)
    entity_id = f"sensor.fordpass_{device_id}_{slug}"

    # Initialize pending dict for this device if needed
    if device_id not in _pending_vehicle_status:
        _pending_vehicle_status[device_id] = {}
        _pending_vehicle_status[device_id]["_recorded_at"] = datetime.now(UTC)

    pending = _pending_vehicle_status[device_id]

    def _distance_converter(v):
        return _resolve_and_convert(
            raw_value=v,
            entity_id=entity_id,
            attribute="",
            new_state=new_state,
            ha_config=ha_config,
            field_type="distance",
            device_id=device_id,
        )

    def _temp_converter(v):
        return _resolve_and_convert(
            raw_value=v,
            entity_id=entity_id,
            attribute="",
            new_state=new_state,
            ha_config=ha_config,
            field_type="temperature",
            device_id=device_id,
        )

    # Map slug to field. Numeric + distance/temp converters route through
    # web.services.units.to_metric via _convert_with_uom; string / _safe_float
    # converters remain pure.
    slug_field_map = {
        "odometer": ("odometer", _distance_converter),
        "speed": ("speed", _distance_converter),
        "acceleratorpedalposition": ("accelerator_position", _safe_float),
        "brakepedalstatus": ("brake_status", str),
        "braketorque": ("brake_torque", _safe_float),
        "gearleverposition": ("gear_position", str),
        "parkingbrakestatus": ("parking_brake", str),
        "ignitionstatus": ("ignition_status", str),
        "yawrate": ("yaw_rate", _safe_float),
        "torqueattransmission": ("torque_at_transmission", _safe_float),
        "wheeltorquestatus": ("wheel_torque_status", str),
        "acceleration": ("acceleration", _safe_float),
        "deepsleep": ("deep_sleep_status", str),
        "deviceconnectivity": ("device_connectivity", str),
        "evccstatus": ("evcc_status", str),
        "outsidetemp": ("outside_temperature", _temp_converter),
        "cabintemperature": ("cabin_temperature", _temp_converter),
    }

    if slug == "lastrefresh":
        # lastrefresh triggers a flush of accumulated vehicle status
        now = time.time()
        _pending_vehicle_status_ts[device_id] = now

        # Also flush battery status on lastrefresh
        await _flush_vehicle_status(device_id, db)
        await _flush_battery_status(device_id, db)
        logger.debug("lastrefresh received, flushed vehicle + battery status for %s", device_id)
        return

    if slug in slug_field_map:
        field_name, converter = slug_field_map[slug]
        if state_val is not None and state_val not in ("unknown", "unavailable"):
            pending[field_name] = converter(state_val)

    if slug == "outsidetemp":
        await ha_fordpass.process_event(entity_id, new_state, db, ha_config)

    # Check timeout-based flush
    _pending_vehicle_status_ts.setdefault(device_id, time.time())
    if time.time() - _pending_vehicle_status_ts[device_id] > _FLUSH_TIMEOUT:
        await _flush_vehicle_status(device_id, db)


# ---------------------------------------------------------------------------
# Battery status handler
# ---------------------------------------------------------------------------

@handles("soc", "elveh", "battery", "lastenergyconsumed")
async def handle_battery_status(slug, new_state, ha_config, device_id, db):
    """Handle battery-related sensors (HV SOC, range, 12V level, energy consumed).

    Accumulates fields similar to vehicle status batching.

    In the legacy adapter path this handler also ingested trip data from
    `elveh.trip*` attributes. Production ingestion now prefers events-entity
    trip data through `ha_fordpass.adapter.process_event`. This handler keeps
    an elveh fallback for installations without that entity.

    Unit resolution for every unit-bearing field goes through
    `detection.resolve_source_unit` — no hardcoded fallback units. When
    resolution fails (no UoM, no device_class, no unit_system, no cross-ref),
    the field is dropped and surfaced on /admin/data-sources.
    """
    state_val = _get_state_value(new_state)
    attrs = _get_attributes(new_state)
    entity_id = f"sensor.fordpass_{device_id}_{slug}"

    # Initialize pending dict for this device if needed
    if device_id not in _pending_battery_status:
        _pending_battery_status[device_id] = {}
        _pending_battery_status[device_id]["_recorded_at"] = datetime.now(UTC)

    pending = _pending_battery_status[device_id]

    if slug == "soc":
        # HV battery state of charge (%)
        pending["hv_battery_soc"] = _safe_float(state_val)
        # batteryRange is an elveh-shaped fallback attribute on the soc entity.
        # The soc state UoM is "%", which is NOT the batteryRange attribute's
        # unit. Fall through to the detection-layer cross-reference path:
        # metrics.xevBatteryRange provides the canonical km value that will
        # back-fill the unit for this source. If detection has no prior
        # signal the value is dropped, not silently defaulted to "mi".
        battery_range = attrs.get("batteryRange")
        if battery_range is not None:
            # The soc entity's unit_of_measurement is "%", which isn't a
            # distance unit. _normalize_uom_string("%") returns None, so the
            # resolver falls through to device_class + ha_config -> cross-ref
            # against metrics.xevBatteryRange -> unknown. No silent "mi" default.
            converted = _resolve_and_convert(
                raw_value=battery_range,
                entity_id=entity_id,
                attribute="batteryRange",
                new_state=new_state,
                ha_config=ha_config,
                field_type="distance",
                device_id=device_id,
            )
            if converted is not None:
                pending["hv_battery_range"] = converted

    elif slug == "elveh":
        # EV range (state value) — resolver reads read-time UoM from the event.
        if state_val not in (None, "unknown", "unavailable"):
            converted = _resolve_and_convert(
                raw_value=state_val,
                entity_id=entity_id,
                attribute="",
                new_state=new_state,
                ha_config=ha_config,
                field_type="distance",
                device_id=device_id,
            )
            if converted is not None:
                pending["hv_battery_range"] = converted
        # Rich battery attributes (SI-already — no conversion needed, no
        # FIELD_CONTRACTS entry per _EXEMPTIONS in test_contract_coverage).
        hv_voltage = _safe_float(attrs.get("batteryVoltage"))
        hv_amperage = _safe_float(attrs.get("batteryAmperage"))
        hv_kw = _safe_float(attrs.get("batterykW"))
        # FordPass `maximumBatteryCapacity` is the GROSS pack capacity
        # (total installed cell kWh). Battery health math on /battery
        # compares against ev_vehicles.battery_gross_capacity_kwh.
        hv_capacity = _safe_float(attrs.get("maximumBatteryCapacity"))
        hv_actual_soc = _safe_float(attrs.get("batteryActualCharge"))
        motor_voltage = _safe_float(attrs.get("motorVoltage"))
        motor_amperage = _safe_float(attrs.get("motorAmperage"))
        motor_kw = _safe_float(attrs.get("motorkW"))
        if hv_voltage is not None:
            pending["hv_battery_voltage"] = hv_voltage
        if hv_amperage is not None:
            pending["hv_battery_amperage"] = hv_amperage
        if hv_kw is not None:
            pending["hv_battery_kw"] = hv_kw
        if hv_capacity is not None:
            pending["hv_battery_capacity"] = hv_capacity
        if hv_actual_soc is not None:
            pending["hv_battery_actual_soc"] = hv_actual_soc
        if motor_voltage is not None:
            pending["motor_voltage"] = motor_voltage
        if motor_amperage is not None:
            pending["motor_amperage"] = motor_amperage
        if motor_kw is not None:
            pending["motor_kw"] = motor_kw
        # Max range from attributes — unit-bearing, resolved via HA signals.
        max_range = _safe_float(attrs.get("maximumBatteryRange"))
        if max_range is not None:
            converted = _resolve_and_convert(
                raw_value=max_range,
                entity_id=entity_id,
                attribute="maximumBatteryRange",
                new_state=new_state,
                ha_config=ha_config,
                field_type="distance",
                device_id=device_id,
            )
            if converted is not None:
                pending["hv_battery_max_range"] = converted

        # --- Trip attributes from elveh entity (legacy fallback) ---
        # Distance + temperature converters resolve per-attribute via the
        # detection layer so each attribute can independently benefit from
        # device_class + unit_system + cross-reference signals. No hardcoded
        # defaults; attributes without resolvable units are dropped.

        def _d(v, _attr: str = ""):
            return _resolve_and_convert(
                raw_value=v,
                entity_id=entity_id,
                attribute=_attr,
                new_state=new_state,
                ha_config=ha_config,
                field_type="distance",
                device_id=device_id,
            )

        def _t(v, _attr: str = ""):
            return _resolve_and_convert(
                raw_value=v,
                entity_id=entity_id,
                attribute=_attr,
                new_state=new_state,
                ha_config=ha_config,
                field_type="temperature",
                device_id=device_id,
            )

        # Adapter-routed converters for elveh-sourced fields that DO have a
        # FIELD_CONTRACTS entry. Routing through ha_fordpass.convert records
        # the conversion in adapter._last_seen_raw so /admin/data-sources
        # shows elveh observations alongside metrics/events ones. Contracts
        # for elveh state-unit distance fields use read-time UoM resolution
        # internally via adapter._resolve_source_unit.
        _elveh_pattern = "sensor.fordpass_{vin}_elveh"
        _efficiency_contract = ha_fordpass.lookup_contract(_elveh_pattern, "tripEfficiency")
        _range_regen_contract = ha_fordpass.lookup_contract(_elveh_pattern, "tripRangeRegenerated")

        def _efficiency_conv(v):
            if _efficiency_contract is None:
                return _d(v)
            return ha_fordpass.convert(_efficiency_contract, v, new_state, ha_config)

        def _range_regen_conv(v):
            if _range_regen_contract is None:
                return _d(v)
            return ha_fordpass.convert(_range_regen_contract, v, new_state, ha_config)

        trip_attr_map = {
            "tripDistanceTraveled": ("distance", _d),
            "tripDuration": ("duration", _safe_float),
            "tripEnergyConsumed": ("energy_consumed", _safe_float),
            "tripEfficiency": ("efficiency", _efficiency_conv),
            "tripDrivingScore": ("driving_score", _safe_float),
            "tripSpeed": ("speed_score", _safe_float),
            "tripAcceleration": ("acceleration_score", _safe_float),
            "tripDeceleration": ("deceleration_score", _safe_float),
            "tripAmbientTemp": ("ambient_temp", _t),
            "tripOutsideAirAmbientTemp": ("outside_air_temp", _t),
            "tripCabinTemp": ("cabin_temp", _t),
            "tripRangeRegenerated": ("range_regenerated", _range_regen_conv),
            "tripElectricalEfficiency": ("electrical_efficiency", _safe_float),
        }

        trip_fields = {}
        # Converters that want the HA attribute name (for detection-layer
        # bookkeeping) are _d and _t. Others accept just the value.
        _attribute_aware = {_d, _t}
        for attr_key, (field_name, converter) in trip_attr_map.items():
            val = attrs.get(attr_key)
            if val is not None:
                if converter in _attribute_aware:
                    converted = converter(val, attr_key)
                else:
                    converted = converter(val)
                if converted is not None:
                    trip_fields[field_name] = converted

        if trip_fields.get("distance") or trip_fields.get("energy_consumed"):
            last = _last_trip_values.get(device_id, {})
            # Check if trip data actually changed (new trip)
            is_new = (
                not last
                or last.get("distance") != trip_fields.get("distance")
                or last.get("duration") != trip_fields.get("duration")
                or last.get("efficiency") != trip_fields.get("efficiency")
            )
            if is_new:
                _last_trip_values[device_id] = trip_fields.copy()
                event_ts = _get_event_timestamp(new_state)
                end_time = event_ts or datetime.now(UTC)
                start_time = None
                if trip_fields.get("duration") and end_time:
                    from datetime import timedelta
                    start_time = end_time - timedelta(minutes=float(trip_fields["duration"]))

                # Match-and-enrich: if an _events row already exists for this
                # trip, enrich it with elveh-owned fields rather than inserting
                # a duplicate.
                from db.models.trip_metrics import EVTripMetrics

                existing = await _find_matching_trip(
                    db,
                    device_id,
                    trip_fields.get("distance"),
                    trip_fields.get("energy_consumed"),
                )
                if existing is not None:
                    # Enrich the existing row with elveh-owned fields.
                    # Scores and regen always overwrite (elveh is canonical).
                    # efficiency and duration only fill NULLs.
                    # Do NOT overwrite temps — events values are canonical °C.
                    for col in (
                        "range_regenerated",
                        "driving_score",
                        "speed_score",
                        "acceleration_score",
                        "deceleration_score",
                        "electrical_efficiency",
                    ):
                        val = trip_fields.get(col)
                        if val is not None:
                            setattr(existing, col, val)
                    if existing.efficiency is None and trip_fields.get("efficiency") is not None:
                        existing.efficiency = trip_fields["efficiency"]
                    if existing.duration is None and trip_fields.get("duration") is not None:
                        existing.duration = trip_fields["duration"]
                    logger.info(
                        "Enriched existing trip row %s for %s with elveh scores/regen",
                        existing.id,
                        device_id,
                    )
                else:
                    trip_record = EVTripMetrics(
                        device_id=device_id,
                        start_time=start_time,
                        end_time=end_time,
                        recorded_at=datetime.now(UTC),
                        is_complete=True,
                        source_system="homeassistant",
                        original_timestamp=event_ts,
                        ingest_schema_version=ha_fordpass.INGEST_SCHEMA_VERSION,
                        **trip_fields,
                    )
                    db.add(trip_record)
                    await db.commit()
                    logger.info(
                        "Trip recorded for %s: %s km, %s min",
                        device_id,
                        trip_fields.get("distance"),
                        trip_fields.get("duration"),
                    )

    elif slug == "battery":
        # 12V battery level (%)
        pending["lv_battery_level"] = _safe_float(state_val)
        # 12V voltage from attributes (SI-already)
        lv_voltage = _safe_float(attrs.get("batteryVoltage"))
        if lv_voltage is not None:
            pending["lv_battery_voltage"] = lv_voltage

    elif slug == "lastenergyconsumed":
        # Energy consumed on last trip (Wh) -- log but no direct column
        energy_wh = _safe_float(state_val)
        if energy_wh is not None:
            try:
                energy_kwh = to_metric(energy_wh, "Wh")
            except UnknownSourceUnit:
                energy_kwh = None
            logger.debug("Last energy consumed: %s kWh", energy_kwh)

    # Check timeout-based flush
    _pending_battery_status_ts.setdefault(device_id, time.time())
    if time.time() - _pending_battery_status_ts[device_id] > _FLUSH_TIMEOUT:
        await _flush_battery_status(device_id, db)


# ---------------------------------------------------------------------------
# Charging live status handler
# ---------------------------------------------------------------------------

@handles("elvehcharging", "elvehplug")
async def handle_charging_live(slug, new_state, ha_config, device_id, db):
    """Handle charging state change events (plug/charge status).

    Logs state transitions for debugging. These supplement session data
    but don't create database records themselves.
    """
    state_val = _get_state_value(new_state)
    attrs = _get_attributes(new_state)

    if slug == "elvehcharging":
        logger.info(
            "Charging state changed: %s (plug=%s, station=%s, type=%s, kW=%s)",
            state_val,
            attrs.get("plugStatus"),
            attrs.get("chargingStationStatus"),
            attrs.get("chargingType"),
            attrs.get("chargingkW"),
        )
        entity_id = f"sensor.fordpass_{device_id}_{slug}"
        await ha_fordpass.process_event(entity_id, new_state, db, ha_config)
    elif slug == "elvehplug":
        logger.info(
            "Plug state changed: %s (station=%s, type=%s)",
            state_val,
            attrs.get("ChargingStationStatus"),
            attrs.get("ChargingType"),
        )


# ---------------------------------------------------------------------------
# GPS handler
# ---------------------------------------------------------------------------

@handles("gps")
async def handle_gps(slug, new_state, ha_config, device_id, db):
    """Handle GPS location updates.

    Parses GPS state (object-string with latitude, longitude)
    and stores EVLocation snapshots with deduplication (60s + 50m).
    """
    from db.models.location import EVLocation
    from web.queries.locations import haversine_meters

    attrs = _get_attributes(new_state)

    # GPS data is in attributes.value.location
    gps_value = attrs.get("value", {})
    location = gps_value.get("location", {}) if isinstance(gps_value, dict) else {}

    lat = _safe_float(location.get("lat"))
    lon = _safe_float(location.get("lon"))
    gps_accuracy = _safe_float(location.get("accuracy") or gps_value.get("accuracy"))

    if lat is not None and lon is not None:
        logger.debug("GPS update: lat=%.6f, lon=%.6f", lat, lon)

        # Deduplication: skip if last record for this device is within 60s AND 50m
        from sqlalchemy import select

        last_result = await db.execute(
            select(EVLocation)
            .where(EVLocation.device_id == device_id)
            .order_by(EVLocation.recorded_at.desc())
            .limit(1)
        )
        last_loc = last_result.scalar_one_or_none()

        now = datetime.now(UTC)

        if last_loc is not None:
            time_diff = (now - last_loc.recorded_at).total_seconds()
            if time_diff < 60 and last_loc.latitude is not None and last_loc.longitude is not None:
                dist = haversine_meters(float(last_loc.latitude), float(last_loc.longitude), lat, lon)
                if dist < 50:
                    logger.debug("GPS dedup: skipping (%.1fs, %.1fm)", time_diff, dist)
                    return

        # Store new EVLocation snapshot
        new_loc = EVLocation(
            device_id=device_id,
            recorded_at=now,
            latitude=lat,
            longitude=lon,
            gps_accuracy=gps_accuracy,
            source_system="home_assistant",
        )
        db.add(new_loc)
        logger.debug("Stored GPS snapshot for %s", device_id)


# ---------------------------------------------------------------------------
# Tire pressure handler
# ---------------------------------------------------------------------------

@handles("tirepressure")
async def handle_tire_pressure(slug, new_state, ha_config, device_id, db):
    """Handle tire pressure sensor updates.

    Parses tire pressure attributes and stores as JSONB in vehicle status.
    """
    attrs = _get_attributes(new_state)

    tire_data = {
        "front_left": attrs.get("frontLeft"),
        "front_right": attrs.get("frontRight"),
        "rear_left": attrs.get("rearLeft"),
        "rear_right": attrs.get("rearRight"),
        "front_left_state": attrs.get("frontLeft_state"),
        "front_right_state": attrs.get("frontRight_state"),
        "rear_left_state": attrs.get("rearLeft_state"),
        "rear_right_state": attrs.get("rearRight_state"),
        "system_state": attrs.get("systemState"),
    }

    # Store in pending vehicle status batch
    if device_id not in _pending_vehicle_status:
        _pending_vehicle_status[device_id] = {}
        _pending_vehicle_status[device_id]["_recorded_at"] = datetime.now(UTC)

    _pending_vehicle_status[device_id]["tire_pressure"] = tire_data
    _pending_vehicle_status_ts.setdefault(device_id, time.time())

    logger.debug("Tire pressure update stored for batch flush")


# ---------------------------------------------------------------------------
# Helper: get device_id (VIN) from entity_id or ha_config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# energytransferlogentry handler (charging session creation)
# ---------------------------------------------------------------------------

# Charger type normalization mapping — collapse all variants to AC/DC codes
# matching csv_parser._normalize_charge_type. Level 1/2 granularity is
# intentionally discarded: callers that need it can derive it from the EVSE
# power/voltage columns.
_CHARGER_TYPE_MAP = {
    "AC": "AC",
    "AC_BASIC": "AC",
    "AC_LEVEL_1": "AC",
    "AC_LEVEL_2": "AC",
    "AC LEVEL 1": "AC",
    "AC LEVEL 2": "AC",
    "LEVEL_1": "AC",
    "LEVEL_2": "AC",
    "LEVEL 1": "AC",
    "LEVEL 2": "AC",
    "L1": "AC",
    "L2": "AC",
    "DC": "DC",
    "DC_FAST": "DC",
    "DC_DCFAST": "DC",
    "DC_COMBO": "DC",
    "DC FAST": "DC",
    "DCFC": "DC",
    "L3": "DC",
    "LEVEL 3": "DC",
}


def _normalize_charge_type(raw: str | None) -> str | None:
    """Normalize charger type string to 'AC' or 'DC'.

    Returns None for empty input. Unrecognized values fall back to the raw
    string (uppercased) so debugging still surfaces the unknown code.
    """
    if not raw:
        return None
    key = raw.strip().upper()
    if not key:
        return None
    return _CHARGER_TYPE_MAP.get(key, key)


def _format_address(addr: dict | None) -> str | None:
    """Format address dict from energytransferlogentry location into a string."""
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


def _parse_iso_datetime(val: str | None) -> datetime | None:
    """Parse ISO 8601 datetime string, returning None on failure."""
    if not val:
        return None
    try:
        # Handle Z suffix and various ISO formats
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        logger.warning("Failed to parse datetime: %s", val)
        return None


@handles("energytransferlogentry")
async def handle_energy_transfer(slug, new_state, ha_config, device_id, db):
    """Handle energytransferlogentry events to create EVChargingSession records.

    Extracts all available fields from the rich payload including energy, SOC,
    duration, power stats, location, and plug times. Performs duplicate detection
    and network resolution.
    """
    from sqlalchemy import select

    from db.models.charging_session import EVChargingSession

    attrs = _get_attributes(new_state)

    if not attrs:
        logger.warning("energytransferlogentry with empty attributes, skipping")
        return

    # Extract core fields
    energy_kwh = _safe_float(attrs.get("energyConsumed"))
    charge_type = _normalize_charge_type(attrs.get("chargerType"))

    # Duration fields
    duration_data = attrs.get("energyTransferDuration", {}) or {}
    session_start_utc = _parse_iso_datetime(duration_data.get("begin"))
    session_end_utc = _parse_iso_datetime(duration_data.get("end"))
    charge_duration_seconds = _safe_float(duration_data.get("totalTime"))

    # Plug details
    plug_data = attrs.get("plugDetails", {}) or {}
    plugged_in_duration_seconds = _safe_float(plug_data.get("totalPluggedInTime"))
    raw_distance_added = _safe_float(plug_data.get("totalDistanceAdded"))
    # plugDetails.totalDistanceAdded is HA-unit-system-converted by ha-fordpass
    # (localize_distance in get_energy_transfer_log_attrs). The contract
    # carries ha_unit_system_converted=True; the adapter resolves the
    # effective source unit per-event from ha_config.unit_system before
    # routing through to_metric.
    dist_contract = ha_fordpass.lookup_contract(
        "sensor.fordpass_{vin}_energytransferlogentry",
        "plugDetails.totalDistanceAdded",
    )
    if dist_contract is not None:
        distance_added = ha_fordpass.convert(
            dist_contract, raw_distance_added, new_state, ha_config
        )
    else:
        # Should never happen — contract is registered unconditionally.
        distance_added = _convert_with_uom(
            raw_distance_added, "km", "distance_added", slug,
        )

    # State of charge
    soc_data = attrs.get("stateOfCharge", {}) or {}
    start_soc = _safe_float(soc_data.get("firstSOC"))
    end_soc = _safe_float(soc_data.get("lastSOC"))

    # Power stats (W -> kW)
    power_data = attrs.get("power", {}) or {}
    max_power = _safe_float(power_data.get("max"))
    min_power = _safe_float(power_data.get("min"))
    weighted_avg_power = _safe_float(power_data.get("weightedAverage"))
    if max_power is not None:
        max_power = max_power / 1000
    if min_power is not None:
        min_power = min_power / 1000
    charging_kw = weighted_avg_power / 1000 if weighted_avg_power is not None else None

    # Location
    location_data = attrs.get("location", {}) or {}
    address_dict = location_data.get("address", {}) or {}
    address = _format_address(address_dict)
    latitude = _safe_float(location_data.get("latitude"))
    longitude = _safe_float(location_data.get("longitude"))
    location_name = location_data.get("name") or (address_dict.get("city") if address_dict else None)
    network_name = location_data.get("network")

    # Timestamp
    original_timestamp = _parse_iso_datetime(attrs.get("timeStamp"))

    # Thermal context.
    # FIELD_CONTRACTS for batteryTemperature / outsidetemp live on the
    # elvehcharging / outsidetemp entity patterns, not energytransferlogentry,
    # so contract-lookup against this payload returns None by design. The
    # adapter's per-device caches (_last_charging_battery_temp, _last_outsidetemp)
    # are populated by the elvehcharging / outsidetemp handlers prior to this
    # event firing and are the canonical source for charging-session thermals.
    # Fall back to the energytransferlogentry payload when caches are empty
    # (e.g. fixture-driven tests that only inject the charging-session event).
    battery_temp = ha_fordpass._last_charging_battery_temp.get(device_id)
    ambient_temp = ha_fordpass._last_outsidetemp.get(device_id)
    if battery_temp is None:
        raw_batt = attrs.get("batteryTemperature")
        if raw_batt is not None:
            battery_temp = _convert_with_uom(raw_batt, "degC", "battery_temp", slug)
    if ambient_temp is None:
        raw_amb = attrs.get("outsidetemp")
        if raw_amb is not None:
            ambient_temp = _convert_with_uom(raw_amb, "degC", "ambient_temp", slug)
    battery_temp_start = battery_temp
    battery_temp_end = battery_temp
    ambient_temp_start = ambient_temp
    ambient_temp_end = ambient_temp

    # -----------------------------------------------------------------------
    # Duplicate detection: fuzzy match +-30min + +-10% energy
    # -----------------------------------------------------------------------
    duplicate_of_id = None
    if session_start_utc is not None:
        from datetime import timedelta
        window_start = session_start_utc - timedelta(minutes=30)
        window_end = session_start_utc + timedelta(minutes=30)

        fuzzy_result = await db.execute(
            select(
                EVChargingSession.id,
                EVChargingSession.energy_kwh,
                EVChargingSession.source_system,
            )
            .where(EVChargingSession.session_start_utc.between(window_start, window_end))
            .where(EVChargingSession.device_id == device_id)
        )
        for match_id, match_energy, match_source in fuzzy_result.all():
            if energy_kwh is not None and match_energy is not None:
                tolerance = abs(float(match_energy)) * 0.1 if float(match_energy) != 0 else 0.5
                if abs(float(energy_kwh) - float(match_energy)) <= tolerance:
                    if match_source == "home_assistant":
                        # Same source duplicate -- skip silently (existing behavior)
                        logger.info(
                            "Duplicate HA session detected (start=%s, energy=%.3f kWh), skipping",
                            session_start_utc, energy_kwh,
                        )
                        return
                    else:
                        # Cross-source duplicate -- create but flag for review
                        duplicate_of_id = match_id
                        break
            elif energy_kwh is None and match_energy is None:
                if match_source == "home_assistant":
                    logger.info("Duplicate HA session detected (start=%s, no energy), skipping", session_start_utc)
                    return
                else:
                    duplicate_of_id = match_id
                    break

    # -----------------------------------------------------------------------
    # Network resolution
    # -----------------------------------------------------------------------
    network_id = None
    if network_name and network_name.upper() != "UNKNOWN":
        from web.queries.settings import resolve_network
        network_id = await resolve_network(db, network_name=network_name, source_system="home_assistant")

    # -----------------------------------------------------------------------
    # Location resolution
    # -----------------------------------------------------------------------
    from web.queries.locations import resolve_location

    location_id = await resolve_location(
        db,
        latitude=latitude,
        longitude=longitude,
        address=address,
        network_name=network_name,
        network_id=network_id,
        location_name=location_name,
        address_dict=address_dict,
        source_system="home_assistant",
        _location_data=location_data,
        _network_name_raw=network_name,
    )

    # -----------------------------------------------------------------------
    # Create session record
    # -----------------------------------------------------------------------
    session = EVChargingSession(
        device_id=device_id,
        source_system="home_assistant",
        charge_type=charge_type,
        location_name=location_name,
        location_id=location_id,
        network_id=network_id,
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
        battery_temp_start=battery_temp_start,
        battery_temp_end=battery_temp_end,
        ambient_temp_start=ambient_temp_start,
        ambient_temp_end=ambient_temp_end,
        original_timestamp=original_timestamp,
        is_complete=True,  # energytransferlogentry fires after session completes
        recorded_at=datetime.now(UTC),
        duplicate_of_id=duplicate_of_id,
        needs_review=duplicate_of_id is not None,
        review_type="duplicate" if duplicate_of_id is not None else None,
        ingest_schema_version=ha_fordpass.INGEST_SCHEMA_VERSION,
    )
    db.add(session)

    logger.info(
        "Created charging session: %.3f kWh, %s -> %s%%, %s, %s",
        energy_kwh or 0,
        start_soc,
        end_soc,
        charge_type,
        location_name or "unknown location",
    )


# ---------------------------------------------------------------------------
# Gas price sensor handling (non-slug, arbitrary entity_ids)
# ---------------------------------------------------------------------------

# Cache for gas sensor entity_ids from app_settings to avoid per-event DB query
_gas_sensor_cache: dict[str, str] = {}
_gas_sensor_cache_ts: float = 0.0
_GAS_SENSOR_CACHE_TTL = 300  # seconds (5 minutes)


async def _get_gas_sensor_entity_ids(db) -> tuple[str | None, str | None]:
    """Return (station_entity_id, average_entity_id) from app_settings, cached.

    Cache is refreshed every 5 minutes to pick up configuration changes
    without querying app_settings on every single event.
    """
    global _gas_sensor_cache, _gas_sensor_cache_ts

    now = time.time()
    if _gas_sensor_cache and (now - _gas_sensor_cache_ts) < _GAS_SENSOR_CACHE_TTL:
        logger.debug("Gas sensor cache hit")
        return (
            _gas_sensor_cache.get("gas_sensor_station_entity_id"),
            _gas_sensor_cache.get("gas_sensor_average_entity_id"),
        )

    from web.queries.settings import get_app_settings_dict

    gas_settings = await get_app_settings_dict(
        db, ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"]
    )
    _gas_sensor_cache = gas_settings
    _gas_sensor_cache_ts = now
    logger.debug("Gas sensor cache refreshed: %s", gas_settings)
    return (
        gas_settings.get("gas_sensor_station_entity_id") or None,
        gas_settings.get("gas_sensor_average_entity_id") or None,
    )


def invalidate_gas_sensor_cache() -> None:
    """Invalidate the gas sensor entity_id cache.

    Call this when gas sensor settings are updated via the settings UI.
    """
    global _gas_sensor_cache, _gas_sensor_cache_ts
    _gas_sensor_cache = {}
    _gas_sensor_cache_ts = 0.0


async def _handle_gas_sensor_event(
    entity_id: str,
    new_state: dict,
    station_entity: str | None,
    average_entity: str | None,
    db,
) -> bool:
    """Handle a gas price sensor event if entity_id matches configured sensors.

    Returns True if the event was handled (entity_id matched), False otherwise.
    Non-numeric state values are skipped gracefully.
    """
    if entity_id != station_entity and entity_id != average_entity:
        return False

    state_val = _get_state_value(new_state)
    if state_val is None or state_val in ("unknown", "unavailable", ""):
        logger.debug("Gas sensor %s has non-numeric state '%s', skipping", entity_id, state_val)
        return True  # Matched but not actionable

    price = _safe_float(state_val)
    if price is None or price <= 0:
        logger.debug("Gas sensor %s value not a valid price: '%s', skipping", entity_id, state_val)
        return True

    recorded_at = _get_event_timestamp(new_state) or datetime.now(UTC)

    from web.queries.gas_prices import (
        compute_monthly_averages,
        store_gas_price_reading,
        upsert_gas_price,
    )

    await store_gas_price_reading(db, entity_id, price, recorded_at)
    logger.info("Gas price reading stored: entity=%s, price=%.3f, at=%s", entity_id, price, recorded_at)

    # Compute monthly averages and upsert into gas_price_history
    month_avg = await compute_monthly_averages(db, entity_id)
    for (year, month), avg_price in month_avg.items():
        if entity_id == station_entity:
            await upsert_gas_price(db, year, month, station_price=avg_price, source="ha_sensor")
        elif entity_id == average_entity:
            await upsert_gas_price(db, year, month, average_price=avg_price, source="ha_sensor")

    return True


# ---------------------------------------------------------------------------
# Main event dispatcher
# ---------------------------------------------------------------------------


async def _ensure_vehicle_exists(device_id: str, entity_id: str, db) -> None:
    """Ensure an EVVehicle record exists for this device_id.

    If no vehicle record exists, creates one with display_name=device_id,
    source_system='home_assistant'. Auto-activates only when no active vehicle
    is currently set.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from db.models.vehicle import EVVehicle
    from web.queries.settings import get_app_setting, set_app_setting

    # Check if vehicle already exists
    result = await db.execute(
        select(EVVehicle.id).where(EVVehicle.device_id == device_id).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return  # Already exists

    # Create new vehicle record
    vehicle = EVVehicle(
        display_name=device_id,
        device_id=device_id,
        vin=device_id,  # For FordPass, device_id IS the VIN
        source_system="home_assistant",
    )
    db.add(vehicle)
    try:
        await db.flush()
    except IntegrityError:
        # Another concurrent request already created it
        await db.rollback()
        return

    logger.info("Auto-created vehicle record for device_id=%s", device_id)

    # Auto-activate only if no active vehicle is set
    active_vid = await get_app_setting(db, "active_vehicle_id", "")
    if not active_vid:
        await set_app_setting(db, "active_vehicle_id", str(vehicle.id))
        logger.info("Auto-activated vehicle %s (id=%d) -- no prior active vehicle", device_id, vehicle.id)


async def process_state_change(
    entity_id: str, old_state: dict, new_state: dict, ha_config: dict
) -> None:
    """Main event handler -- dispatches to registered sensor handlers.

    Called by HASSClient for each state_changed event. First checks if the
    entity_id matches a configured gas price sensor (arbitrary entity_ids).
    Then falls through to slug-based FordPass handler dispatch.
    """
    from db.engine import AsyncSessionLocal

    # --- Gas price sensor check (before slug-based dispatch) ---
    # Gas sensors use arbitrary entity_ids, not the FordPass slug pattern
    async with AsyncSessionLocal() as db:
        try:
            station_entity, average_entity = await _get_gas_sensor_entity_ids(db)
            if station_entity or average_entity:
                handled = await _handle_gas_sensor_event(
                    entity_id, new_state, station_entity, average_entity, db
                )
                if handled:
                    return  # Gas sensor event fully handled
        except Exception as e:
            await db.rollback()
            logger.error("Error checking gas sensor for %s: %s", entity_id, e, exc_info=True)

    # --- Slug-based FordPass handler dispatch ---
    slug = extract_slug(entity_id)
    if slug is None or slug not in SENSOR_HANDLERS:
        return  # Unhandled entity, ignore silently

    handler = SENSOR_HANDLERS[slug]
    device_id = get_device_id(entity_id, ha_config)

    async with AsyncSessionLocal() as db:
        try:
            # Ensure vehicle record exists before processing any sensor data
            await _ensure_vehicle_exists(device_id, entity_id, db)
            await handler(slug, new_state, ha_config, device_id, db)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("Error processing %s: %s", entity_id, e, exc_info=True)


# ---------------------------------------------------------------------------
# Helper: get device_id (VIN) from entity_id or ha_config
# ---------------------------------------------------------------------------

def get_device_id(entity_id: str, ha_config: dict) -> str:
    """Resolve device_id (VIN) from entity_id pattern or config override.

    Extracts VIN from sensor.fordpass_{vin}_{slug} pattern.
    Falls back to ha_config override or 'unknown'.
    """
    # Check for VIN override in ha_config
    vin_override = ha_config.get("_vin_override")
    if vin_override:
        return vin_override

    # Extract from entity_id
    if entity_id and entity_id.startswith("sensor.fordpass_"):
        remainder = entity_id[len("sensor.fordpass_"):]
        parts = remainder.split("_", 1)
        if parts:
            return parts[0]

    return "unknown"
