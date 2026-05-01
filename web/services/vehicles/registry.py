"""Vehicle-profile registry (eager init at import time).

VehicleRegistry.get(make) returns the matching VehicleProfile or None.
Today: Ford only. Adding a future OEM = one new directory + one new entry.
"""

from __future__ import annotations

from web.services.vehicles.base import VehicleProfile
from web.services.vehicles.ford import FordProfile


class VehicleRegistry:
    """Eager class-level dict of make -> VehicleProfile instances."""

    _profiles: dict[str, VehicleProfile] = {
        "Ford": FordProfile(),
    }

    @classmethod
    def get(cls, make: str) -> VehicleProfile | None:
        return cls._profiles.get(make)
