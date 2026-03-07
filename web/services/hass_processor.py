"""Home Assistant sensor event processor.

Dispatches HA state_changed events to registered sensor handlers.
Maps 29 FordPass entities to database records: charging sessions from
energytransferlogentry, vehicle status snapshots, and battery status updates.
Normalizes units to metric before storage.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("lightningrod.hass.processor")

# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


def miles_to_km(miles: float) -> float:
    """Convert miles to kilometers."""
    return miles * 1.60934


def fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32) * 5 / 9


def wh_to_kwh(wh: float) -> float:
    """Convert watt-hours to kilowatt-hours."""
    return wh / 1000


def normalize_value(value, unit: str, ha_unit_system: dict) -> float:
    """Normalize a value to metric for storage based on HA's detected units."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if unit in ("mi", "mph") or (
        unit in ("distance", "length")
        and ha_unit_system.get("length") == "mi"
    ):
        return miles_to_km(value)
    if unit in ("degF", "F") or (
        unit == "temperature"
        and ha_unit_system.get("temperature") in ("F", "\u00b0F")
    ):
        return fahrenheit_to_celsius(value)
    if unit == "Wh":
        return wh_to_kwh(value)
    return value  # already metric or unitless


# ---------------------------------------------------------------------------
# Slug extractor
# ---------------------------------------------------------------------------


def extract_slug(entity_id: str) -> Optional[str]:
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
        recorded_at=fields.pop("_recorded_at", datetime.now(timezone.utc)),
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
        recorded_at=fields.pop("_recorded_at", datetime.now(timezone.utc)),
        source_system="home_assistant",
        **fields,
    )
    db.add(record)
    logger.debug("Flushed battery status for %s (%d fields)", device_id, len(fields))


