"""Trips and charge sessions folded from a series of Ford `metrics` samples. Pure.

A trip is an ignition-ON span in which the odometer moves. A park with the
vehicle left running stays one trip; switching off ends it. Remote start
sets ignition ON with the vehicle stationary, so a span without odometer
movement is not a trip. `tripProgress` is not used yet.

A session is the plug span: it starts at the first sample that is plugged in
and charging, and ends when the plug reads DISCONNECTED, nothing else. Every
other display state (PAUSED, SCHEDULED, ...) simply accrues no charge time,
so no debounce is needed and a pause cannot split a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from web.services.charge_types import normalize_charge_type
from web.util.timestamps import parse_iso, utc


def parse_time(raw: Any) -> datetime | None:
    return utc(parse_iso(raw))


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


IGNITION_ON = frozenset({"ON", "RUN", "START"})


@dataclass(frozen=True)
class TripSample:
    # When the ignition last reported, falling back to the payload's time.
    at: datetime
    ignition_on: bool
    odometer: float | None
    energy: float | None
    outside_temp: float | None
    brake_torque: float | None
    brake_torque_at: datetime | None
    range_regenerated: float | None

    @classmethod
    def from_payload(cls, payload: dict) -> TripSample | None:
        m = payload.get("metrics") or {}
        ignition = _entry(m, "ignitionStatus")
        at = parse_time(ignition.get("updateTime")) or parse_time(payload.get("updateTime"))
        if at is None:
            return None
        return cls(
            at=at,
            ignition_on=str(ignition.get("value") or "").upper() in IGNITION_ON,
            odometer=_float(m, "odometer"),
            energy=_float(m, "xevBatteryEnergyRemaining"),
            outside_temp=_float(m, "outsideTemperature"),
            brake_torque=_float(m, "brakeTorque"),
            brake_torque_at=parse_time(_entry(m, "brakeTorque").get("updateTime")),
            range_regenerated=_float(m, "tripXevBatteryRangeRegenerated"),
        )


def _last(values: list[float | None]) -> float | None:
    return next((v for v in reversed(values) if v is not None), None)


def derive_trip(samples: list[TripSample]) -> dict[str, Any] | None:
    """Trip columns for samples ending at ignition-off; None when it never moved.

    Samples may lead with the parked reading before switch-on: the first ON
    poll can arrive after the vehicle has already moved.
    """
    driving = [s for s in samples if s.ignition_on]
    if not driving or samples[-1].ignition_on:
        return None
    start, end = driving[0], samples[-1]
    from_start = samples[samples.index(start):]
    odometers = [s.odometer for s in samples if s.odometer is not None]
    odo_start = next((s.odometer for s in samples if s.odometer is not None), None)
    if odo_start is None:
        return None
    # Clamped: an odometer reading never moves backwards.
    odo_end = max(odometers)
    distance = round(odo_end - odo_start, 3)
    if distance <= 0:
        return None
    # Parked drain before switch-on is not trip energy.
    energies = [s.energy for s in from_start]
    first_energy = next((e for e in energies if e is not None), None)
    last_energy = _last(energies)
    energy = (
        round(max(0.0, first_energy - last_energy), 3)
        if first_energy is not None and last_energy is not None
        else None
    )
    # Peak of the instantaneous Nm readings the vehicle refreshed during this
    # drive; at one snapshot a minute it is a sampled lower bound, not braking
    # behaviour (that needs the per-sample ev_vehicle_status series).
    torques = [
        s.brake_torque
        for s in samples
        if s.brake_torque is not None
        and s.brake_torque_at is not None
        and start.at <= s.brake_torque_at <= end.at
    ]
    return {
        "start_time": start.at,
        "end_time": end.at,
        "duration": (end.at - start.at).total_seconds(),
        "distance": distance,
        "odometer_start": odo_start,
        "odometer_end": odo_end,
        "energy_consumed": energy,
        "efficiency": round(distance / energy, 3) if energy else None,
        "range_regenerated": _last([s.range_regenerated for s in samples]),
        "ambient_temp": _last([s.outside_temp for s in samples]),
        "brake_torque": max(torques) if torques else None,
    }


IN_PROGRESS = "IN_PROGRESS"
CONNECTED = "CONNECTED"
DISCONNECTED = "DISCONNECTED"
# Never observed, but the reference integration treats them as charging.
CHARGING_PLUG = frozenset({"CHARGING", "CHARGINGAC"})
# The charger output reads 2-3 V with nothing plugged in; ~350 V when live.
IDLE_CHARGER_VOLTS = 50.0


@dataclass(frozen=True)
class ChargeSample:
    at: datetime
    plug: str | None
    display: str | None
    station: str | None
    power_type: str | None
    soc: float | None
    energy: float | None
    range_km: float | None
    battery_temp: float | None
    outside_temp: float | None
    charger_volts: float | None
    charger_amps: float | None
    pack_amps: float | None
    estimated_end: datetime | None
    # Only a measured fix: INFERRED positions are dead-reckoned guesses.
    latitude: float | None
    longitude: float | None

    @classmethod
    def from_payload(cls, payload: dict) -> ChargeSample | None:
        at = parse_time(payload.get("updateTime"))
        if at is None:
            return None
        m = payload.get("metrics") or {}
        ttf = m.get("xevBatteryTimeToFullCharge")
        ttf_at = parse_time(ttf.get("updateTime")) if isinstance(ttf, dict) else None
        ttf_min = _float(m, "xevBatteryTimeToFullCharge")
        position = _value(m, "position")
        lat = lon = None
        if isinstance(position, dict) and position.get("gpsCoordinateMethod") == "ACTUAL":
            loc = position.get("location") or {}
            if isinstance(loc.get("lat"), (int, float)) and isinstance(loc.get("lon"), (int, float)):
                lat, lon = float(loc["lat"]), float(loc["lon"])
        return cls(
            at=at,
            plug=_text(m, "xevPlugChargerStatus"),
            display=_text(m, "xevBatteryChargeDisplayStatus"),
            station=_text(m, "xevChargeStationCommunicationStatus"),
            power_type=_text(m, "xevChargeStationPowerType"),
            soc=_float(m, "xevBatteryStateOfCharge"),
            energy=_float(m, "xevBatteryEnergyRemaining"),
            range_km=_float(m, "xevBatteryRange"),
            battery_temp=_float(m, "xevBatteryTemperature"),
            outside_temp=_float(m, "outsideTemperature"),
            charger_volts=_float(m, "xevBatteryChargerVoltageOutput"),
            charger_amps=_float(m, "xevBatteryChargerCurrentOutput"),
            pack_amps=_float(m, "xevBatteryIoCurrent"),
            estimated_end=(
                ttf_at + timedelta(minutes=ttf_min)
                if ttf_at is not None and ttf_min is not None and ttf_min > 0
                else None
            ),
            latitude=lat,
            longitude=lon,
        )

    @property
    def charging(self) -> bool:
        return (self.plug == CONNECTED and self.display == IN_PROGRESS) or (
            self.plug in CHARGING_PLUG
        )

    @property
    def unplugged(self) -> bool:
        return self.plug == DISCONNECTED

    @property
    def volts(self) -> float | None:
        v = self.charger_volts
        return v if v is not None and v >= IDLE_CHARGER_VOLTS else None

    @property
    def kw(self) -> float | None:
        """Charger-side power; on DC the charger current reads 0, so use the pack's."""
        volts = self.volts
        if volts is None:
            return None
        if self.charger_amps:
            return round(volts * self.charger_amps / 1000.0, 3)
        if self.pack_amps is not None:
            return round(volts * abs(self.pack_amps) / 1000.0, 3)
        return None


