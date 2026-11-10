"""Unit-string normalisation and the unit-to-family tables."""

from __future__ import annotations

from typing import Any

# Normalised unit string -> semantic family. Lets a read-time unit_of_measurement
# for one field (e.g. the state's "mi") be rejected for another (a temperature).
FAMILY_OF_UNIT: dict[str, str] = {
    "km": "distance",
    "mi": "distance",
    "kmh": "speed",
    "mph": "speed",
    "degC": "temperature",
    "degF": "temperature",
    "F": "temperature",
    "kWh": "energy",
    "Wh": "energy",
    "kW": "power",
    "s": "duration",
    "seconds": "duration",
}

# (family, unit system) -> the unit Home Assistant emits for it.
UNIT_FOR_FAMILY: dict[tuple[str, str], str] = {
    ("distance", "metric"): "km",
    ("distance", "imperial"): "mi",
    ("speed", "metric"): "kmh",
    ("speed", "imperial"): "mph",
    ("temperature", "metric"): "degC",
    ("temperature", "imperial"): "degF",
}


def normalize_unit_string(raw: Any) -> str | None:
    """Normalize an HA `unit_of_measurement` string to a to_metric key.

    None for spellings to_metric would not recognize, so callers can fall back.
    """
    if not isinstance(raw, str):
        return None
    u = raw.strip()
    lowered = u.lower()
    if lowered in ("mi", "mile", "miles"):
        return "mi"
    if lowered in ("km", "kilometer", "kilometers"):
        return "km"
    if lowered in ("mph", "mi/h"):
        return "mph"
    if lowered in ("kmh", "km/h"):
        return "kmh"
    if u in ("°C", "degC") or lowered in ("c", "°c"):
        return "degC"
    if u in ("°F", "degF") or lowered in ("f", "°f"):
        return "degF"
    if u == "kWh" or lowered == "kwh":
        return "kWh"
    if u == "Wh" or lowered == "wh":
        return "Wh"
    if lowered in ("s", "sec", "seconds"):
        return "s"
    return None
