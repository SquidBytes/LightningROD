"""Request parameter types shared by routes."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


# An empty form field arrives as "", which a typed parameter would reject.
OptionalInt = Annotated[int | None, BeforeValidator(_blank_to_none)]
OptionalDecimal = Annotated[Decimal | None, BeforeValidator(_blank_to_none)]
