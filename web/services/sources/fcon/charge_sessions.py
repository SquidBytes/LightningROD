"""Charge sessions derived from FordConnect telemetry.

A session is the plug span: it starts at the first sample that is plugged in
and charging, and ends when the plug reads DISCONNECTED — nothing else. Every
other display state (PAUSED, SCHEDULED, ...) simply accrues no charge time,
so no debounce is needed and a pause cannot split a session.

Derivation is stateless: when an unplug arrives, the samples since the
previous unplug are read back from the raw archive and folded in one pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.fcon_raw_sample import FconRawSample
from web.queries.locations import (
    LOCATION_MATCH_RADIUS_M,
    get_location_defaults,
    haversine_meters,
    resolve_location,
)
from web.queries.sessions import find_matching_sessions
from web.services.sources.fcon.history import telemetry_since_boundary
from web.services.sources.fcon.identity import SOURCE_SYSTEM
from web.services.sources.ford_metrics.parser import parse_time

logger = logging.getLogger("lightningrod.sources.fcon.sessions")

INGEST_SCHEMA_VERSION = 2
IN_PROGRESS = "IN_PROGRESS"
CONNECTED = "CONNECTED"
DISCONNECTED = "DISCONNECTED"
# Never observed, but the reference integration treats them as charging.
CHARGING_PLUG = frozenset({"CHARGING", "CHARGINGAC"})
# The charger output reads 2-3 V with nothing plugged in; ~350 V when live.
IDLE_CHARGER_VOLTS = 50.0


def _value(metrics: dict, name: str) -> Any:
    entry = metrics.get(name)
    return entry.get("value") if isinstance(entry, dict) else None


def _float(metrics: dict, name: str) -> float | None:
    value = _value(metrics, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(metrics: dict, name: str) -> str | None:
    value = _value(metrics, name)
    return str(value).upper() if value is not None else None


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
        "charge_type": start.power_type,
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


def schedule_location_name(schedules: dict | None, lat: float, lon: float) -> str | None:
    """The user's own name for a saved charge-schedule location at these coordinates."""
    for entry in (schedules or {}).get("chargeSchedules") or []:
        loc = entry.get("location") or {}
        s_lat, s_lon, name = loc.get("latitude"), loc.get("longitude"), loc.get("name")
        if not isinstance(s_lat, (int, float)) or not isinstance(s_lon, (int, float)):
            continue
        if name and haversine_meters(lat, lon, float(s_lat), float(s_lon)) <= LOCATION_MATCH_RADIUS_M:
            return str(name)
    return None


def _is_unplug(payload: dict) -> bool:
    sample = ChargeSample.from_payload(payload)
    return sample is not None and sample.unplugged


async def _samples_since_last_unplug(
    db: AsyncSession, device_id: str, until: datetime
) -> list[ChargeSample]:
    payloads = await telemetry_since_boundary(db, device_id, until, _is_unplug)
    return [s for p in payloads if (s := ChargeSample.from_payload(p)) is not None]


async def _latest_schedules(db: AsyncSession, config_id: int | None) -> dict | None:
    stmt = select(FconRawSample.payload).where(FconRawSample.endpoint == "charge-schedules")
    if config_id is not None:
        stmt = stmt.where(FconRawSample.config_id == config_id)
    return await db.scalar(stmt.order_by(FconRawSample.last_seen_at.desc()).limit(1))


async def record_session_if_ended(
    db: AsyncSession, device_id: str, payload: dict, config_id: int | None = None
) -> EVChargingSession | None:
    """If this telemetry sample is an unplug that closes a charge, write the session."""
    current = ChargeSample.from_payload(payload)
    if current is None or not current.unplugged:
        return None
    samples = await _samples_since_last_unplug(db, device_id, current.at)
    if not samples or samples[-1].at != current.at:
        samples.append(current)
    fields = derive_session(samples)
    if fields is None:
        return None

    location_name = None
    if fields["latitude"] is not None:
        location_name = schedule_location_name(
            await _latest_schedules(db, config_id), fields["latitude"], fields["longitude"]
        )
    location_id = await resolve_location(
        db,
        latitude=fields["latitude"],
        longitude=fields["longitude"],
        location_name=location_name,
        source_system=SOURCE_SYSTEM,
    )
    defaults = await get_location_defaults(db, location_id)
    fields.update(
        location_id=location_id,
        location_name=defaults.location_name or location_name,
        location_type=defaults.location_type,
        network_id=defaults.network_id,
        charge_type=fields["charge_type"] or defaults.charge_type,
        stall_id=defaults.stall_id,
        evse_voltage=defaults.evse_voltage,
        evse_amperage=defaults.evse_amperage,
        charger_rated_kw=defaults.charger_rated_kw,
        evse_source="stall_default" if defaults.stall_id else None,
    )

    existing = await db.scalar(
        select(EVChargingSession).where(
            EVChargingSession.device_id == device_id,
            EVChargingSession.source_system == SOURCE_SYSTEM,
            EVChargingSession.session_start_utc == fields["session_start_utc"],
        )
    )
    if existing is not None:
        # Re-deriving the same charge (a replay or re-parse) refreshes it.
        for column, value in fields.items():
            setattr(existing, column, value)
        return existing

    others = [
        m for m in await find_matching_sessions(
            db, device_id, fields["session_start_utc"], fields["energy_kwh"]
        )
        if m[1] != SOURCE_SYSTEM
    ]
    duplicate_of_id = others[0][0] if others else None
    session = EVChargingSession(
        device_id=device_id,
        source_system=SOURCE_SYSTEM,
        is_complete=True,
        recorded_at=current.at,
        original_timestamp=fields["session_start_utc"],
        duplicate_of_id=duplicate_of_id,
        needs_review=duplicate_of_id is not None,
        review_type="duplicate" if duplicate_of_id is not None else None,
        ingest_schema_version=INGEST_SCHEMA_VERSION,
        **fields,
    )
    db.add(session)
    await db.flush()
    logger.info(
        "Derived fcon charging session: %s kWh, %s -> %s%%",
        fields["energy_kwh"],
        fields["start_soc"],
        fields["end_soc"],
    )
    return session
