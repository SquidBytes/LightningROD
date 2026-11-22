"""Money rounding; the one place a total or a rate is rounded."""

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")
_RATE = Decimal("0.0001")


def to_cents(value: Decimal) -> Decimal:
    """Round to the cent, half up."""
    return value.quantize(_CENT, ROUND_HALF_UP)


def to_rate(value: Decimal) -> Decimal:
    """Round a per-unit price to four places, half up."""
    return value.quantize(_RATE, ROUND_HALF_UP)
