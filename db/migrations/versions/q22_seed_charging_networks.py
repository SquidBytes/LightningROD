"""Seed Big 7 charging networks
Insert the 7 most common US EV charging networks into ev_charging_networks
with source_system='seed'. Idempotent — skips networks that already exist
(matched by network_name). Downgrade removes only seed-sourced rows.
Revision ID: q22_seed_networks
Revises: p20_gas_comparison
Create Date: 2026-03-26
"""

import sqlalchemy as sa
from alembic import op

revision = "q22_seed_networks"
down_revision = "p20_gas_comparison"
branch_labels = None
depends_on = None

# Big 7 seed data
SEED_NETWORKS = [
    {"network_name": "Tesla Supercharger", "cost_per_kwh": 0.42, "color": "#CC0000", "is_verified": True, "source_system": "seed", "is_free": False},
    {"network_name": "Electrify America", "cost_per_kwh": 0.48, "color": "#00A94F", "is_verified": True, "source_system": "seed", "is_free": False},
    {"network_name": "ChargePoint", "cost_per_kwh": 0.35, "color": "#FF6B2D", "is_verified": True, "source_system": "seed", "is_free": False},
    {"network_name": "EVgo", "cost_per_kwh": 0.39, "color": "#00AEEF", "is_verified": True, "source_system": "seed", "is_free": False},
    {"network_name": "EV Connect", "cost_per_kwh": 0.30, "color": "#4CAF50", "is_verified": True, "source_system": "seed", "is_free": False},
    {"network_name": "IONNA", "cost_per_kwh": 0.40, "color": "#1A1A2E", "is_verified": True, "source_system": "seed", "is_free": False},
    {"network_name": "Rivian Adventure Network", "cost_per_kwh": 0.35, "color": "#517B50", "is_verified": True, "source_system": "seed", "is_free": False},
]


def upgrade() -> None:
    conn = op.get_bind()

    for net in SEED_NETWORKS:
        # Check if network already exists (by name)
        exists = conn.execute(
            sa.text("SELECT id FROM ev_charging_networks WHERE network_name = :name"),
            {"name": net["network_name"]},
        ).fetchone()

        if exists is None:
            conn.execute(
                sa.text(
                    "INSERT INTO ev_charging_networks "
                    "(network_name, cost_per_kwh, color, is_verified, source_system, is_free) "
                    "VALUES (:network_name, :cost_per_kwh, :color, :is_verified, :source_system, :is_free)"
                ),
                net,
            )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM ev_charging_networks WHERE source_system = 'seed'")
    )
