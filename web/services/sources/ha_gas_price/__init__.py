"""ha-gas-price source adapter package.

Handles gas-price sensor entities matched by configured entity_id (not by
slug pattern). The two `app_settings` keys
(`gas_sensor_station_entity_id`, `gas_sensor_average_entity_id`) drive
matching. Migrating those keys into
`data_source_configs[ha_gas_price].config_json` is deferred to a future
milestone; v1 keeps the existing app_settings shape and only relocates the
handler from the legacy `hass_processor.py`.

See `adapter.py` for the entry point and `handlers.py` for the writer.
"""
