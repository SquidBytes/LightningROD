"""Admin: Data Sources diagnostic page.
Reads the DATA_MAP registry + _last_seen_raw cache from each source
adapter and renders a searchable table. Purpose: ,
let the user trace any displayed value -> source entity -> attribute -> unit ->
raw value last observed.
Single-user app -> ungated route . Future multi-user would add an
admin-only guard here.

Also merges in unit-detection records (web.services.units.detection) so the
table surfaces cross-referenced + read-time-detected sources that do NOT
appear in DATA_MAP.
"""
from __future__ import annotations

import importlib
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.vehicles import get_active_vehicle, get_all_vehicles
from web.services.ingestion import supervisor
from web.services.sources.registry import REGISTRY
from web.services.units import detection
from web.services.units.data_map import DataMapping

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")


def _mapping_key(c: DataMapping) -> str:
    """Key convention matches ha_fordpass.adapter._record_last_seen."""
    return f"{c.source.pattern}|{c.source_field}"


def _load_groups() -> list[dict[str, Any]]:
    """Read every adapter's DATA_MAP + _last_seen_raw at request time.

    Returns a list of dicts shaped for the template:
        [
            {
                "source_name": "ha_fordpass",
                "rows": [
                    {"mapping": DataMapping, "last_seen": dict | None,
                     "detection": DetectionRecord | None},
                    ...
                ],
            },
            ...
        ]
    """
    out: list[dict[str, Any]] = []
    detection_index = {
        (r.entity_pattern, r.attribute): r for r in detection.snapshot()
    }

    for source in REGISTRY:
        source_name = source.source_name
        module = importlib.import_module(source.adapter_module)
        mappings: list[DataMapping] = list(getattr(module, "DATA_MAP", []))
        last_seen: dict[str, dict[str, Any]] = dict(getattr(module, "_last_seen_raw", {}))
        rows: list[dict[str, Any]] = []
        covered: set[tuple[str, str]] = set()
        for c in sorted(
            mappings, key=lambda x: (x.source.pattern, x.source_field)
        ):
            key = (c.source.pattern, c.source_field)
            covered.add(key)
            rows.append(
                {
                    "mapping": c,
                    "last_seen": last_seen.get(_mapping_key(c)),
                    "detection": detection_index.get(key),
                }
            )

        # Surface detection-only sources (no DATA_MAP entry) so the
        # user can see unit-detection coverage for elveh attributes, soc
        # batteryRange, vehicle-status sensors, etc.
        for (ent, attr), rec in sorted(detection_index.items()):
            if (ent, attr) in covered:
                continue
            rows.append({"mapping": None, "last_seen": None, "detection": rec})

        out.append({"source_name": source_name, "rows": rows})
    out.sort(key=lambda g: g["source_name"])
    return out


@router.get("/data-sources", response_class=HTMLResponse)
async def data_sources_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)
    return templates.TemplateResponse(
        request,
        "admin/data_sources.html",
        {
            "active_page": "data_sources",
            "active_vehicle": active_vehicle,
            "all_vehicles": all_vehicles,
            "groups": _load_groups(),
            "runtimes": supervisor.health(),
        },
    )
