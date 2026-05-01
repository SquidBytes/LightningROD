"""Registry lookup + Pydantic round-trip tests for the source registry."""

import importlib

import pytest
from pydantic import ValidationError

from web.services.sources.ha_fordpass.config import HAFordpassConfig
from web.services.sources.registry import REGISTRY, DataSourceDescriptor

pytestmark = pytest.mark.unit


def test_registry_has_ha_fordpass():
    assert len(REGISTRY) == 1
    d = REGISTRY[0]
    assert isinstance(d, DataSourceDescriptor)
    assert d.source_name == "ha_fordpass"
    assert d.adapter_module == "web.services.sources.ha_fordpass.adapter"
    assert d.config_schema is HAFordpassConfig
    assert d.setup_flow == "ha_websocket"


def test_registry_adapter_module_importable():
    d = REGISTRY[0]
    module = importlib.import_module(d.adapter_module)
    assert hasattr(module, "FIELD_CONTRACTS")


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
