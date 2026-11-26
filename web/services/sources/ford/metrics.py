"""Ford `metrics` dict -> typed rows, whichever source delivered it.

FordConnect `/telemetry` and Home Assistant's fordpass `metrics` entity carry
the same Ford payload: `{name: {"value": ..., "updateTime": ...}}`, never
localized, always SI. The parser takes only that mapping — never an HTTP
body or an HA state — so both sources adapt to it rather than it to them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from web.services.units.data_map import DataMapping, SourceRef
from web.services.units.to_metric import to_metric
from web.util.timestamps import parse_iso, utc

Shape = Literal["number", "text", "json", "json_text"]
# Units to_metric does not know, all already SI: taken as-is.
_SI_PASSTHROUGH = {"%", "V", "A", "Nm", "deg/s"}


@dataclass(frozen=True)
class MetricField:
    metric: str
    table: str
    column: str
    unit: str
    shape: Shape = "number"
    notes: str | None = None
    # Capacity arrives as Wh on some firmware and kWh on others.
    wh_autoscale: bool = False
    # Found inside `customMetrics`, whose keys carry a vehicle-specific prefix.
    custom: bool = False
    # Ford sends 0 when it has no reading for these.
    zero_is_unmeasured: bool = False

    @property
    def db_unit(self) -> str:
        return {"kmh": "km/h"}.get(self.unit, self.unit)


BATTERY = "ev_battery_status"
VEHICLE = "ev_vehicle_status"
TRIPS = "ev_trip_metrics"

FIELDS: tuple[MetricField, ...] = (
    MetricField("xevBatteryStateOfCharge", BATTERY, "hv_battery_soc", "%"),
    MetricField("xevBatteryActualStateOfCharge", BATTERY, "hv_battery_actual_soc", "%"),
    MetricField("xevBatteryVoltage", BATTERY, "hv_battery_voltage", "V"),
    MetricField(
        "xevBatteryIoCurrent", BATTERY, "hv_battery_amperage", "A",
        notes="Negative while charging",
    ),
    MetricField(
        "xevBatteryCapacity", BATTERY, "hv_battery_capacity", "kWh", wh_autoscale=True,
        notes="Wh on some firmware; a value of 1000 or more is taken as Wh",
    ),
    MetricField("xevBatteryRange", BATTERY, "hv_battery_range", "km"),
    MetricField("xevBatteryMaximumRange", BATTERY, "hv_battery_max_range", "km"),
    MetricField("xevBatteryTemperature", BATTERY, "hv_battery_temperature", "degC"),
    MetricField("batteryStateOfCharge", BATTERY, "lv_battery_level", "%", notes="12V"),
    MetricField("batteryVoltage", BATTERY, "lv_battery_voltage", "V", notes="12V"),
    MetricField("batteryLoadStatus", BATTERY, "lv_load_status", "enum", "text", notes="12V"),
    MetricField("xevTractionMotorVoltage", BATTERY, "motor_voltage", "V"),
    MetricField("xevTractionMotorCurrent", BATTERY, "motor_amperage", "A"),
    MetricField("xevBatteryPerformanceStatus", BATTERY, "performance_status", "enum", "text"),
    MetricField("odometer", VEHICLE, "odometer", "km", notes="Clamped so it never runs backwards"),
    MetricField("speed", VEHICLE, "speed", "kmh"),
    MetricField("acceleratorPedalPosition", VEHICLE, "accelerator_position", "%"),
    MetricField("gearLeverPosition", VEHICLE, "gear_position", "enum", "text"),
    MetricField("ignitionStatus", VEHICLE, "ignition_status", "enum", "text"),
    MetricField("torqueAtTransmission", VEHICLE, "torque_at_transmission", "Nm"),
    MetricField("brakeTorque", VEHICLE, "brake_torque", "Nm"),
    MetricField("wheelTorqueStatus", VEHICLE, "wheel_torque_status", "enum", "text"),
    MetricField("yawRate", VEHICLE, "yaw_rate", "deg/s"),
    MetricField("seatBeltStatus", VEHICLE, "seatbelt_status", "list", "json_text"),
    MetricField("doorLockStatus", VEHICLE, "door_lock_status", "list", "json"),
    MetricField("tirePressure", VEHICLE, "tire_pressure", "list", "json", notes="kPa per wheel"),
    MetricField(
        "outsideTemperature", VEHICLE, "outside_temperature", "degC",
        notes="Not ambientTemp, which is unreliable",
    ),
)

# Ford's figures for the drive just driven; they describe a trip, not an instant.
TRIP_FIELDS: tuple[MetricField, ...] = (
    MetricField(
        "tripXevBatteryRangeRegenerated", TRIPS, "range_regenerated", "km",
        zero_is_unmeasured=True,
    ),
    MetricField(
        "tripXevBatteryChargeRegenerated", TRIPS, "charge_regenerated", "%",
        zero_is_unmeasured=True,
        notes="Estimated electrical charge recovered through regeneration during the trip, "
        "per Ford's data dictionary",
    ),
    MetricField(
        "accumulated-vehicle-speed-cruising-coaching-score", TRIPS, "speed_score", "%",
        custom=True, zero_is_unmeasured=True,
    ),
    MetricField(
        "accumulated-acceleration-coaching-score", TRIPS, "acceleration_score", "%",
        custom=True, zero_is_unmeasured=True,
    ),
    MetricField(
        "accumulated-deceleration-coaching-score", TRIPS, "deceleration_score", "%",
        custom=True, zero_is_unmeasured=True,
    ),
    MetricField(
        "custom:vehicle-electrical-efficiency", TRIPS, "electrical_efficiency", "%",
        custom=True, zero_is_unmeasured=True,
    ),
)


def data_map(source: SourceRef, fields: tuple[MetricField, ...] = FIELDS) -> list[DataMapping]:
    """The parser's mappings, attributed to one source."""
    return [
        DataMapping(
            source=source,
            source_field=f.metric,
            source_unit=f.unit,
            db_table=f.table,
            db_column=f.column,
            db_unit=f.db_unit,
            notes=f.notes,
            wh_autoscale=f.wh_autoscale,
        )
        for f in fields
    ]


