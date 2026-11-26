"""FordConnect (FCON) source: polls Ford's official vehicle API over OAuth."""

from web.services.source_system import SourceSystem
from web.services.sources.base import DataSource
from web.services.sources.ford.fcon.config import FconConfig
from web.services.sources.ford.fcon.runtime import build_runtime

DATA_SOURCE = DataSource(
    source_name=SourceSystem.FCON,
    display_name="FordConnect",
    adapter_module="web.services.sources.ford.fcon.adapter",
    config_schema=FconConfig,
    setup_flow="oauth",
    # client_id is not secret: it travels in the authorize URL.
    secret_fields=frozenset({"client_secret", "access_token", "refresh_token", "id_token"}),
    build_runtime=build_runtime,
)
