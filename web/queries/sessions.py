from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession

PAGE_SIZE = 25

SORTABLE_COLUMNS = {
    "date": EVChargingSession.session_start_utc,
    "energy": EVChargingSession.energy_kwh,
    "cost": EVChargingSession.cost,
    "location": EVChargingSession.location_name,
    "network": EVChargingSession.network_id,
    "charge_type": EVChargingSession.charge_type,
    "duration": EVChargingSession.charge_duration_seconds,
}


async def get_most_recent_location(db: AsyncSession) -> Optional[str]:
    """Return the location_name of the most recent session, or None."""
    stmt = (
        select(EVChargingSession.location_name)
        .where(EVChargingSession.location_name.isnot(None))
        .order_by(EVChargingSession.session_start_utc.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def query_sessions(
    db: AsyncSession,
    page: int = 1,
    date_preset: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    charge_type: Optional[str] = None,
    location_type: Optional[str] = None,
    network_id: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> tuple[list[EVChargingSession], int, dict]:
    """Query charging sessions with optional filters and pagination.

    Returns a tuple of (sessions, total_count, summary_dict).
    summary_dict contains: count, total_kwh
    """
    now = datetime.now(timezone.utc)

    # Determine sort column and direction
    sort_col = SORTABLE_COLUMNS.get(sort_by) if sort_by else None
    if sort_col is not None:
        order_expr = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    else:
        order_expr = EVChargingSession.session_start_utc.desc()

    # Base statement with resolved order
    stmt = select(EVChargingSession).order_by(order_expr)

    # Accumulate filter clauses
    filters = []

    # Date preset filter
    if date_preset and date_preset != "all":
        if date_preset == "7d":
            cutoff = now - timedelta(days=7)
            filters.append(EVChargingSession.session_start_utc >= cutoff)
        elif date_preset == "30d":
            cutoff = now - timedelta(days=30)
            filters.append(EVChargingSession.session_start_utc >= cutoff)
        elif date_preset == "90d":
            cutoff = now - timedelta(days=90)
            filters.append(EVChargingSession.session_start_utc >= cutoff)
        elif date_preset == "ytd":
            cutoff = datetime(now.year, 1, 1, tzinfo=timezone.utc)
            filters.append(EVChargingSession.session_start_utc >= cutoff)
        elif date_preset == "1y":
            cutoff = now - timedelta(days=365)
            filters.append(EVChargingSession.session_start_utc >= cutoff)

    # Custom date range (only if no preset is active)
    if not date_preset or date_preset == "all":
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
                filters.append(EVChargingSession.session_start_utc >= dt_from)
            except ValueError:
                pass
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59, tzinfo=timezone.utc
                )
                filters.append(EVChargingSession.session_start_utc <= dt_to)
            except ValueError:
                pass

    # Charge type filter
    if charge_type:
        filters.append(EVChargingSession.charge_type == charge_type)

    # Location type filter
    if location_type:
        filters.append(EVChargingSession.location_type == location_type)

    # Network filter
    if network_id:
        filters.append(EVChargingSession.network_id == network_id)

    # Apply all filters
    for f in filters:
        stmt = stmt.where(f)

    # Count query
    count_subq = stmt.subquery()
    count_stmt = select(func.count()).select_from(count_subq)
    total_result = await db.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Summary query — count + sum(energy_kwh) from same subquery
    summary_subq = stmt.subquery()
    summary_stmt = select(
        func.count().label("count"),
        func.sum(summary_subq.c.energy_kwh).label("total_kwh"),
    ).select_from(summary_subq)
    summary_result = await db.execute(summary_stmt)
    summary_row = summary_result.one()
    summary = {
        "count": summary_row.count or 0,
        "total_kwh": float(summary_row.total_kwh) if summary_row.total_kwh else 0.0,
    }

    # Data query with pagination
    data_stmt = stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    data_result = await db.execute(data_stmt)
    sessions = list(data_result.scalars().all())

    return sessions, total, summary
