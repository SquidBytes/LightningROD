from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings, EVChargingNetwork


async def get_all_networks(db: AsyncSession) -> list[EVChargingNetwork]:
    """Return all charging networks ordered by network_name."""
    result = await db.execute(
        select(EVChargingNetwork).order_by(EVChargingNetwork.network_name)
    )
    return list(result.scalars().all())


async def upsert_network(
    db: AsyncSession, network_id: int, cost_per_kwh: float, is_free: bool
) -> None:
    """Update cost_per_kwh and is_free for an existing charging network row."""
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if network is not None:
        network.cost_per_kwh = cost_per_kwh
        network.is_free = is_free
        await db.commit()


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
