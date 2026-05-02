"""Registry lookup + Pydantic round-trip tests for the source registry."""

import importlib

import pytest
from pydantic import BaseModel, ValidationError

from web.services.sources.ha_fordpass.config import HAFordpassConfig
from web.services.sources.registry import REGISTRY, DataSourceDescriptor

pytestmark = pytest.mark.unit


def test_registry_has_two_descriptors():
    """ha_gas_price joined ha_fordpass in the registry."""
    assert len(REGISTRY) == 2


def test_registry_has_ha_fordpass():
    """Existing ha_fordpass descriptor still present (regression-lock)."""
    by_name = {d.source_name: d for d in REGISTRY}
    assert "ha_fordpass" in by_name
    d = by_name["ha_fordpass"]
    assert isinstance(d, DataSourceDescriptor)
    assert d.display_name == "Home Assistant (FordPass)"
    assert d.adapter_module == "web.services.sources.ha_fordpass.adapter"
    assert d.config_schema is HAFordpassConfig
    assert d.setup_flow == "ha_websocket"


def test_registry_has_ha_gas_price():
    """ha_gas_price descriptor with static setup flow.

    `setup_flow="static"` reflects v1's app_settings-keyed config — there is
    no auth handshake to run; entity_ids live in app_settings keys.
    """
    by_name = {d.source_name: d for d in REGISTRY}
    assert "ha_gas_price" in by_name
    d = by_name["ha_gas_price"]
    assert isinstance(d, DataSourceDescriptor)
    assert d.display_name == "Home Assistant (Gas Price Sensors)"
    assert d.adapter_module == "web.services.sources.ha_gas_price.adapter"
    assert issubclass(d.config_schema, BaseModel)
    assert d.setup_flow == "static"


def test_registry_adapter_module_importable():
    """Every registered adapter module imports cleanly and exposes Protocol attrs."""
    for d in REGISTRY:
        module = importlib.import_module(d.adapter_module)
        assert hasattr(module, "FIELD_CONTRACTS"), (
            f"{d.source_name} missing FIELD_CONTRACTS"
        )
        assert hasattr(module, "process_event"), (
            f"{d.source_name} missing process_event"
        )


def test_ha_fordpass_config_validates():
    cfg = HAFordpassConfig.model_validate(
        {"ha_url": "http://homeassistant.local:8123/", "ha_token": "secret"}
    )
    assert cfg.ha_url == "http://homeassistant.local:8123"  # trailing slash stripped
    assert cfg.ha_token == "secret"
    assert cfg.ha_unit_system == "auto"
    assert cfg.ha_auto_connect is True
    assert cfg.ha_vin_override is None


def test_ha_fordpass_config_rejects_empty():
    with pytest.raises(ValidationError):
        HAFordpassConfig.model_validate({"ha_url": "", "ha_token": "x"})
    with pytest.raises(ValidationError):
        HAFordpassConfig.model_validate({"ha_url": "http://x", "ha_token": ""})


def test_ha_fordpass_config_round_trip():
    cfg = HAFordpassConfig.model_validate(
        {
            "ha_url": "http://x",
            "ha_token": "t",
            "ha_vin_override": "1FT",
            "ha_unit_system": "metric",
            "ha_auto_connect": False,
        }
    )
    assert HAFordpassConfig.model_validate(cfg.model_dump()) == cfg
