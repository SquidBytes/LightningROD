"""What FordConnect's /garage and telemetry say about the vehicle itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from db.models.vehicle import EVVehicle
from web.services.vehicles.auto_create import fill_vehicle
from web.services.vehicles.battery_match import match_battery, preset_model, presets_for

# The spec names it nickName; the others guard against a renamed key.
NICKNAME_KEYS = ("nickName", "nickname", "vehicleNickname", "vehicleNickName")
# /garage abbreviates make to its first letter.
MAKES = {"F": "Ford", "L": "Lincoln"}
ELECTRIC = {"BEV", "ELECTRIC", "EV"}


@dataclass(frozen=True)
class GarageFacts:
    facts: dict[str, Any] = field(default_factory=dict)
    display_name: str | None = None
    # Names we may have set automatically before; they are not the user's.
    auto_names: tuple[str, ...] = ()


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""


def garage_facts(garage: dict) -> GarageFacts:
    raw_make = _text(garage, "make")
    make = MAKES.get(raw_make.upper(), raw_make) or None
    model_name = _text(garage, "modelName")
    electric = _text(garage, "engineType").upper() in ELECTRIC
    model = preset_model(make, model_name, electric) or model_name or None
    year_text = _text(garage, "modelYear")
    year = int(year_text) if year_text.isdigit() else None
    nickname = next((_text(garage, k) for k in NICKNAME_KEYS if _text(garage, k)), "")
    fallback = " ".join(p for p in (year_text, model_name) if p)
    auto_names = tuple(
        n for n in {fallback, _text(garage, "vehicleType"), f"{year_text} {model}".strip()} if n
    )
    return GarageFacts(
        facts={"make": make, "model": model, "year": year},
        display_name=nickname or fallback or None,
        auto_names=auto_names,
    )


def fill_battery(vehicle: EVVehicle, capacity_kwh: float) -> dict[str, Any]:
    """Fill the battery columns from the preset a measured gross capacity identifies."""
    if vehicle.battery_option and vehicle.battery_capacity_kwh and vehicle.battery_gross_capacity_kwh:
        return {}
    match = match_battery(presets_for(vehicle.make, vehicle.model, vehicle.year), capacity_kwh)
    if match is None:
        return {}
    if vehicle.battery_option and vehicle.battery_option != match.battery_option:
        # The user chose a different pack; do not mix its option with our capacities.
        return {}
    return fill_vehicle(
        vehicle,
        {
            "battery_option": match.battery_option,
            "battery_capacity_kwh": match.usable_kwh,
            "battery_gross_capacity_kwh": match.gross_kwh,
            "trim_level": match.trim_level,
        },
    )
