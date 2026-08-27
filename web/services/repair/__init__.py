"""Data-repair framework: restorable, idempotent operations over ingested rows."""

from web.services.repair.base import (
    DEFAULT_PREVIEW_LIMIT,
    MUTABLE_SOURCE_SYSTEMS,
    RepairDiff,
    RepairGroup,
    RepairOperation,
    RepairPreview,
    RepairResult,
    mutable_only,
    rollback_session,
)
from web.services.repair.registry import REPAIR_REGISTRY, get_operation
from web.services.repair.snapshot import (
    deserialize_row,
    list_runs,
    purge_run,
    restore_run,
    serialize_row,
    snapshot_rows,
)

__all__ = [
    "DEFAULT_PREVIEW_LIMIT",
    "MUTABLE_SOURCE_SYSTEMS",
    "REPAIR_REGISTRY",
    "RepairDiff",
    "RepairGroup",
    "RepairOperation",
    "RepairPreview",
    "RepairResult",
    "deserialize_row",
    "get_operation",
    "list_runs",
    "mutable_only",
    "purge_run",
    "restore_run",
    "rollback_session",
    "serialize_row",
    "snapshot_rows",
]
