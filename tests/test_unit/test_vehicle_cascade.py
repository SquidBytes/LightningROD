"""Unit tests for the split trim_level + battery_option cascade lookup.
split EVVehicle.trim into trim_level + battery_option and
seeded a 65-row preset matrix. rewires the vehicle edit modal and
filter bar to use both fields and makes the cascade auto-fill re-resolve on
any upstream change. The root cause of the pre-27 cascade bug was that the
single `trim` input conflated "Lariat SR" and "Lariat ER" -- selecting either
one returned an ambiguous match and the battery fields never populated.
This file is the regression surface for that bug: each test below would have
been impossible to satisfy with the single-field cascade.
See:
web/queries/vehicles.py::lookup_battery_values
"""
from web.queries.vehicles import lookup_battery_values


def test_cascade_resolves_both_fields():
    """A 2024 Lightning Lariat ER resolves to the full ER pack (131/143)."""
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 2024, "Lariat", "Extended Range"
    ) == (131.0, 143.0)


def test_cascade_rejects_partial_match():
    """Missing battery_option is not enough to resolve -- lookup returns None.

    Pre-27, a single `trim='Lariat'` input would ambiguously match both SR and
    ER rows and the caller silently picked the first. Post-27, battery_option
    is required for an unambiguous resolution.
    """
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 2024, "Lariat", None
    ) is None


def test_cascade_lariat_sr_vs_er_distinguishable():
    """Same year/trim_level, different battery_option -> different pack values.
    This is the explicit regression test for the cascade bug: 2024
    Lariat SR (98/108) must differ from 2024 Lariat ER (131/143).
    """
    sr = lookup_battery_values(
        "Ford", "F-150 Lightning", 2024, "Lariat", "Standard Range"
    )
    er = lookup_battery_values(
        "Ford", "F-150 Lightning", 2024, "Lariat", "Extended Range"
    )
    assert sr == (98.0, 108.0)
    assert er == (131.0, 143.0)
    assert sr != er


def test_cascade_flash_2025_collapsed_single_row():
    """2025 Flash uses the ER-123 pack (123/135), not the full ER pack.
    §5 Q1: MY2025 Flash swaps to the 123 kWh usable pack while the
    rest of the 2025 lineup keeps the 131 kWh pack. The cascade must honor
    that single-row swap.
    """
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 2025, "Flash", "Extended Range"
    ) == (123.0, 135.0)


def test_cascade_stx_alias_to_xlt():
    """MY2026 XLT ER resolves to the 123/135 ER-123 pack.
    §5 Q3 noted the "STX" marketing name is treated as an alias
    for XLT in our catalog -- the catalog stores XLT and the user enters XLT
    via the trim_level datalist (populated from VEHICLE_PRESETS). This test
    pins the post-alias lookup so future edits cannot accidentally split XLT
    and STX into different rows.
    """
    assert lookup_battery_values(
        "Ford", "F-150 Lightning", 2026, "XLT", "Extended Range"
    ) == (123.0, 135.0)
