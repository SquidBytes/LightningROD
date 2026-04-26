"""Unit system conversion and labels.

All DB values are stored in metric (km, °C, km/kWh, L/100km, liters). This
module converts to/from display units based on two independent user settings:

    distance_unit: "us"  -> mi, mi/kWh, MPG, gal, mph
                   "metric" -> km, km/kWh, L/100km, L, km/h
    temp_unit:     "us"  -> °F
                   "metric" -> °C

Users can choose them independently (e.g. mi/kWh with °C).
"""


# Conversion constants
KM_PER_MI = 1.60934
MI_PER_KM = 0.621371
GAL_PER_LITER = 0.264172
LITER_PER_GAL = 3.78541

# Valid values for settings
DISTANCE_UNIT_VALUES = ("us", "metric")
TEMP_UNIT_VALUES = ("us", "metric")

# Label dicts — merged into a single `units` dict for template use
DISTANCE_UNITS = {
    "us": {
        "distance_label": "mi",
        "distance_long": "miles",
        "range_label": "mi",
        "speed_label": "mph",
        "efficiency_label": "mi/kWh",
        "fuel_efficiency_label": "MPG",
        "fuel_volume_label": "gal",
    },
    "metric": {
        "distance_label": "km",
        "distance_long": "kilometers",
        "range_label": "km",
        "speed_label": "km/h",
        "efficiency_label": "km/kWh",
        "fuel_efficiency_label": "L/100km",
        "fuel_volume_label": "L",
    },
}

TEMP_UNITS = {
    "us": {"temp_label": "\u00b0F"},
    "metric": {"temp_label": "\u00b0C"},
}


def _normalize_distance_unit(value: str | None) -> str:
    return value if value in DISTANCE_UNIT_VALUES else "us"


def _normalize_temp_unit(value: str | None) -> str:
    return value if value in TEMP_UNIT_VALUES else "us"


def get_units(distance_unit: str = "us", temp_unit: str = "us") -> dict:
    """Return merged label dict for template context."""
    d = _normalize_distance_unit(distance_unit)
    t = _normalize_temp_unit(temp_unit)
    return {**DISTANCE_UNITS[d], **TEMP_UNITS[t]}


# ---------------------------------------------------------------------------
# Outbound conversions: metric (DB canonical) -> display unit
# ---------------------------------------------------------------------------


def convert_distance(km: float | None, distance_unit: str) -> float | None:
    """Convert km (DB) to display unit."""
    if km is None:
        return None
    return float(km) * MI_PER_KM if _normalize_distance_unit(distance_unit) == "us" else float(km)


def convert_efficiency(km_per_kwh: float | None, distance_unit: str) -> float | None:
    """Convert km/kWh (DB) to display efficiency unit (mi/kWh or km/kWh)."""
    if km_per_kwh is None:
        return None
    return float(km_per_kwh) * MI_PER_KM if _normalize_distance_unit(distance_unit) == "us" else float(km_per_kwh)


def convert_fuel_efficiency(l_per_100km: float | None, distance_unit: str) -> float | None:
    """Convert L/100km (DB) to display unit (MPG or L/100km).

    MPG = 235.215 / L_per_100km
    """
    if l_per_100km is None or l_per_100km == 0:
        return None
    if _normalize_distance_unit(distance_unit) == "us":
        return 235.215 / float(l_per_100km)
    return float(l_per_100km)


def convert_fuel_volume(liters: float | None, distance_unit: str) -> float | None:
    """Convert liters (DB) to display unit (gallons or liters)."""
    if liters is None:
        return None
    return float(liters) * GAL_PER_LITER if _normalize_distance_unit(distance_unit) == "us" else float(liters)


def convert_speed(kmh: float | None, distance_unit: str) -> float | None:
    """Convert km/h (DB) to display unit (mph or km/h)."""
    if kmh is None:
        return None
    return float(kmh) * MI_PER_KM if _normalize_distance_unit(distance_unit) == "us" else float(kmh)


def convert_temp(celsius: float | None, temp_unit: str) -> float | None:
    """Convert °C (DB) to display unit (°F or °C)."""
    if celsius is None:
        return None
    if _normalize_temp_unit(temp_unit) == "us":
        return float(celsius) * 9 / 5 + 32
    return float(celsius)


# ---------------------------------------------------------------------------
# Inbound conversions: display unit (user input) -> metric (DB canonical)
# ---------------------------------------------------------------------------


def to_metric_distance(value: float | None, distance_unit: str) -> float | None:
    """Convert user-entered distance to km for storage."""
    if value is None:
        return None
    return float(value) * KM_PER_MI if _normalize_distance_unit(distance_unit) == "us" else float(value)


def to_metric_fuel_efficiency(value: float | None, distance_unit: str) -> float | None:
    """Convert user-entered fuel economy to L/100km for storage.

    US input is MPG -> L/100km = 235.215 / MPG.
    """
    if value is None or value == 0:
        return None
    if _normalize_distance_unit(distance_unit) == "us":
        return 235.215 / float(value)
    return float(value)


def to_metric_fuel_volume(value: float | None, distance_unit: str) -> float | None:
    """Convert user-entered fuel volume to liters for storage."""
    if value is None:
        return None
    return float(value) * LITER_PER_GAL if _normalize_distance_unit(distance_unit) == "us" else float(value)


def to_metric_temp(value: float | None, temp_unit: str) -> float | None:
    """Convert user-entered temperature to °C for storage."""
    if value is None:
        return None
    if _normalize_temp_unit(temp_unit) == "us":
        return (float(value) - 32) * 5 / 9
    return float(value)
