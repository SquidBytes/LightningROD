"""Factory for HARawEvent model instances."""

import copy
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.raw_event import HARawEvent
from tests.factories import BaseFactory

_PAYLOADS = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "ha_payloads"
        / "metric_ha_metric_vehicle.json"
    ).read_text()
)


class RawEventFactory(BaseFactory):
    """Create ha_raw_events rows from a fixture payload at a given timestamp."""

    @classmethod
    def payload(
        cls, suffix: str, device_id: str, recorded_at: datetime
    ) -> dict:
        """Fixture state for one entity suffix, re-VINed and restamped."""
        state = copy.deepcopy(_PAYLOADS[f"sensor.fordpass_YOUR_VIN_{suffix}"])
        state["entity_id"] = f"sensor.fordpass_{device_id}_{suffix}"
        state["last_changed"] = recorded_at.isoformat()
        state["last_updated"] = recorded_at.isoformat()
        return state

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        suffix: str = "events",
        device_id: str = "TESTVIN001",
        recorded_at: datetime | None = None,
        **overrides,
    ) -> HARawEvent:
        cls._next_id()
        recorded_at = recorded_at or cls._random_datetime(days_back=7)
        state = cls.payload(suffix, device_id, recorded_at)
        defaults = {
            "entity_id": state["entity_id"],
            "device_id": device_id,
            "slug": suffix,
            "state": state.get("state"),
            "payload": state,
            "recorded_at": recorded_at,
            "config_id": 1,
            "source_system": "ha_fordpass",
            "ingest_schema_version": 2,
        }
        defaults.update(overrides)
        row = HARawEvent(**defaults)
        db.add(row)
        await db.flush()
        return row
