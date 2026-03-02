from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings, EVChargingNetwork, EVLocationLookup

# Predefined EV charging networks with brand-accurate colors
PREDEFINED_NETWORKS = [
    {"name": "Tesla Supercharger", "color": "#E31937", "cost_per_kwh": None, "is_free": False},
    {"name": "Electrify America", "color": "#00B140", "cost_per_kwh": None, "is_free": False},
    {"name": "ChargePoint", "color": "#00A4E4", "cost_per_kwh": None, "is_free": False},
    {"name": "EVgo", "color": "#F7941D", "cost_per_kwh": None, "is_free": False},
    {"name": "Blink", "color": "#0072CE", "cost_per_kwh": None, "is_free": False},
    {"name": "Flo", "color": "#6CBE45", "cost_per_kwh": None, "is_free": False},
    {"name": "Ford BlueOval", "color": "#003478", "cost_per_kwh": None, "is_free": False},
    {"name": "Rivian Adventure Network", "color": "#4DB848", "cost_per_kwh": None, "is_free": False},
    {"name": "Shell Recharge", "color": "#FFD500", "cost_per_kwh": None, "is_free": False},
    {"name": "BP Pulse", "color": "#009B3A", "cost_per_kwh": None, "is_free": False},
    {"name": "Home", "color": "#6366F1", "cost_per_kwh": None, "is_free": True},
]

# Derived color lookup for backward compatibility
NETWORK_COLORS = {n["name"]: n["color"] for n in PREDEFINED_NETWORKS}
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


async def seed_predefined_networks(db: AsyncSession) -> int:
    """Seed predefined networks into DB. Matches by name — skips existing.

    Also discovers unique location_name values from sessions and creates
    network rows for any not already in the DB.

    Returns count of networks added.
    """
    existing = await get_all_networks(db)
    existing_names = {n.network_name for n in existing}
    added = 0

    # Seed predefined networks
    for pn in PREDEFINED_NETWORKS:
        if pn["name"] not in existing_names:
            network = EVChargingNetwork(
                network_name=pn["name"],
                color=pn["color"],
                cost_per_kwh=pn["cost_per_kwh"],
                is_free=pn["is_free"] or False,
            )
            db.add(network)
            existing_names.add(pn["name"])
            added += 1

    # Discover networks from session data
    from db.models.charging_session import EVChargingSession

    result = await db.execute(
        select(EVChargingSession.location_name)
        .where(EVChargingSession.location_name.isnot(None))
        .distinct()
    )
    session_networks = {row[0] for row in result.all()}

    for name in session_networks:
        if name and name not in existing_names:
            network = EVChargingNetwork(
                network_name=name,
                color=NETWORK_COLORS.get(name, DEFAULT_COLOR),
                is_free=False,
            )
            db.add(network)
            existing_names.add(name)
            added += 1

    await db.commit()
    return added


async def get_locations_for_network(
    db: AsyncSession, network_id: int
) -> list[EVLocationLookup]:
    """Return all locations linked to a specific network."""
    result = await db.execute(
        select(EVLocationLookup)
        .where(EVLocationLookup.network_id == network_id)
        .order_by(EVLocationLookup.location_name)
    )
    return list(result.scalars().all())


async def create_location(
    db: AsyncSession,
    network_id: int,
    name: str,
    location_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> EVLocationLookup:
    """Create a new location linked to a network."""
    loc = EVLocationLookup(
        location_name=name,
        network_id=network_id,
        location_type=location_type,
        notes=notes,
    )
    db.add(loc)
    await db.commit()
    await db.refresh(loc)
    return loc


async def update_location(
    db: AsyncSession,
    location_id: int,
    name: str,
    location_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[EVLocationLookup]:
    """Update a location row. Returns updated location or None if not found."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        return None
    loc.location_name = name
    loc.location_type = location_type
    loc.notes = notes
    await db.commit()
    await db.refresh(loc)
    return loc


async def delete_location(db: AsyncSession, location_id: int) -> bool:
    """Delete a location by id. Returns True if deleted, False if not found."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        return False
    await db.delete(loc)
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
