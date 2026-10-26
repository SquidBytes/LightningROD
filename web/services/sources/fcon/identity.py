"""How FordConnect rows are labelled."""

from __future__ import annotations

SOURCE_SYSTEM = "fcon"


def device_id_of(payload: dict) -> str | None:
    """Lower-cased VIN — the same device_id the Home Assistant source writes."""
    vin = payload.get("vin")
    return vin.strip().lower() if isinstance(vin, str) and vin.strip() else None
