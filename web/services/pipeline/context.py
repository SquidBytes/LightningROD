"""Per-runtime facts a parser or writer needs about where a payload came from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IngestContext:
    source_system: str
    config_id: int | None = None
    # Only Home Assistant localizes values; None means the payload is SI.
    unit_system: str | None = None

    @classmethod
    def from_config(cls, source_system: str, source_config: dict[str, Any] | None) -> IngestContext:
        config = source_config or {}
        return cls(source_system, config.get("config_id"), config.get("unit_system"))
