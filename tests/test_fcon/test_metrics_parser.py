"""Shared Ford metrics parser: shape traps, derived power, row timestamps."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.test_fcon.payloads import telemetry
from web.services.sources.ford_metrics.parser import (
    BATTERY,
    VEHICLE,
    data_map,
    parse_metrics,
)
from web.services.units.data_map import SourceKind, SourceRef

pytestmark = pytest.mark.unit

AT = datetime(2026, 8, 24, 16, 59, 31, tzinfo=UTC)


def _rows(metrics) -> dict:
    return {r.table: r for r in parse_metrics(metrics)}


def test_battery_row_from_parked_telemetry():
    row = _rows(telemetry()["metrics"])[BATTERY]
    v = row.values
    assert row.recorded_at == AT
    assert v["hv_battery_soc"] == 76.5
    assert v["hv_battery_range"] == 272.5
    assert v["hv_battery_capacity"] == 106.75
    assert v["lv_battery_voltage"] == 12.8125
    assert v["lv_load_status"] == "NORMAL_OPERATION"
    assert v["performance_status"] == "NORMAL"
    assert v["hv_battery_kw"] == round(353.0 * 0.080078125 / 1000, 3)
    assert v["motor_kw"] == 0.0


def test_vehicle_row_and_shape_traps():
    v = _rows(telemetry()["metrics"])[VEHICLE].values
    assert v["odometer"] == 30549.0  # raw km, converted once (not at all)
    assert v["outside_temperature"] == 33.5  # not ambientTemp's 0.0
    assert "acceleration" not in v  # a vector never lands in a scalar column
    assert v["door_lock_status"][0]["value"] == "UNLOCKED"
    assert [w["vehicleWheel"] for w in v["tire_pressure"]] == ["FRONT_LEFT", "FRONT_RIGHT"]
    assert json.loads(v["seatbelt_status"])[0]["value"] == "UNBUCKLED"
    assert v["ignition_status"] == "OFF"
    assert "cabin_temperature" not in v


def test_recorded_at_is_newest_metric_time():
    metrics = telemetry()["metrics"]
    metrics["xevBatteryRange"]["updateTime"] = "2026-08-24T17:30:00Z"
    metrics["xevBatteryTemperature"]["updateTime"] = "2026-08-24T10:00:00Z"
    assert _rows(metrics)[BATTERY].recorded_at == datetime(2026, 8, 24, 17, 30, tzinfo=UTC)


def test_structured_value_in_numeric_field_is_dropped():
    metrics = telemetry(speed={"x": 1.0})["metrics"]
    assert "speed" not in _rows(metrics)[VEHICLE].values


def test_empty_metrics_yield_no_rows():
    assert parse_metrics({}) == []
    assert _rows({"odometer": {"value": 5.0, "updateTime": "2026-08-24T00:00:00Z"}}).keys() == {VEHICLE}


def test_same_map_attributes_to_either_source():
    http = data_map(SourceRef("telemetry", SourceKind.HTTP_ENDPOINT))
    ha = data_map(SourceRef("sensor.fordpass_{vin}_metrics", SourceKind.HA_ENTITY))
    assert [m.source_field for m in http] == [m.source_field for m in ha]
    assert {m.key for m in http}.isdisjoint({m.key for m in ha})
    speed = next(m for m in http if m.source_field == "speed")
    assert (speed.source_unit, speed.db_unit) == ("kmh", "km/h")
