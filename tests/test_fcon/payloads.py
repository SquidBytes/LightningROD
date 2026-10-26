"""Synthetic FordConnect payload builders (shaped after captures, no real identifiers)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_PARKED = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "fcon" / "telemetry_parked.json").read_text()
)


def telemetry(at: str | None = None, **values: Any) -> dict:
    """Parked telemetry; `at` restamps every metric, `values` override metric values."""
    payload = copy.deepcopy(_PARKED)
    metrics = payload["metrics"]
    if at is not None:
        payload["updateTime"] = at
        for entry in metrics.values():
            for item in entry if isinstance(entry, list) else [entry]:
                item["updateTime"] = at
    for name, value in values.items():
        stamp = at or payload["updateTime"]
        metrics[name] = {"updateTime": stamp, "oemCorrelationId": "1", "value": value}
    return payload
