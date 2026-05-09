"""Unit tests for VEHICLE_PRESETS catalog shape and lookup_battery_values helper.

Seeds 35 Lightning + 30 Mach-E rows and exposes
`lookup_battery_values(make, model, year, trim_level, battery_option)` for the
cascade auto-fill. These tests pin:
- row counts per model (drift detector for changes),
- attribute-access schema (post-relocation: VehiclePresetRow dataclass),
- representative cascade lookups covering the two pack-swap edges
  (Flash 2024 full ER vs 2025 ER-123, Mach-E 2023 LFP transition),
- miss returns None.
"""
from web.queries.vehicles import lookup_battery_values
from web.services.vehicles.ford.presets import VEHICLE_PRESETS


def test_lightning_row_count():
    """35 Lightning rows across MY2022-MY2026."""
    assert len([r for r in VEHICLE_PRESETS if r.model == "F-150 Lightning"]) == 35


def test_mache_row_count():
    """30 Mach-E rows across MY2021-MY2026."""
    assert len([r for r in VEHICLE_PRESETS if r.model == "Mustang Mach-E"]) == 30


def test_rows_use_attribute_access():
    """Every row exposes the 8 documented fields as attributes."""
    for row in VEHICLE_PRESETS:
        assert hasattr(row, "trim_level"), row
        assert hasattr(row, "battery_option"), row
        assert hasattr(row, "make"), row
        assert hasattr(row, "model"), row
        assert hasattr(row, "battery_usable_kwh"), row
        assert hasattr(row, "battery_gross_kwh"), row
        assert hasattr(row, "year_min"), row
        assert hasattr(row, "year_max"), row


def test_lookup_lightning_flash_2024_er():
    """MY2024 Flash still uses the full 131/143 ER pack."""
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 2024, "Flash", "Extended Range"
    ) == (131.0, 143.0)


def test_lookup_lightning_pro_2026_er():
    """MY2026 Pro is ER-123 (lower-capacity pack adopted fleet-wide)."""
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 2026, "Pro", "Extended Range"
    ) == (123.0, 135.0)


def test_lookup_mache_premium_sr_2023():
    """MY2023 Mach-E SR switches to LFP chemistry at 70/72."""
    assert lookup_battery_values(
        "Ford", "Mustang Mach-E", 2023, "Premium", "Standard Range"
    ) == (70.0, 72.0)


def test_lookup_mache_select_sr_2021():
    """MY2021 Mach-E SR is the original NCM pack at 68/75.7."""
    assert lookup_battery_values(
        "Ford", "Mustang Mach-E", 2021, "Select", "Standard Range"
    ) == (68.0, 75.7)


def test_lookup_miss_returns_none():
    """Year out of range -> miss -> None. Guards against accidental fallback matches."""
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 1999, "Pro", "Standard Range"
    ) is None
