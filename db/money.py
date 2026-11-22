"""Money rounding; the one place a total is rounded to the cent."""

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def to_cents(value: Decimal) -> Decimal:
    """Round to the cent, half up."""
    return value.quantize(_CENT, ROUND_HALF_UP)
