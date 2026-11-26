"""Ford `metrics` payloads as the pipeline's samples and GPS fixes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from web.services.pipeline.derive import ChargeSample, TripSample
from web.services.pipeline.observation import PositionObservation
from web.services.sources.ford.metrics import parse_time

IGNITION_ON = frozenset({"ON", "RUN", "START"})
IN_PROGRESS = "IN_PROGRESS"
CONNECTED = "CONNECTED"
DISCONNECTED = "DISCONNECTED"
# Never observed, but the reference integration treats them as charging.
CHARGING_PLUG = frozenset({"CHARGING", "CHARGINGAC"})
# The charger output reads 2-3 V with nothing plugged in; ~350 V when live.
IDLE_CHARGER_VOLTS = 50.0


def _entry(metrics: dict, name: str) -> dict:
    entry = metrics.get(name)
    return entry if isinstance(entry, dict) else {}


def _value(metrics: dict, name: str) -> Any:
    return _entry(metrics, name).get("value")


def _float(metrics: dict, name: str) -> float | None:
    value = _value(metrics, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(metrics: dict, name: str) -> str | None:
    value = _value(metrics, name)
    return str(value).upper() if value is not None else None


def _measured_fix(metrics: dict) -> tuple[float, float, float | None] | None:
    """(lat, lon, alt) of an ACTUAL fix; INFERRED positions are dead-reckoned guesses."""
    value = _value(metrics, "position")
    if not isinstance(value, dict) or value.get("gpsCoordinateMethod") != "ACTUAL":
        return None
    loc = value.get("location") or {}
    lat, lon, alt = loc.get("lat"), loc.get("lon"), loc.get("alt")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return float(lat), float(lon), alt if isinstance(alt, (int, float)) else None


def position_observation(
    device_id: str, metrics: dict, source_system: str
) -> PositionObservation | None:
    fix = _measured_fix(metrics)
    at = parse_time(_entry(metrics, "position").get("updateTime"))
    if fix is None or at is None:
        return None
    return PositionObservation(
        device_id, at, fix[0], fix[1], source_system,
        altitude=fix[2], compass_direction=_value(metrics, "compassDirection"),
    )


def trip_sample(payload: dict) -> TripSample | None:
    """Stamped when the ignition last reported, falling back to the payload's time."""
    m = payload.get("metrics") or {}
    ignition = _entry(m, "ignitionStatus")
    at = parse_time(ignition.get("updateTime")) or parse_time(payload.get("updateTime"))
    if at is None:
        return None
    return TripSample(
        at=at,
        ignition_on=str(ignition.get("value") or "").upper() in IGNITION_ON,
        odometer=_float(m, "odometer"),
        energy=_float(m, "xevBatteryEnergyRemaining"),
        outside_temp=_float(m, "outsideTemperature"),
        brake_torque=_float(m, "brakeTorque"),
        brake_torque_at=parse_time(_entry(m, "brakeTorque").get("updateTime")),
        range_regenerated=_float(m, "tripXevBatteryRangeRegenerated"),
    )


def _charger_kw(volts: float | None, charger_amps: float | None, pack_amps: float | None) -> float | None:
    """Charger-side power; on DC the charger current reads 0, so use the pack's."""
    if volts is None:
        return None
    if charger_amps:
        return round(volts * charger_amps / 1000.0, 3)
    if pack_amps is not None:
        return round(volts * abs(pack_amps) / 1000.0, 3)
    return None


def charge_sample(payload: dict) -> ChargeSample | None:
    at = parse_time(payload.get("updateTime"))
    if at is None:
        return None
    m = payload.get("metrics") or {}
    ttf_at = parse_time(_entry(m, "xevBatteryTimeToFullCharge").get("updateTime"))
    ttf_min = _float(m, "xevBatteryTimeToFullCharge")
    fix = _measured_fix(m)
    plug, display = _text(m, "xevPlugChargerStatus"), _text(m, "xevBatteryChargeDisplayStatus")
    raw_volts = _float(m, "xevBatteryChargerVoltageOutput")
    volts = raw_volts if raw_volts is not None and raw_volts >= IDLE_CHARGER_VOLTS else None
    amps = _float(m, "xevBatteryChargerCurrentOutput")
    return ChargeSample(
        at=at,
        charging=(plug == CONNECTED and display == IN_PROGRESS) or plug in CHARGING_PLUG,
        unplugged=plug == DISCONNECTED,
        plug=plug,
        display=display,
        station=_text(m, "xevChargeStationCommunicationStatus"),
        power_type=_text(m, "xevChargeStationPowerType"),
        soc=_float(m, "xevBatteryStateOfCharge"),
        energy=_float(m, "xevBatteryEnergyRemaining"),
        range_km=_float(m, "xevBatteryRange"),
        battery_temp=_float(m, "xevBatteryTemperature"),
        outside_temp=_float(m, "outsideTemperature"),
        volts=volts,
        amps=amps,
        kw=_charger_kw(volts, amps, _float(m, "xevBatteryIoCurrent")),
        estimated_end=(
            ttf_at + timedelta(minutes=ttf_min)
            if ttf_at is not None and ttf_min is not None and ttf_min > 0
            else None
        ),
        latitude=fix[0] if fix else None,
        longitude=fix[1] if fix else None,
    )
