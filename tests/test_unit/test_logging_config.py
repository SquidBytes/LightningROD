"""LR_LOG_LEVEL reaches the app's loggers only; a removed entity logs cleanly."""
import logging

from web.main import _configure_logging
from web.services.ingestion.ha_websocket import HAWebSocketRuntime


def test_log_level_applies_to_app_loggers_not_libraries(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "log_level", "DEBUG")
    try:
        _configure_logging()

        assert logging.getLogger("lightningrod.hass").isEnabledFor(logging.DEBUG)
        assert not logging.getLogger("aiosqlite").isEnabledFor(logging.INFO)
        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    finally:
        monkeypatch.undo()
        _configure_logging()


def test_event_line_for_a_removed_entity_does_not_raise():
    # HA sends new_state: null when an entity is removed.
    HAWebSocketRuntime._log_event(None, "sensor.gone", None, "none", False)  # type: ignore[arg-type]
