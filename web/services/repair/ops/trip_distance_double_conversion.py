"""Fix trips whose distance was km-converted twice (contradicts the odometer)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.services.repair.base import RepairDiff, RepairOperation, mutable_only

KM_PER_MILE = 1.609344
# distance / odometer-delta near x1.609 marks a double conversion; after the
# fix the ratio is ~1.0, so the operation is naturally idempotent.
RATIO_BAND = (1.55, 1.67)
MIN_ODO_DELTA_KM = 0.5


class TripDistanceDoubleConversion(RepairOperation):
    """Divide double-converted distances by 1.609344 and recompute efficiency."""

    slug = "trip-distance-double-conversion"
    display_name = "Trip distance double conversion"
    description = (
        "Finds trips whose distance is x1.609 the odometer delta (converted "
        "to km twice) and restores the true distance and efficiency."
    )
    model = EVTripMetrics

    async def _candidates(self, db: AsyncSession) -> list[EVTripMetrics]:
        # Coarse SQL filter; exact ratio check stays in Python for portability.
        stmt = (
            select(EVTripMetrics)
            .where(
                mutable_only(EVTripMetrics),
                EVTripMetrics.distance.is_not(None),
                EVTripMetrics.odometer_start.is_not(None),
                EVTripMetrics.odometer_end.is_not(None),
            )
            .order_by(EVTripMetrics.id)
        )
        rows = (await db.execute(stmt)).scalars().all()
        out: list[EVTripMetrics] = []
        for row in rows:
            odo_delta = float(row.odometer_end - row.odometer_start)
            if odo_delta < MIN_ODO_DELTA_KM:
                continue
            ratio = float(row.distance) / odo_delta
            if RATIO_BAND[0] <= ratio <= RATIO_BAND[1]:
                out.append(row)
        return out

    @staticmethod
    def _fixed_values(row: EVTripMetrics) -> dict:
        new_distance = float(row.distance) / KM_PER_MILE
        fixed = {"distance": new_distance}
        if row.energy_consumed is not None and float(row.energy_consumed) > 0:
            fixed["efficiency"] = new_distance / float(row.energy_consumed)
        return fixed

    async def census(self, db: AsyncSession) -> int:
        return len(await self._candidates(db))

    async def preview(self, db: AsyncSession, limit: int = 10) -> list[RepairDiff]:
        diffs: list[RepairDiff] = []
        for row in (await self._candidates(db))[:limit]:
            fixed = self._fixed_values(row)
            before = {field: getattr(row, field) for field in fixed}
            diffs.append(RepairDiff(row.id, before, fixed, "update"))
        return diffs

    async def affected_rows(self, db: AsyncSession) -> list[EVTripMetrics]:
        return await self._candidates(db)

    async def execute(self, db: AsyncSession) -> int:
        rows = await self._candidates(db)
        for row in rows:
            for field, val in self._fixed_values(row).items():
                setattr(row, field, val)
        await db.flush()
        return len(rows)
