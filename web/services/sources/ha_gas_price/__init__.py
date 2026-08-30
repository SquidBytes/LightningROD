"""ha-gas-price source adapter package.

Handles gas-price sensor entities matched by configured entity_id (not by
slug pattern). The two `app_settings` keys
(`gas_sensor_station_entity_id`, `gas_sensor_average_entity_id`) drive
matching. Migrating those keys into
`data_source_configs[ha_gas_price].config_json` is deferred to a future
milestone; for now this package keeps the existing app_settings shape.

See `adapter.py` for the entry point and `handlers.py` for the writer.
"""

from web.services.sources.base import DataSource
from web.services.sources.ha_gas_price.config import HAGasPriceConfig

DATA_SOURCE = DataSource(
    source_name="ha_gas_price",
    display_name="Home Assistant (Gas Price Sensors)",
    adapter_module="web.services.sources.ha_gas_price.adapter",
    config_schema=HAGasPriceConfig,
    setup_flow="static",
    # Entity ids only — nothing in this config is a credential.
    secret_fields=frozenset(),
)
