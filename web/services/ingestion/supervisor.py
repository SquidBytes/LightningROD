"""Ingestion supervisor.

Reads ``data_source_configs WHERE enabled=true`` and spawns one
``IngestionRuntime`` per row. Owns lifecycle: ``start_all`` on lifespan
startup, ``stop_all`` on shutdown, ``restart_runtime`` on settings save.

Multi-instance is latent in v1 — the live deployment has exactly one
``ha_fordpass`` row, so exactly one runtime is spawned. A user with two HA
servers adds a second config row and gets a second runtime; no code change.

The ``ha_gas_price`` source name does not spawn a transport runtime — it
contributes a handler that ``HAWebSocketRuntime._dispatch`` fans out to.
A source gets a runtime when its ``DataSource.build_runtime`` is set.

Secrets are decrypted here, once, before a builder sees the config. A row
whose secrets the current key cannot read is not started; it is reported as
needing re-authorization instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models.data_source_config import DataSourceConfig
from web.services.ingestion.base import IngestionRuntime
from web.services.sources.base import RuntimeBuilder
from web.services.sources.config_secrets import open_config
from web.services.sources.registry import REGISTRY

logger = logging.getLogger("lightningrod.ingestion.supervisor")


def _builders() -> dict[str, RuntimeBuilder]:
    return {s.source_name: s.build_runtime for s in REGISTRY if s.build_runtime}


class IngestionSupervisor:
    """Owns the lifecycle of every IngestionRuntime spawned from data_source_configs."""

    def __init__(self) -> None:
        self._runtimes: dict[int, IngestionRuntime] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        # config_id -> (source_name, instance_label) for rows whose stored
        # secrets are unreadable with the current key.
        self._needs_reauth: dict[int, tuple[str, str]] = {}

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
        self._needs_reauth.pop(config_id, None)
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
        """Ask the source to build its runtime, then schedule its start coroutine.

        A source_name with no builder is logged and skipped explicitly — never
        a silent pass.
        """
        self._needs_reauth.pop(row.id, None)
        builder = _builders().get(row.source_name)
        if builder is None:
            logger.info(
                "No transport runtime registered for source_name=%s "
                "(config_id=%d) - skipping",
                row.source_name,
                row.id,
            )
            return

        opened = open_config(row.source_name, row.config_json)
        if opened.unreadable:
            # Never retry with garbage: the user re-enters the credentials.
            logger.warning(
                "Stored credentials for config_id=%d (source=%s) are encrypted "
                "but the key is missing or changed - re-authorization required",
                row.id,
                row.source_name,
            )
            self._needs_reauth[row.id] = (row.source_name, row.instance_label)
            return

        try:
            rt = builder(row, opened.values)
        except Exception:
            # Never log raw config_json — it carries credentials. Pydantic's
            # default exception repr does not include field values, so the
            # exception itself is safe to log; builders must keep it that way.
            logger.exception(
                "Runtime construction failed for config_id=%d (source=%s) "
                "- skipping",
                row.id,
                row.source_name,
            )
            return

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
            if rt.source_name == source_name and rt.instance_label == instance_label:
                return rt
        return None

    def get_runtime_by_config_id(self, config_id: int) -> IngestionRuntime | None:
        return self._runtimes.get(config_id)

    def needs_reauth(self, config_id: int) -> bool:
        return config_id in self._needs_reauth

    def health(self) -> list[dict[str, Any]]:
        """Aggregated runtime health for the admin Runtimes section.

        Returns a list of flattened dicts. Each entry contains
        ``config_id`` / ``source_name`` / ``instance_label`` plus every key
        from the runtime's own ``health`` property.
        """
        blocked = [
            {
                "config_id": cid,
                "source_name": source_name,
                "instance_label": label,
                "connected": False,
                "connection_state": "needs_reauth",
            }
            for cid, (source_name, label) in self._needs_reauth.items()
        ]
        return blocked + [
            {
                "config_id": cid,
                "source_name": rt.source_name,
                "instance_label": rt.instance_label,
                **rt.health,
            }
            for cid, rt in self._runtimes.items()
        ]


# Module-level singleton — accessed by routes for status; lifespan calls
# start_all / stop_all on this instance.
supervisor = IngestionSupervisor()
