"""Money is Decimal dollars; parsing and cost arithmetic for the web layer."""

from decimal import Decimal, InvalidOperation
from typing import overload

from db.money import to_cents

__all__ = ["ZERO", "cost_of", "per_unit", "to_cents", "to_money"]

ZERO = Decimal("0.00")


@overload
def to_money(value: str | int | Decimal) -> Decimal: ...
@overload
def to_money(value: str | int | Decimal | None) -> Decimal | None: ...
def to_money(value: str | int | Decimal | None) -> Decimal | None:
    """Parse outside input straight to Decimal; blank text is None, NaN and infinity are refused."""
    if isinstance(value, float):
        raise TypeError("money must not pass through float; give text, int or Decimal")
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, str) and not value.strip():
        return None
    money = Decimal(value.strip() if isinstance(value, str) else value)
    if not money.is_finite():
        raise InvalidOperation(f"money must be a finite number, got {money}")
    return money


def cost_of(rate: Decimal, quantity: float) -> Decimal:
    """Rate times a measured quantity (kWh, gallons), rounded to the cent."""
    # The one place a float enters money arithmetic; its repr keeps 61.3 as 61.3.
    return to_cents(rate * Decimal(str(quantity)))


def per_unit(total: Decimal, quantity: float) -> float:
    """Dollars per measured unit, for display; a ratio, not money, so a float."""
    return float(total) / quantity
