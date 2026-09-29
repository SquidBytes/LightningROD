"""Merge one source's record into another source's record of the same event.

A precedence table maps each column to a `Rule`. A column NULL on one side is
filled from the other; where both hold a value, the rule's winner keeps it, or
its `resolve` decides. The same table serves both arrival orders, so the
merged row does not depend on which source came first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HA = "ha_fordpass"
FCON = "fcon"
# Ingested rows a merge may enrich; manual and imported rows never.
MERGEABLE_SOURCES = (HA, FCON)

# (current, incoming, incoming_source) -> the value the column should hold.
Resolver = Callable[[dict, dict, str], Any]


@dataclass(frozen=True)
class Rule:
    winner: str
    why: str
    resolve: Resolver | None = None


def merge_values(
    precedence: dict[str, Rule], current: dict, incoming: dict, incoming_source: str
) -> dict[str, Any]:
    """Columns to change on `current` after merging in `incoming`."""
    changes: dict[str, Any] = {}
    for field, rule in precedence.items():
        new = incoming.get(field)
        old = current.get(field)
        if rule.resolve is not None and new is not None and old is not None:
            value = rule.resolve(current, incoming, incoming_source)
            if value != old:
                changes[field] = value
            continue
        if new is None:
            continue
        if old is None or (rule.winner == incoming_source and old != new):
            changes[field] = new
    return changes


def is_km_mile_twin(a: float, b: float) -> bool:
    """Whether one value is the other converted between km and miles by mistake."""
    from web.services.repair.ops.trip_duplicates import RATIO_BAND

    small, large = sorted((abs(a), abs(b)))
    return small > 0 and RATIO_BAND[0] <= large / small <= RATIO_BAND[1]
