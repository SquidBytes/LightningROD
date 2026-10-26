"""Runtime builder for the fcon source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.models.data_source_config import DataSourceConfig
from web.services.sources.fcon.config import FconConfig

if TYPE_CHECKING:
    from web.services.ingestion.base import IngestionRuntime


def build_runtime(row: DataSourceConfig, config: dict) -> IngestionRuntime:
    """Build the poller for one fcon row; `config` is the decrypted config_json."""
    # Deferred for the same import cycle ha_fordpass.runtime avoids.
    from web.services.ingestion.fcon_poller import FconPollingRuntime
    from web.services.sources.fcon.ingest import store_sample

    return FconPollingRuntime(
        config_id=row.id,
        config=FconConfig.model_validate(config),
        sink=store_sample,
        source_name=row.source_name,
        instance_label=row.instance_label,
    )
