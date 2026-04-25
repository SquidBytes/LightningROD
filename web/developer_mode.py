"""In-process developer mode flag.

Loaded from app_settings at startup; updated immediately when the user
toggles the setting. No per-request DB cost.
"""
from __future__ import annotations

_enabled: bool = False


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = value
