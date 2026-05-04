"""Ingestion supervisor.

Reads ``data_source_configs WHERE enabled=true`` and spawns one
``IngestionRuntime`` per row. Owns lifecycle: ``start_all`` on lifespan
startup, ``stop_all`` on shutdown, ``restart_runtime`` on settings save.

Multi-instance is latent in v1 — the live deployment has exactly one
``ha_fordpass`` row, so exactly one runtime is spawned. A user with two HA
servers adds a second config row and gets a second runtime; no code change.

The ``ha_gas_price`` source name does not spawn a transport runtime — it
contributes a handler that ``HAWebSocketRuntime._dispatch`` fans out to.
``_RUNTIME_CLASSES`` defines which source names get a transport runtime.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models.data_source_config import DataSourceConfig
from web.services.ingestion.base import IngestionRuntime
from web.services.ingestion.ha_websocket import HAWebSocketRuntime
from web.services.sources.ha_fordpass.config import HAFordpassConfig

logger = logging.getLogger("lightningrod.ingestion.supervisor")

# source_name -> runtime class mapping. Right-sized for v1's two source names;
# adding a third source with its own transport (FCON polling, MQTT) means one
# new entry here. ha_gas_price is intentionally absent — it has no transport
# runtime, only a handler that HAWebSocketRuntime._dispatch fans out to.
_RUNTIME_CLASSES: dict[str, type] = {
    "ha_fordpass": HAWebSocketRuntime,
}


class IngestionSupervisor:
    """Owns the lifecycle of every IngestionRuntime spawned from data_source_configs."""

    def __init__(self) -> None:
        self._runtimes: dict[int, IngestionRuntime] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    # ----- Lifecycle -----

    async def start_all(self) -> None:
        """Read enabled ``data_source_configs`` rows and spawn one runtime per row.

        Config rows are the only credential source; ``app_settings`` is not
        consulted for ingestion runtime startup.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DataSourceConfig).where(DataSourceConfig.enabled.is_(True))
            )
            rows = result.scalars().all()
        for row in rows:
            await self._start_one(row)

    async def stop_all(self) -> None:
        """Stop every spawned runtime; clear the registry."""
        for config_id in list(self._runtimes):
            await self.stop_runtime(config_id)

    async def restart_runtime(self, config_id: int) -> None:
        """Stop the affected runtime, re-read config, start a fresh runtime.

        Other runtimes are untouched. Toggling ``enabled``:
          * true -> false: stops the runtime with no fresh start.
          * false -> true: starts a runtime (no prior to stop).
          * config_json mutation while enabled: stop + start with new credentials.
        """
        if config_id in self._runtimes:
            await self.stop_runtime(config_id)
        async with AsyncSessionLocal() as db:
            row = await db.get(DataSourceConfig, config_id)
        if row is None or not row.enabled:
            logger.info(
                "restart_runtime(%d): row absent/disabled, no fresh start", config_id
            )
            return
        await self._start_one(row)

    async def stop_runtime(self, config_id: int) -> None:
        """Stop a single runtime, cancel its task, drop from the registry."""
        rt = self._runtimes.pop(config_id, None)
        task = self._tasks.pop(config_id, None)
        if rt is None:
            return
        try:
            await rt.stop()
        except Exception:
            logger.exception("Error stopping runtime %d", config_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # ----- Internal -----

    async def _start_one(self, row: DataSourceConfig) -> None:
        """Validate config_json, build the runtime, schedule its start coroutine.

        Membership in ``_RUNTIME_CLASSES`` is the single source of truth for
        "this supervisor knows how to spawn this source_name". Adding a new
        source means adding a row to ``_RUNTIME_CLASSES`` AND a matching
        per-source construction branch below — there is no defensive
        fall-through that silently skips unrecognized source_names while
        leaving them in the registry.
        """
        runtime_cls = _RUNTIME_CLASSES.get(row.source_name)
        if runtime_cls is None:
            logger.info(
                "No transport runtime registered for source_name=%s "
                "(config_id=%d) - skipping",
                row.source_name,
                row.id,
            )
            return

        if row.source_name == "ha_fordpass":
            try:
                cfg = HAFordpassConfig.model_validate(row.config_json or {})
            except Exception:
                # Never log raw config_json — it carries the HA token. Pydantic's
                # default exception repr does not include field values, so the
                # exception itself is safe to log.
                logger.exception(
                    "config_json validation failed for config_id=%d (source=%s) "
                    "- skipping",
                    row.id,
                    row.source_name,
                )
                return
            rt: IngestionRuntime = runtime_cls(
                config_id=row.id,
                ha_url=cfg.ha_url,
                ha_token=cfg.ha_token,
                source_name=row.source_name,
                instance_label=row.instance_label,
            )
        else:
            # _RUNTIME_CLASSES has an entry for row.source_name but _start_one
            # has no per-source construction branch — a contributor added the
            # registry entry without wiring up the build step. Fail loudly.
            raise RuntimeError(
                f"_RUNTIME_CLASSES contains source_name={row.source_name!r} "
                "but _start_one has no construction branch for it. Add an "
                "elif branch above next to the matching registry entry."
            )

        self._runtimes[row.id] = rt
        self._tasks[row.id] = asyncio.create_task(
            rt.start(), name=f"ingestion-{row.source_name}-{row.id}"
        )
        logger.info(
            "Spawned runtime: source=%s instance=%s config_id=%d",
            row.source_name,
            row.instance_label,
            row.id,
        )

    # ----- Accessors -----

    def get_runtime(
        self, source_name: str, instance_label: str = "default"
    ) -> IngestionRuntime | None:
        """Resolve a runtime by ``(source_name, instance_label)``.

        v1: settings.py treats ``ha_fordpass/default`` as the singleton. The
        same accessor works for multi-instance lookups in v2.
        """
        for rt in self._runtimes.values():
            if (
                getattr(rt, "source_name", None) == source_name
                and getattr(rt, "instance_label", None) == instance_label
            ):
                return rt
        return None

    def get_runtime_by_config_id(self, config_id: int) -> IngestionRuntime | None:
        return self._runtimes.get(config_id)

    def health(self) -> list[dict[str, Any]]:
        """Aggregated runtime health for the admin Runtimes section.

        Returns a list of flattened dicts. Each entry contains
        ``config_id`` / ``source_name`` / ``instance_label`` plus every key
        from the runtime's own ``health`` property.
        """
        return [
            {
                "config_id": cid,
                "source_name": getattr(rt, "source_name", "unknown"),
                "instance_label": getattr(rt, "instance_label", "default"),
                **rt.health,
            }
            for cid, rt in self._runtimes.items()
        ]


# Module-level singleton — accessed by routes for status; lifespan calls
# start_all / stop_all on this instance.
supervisor = IngestionSupervisor()
