"""Vehicle profile package shape tests (Protocol + Registry + FordProfile).

Validates the relocated package structure under web/services/vehicles/:
- VehicleProfile Protocol with presets() and display_name()
- VehiclePresetRow frozen dataclass with the 8-field shape
- VehicleRegistry.get(make) eager lookup
- FordProfile providing the 65-row Lightning + Mach-E catalog

Row counts mirror tests/test_unit/test_vehicle_presets.py (35 Lightning + 30 Mach-E).
"""
from dataclasses import FrozenInstanceError, asdict

import pytest
from web.services.vehicles.base import VehiclePresetRow, VehicleProfile
from web.services.vehicles.ford import FordProfile
from web.services.vehicles.ford.presets import VEHICLE_PRESETS
from web.services.vehicles.registry import VehicleRegistry


def test_vehicle_preset_row_is_frozen():
    """VehiclePresetRow rejects attribute mutation (frozen dataclass)."""
    row = VEHICLE_PRESETS[0]
    with pytest.raises(FrozenInstanceError):
        row.make = "X"


def test_vehicle_preset_row_field_set_matches_legacy_keys():
    """asdict produces the 8-key dict shape the cascade JSON has always emitted."""
    row = VEHICLE_PRESETS[0]
    expected_keys = {
        "make", "model", "trim_level", "battery_option",
        "battery_usable_kwh", "battery_gross_kwh", "year_min", "year_max",
    }
    assert set(asdict(row).keys()) == expected_keys


def test_total_preset_count():
    """65 rows total — 35 Lightning + 30 Mach-E."""
    assert len(VEHICLE_PRESETS) == 65


def test_lightning_row_count():
    """35 Lightning rows across MY2022-MY2026."""
    assert len([r for r in VEHICLE_PRESETS if r.model == "F-150 Lightning"]) == 35


def test_mache_row_count():
    """30 Mach-E rows across MY2021-MY2026."""
    assert len([r for r in VEHICLE_PRESETS if r.model == "Mustang Mach-E"]) == 30


def test_ford_profile_implements_vehicle_profile():
    """FordProfile duck-types to the runtime_checkable Protocol."""
    assert isinstance(FordProfile(), VehicleProfile)


def test_ford_profile_display_name():
    assert FordProfile().display_name() == "Ford"


def test_ford_profile_presets_returns_list_of_preset_rows():
    profile = FordProfile()
    rows = profile.presets()
    assert len(rows) == 65
    assert all(isinstance(r, VehiclePresetRow) for r in rows)


def test_registry_returns_ford_profile_for_ford():
    profile = VehicleRegistry.get("Ford")
    assert profile is not None
    assert profile.display_name() == "Ford"


def test_registry_returns_none_for_unknown_make():
    assert VehicleRegistry.get("Toyota") is None


def test_registry_get_returns_isinstance_vehicle_profile():
    profile = VehicleRegistry.get("Ford")
    assert isinstance(profile, VehicleProfile)
