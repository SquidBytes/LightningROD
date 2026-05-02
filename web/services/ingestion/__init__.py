"""Ingestion runtime layer.

Houses transport-shaped runtimes (HA WebSocket today; FCON polling, MQTT
tomorrow) plus the supervisor that spawns one per enabled
data_source_configs row. Source-shaped logic (slug handlers, gas-price
matchers) lives in `web/services/sources/<source_name>/`.

The supervisor singleton will be exported here once it is implemented;
until then this is a bare package marker.
"""
