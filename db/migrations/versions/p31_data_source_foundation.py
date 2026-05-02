"""Phase 31 data-source foundation: combined data move + value standardization.

Atomically:
  (a) Create data_source_configs (JSONStorage config_json, UNIQUE on
      (source_name, instance_label))
  (b) Seed one row from the existing app_settings ha_url + ha_token
  (c) Rewrite source_system 'home_assistant' -> 'ha_fordpass' across 8 tables
  (d) Add ev_vehicles.primary_source_id FK (nullable, ON DELETE SET NULL)
  (e) Backfill primary_source_id for vehicles whose source_system='ha_fordpass'
  (f) Delete app_settings ha_url + ha_token rows (LAST, so any earlier
      step's failure rolls back atomically)

Cross-dialect: JSONStorage TypeDecorator, sa.func.now() server defaults,
op.bulk_insert seed, parameterized op.execute for data fixups. No
per-dialect SQL branching.

Revision ID: p31_data_source_foundation
Revises: p30_drop_vstatus_legacy_cols
Create Date: 2026-05-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from db.types import JSONStorage

revision: str = "p31_data_source_foundation"
down_revision: str | Sequence[str] | None = "p30_drop_vstatus_legacy_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def upgrade() -> None:
    """Apply data-source foundation moves atomically."""
    # ---------------------------------------------------------------
    # (a) Create data_source_configs.
    # ---------------------------------------------------------------
    op.create_table(
        "data_source_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("instance_label", sa.String(), nullable=False),
        sa.Column("config_json", JSONStorage(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_name",
            "instance_label",
            name="uq_data_source_configs_name_label",
        ),
    )

    # ---------------------------------------------------------------
    # (b) Seed one row from existing app_settings ha_url + ha_token.
    #     Skipped under --sql offline mode: no live connection to read
    #     existing values, and JSONStorage has no literal-rendering
    #     compiler hook. Online runs always perform the seed.
    # ---------------------------------------------------------------
    if not context.is_offline_mode():
        bind = op.get_bind()
        rows = bind.execute(
            sa.text(
                "SELECT key, value FROM app_settings "
                "WHERE key IN ('ha_url', 'ha_token')"
            )
        ).fetchall()
        existing = {r[0]: (r[1] or "") for r in rows}

        dsc_tbl = sa.table(
            "data_source_configs",
            sa.column("source_name", sa.String),
            sa.column("instance_label", sa.String),
            sa.column("config_json", JSONStorage()),
            sa.column("enabled", sa.Boolean),
        )
        op.bulk_insert(
            dsc_tbl,
            [
                {
                    "source_name": "ha_fordpass",
                    "instance_label": "default",
                    "config_json": {
                        "ha_url": existing.get("ha_url", ""),
                        "ha_token": existing.get("ha_token", ""),
                    },
                    "enabled": True,
                }
            ],
        )

    # ---------------------------------------------------------------
    # (c) Rewrite source_system 'home_assistant' -> 'ha_fordpass'
    #     across every table that carries the column. Parameterized
    #     SQL via sa.text().bindparams keeps this dialect-portable.
    # ---------------------------------------------------------------
    for table in TABLES_WITH_SOURCE_SYSTEM:
        op.execute(
            sa.text(
                f"UPDATE {table} SET source_system = :new "
                f"WHERE source_system = :old"
            ).bindparams(new="ha_fordpass", old="home_assistant")
        )

    # ---------------------------------------------------------------
    # (d) Add ev_vehicles.primary_source_id FK (nullable, SET NULL).
    #     PG supports ALTER TABLE ADD CONSTRAINT directly. SQLite
    #     requires batch_alter_table (copy-and-move) which itself
    #     needs a live connection for table reflection. Branch on
    #     dialect so both online runs succeed; --sql offline render
    #     for SQLite skips the FK constraint (the column itself is
    #     added so subsequent steps still render).
    # ---------------------------------------------------------------
    bind_dialect = op.get_bind().dialect.name if not context.is_offline_mode() else (
        context.get_context().dialect.name
    )
    if bind_dialect == "sqlite":
        if context.is_offline_mode():
            # WARNING: SQLite offline-generated SQL omits the FK constraint
            # because batch_alter_table needs a live connection for table
            # reflection. PostgreSQL offline mode handles the FK correctly.
            # Do NOT use offline SQL for SQLite production targets — the
            # generated schema would have the column without the
            # fk_ev_vehicles_primary_source_id constraint and SQLAlchemy
            # does not validate runtime FKs against schema FKs.
            op.add_column(
                "ev_vehicles",
                sa.Column("primary_source_id", sa.Integer(), nullable=True),
            )
        else:
            with op.batch_alter_table("ev_vehicles") as batch_op:
                batch_op.add_column(
                    sa.Column("primary_source_id", sa.Integer(), nullable=True),
                )
                batch_op.create_foreign_key(
                    "fk_ev_vehicles_primary_source_id",
                    "data_source_configs",
                    ["primary_source_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
    else:
        op.add_column(
            "ev_vehicles",
            sa.Column("primary_source_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_ev_vehicles_primary_source_id",
            "ev_vehicles",
            "data_source_configs",
            ["primary_source_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ---------------------------------------------------------------
    # (e) Backfill primary_source_id for vehicles whose post-rewrite
    #     source_system is 'ha_fordpass'. Correlated subquery is
    #     dialect-portable on PG + SQLite.
    # ---------------------------------------------------------------
    op.execute(
        sa.text(
            """
            UPDATE ev_vehicles
            SET primary_source_id = (
                SELECT id FROM data_source_configs
                WHERE source_name = :sn AND instance_label = :il
            )
            WHERE source_system = :ss
            """
        ).bindparams(sn="ha_fordpass", il="default", ss="ha_fordpass")
    )

    # ---------------------------------------------------------------
    # (f) Delete legacy app_settings keys LAST so any earlier failure
    #     rolls back the entire migration atomically.
    # ---------------------------------------------------------------
    op.execute(
        sa.text(
            "DELETE FROM app_settings WHERE key IN ('ha_url', 'ha_token')"
        )
    )


def downgrade() -> None:
    """No-op: one-way migration."""
    raise NotImplementedError(
        "p31_data_source_foundation is a one-way migration and cannot be downgraded."
    )
