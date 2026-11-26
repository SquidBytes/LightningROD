"""Per-source config schema for ha_gas_price.

Mirrors today's app_settings keys (gas_sensor_station_entity_id,
gas_sensor_average_entity_id). The config keys still live in app_settings
for v1; this Pydantic model is registered with the registry so a future
migration can flip the storage layer to
`data_source_configs.config_json` without changing the validation surface.

Both fields are optional — a user with only one gas sensor is supported.
"""

from pydantic import BaseModel


class HAGasPriceConfig(BaseModel):
    gas_sensor_station_entity_id: str | None = None
    gas_sensor_average_entity_id: str | None = None
