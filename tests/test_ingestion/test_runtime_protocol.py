"""IngestionRuntime Protocol conformance tests.

Locks the Protocol shape (`@runtime_checkable`) and the legacy health-dict
keys so the inline status badge on the Settings → Data Sources card keeps
working unchanged across this refactor.
"""

from __future__ import annotations

import pytest

from web.services.ingestion.base import IngestionRuntime
from web.services.ingestion.ha_websocket import HAWebSocketRuntime

pytestmark = pytest.mark.unit


def test_ha_websocket_runtime_is_ingestion_runtime():
    """isinstance check — the Protocol is @runtime_checkable."""
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    assert isinstance(rt, IngestionRuntime)


def test_ha_websocket_runtime_carries_config_id():
    rt = HAWebSocketRuntime(config_id=42, ha_url="http://x", ha_token="t")
    assert rt.config_id == 42


def test_ha_websocket_runtime_default_source_name_and_instance_label():
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    assert rt.source_name == "ha_fordpass"
    assert rt.instance_label == "default"


def test_ha_websocket_runtime_accepts_explicit_source_name_and_instance_label():
    rt = HAWebSocketRuntime(
        config_id=2,
        ha_url="http://x",
        ha_token="t",
        source_name="ha_fordpass",
        instance_label="secondary",
    )
    assert rt.source_name == "ha_fordpass"
    assert rt.instance_label == "secondary"


def test_health_dict_shape_compatible_with_phase31_d12_badge():
    """The inline status badge reads connection_state + last_event_at;
    the runtime must preserve every key from the legacy HASSClient.health
    shape so the badge code keeps working unchanged. Regression-locks the
    contract.
    """
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    h = rt.health
    for key in (
        "connected",
        "connection_state",
        "last_event_at",
        "events_processed",
        "errors",
        "last_error",
        "last_successful_write",
    ):
        assert key in h, f"health dict missing legacy key: {key}"


def test_health_dict_returns_a_copy_not_a_reference():
    """Mutating the returned dict must not corrupt the runtime's internal state."""
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    h = rt.health
    h["connected"] = True
    assert rt.health["connected"] is False
