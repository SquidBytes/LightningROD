"""Repair groups a reviewer chose to skip."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

TIMESTAMPTZ = DateTime(timezone=True)


class RepairSkip(Base):
    """One review group, by its stable key, that an operation must leave alone."""

    __tablename__ = "repair_skip"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    group_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("operation", "group_key", name="uq_repair_skip_operation_key"),
    )
