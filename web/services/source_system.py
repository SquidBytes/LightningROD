"""Values of the persisted `source_system` column."""

from __future__ import annotations

from enum import StrEnum


class SourceSystem(StrEnum):
    HA_FORDPASS = "ha_fordpass"
    FCON = "fcon"
    MANUAL = "manual"
    MANUAL_ENTRY = "manual_entry"
    CSV_IMPORT = "csv_import"


# Machine-ingested sources; a merge may enrich these rows, manual and imported rows never.
INGESTED: tuple[str, ...] = (SourceSystem.FCON, SourceSystem.HA_FORDPASS)