def _delta(end: float | None, start: float | None) -> float | None:
    if end is None or start is None:
        return None
    return round(max(0.0, end - start), 3)


def derive_session(samples: list[ChargeSample]) -> dict[str, Any] | None:
    """Session columns for samples ending in an unplug; None if it never charged."""
    start_at = next((i for i, s in enumerate(samples) if s.charging), None)
    if start_at is None:
        return None
    window = samples[start_at:]
    start, end = window[0], window[-1]

    charge_seconds = sum(
        (b.at - a.at).total_seconds() for a, b in pairwise(window) if a.charging
    )
    # The pack's energy estimate backslides mid-charge; clamp the series so
    # it never falls, then take endpoint minus endpoint.
    energies = [s.energy for s in window if s.energy is not None]
    energy = _delta(max(energies), start.energy) if energies else None
    powers = [kw for s in window if s.charging and (kw := s.kw)]
    last_live = next((s for s in reversed(window) if s.charging), start)
    fix = next((s for s in window if s.latitude is not None), None)
    estimate = next((s.estimated_end for s in reversed(window) if s.estimated_end), None)

    return {
        "charge_type": normalize_charge_type(start.power_type),
        "plug_status": end.plug,
        "charging_status": last_live.display,
        "station_status": last_live.station,
        "charging_voltage": last_live.volts,
        "charging_amperage": last_live.charger_amps if last_live.volts else None,
        "charging_kw": last_live.kw,
        "session_start_utc": start.at,
        "session_end_utc": end.at,
        "estimated_end_utc": estimate,
        "charge_duration_seconds": charge_seconds,
        "plugged_in_duration_seconds": (end.at - start.at).total_seconds(),
        "start_soc": start.soc,
        "end_soc": end.soc,
        "energy_kwh": energy,
        "max_power": max(powers) if powers else None,
        "min_power": min(powers) if powers else None,
        "distance_added": _delta(end.range_km, start.range_km),
        "battery_temp_start": start.battery_temp,
        "battery_temp_end": end.battery_temp,
        "ambient_temp_start": start.outside_temp,
        "ambient_temp_end": end.outside_temp,
        "latitude": fix.latitude if fix else None,
        "longitude": fix.longitude if fix else None,
    }
