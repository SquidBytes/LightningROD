"""Ford vehicle preset rows (relocated from web/queries/vehicles.py).

Each entry carries BOTH the usable and gross pack capacity because the two
values serve different calculations:

  - battery_usable_kwh -> what energy_kwh can be compared against (drive
    efficiency, gas-equivalent fallback in comparisons.py). This is the
    "driver-facing" number you see on marketing material.
  - battery_gross_kwh  -> total installed cell capacity. FordPass reports
    this via the `maximumBatteryCapacity` attribute, and battery health /
    degradation math on /battery must compare against it (otherwise a fresh
    pack reads >100% health).
"""

from __future__ import annotations

from web.services.vehicles.base import VehiclePresetRow

VEHICLE_PRESETS: list[VehiclePresetRow] = [
    # -----------------------------------------------------------------
    # Ford F-150 Lightning — 35 rows, MY2022-MY2026
    # -----------------------------------------------------------------
    # MY2022 — 7 rows
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Platinum", battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2022, year_max=2022),
    # MY2023 — 7 rows
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Platinum", battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2023, year_max=2023),
    # MY2024 — 8 rows (Flash debuts)
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Flash",    battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Platinum", battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2024, year_max=2024),
    # MY2025 — 8 rows (Flash uses lower-capacity ER-123 pack)
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Flash",    battery_option="Extended Range", battery_usable_kwh=123.0, battery_gross_kwh=135.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Standard Range", battery_usable_kwh=98.0,  battery_gross_kwh=108.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Platinum", battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2025, year_max=2025),
    # MY2026 — 5 rows (SR discontinued fleet-wide; Pro/XLT/Flash on ER-123, Lariat/Platinum retain full ER)
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Pro",      battery_option="Extended Range", battery_usable_kwh=123.0, battery_gross_kwh=135.0, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="XLT",      battery_option="Extended Range", battery_usable_kwh=123.0, battery_gross_kwh=135.0, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Flash",    battery_option="Extended Range", battery_usable_kwh=123.0, battery_gross_kwh=135.0, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Lariat",   battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="F-150 Lightning", trim_level="Platinum", battery_option="Extended Range", battery_usable_kwh=131.0, battery_gross_kwh=143.0, year_min=2026, year_max=2026),

    # -----------------------------------------------------------------
    # Ford Mustang Mach-E — 30 rows, MY2021-MY2026
    # -----------------------------------------------------------------
    # MY2021 — 5 rows (NCM SR 68/75.7, ER 88/98.8)
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Select",             battery_option="Standard Range", battery_usable_kwh=68.0, battery_gross_kwh=75.7, year_min=2021, year_max=2021),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Standard Range", battery_usable_kwh=68.0, battery_gross_kwh=75.7, year_min=2021, year_max=2021),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Extended Range", battery_usable_kwh=88.0, battery_gross_kwh=98.8, year_min=2021, year_max=2021),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="California Route 1", battery_option="Extended Range", battery_usable_kwh=88.0, battery_gross_kwh=98.8, year_min=2021, year_max=2021),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="GT",                 battery_option="Extended Range", battery_usable_kwh=88.0, battery_gross_kwh=98.8, year_min=2021, year_max=2021),
    # MY2022 — 5 rows (same packs as MY2021)
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Select",             battery_option="Standard Range", battery_usable_kwh=68.0, battery_gross_kwh=75.7, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Standard Range", battery_usable_kwh=68.0, battery_gross_kwh=75.7, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Extended Range", battery_usable_kwh=88.0, battery_gross_kwh=98.8, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="California Route 1", battery_option="Extended Range", battery_usable_kwh=88.0, battery_gross_kwh=98.8, year_min=2022, year_max=2022),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="GT",                 battery_option="Extended Range", battery_usable_kwh=88.0, battery_gross_kwh=98.8, year_min=2022, year_max=2022),
    # MY2023 — 5 rows (LFP SR 70/72 transition, ER usable grows to 91)
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Select",             battery_option="Standard Range", battery_usable_kwh=70.0, battery_gross_kwh=72.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Standard Range", battery_usable_kwh=70.0, battery_gross_kwh=72.0, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="California Route 1", battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2023, year_max=2023),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="GT",                 battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2023, year_max=2023),
    # MY2024 — 5 rows (LFP SR 72/73, Cal Route 1 discontinued, Rally debuts)
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Select",             battery_option="Standard Range", battery_usable_kwh=72.0, battery_gross_kwh=73.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Standard Range", battery_usable_kwh=72.0, battery_gross_kwh=73.0, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="GT",                 battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2024, year_max=2024),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Rally",              battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2024, year_max=2024),
    # MY2025 — 5 rows (same as MY2024)
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Select",             battery_option="Standard Range", battery_usable_kwh=72.0, battery_gross_kwh=73.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Standard Range", battery_usable_kwh=72.0, battery_gross_kwh=73.0, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="GT",                 battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2025, year_max=2025),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Rally",              battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2025, year_max=2025),
    # MY2026 — 5 rows (same as MY2025)
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Select",             battery_option="Standard Range", battery_usable_kwh=72.0, battery_gross_kwh=73.0, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Standard Range", battery_usable_kwh=72.0, battery_gross_kwh=73.0, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Premium",            battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="GT",                 battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2026, year_max=2026),
    VehiclePresetRow(make="Ford", model="Mustang Mach-E", trim_level="Rally",              battery_option="Extended Range", battery_usable_kwh=91.0, battery_gross_kwh=98.8, year_min=2026, year_max=2026),
]
