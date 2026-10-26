"""Read and update `data_source_configs` rows with secrets sealed at rest.

Writers merge only the keys they own onto the row's current values, so a
runtime refreshing tokens and a settings save editing credentials cannot
overwrite each other's fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.data_source_config import DataSourceConfig
from web.services.sources.config_secrets import OpenedConfig, open_config, seal_config


async def find_row(
    db: AsyncSession, source_name: str, instance_label: str = "default"
) -> DataSourceConfig | None:
    result = await db.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == source_name,
            DataSourceConfig.instance_label == instance_label,
        )
    )
    return result.scalar_one_or_none()


def read_row(row: DataSourceConfig | None) -> OpenedConfig:
    if row is None:
        return OpenedConfig({})
    return open_config(row.source_name, row.config_json)


async def merge_into_row(
    db: AsyncSession,
    source_name: str,
    updates: dict[str, Any],
    *,
    instance_label: str = "default",
    row: DataSourceConfig | None = None,
) -> DataSourceConfig:
    """Merge `updates` over the stored config, seal, and commit; inserts if absent."""
    row = row or await find_row(db, source_name, instance_label)
    base = read_row(row).values if row is not None else {}
    # Sealing first means a missing key raises before the row is touched.
    sealed = seal_config(source_name, {**base, **updates})
    if row is None:
        row = DataSourceConfig(
            source_name=source_name,
            instance_label=instance_label,
            config_json=sealed,
            enabled=True,
        )
        db.add(row)
    else:
        row.config_json = sealed
        row.updated_at = datetime.now(UTC)
    await db.commit()
    return row
