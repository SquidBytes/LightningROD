"""Repair-run row snapshot model."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base
from db.types import JSONStorage

TIMESTAMPTZ = DateTime(timezone=True)


class RepairBackup(Base):
    """Pre-repair snapshot of one row, grouped into a restorable run by run_id."""

    __tablename__ = "repair_backup"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    row_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    row_json: Mapped[dict[str, Any]] = mapped_column(JSONStorage, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_repair_backup_run_id", "run_id"),)
