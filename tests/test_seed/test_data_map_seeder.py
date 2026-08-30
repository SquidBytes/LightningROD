"""Tests for DataMapSeeder, realistic_value, and write_data_map_gap_report.

These tests are DB-free — no fixtures from conftest that touch Postgres are used.
"""
from __future__ import annotations

import random

import pytest

from scripts.seed.base import (
    DataMapSeeder,
    realistic_value,
    write_data_map_gap_report,
)
from web.services.units.data_map import DataMapping, SourceKind, SourceRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fc(table: str, column: str, unit: str, notes: str | None = None) -> DataMapping:
    return DataMapping(
        source=SourceRef("sensor.test_*", SourceKind.HA_ENTITY),
        source_field="value",
        source_unit=unit,
        db_table=table,
        db_column=column,
        db_unit=unit,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# DataMapSeeder tests
# ---------------------------------------------------------------------------

def test_value_for_declared_mapping():
    """value_for returns a float in [5, 80] for a declared kWh mapping."""
    fc = _fc("ev_battery_status", "usable_kwh", "kWh")
    seeder = DataMapSeeder(declared=[fc])
    val = seeder.value_for("ev_battery_status", "usable_kwh")
    assert isinstance(val, float)
    assert 5.0 <= val <= 80.0


def test_value_for_expected_records_gap():
    """A hit on an expected (not declared) mapping is recorded in gaps_report."""
    fc = _fc("ev_vehicle_status", "range_km", "km")
    seeder = DataMapSeeder(declared=[], expected=[fc])
    val = seeder.value_for("ev_vehicle_status", "range_km")
    assert val is not None
    gaps = seeder.gaps_report()
    assert len(gaps) == 1
    assert gaps[0].db_column == "range_km"


def test_value_for_dedupes_gaps():
    """Calling value_for for the same expected mapping twice yields one gap entry."""
    fc = _fc("ev_vehicle_status", "range_km", "km")
    seeder = DataMapSeeder(declared=[], expected=[fc])
    seeder.value_for("ev_vehicle_status", "range_km")
    seeder.value_for("ev_vehicle_status", "range_km")
    assert len(seeder.gaps_report()) == 1


def test_value_for_missing_raises():
    """value_for raises KeyError when the pair is in neither declared nor expected."""
    seeder = DataMapSeeder(declared=[], expected=[])
    with pytest.raises(KeyError, match="ev_vehicle_status.nonexistent"):
        seeder.value_for("ev_vehicle_status", "nonexistent")


# ---------------------------------------------------------------------------
# realistic_value unit coverage
# ---------------------------------------------------------------------------

def test_realistic_value_units():
    """Check type and range for at least 6 known units."""
    rng = random.Random(0)

    kwh = realistic_value("kWh", rng=rng)
    assert isinstance(kwh, float)
    assert 5.0 <= kwh <= 80.0

    pct = realistic_value("%", rng=rng)
    assert isinstance(pct, float)
    assert 5.0 <= pct <= 100.0

    temp = realistic_value("degC", rng=rng)
    assert isinstance(temp, float)
    assert -10.0 <= temp <= 40.0

    dist = realistic_value("km", rng=rng)
    assert isinstance(dist, float)
    assert 0.0 <= dist <= 500.0

    voltage = realistic_value("V", rng=rng)
    assert isinstance(voltage, float)
    assert 200.0 <= voltage <= 500.0

    secs = realistic_value("s", rng=rng)
    assert isinstance(secs, int)
    assert 60 <= secs <= 7200

    flag = realistic_value("bool", rng=rng)
    assert isinstance(flag, bool)

    unknown = realistic_value("furlongs", rng=rng)
    assert unknown is None


# ---------------------------------------------------------------------------
# write_data_map_gap_report tests
# ---------------------------------------------------------------------------

def test_write_data_map_gap_report(tmp_path):
    """Gap report contains both DataMapping blocks grouped by table."""
    gaps = [
        _fc("ev_vehicle_status", "range_km", "km", notes="estimated range"),
        _fc("ev_battery_status", "soc_pct", "%", notes="state of charge"),
    ]
    out = tmp_path / "reports" / "gaps.md"
    write_data_map_gap_report(gaps, out)

    assert out.exists()
    text = out.read_text()

    assert "Total gaps: 2" in text
    assert "## ev_vehicle_status" in text
    assert "## ev_battery_status" in text
    assert 'db_column="range_km"' in text
    assert 'db_column="soc_pct"' in text
    assert text.count("DataMapping(") == 2


def test_write_data_map_gap_report_empty(tmp_path):
    """Empty gaps list produces a 'No gaps detected' message."""
    out = tmp_path / "gaps.md"
    write_data_map_gap_report([], out)

    assert out.exists()
    text = out.read_text()
    assert "No gaps detected" in text
    assert "DataMapping(" not in text
