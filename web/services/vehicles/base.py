"""Vehicle-profile Protocol + preset row dataclass.

Concrete profiles live under web/services/vehicles/<oem>/__init__.py
(e.g. ford/__init__.py provides FordProfile).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class VehiclePresetRow:
    """One make/model/trim/battery-option/year-range row in the OEM preset catalog."""

    make: str
    model: str
    trim_level: str
    battery_option: str
    battery_usable_kwh: float
    battery_gross_kwh: float
    year_min: int
    year_max: int


@runtime_checkable
class VehicleProfile(Protocol):
    """Per-OEM profile surface — minimal v1: presets + display name only."""

    def presets(self) -> list[VehiclePresetRow]: ...
    def display_name(self) -> str: ...
