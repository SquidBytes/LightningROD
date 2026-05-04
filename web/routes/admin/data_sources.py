"""Admin: Data Sources diagnostic page.
Reads the FIELD_CONTRACTS registry + _last_seen_raw cache from each source
adapter and renders a searchable table. Purpose: ,
let the user trace any displayed value -> source entity -> attribute -> unit ->
raw value last observed.
Single-user app -> ungated route . Future multi-user would add an
admin-only guard here.

Also merges in unit-detection records (web.services.units.detection) so the
table surfaces cross-referenced + read-time-detected sources that do NOT
appear in FIELD_CONTRACTS.
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
from web.services.units.contracts import FieldContract

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")


def _contract_key(c: FieldContract) -> str:
    """Key convention matches ha_fordpass.adapter._record_last_seen."""
    return f"{c.source_locator.pattern}|{c.source_attribute}"


def _load_groups() -> list[dict[str, Any]]:
    """Read every adapter's FIELD_CONTRACTS + _last_seen_raw at request time.

    Returns a list of dicts shaped for the template:
        [
            {
                "source_name": "ha_fordpass",
                "rows": [
                    {"contract": FieldContract, "last_seen": dict | None,
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

    for descriptor in REGISTRY:
        source_name = descriptor.source_name
        module = importlib.import_module(descriptor.adapter_module)
        contracts: list[FieldContract] = list(getattr(module, "FIELD_CONTRACTS", []))
        last_seen: dict[str, dict[str, Any]] = dict(getattr(module, "_last_seen_raw", {}))
        rows = []
        covered: set[tuple[str, str]] = set()
        for c in sorted(
            contracts, key=lambda x: (x.source_locator.pattern, x.source_attribute)
        ):
            key = (c.source_locator.pattern, c.source_attribute)
            covered.add(key)
            rows.append(
                {
                    "contract": c,
                    "last_seen": last_seen.get(_contract_key(c)),
                    "detection": detection_index.get(key),
                }
            )

        # Surface detection-only sources (no FIELD_CONTRACTS entry) so the
        # user can see unit-detection coverage for elveh attributes, soc
        # batteryRange, vehicle-status sensors, etc.
        for (ent, attr), rec in sorted(detection_index.items()):
            if (ent, attr) in covered:
                continue
            rows.append({"contract": None, "last_seen": None, "detection": rec})

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
