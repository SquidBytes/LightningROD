"""Money values are stored as Decimal; callers pass floats or Decimals."""

from decimal import Decimal
from typing import overload


@overload
def to_money(value: float | Decimal) -> Decimal: ...
@overload
def to_money(value: float | Decimal | None) -> Decimal | None: ...
def to_money(value: float | Decimal | None) -> Decimal | None:
    """Convert a float via its repr so 0.1 stores as 0.1, not 0.1000000000000000055."""
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))