def _safe_float(val) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _get_state_value(new_state: dict) -> Optional[str]:
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
    """Extract HA unit system from config."""
    return ha_config.get("unit_system", {})


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
    """
    state_val = _get_state_value(new_state)
    attrs = _get_attributes(new_state)
    unit_system = _get_unit_system(ha_config)

    # Initialize pending dict for this device if needed
    if device_id not in _pending_vehicle_status:
        _pending_vehicle_status[device_id] = {}
        _pending_vehicle_status[device_id]["_recorded_at"] = datetime.now(timezone.utc)

    pending = _pending_vehicle_status[device_id]

    # Map slug to field
    slug_field_map = {
        "odometer": ("odometer", lambda v: normalize_value(v, "mi", unit_system)),
        "speed": ("speed", lambda v: normalize_value(v, "mph", unit_system)),
        "acceleratorpedalposition": ("accelerator_position", _safe_float),
        "brakepedalstatus": ("brake_status", str),
        "braketorque": ("brake_torque", _safe_float),
        "gearleverposition": ("gear_position", str),
        "parkingbrakestatus": ("parking_brake", str),
        "ignitionstatus": ("ignition_status", str),
        "yawrate": ("yaw_rate", _safe_float),
        "torqueattransmission": ("torque_at_transmission", _safe_float),
        "wheeltorquestatus": ("wheel_torque_status", str),
        "enginespeed": ("engine_speed", _safe_float),
        "cabintemperature": ("cabin_temperature", lambda v: normalize_value(v, "degF", unit_system)),
        "coolanttemp": ("coolant_temp", lambda v: normalize_value(v, "degF", unit_system)),
        "outsidetemp": ("outside_temperature", lambda v: normalize_value(v, "degF", unit_system)),
        "acceleration": ("acceleration", _safe_float),
        "deepsleep": ("deep_sleep_status", str),
        "deviceconnectivity": ("device_connectivity", str),
        "evccstatus": ("evcc_status", str),
    }

    if slug == "lastrefresh":
        # lastrefresh triggers a flush of accumulated vehicle status
        now = time.time()
        prev_ts = _pending_vehicle_status_ts.get(device_id, 0)
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
    """
    state_val = _get_state_value(new_state)
    attrs = _get_attributes(new_state)
    unit_system = _get_unit_system(ha_config)

    # Initialize pending dict for this device if needed
    if device_id not in _pending_battery_status:
        _pending_battery_status[device_id] = {}
        _pending_battery_status[device_id]["_recorded_at"] = datetime.now(timezone.utc)

    pending = _pending_battery_status[device_id]

    if slug == "soc":
        # HV battery state of charge (%)
        pending["hv_battery_soc"] = _safe_float(state_val)
        # batteryRange is in the soc entity attributes (distance in HA unit)
        battery_range = attrs.get("batteryRange")
        if battery_range is not None:
            pending["hv_battery_range"] = normalize_value(battery_range, "mi", unit_system)

    elif slug == "elveh":
        # EV range (miles) and rich battery attributes
        if state_val not in (None, "unknown", "unavailable"):
            pending["hv_battery_range"] = normalize_value(state_val, "mi", unit_system)
        # Extract rich attributes from elveh entity
        hv_voltage = _safe_float(attrs.get("batteryVoltage"))
        hv_amperage = _safe_float(attrs.get("batteryAmperage"))
        hv_kw = _safe_float(attrs.get("batterykW"))
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
        # Max range from attributes
        max_range = _safe_float(attrs.get("maximumBatteryRange"))
        if max_range is not None:
            pending["hv_battery_max_range"] = normalize_value(max_range, "mi", unit_system)

    elif slug == "battery":
        # 12V battery level (%)
        pending["lv_battery_level"] = _safe_float(state_val)
        # 12V voltage from attributes
        lv_voltage = _safe_float(attrs.get("batteryVoltage"))
        if lv_voltage is not None:
            pending["lv_battery_voltage"] = lv_voltage

    elif slug == "lastenergyconsumed":
        # Energy consumed on last trip (Wh) -- log but no direct column
        energy_wh = _safe_float(state_val)
        if energy_wh is not None:
            energy_kwh = wh_to_kwh(energy_wh)
            logger.debug("Last energy consumed: %.3f kWh", energy_kwh)

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
    and stores as part of vehicle status batch.
    """
    attrs = _get_attributes(new_state)

    # GPS data is in attributes.value.location
    gps_value = attrs.get("value", {})
    location = gps_value.get("location", {}) if isinstance(gps_value, dict) else {}

    lat = _safe_float(location.get("lat"))
    lon = _safe_float(location.get("lon"))

    if lat is not None and lon is not None:
        logger.debug("GPS update: lat=%.6f, lon=%.6f", lat, lon)
        # Store in pending vehicle status for the batch
        # GPS doesn't have dedicated columns on EVVehicleStatus but we log it
        # Future: could add lat/lon columns to vehicle_status
        # For now, just log the position
        pass


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
        _pending_vehicle_status[device_id]["_recorded_at"] = datetime.now(timezone.utc)

    _pending_vehicle_status[device_id]["tire_pressure"] = tire_data
    _pending_vehicle_status_ts.setdefault(device_id, time.time())

    logger.debug("Tire pressure update stored for batch flush")


# ---------------------------------------------------------------------------
# Helper: get device_id (VIN) from entity_id or ha_config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# energytransferlogentry handler (charging session creation)
# ---------------------------------------------------------------------------

# Charger type normalization mapping
_CHARGER_TYPE_MAP = {
    "AC_BASIC": "AC Level 2",
    "AC_LEVEL_2": "AC Level 2",
    "DC_FAST": "DC Fast",
    "DC_DCFAST": "DC Fast",
    "DC_COMBO": "DC Fast",
    "LEVEL_1": "AC Level 1",
    "AC_LEVEL_1": "AC Level 1",
}


def _normalize_charge_type(raw: Optional[str]) -> Optional[str]:
    """Normalize charger type string to standard display format."""
    if not raw:
        return None
    return _CHARGER_TYPE_MAP.get(raw.upper(), raw)


def _format_address(addr: Optional[dict]) -> Optional[str]:
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


def _parse_iso_datetime(val: Optional[str]) -> Optional[datetime]:
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
    from db.models.charging_session import EVChargingSession
    from sqlalchemy import select

    attrs = _get_attributes(new_state)
    unit_system = _get_unit_system(ha_config)

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
    total_distance_added = _safe_float(plug_data.get("totalDistanceAdded"))
    miles_added = normalize_value(total_distance_added, "mi", unit_system) if total_distance_added is not None else None

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

    # -----------------------------------------------------------------------
    # Duplicate detection: match on session_start_utc + energy_kwh
    # -----------------------------------------------------------------------
    if session_start_utc is not None and energy_kwh is not None:
        existing = await db.execute(
            select(EVChargingSession.id)
            .where(EVChargingSession.session_start_utc == session_start_utc)
            .where(EVChargingSession.energy_kwh == energy_kwh)
            .where(EVChargingSession.source_system == "home_assistant")
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(
                "Duplicate session detected (start=%s, energy=%.3f kWh), skipping",
                session_start_utc, energy_kwh,
            )
            return

    # -----------------------------------------------------------------------
    # Network resolution
    # -----------------------------------------------------------------------
    network_id = None
    if network_name and network_name.upper() != "UNKNOWN":
        from web.queries.settings import resolve_network
        network_id = await resolve_network(db, network_name=network_name)

    # -----------------------------------------------------------------------
    # Create session record
    # -----------------------------------------------------------------------
    session = EVChargingSession(
        device_id=device_id,
        source_system="home_assistant",
        charge_type=charge_type,
        location_name=location_name,
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
        miles_added=miles_added,
        original_timestamp=original_timestamp,
        is_complete=True,  # energytransferlogentry fires after session completes
        recorded_at=datetime.now(timezone.utc),
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
# Main event dispatcher
# ---------------------------------------------------------------------------


async def process_state_change(
    entity_id: str, old_state: dict, new_state: dict, ha_config: dict
) -> None:
    """Main event handler -- dispatches to registered sensor handlers.

    Called by HASSClient for each state_changed event. Resolves the sensor
    slug, looks up the handler, opens a DB session, and delegates.
    """
    slug = extract_slug(entity_id)
    if slug is None or slug not in SENSOR_HANDLERS:
        return  # Unhandled entity, ignore silently

    handler = SENSOR_HANDLERS[slug]
    device_id = get_device_id(entity_id, ha_config)

    from db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
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
