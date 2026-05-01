"""Ford OEM profile."""

from __future__ import annotations

from web.services.vehicles.base import VehiclePresetRow
from web.services.vehicles.ford.presets import VEHICLE_PRESETS


class FordProfile:
    """Ford-OEM VehicleProfile implementation (Lightning + Mach-E)."""

    def presets(self) -> list[VehiclePresetRow]:
        return list(VEHICLE_PRESETS)

    def display_name(self) -> str:
        return "Ford"
