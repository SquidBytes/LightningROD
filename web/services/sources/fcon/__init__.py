"""FordConnect (FCON) source: polls Ford's official vehicle API over OAuth."""

from web.services.sources.base import DataSource
from web.services.sources.fcon.config import FconConfig
from web.services.sources.fcon.runtime import build_runtime

DATA_SOURCE = DataSource(
    source_name="fcon",
    display_name="FordConnect",
    adapter_module="web.services.sources.fcon.adapter",
    config_schema=FconConfig,
    setup_flow="oauth",
    # client_id is not secret: it travels in the authorize URL.
    secret_fields=frozenset({"client_secret", "access_token", "refresh_token", "id_token"}),
    build_runtime=build_runtime,
)
