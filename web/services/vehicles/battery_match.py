"""Identify a vehicle's battery from a measured pack capacity and the OEM presets."""

from __future__ import annotations

from dataclasses import dataclass

from web.services.vehicles.base import VehiclePresetRow
from web.services.vehicles.registry import VehicleRegistry

# A reported capacity is matched to the preset whose gross kWh is nearest, if
# it lies within 90-103% of it: packs lose capacity with age (a 2023 Lightning
# SR read 106.75 of 108), and a small overshoot allows for rounding. Beyond 10%
# loss a pack could be mistaken for the next size down, so it is left alone.
MIN_RATIO = 0.90
MAX_RATIO = 1.03


@dataclass(frozen=True)
class BatteryMatch:
    battery_option: str
    usable_kwh: float | None
    gross_kwh: float | None
    trim_level: str | None


def presets_for(make: str | None, model: str | None, year: int | None) -> list[VehiclePresetRow]:
    profile = VehicleRegistry.get(make or "")
    if profile is None or not model:
        return []
    return [
        p
        for p in profile.presets()
        if p.model == model and (year is None or p.year_min <= year <= p.year_max)
    ]


def preset_model(make: str | None, model_name: str | None, electric: bool) -> str | None:
    """The preset model a source's model name refers to, e.g. "F-150" -> "F-150 Lightning"."""
    profile = VehicleRegistry.get(make or "")
    if profile is None or not model_name:
        return None
    wanted = model_name.strip().lower()
    names = {p.model for p in profile.presets()}
    exact = [n for n in names if n.lower() == wanted]
    if exact:
        return exact[0]
    # The presets are EVs only, so a partial name match is trusted only for an EV.
    partial = [n for n in names if wanted in n.lower()]
    return partial[0] if electric and len(partial) == 1 else None


def match_battery(presets: list[VehiclePresetRow], capacity_kwh: float) -> BatteryMatch | None:
    """The battery `capacity_kwh` (gross) identifies; None when it is not clear."""
    if capacity_kwh <= 0 or not presets:
        return None
    nearest = min(presets, key=lambda p: abs(p.battery_gross_kwh - capacity_kwh))
    if not MIN_RATIO <= capacity_kwh / nearest.battery_gross_kwh <= MAX_RATIO:
        return None
    same_pack = [
        p for p in presets
        if p.battery_gross_kwh == nearest.battery_gross_kwh
        and p.battery_usable_kwh == nearest.battery_usable_kwh
    ]
    options = {p.battery_option for p in same_pack}
    if len(options) != 1:
        return None
    trims = {p.trim_level for p in same_pack}
    return BatteryMatch(
        battery_option=nearest.battery_option,
        usable_kwh=nearest.battery_usable_kwh,
        gross_kwh=nearest.battery_gross_kwh,
        # Several trims share a pack; only a single candidate names the trim.
        trim_level=trims.pop() if len(trims) == 1 else None,
    )