def parse_time(raw: Any) -> datetime | None:
    return utc(parse_iso(raw))


def _update_time(entry: Any) -> datetime | None:
    if isinstance(entry, list):
        stamps = [t for t in (_update_time(i) for i in entry) if t is not None]
        return max(stamps, default=None)
    if isinstance(entry, dict):
        return parse_time(entry.get("updateTime"))
    return None


def _number(value: Any, unit: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        # A structured value (e.g. the acceleration vector) is not a scalar.
        return None
    if unit in _SI_PASSTHROUGH:
        try:
            return float(value)
        except ValueError:
            return None
    return to_metric(value, unit)


def _convert(f: MetricField, entry: Any) -> Any:
    if f.shape == "json":
        return entry if isinstance(entry, list) else None
    if f.shape == "json_text":
        return json.dumps(entry, sort_keys=True) if isinstance(entry, list) else None
    value = entry.get("value") if isinstance(entry, dict) else None
    if value is None:
        return None
    if f.shape == "text":
        return str(value)
    number = _number(value, f.unit)
    if number is None or (f.zero_is_unmeasured and number == 0):
        return None
    # Real packs are ~10-300 kWh, so 1000 or more can only be Wh.
    return number / 1000.0 if f.wh_autoscale and abs(number) >= 1000 else number


def _entry(f: MetricField, metrics: Mapping[str, Any]) -> Any:
    if not f.custom:
        return metrics.get(f.metric)
    custom = metrics.get("customMetrics")
    if not isinstance(custom, Mapping):
        return None
    return next((entry for key, entry in custom.items() if f.metric in key), None)


def _kw(volts: Any, amps: Any) -> float | None:
    if volts is None or amps is None:
        return None
    return round(float(volts) * float(amps) / 1000.0, 3)


@dataclass
class ParsedRow:
    table: str
    recorded_at: datetime
    values: dict[str, Any] = field(default_factory=dict)
    # metric -> raw entry, for diagnostics.
    raw: dict[str, Any] = field(default_factory=dict)


def parse_metrics(
    metrics: Mapping[str, Any], fields: tuple[MetricField, ...] = FIELDS
) -> list[ParsedRow]:
    """One row per table the metrics cover, stamped with the newest metric time.

    A row's recorded_at is the latest updateTime among its own metrics, so an
    unchanged reading re-parses to the same row.
    """
    rows: dict[str, ParsedRow] = {}
    for f in fields:
        entry = _entry(f, metrics)
        stamp = _update_time(entry)
        if entry is None or stamp is None:
            continue
        value = _convert(f, entry)
        if value is None:
            continue
        row = rows.get(f.table)
        if row is None:
            row = rows[f.table] = ParsedRow(f.table, stamp)
        row.recorded_at = max(row.recorded_at, stamp)
        row.values[f.column] = value
        row.raw[f.metric] = entry

    battery = rows.get(BATTERY)
    if battery is not None:
        v = battery.values
        if (kw := _kw(v.get("hv_battery_voltage"), v.get("hv_battery_amperage"))) is not None:
            v["hv_battery_kw"] = kw
        if (kw := _kw(v.get("motor_voltage"), v.get("motor_amperage"))) is not None:
            v["motor_kw"] = kw
    return list(rows.values())


def parse_trip_enrichment(metrics: Mapping[str, Any]) -> ParsedRow | None:
    """Regen and coaching scores for the newest trip, or None when the dict has none."""
    return next(iter(parse_metrics(metrics, TRIP_FIELDS)), None)
