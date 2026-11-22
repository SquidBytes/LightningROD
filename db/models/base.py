"""Declarative base class for SQLAlchemy models."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from sqlalchemy import BigInteger, DateTime, Double, Numeric, SmallInteger, String, func
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from web.money import to_cents

# All timestamps carry timezone info.
TIMESTAMPTZ = DateTime(timezone=True)

# Parser versions stamped on ingested rows; each re-parse op compares its own.
HA_INGEST_SCHEMA_VERSION = 2  # adapter-driven ingest with declared source units
FCON_INGEST_SCHEMA_VERSION = 2


class _DecimalOnly(TypeDecorator[Decimal]):
    """Shared float rejection for the money column types."""

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if isinstance(value, float):
            raise TypeError(f"money column got float {value!r}; give a Decimal")
        return value


class Cents(_DecimalOnly):
    """Decimal dollars stored as whole cents; SQL arithmetic on the column works in cents."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> int | None:
        value = super().process_bind_param(value, dialect)
        return None if value is None else int(to_cents(Decimal(value)).scaleb(2))

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        # str() because avg() over cents comes back as a float on SQLite.
        return None if value is None else Decimal(str(value)).scaleb(-2)


class Rate(_DecimalOnly):
    """Per-unit price as Decimal, four decimal places."""

    impl = Numeric(12, 4)
    cache_ok = True


# Both are Decimal in Python; the Annotated alias picks the column type.
CentsAmount = Annotated[Decimal, "cents"]
RateAmount = Annotated[Decimal, "rate"]


class Base(DeclarativeBase):
    type_annotation_map = {float: Double(), CentsAmount: Cents(), RateAmount: Rate()}


class IngestMetaMixin:
    """Pipeline metadata shared by the ingested telemetry tables."""

    source_system: Mapped[str | None] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    original_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class IngestVersionMixin:
    """NULL = older rows with uncertain unit provenance; 2 = declared-unit ingest."""

    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
