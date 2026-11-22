"""Money is Decimal dollars; totals are rounded to the cent here and nowhere else."""

from decimal import ROUND_HALF_UP, Decimal
from typing import overload

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")


def to_cents(value: Decimal) -> Decimal:
    """Round to the cent, half up."""
    return value.quantize(_CENT, ROUND_HALF_UP)


@overload
def to_money(value: str | int | Decimal) -> Decimal: ...
@overload
def to_money(value: str | int | Decimal | None) -> Decimal | None: ...
def to_money(value: str | int | Decimal | None) -> Decimal | None:
    """Parse outside input straight to Decimal; blank text is None."""
    if isinstance(value, float):
        raise TypeError("money must not pass through float; give text, int or Decimal")
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, str) and not value.strip():
        return None
    return Decimal(value.strip() if isinstance(value, str) else value)


def cost_of(rate: Decimal, quantity: float) -> Decimal:
    """Rate times a measured quantity (kWh, gallons), rounded to the cent."""
    # The one place a float enters money arithmetic; its repr keeps 61.3 as 61.3.
    return to_cents(rate * Decimal(str(quantity)))


def per_unit(total: Decimal, quantity: float) -> float:
    """Dollars per measured unit, for display; a ratio, not money, so a float."""
    return float(total) / quantity
