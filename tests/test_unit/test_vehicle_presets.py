"""Unit tests for VEHICLE_PRESETS catalog shape and lookup_battery_values helper.
seeds 34 Lightning + 29 Mach-E rows from §2-3 and
exposes `lookup_battery_values(make, model, year, trim_level, battery_option)`
for 's cascade auto-fill. These tests pin:
- row counts per model (drift detector for changes),
- schema (no legacy `trim` key),
- representative cascade lookups covering the two pack-swap edges
(Flash 2024 full ER vs 2025 ER-123, Mach-E 2023 LFP transition),
- miss returns None.
"""
from web.queries.vehicles import VEHICLE_PRESETS, lookup_battery_values


def test_lightning_row_count():
    """§2 seeds 35 Lightning rows across MY2022-MY2026.
    must_haves.truths said 34, but §2's authoritative
    table has 35 rows (MY2022-MY2025 each have 7 SR/ER + Flash-or-Platinum
    rows summing to 7, 7, 8, 8, plus MY2026's 5). The plan's Action step 1
    says "copy every row" from RESEARCH — so RESEARCH wins, count is 35.
    """
    assert len([r for r in VEHICLE_PRESETS if r["model"] == "F-150 Lightning"]) == 35


def test_mache_row_count():
    """§3 seeds 30 Mach-E rows across MY2021-MY2026.
    must_haves and RESEARCH §6 both said 29, but the RESEARCH §3
    row-by-row table has 30 entries (5 per MY × 6 MYs). RESEARCH §3's table
    wins per Action step 1 ("copy every row"), so count is 30.
    """
    assert len([r for r in VEHICLE_PRESETS if r["model"] == "Mustang Mach-E"]) == 30


def test_no_legacy_trim_key():
    """Every row must use trim_level + battery_option, never the pre-27-03 `trim` key."""
    for row in VEHICLE_PRESETS:
        assert "trim_level" in row, row
        assert "battery_option" in row, row
        assert "trim" not in row, row


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
