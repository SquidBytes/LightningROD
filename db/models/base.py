"""Declarative base class for SQLAlchemy models."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric, SmallInteger, String, func
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# All timestamps carry timezone info.
TIMESTAMPTZ = DateTime(timezone=True)

# Parser versions stamped on ingested rows; each re-parse op compares its own.
HA_INGEST_SCHEMA_VERSION = 2  # adapter-driven ingest with declared source units
FCON_INGEST_SCHEMA_VERSION = 2


class FloatNumeric(TypeDecorator[float]):
    """NUMERIC read as float; SQLite hands back int for whole values otherwise."""

    impl = Numeric(asdecimal=False)
    cache_ok = True

    def process_result_value(self, value: Any, dialect: Dialect) -> float | None:
        return None if value is None else float(value)


class Money(TypeDecorator[Decimal]):
    """NUMERIC read as Decimal; a float is written via its repr, so 0.35 stores as 0.35."""

    impl = Numeric
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return Decimal(str(value)) if isinstance(value, float) else value


class Base(DeclarativeBase):
    type_annotation_map = {float: FloatNumeric(), Decimal: Money()}


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
