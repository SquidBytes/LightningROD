from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings, EVChargingNetwork

# Predefined network colors for auto-assignment
NETWORK_COLORS = {
    "Tesla": "#E31937",
    "Electrify America": "#00B140",
    "ChargePoint": "#FF6600",
    "EVgo": "#00AEEF",
    "Blink": "#39B54A",
    "Home": "#6366F1",
    "FordPass": "#003478",
}
DEFAULT_COLOR = "#6B7280"  # gray-500


async def get_all_networks(db: AsyncSession) -> list[EVChargingNetwork]:
    """Return all charging networks ordered by network_name."""
    result = await db.execute(
        select(EVChargingNetwork).order_by(EVChargingNetwork.network_name)
    )
    return list(result.scalars().all())


async def create_network(
    db: AsyncSession,
    name: str,
    cost_per_kwh: Optional[float],
    is_free: bool,
    color: Optional[str],
) -> EVChargingNetwork:
    """Create a new charging network row.

    If color is None or empty, auto-assign from NETWORK_COLORS lookup by name,
    falling back to DEFAULT_COLOR.
    """
    resolved_color = color if color else NETWORK_COLORS.get(name, DEFAULT_COLOR)
    network = EVChargingNetwork(
        network_name=name,
        cost_per_kwh=cost_per_kwh,
        is_free=is_free,
        color=resolved_color,
    )
    db.add(network)
    await db.commit()
    await db.refresh(network)
    return network


async def update_network(
    db: AsyncSession,
    network_id: int,
    name: str,
    cost_per_kwh: Optional[float],
    is_free: bool,
    color: Optional[str],
) -> Optional[EVChargingNetwork]:
    """Update all fields of an existing charging network.

    Returns the updated network or None if not found.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if network is None:
        return None
    network.network_name = name
    network.cost_per_kwh = cost_per_kwh
    network.is_free = is_free
    network.color = color if color else NETWORK_COLORS.get(name, DEFAULT_COLOR)
    await db.commit()
    await db.refresh(network)
    return network


async def delete_network(db: AsyncSession, network_id: int) -> bool:
    """Delete a charging network by id.

    Returns True if deleted, False if not found.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if network is None:
        return False
    await db.delete(network)
    await db.commit()
    return True


async def get_app_setting(
    db: AsyncSession, key: str, default: str = ""
) -> str:
    """Return the value of an app_settings key, or default if not found."""
    result = await db.execute(
        select(AppSettings.value).where(AppSettings.key == key)
    )
    value = result.scalar_one_or_none()
    return value if value is not None else default


async def get_app_settings_dict(
    db: AsyncSession, keys: list[str]
) -> dict[str, str]:
    """Return a dict of key->value for the given app_settings keys.

    Missing keys default to empty string.
    """
    result = await db.execute(
        select(AppSettings.key, AppSettings.value).where(
            AppSettings.key.in_(keys)
        )
    )
    rows = result.all()
    found = {row.key: (row.value or "") for row in rows}
    # Fill missing keys with empty string
    return {k: found.get(k, "") for k in keys}


async def set_app_setting(db: AsyncSession, key: str, value: str) -> None:
    """Upsert a single key-value pair in app_settings."""
    stmt = pg_insert(AppSettings).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": value, "updated_at": stmt.excluded.updated_at},
    )
    await db.execute(stmt)
    await db.commit()
