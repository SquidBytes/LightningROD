"""Pure unit-to-metric conversion.

Replaces web/services/hass_processor.normalize_value. The key difference:
`to_metric` dispatches on an explicit, declared `source_unit` string supplied
by the caller (typically an adapter's FIELD_CONTRACTS entry). There is no
process-global flag, no runtime unit detection, and no silent fallback.
Unknown source_unit raises UnknownSourceUnit so callers can log full context
and fail loudly.

Supported source units (target in parens):
    km, mi          -> km
    kmh, mph        -> km/h
    degC, degF      -> degC
    kWh, Wh         -> kWh
    s, seconds      -> s (passthrough)
"""

import logging
from typing import Optional

logger = logging.getLogger("lightningrod.units")


class UnknownSourceUnit(ValueError):
    """Raised when to_metric is called with an unrecognized source_unit."""


# Conversion constants — SI-exact where possible
_MILES_TO_KM = 1.609344
# Wh -> kWh is exactly 1/1000 (no constant needed)


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def to_metric(value, source_unit: str) -> Optional[float]:
    """Convert `value` from `source_unit` to the canonical metric unit.

    Returns None when `value` is None or cannot be coerced to float.
    Raises UnknownSourceUnit when `source_unit` is not in the supported set.
    """
    numeric = _safe_float(value)
    if numeric is None:
        return None

    unit = source_unit.strip() if isinstance(source_unit, str) else ""

    # --- passthrough (already metric) ---
    if unit in ("km", "kmh", "degC", "kWh", "s", "seconds"):
        return numeric

    # --- distance ---
    if unit == "mi":
        return numeric * _MILES_TO_KM
    if unit == "mph":
        return numeric * _MILES_TO_KM

    # --- temperature ---
    if unit in ("degF", "F"):
        return (numeric - 32.0) * 5.0 / 9.0

    # --- energy ---
    if unit == "Wh":
        return numeric / 1000.0

    raise UnknownSourceUnit(
        f"to_metric: unrecognized source_unit={source_unit!r} (value={value!r}). "
        "Add a branch here or fix the FIELD_CONTRACTS entry."
    )
