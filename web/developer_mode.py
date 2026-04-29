"""In-process developer mode flag.

Loaded from app_settings at startup; updated immediately when the user
toggles the setting. No per-request DB cost.

Hard-disabled in DEMO_MODE — diagnostic surfaces (Data Sources, etc.) are
not part of the demo experience, and the toggle becomes a no-op.
"""
from __future__ import annotations

import os

_enabled: bool = False


def _demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").lower() == "true"


def is_enabled() -> bool:
    if _demo_mode():
        return False
    return _enabled


def set_enabled(value: bool) -> None:
    if _demo_mode():
        return
    global _enabled
    _enabled = value
