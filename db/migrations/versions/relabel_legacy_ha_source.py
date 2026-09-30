"""Relabel rows written as 'homeassistant' by pre-rename ingestion to 'ha_fordpass'.

The p31 rename only caught 'home_assistant'; early trip ingestion used the
unseparated form, which left those rows outside repairs and cross-source merges.

Revision ID: relabel_legacy_ha_source
Revises: repair_skip
Create Date: 2026-09-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "relabel_legacy_ha_source"
down_revision = "repair_skip"
branch_labels = None
depends_on = None

# Frozen copy of p31's list: a migration must not change with later code.
TABLES_WITH_SOURCE_SYSTEM: tuple[str, ...] = (
    "ev_charging_session",
    "ev_battery_status",
    "ev_location",
    "ev_trip_metrics",
    "ev_vehicle_status",
    "ev_charging_networks",
    "ev_location_lookup",
    "ev_vehicles",
)
LEGACY_LABEL = "homeassistant"
CURRENT_LABEL = "ha_fordpass"


def relabel_legacy_source(conn) -> None:
    """Rewrite the legacy label on every table that carries source_system."""
    for table in TABLES_WITH_SOURCE_SYSTEM:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET source_system = :new WHERE source_system = :old"
            ).bindparams(new=CURRENT_LABEL, old=LEGACY_LABEL)
        )


def upgrade() -> None:
    relabel_legacy_source(op.get_bind())


def downgrade() -> None:
    # Irreversible: relabelled rows can't be told apart from native ha_fordpass rows.
    pass
