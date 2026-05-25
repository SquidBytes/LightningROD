"""Database model for data source config (one row per configured connection)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base
from db.types import JSONStorage

TIMESTAMPTZ = DateTime(timezone=True)


class DataSourceConfig(Base):
    """Per-source connection config; one row per (source_name, instance_label)."""

    __tablename__ = "data_source_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    instance_label: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONStorage, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "instance_label",
            name="uq_data_source_configs_name_label",
        ),
    )
