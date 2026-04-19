"""Admin: Data Sources diagnostic page.

Reads the FIELD_CONTRACTS registry + _last_seen_raw cache from each source
adapter and renders a searchable table. Purpose: per Phase 29 CONTEXT.md D-C3,
let the user trace any displayed value -> source entity -> attribute -> unit ->
raw value last observed.

Single-user app -> ungated route per D-C3. Future multi-user would add an
admin-only guard here.
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
from web.services.units.contracts import FieldContract

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")

# Explicit manifest. Keep in sync with scripts/gen_data_sources_doc.py.
_ADAPTER_MODULES: list[tuple[str, str]] = [
    ("ha_fordpass", "web.services.sources.ha_fordpass.adapter"),
]


def _contract_key(c: FieldContract) -> str:
    """Key convention matches ha_fordpass.adapter._record_last_seen."""
    return f"{c.source_entity_pattern}|{c.source_attribute}"


def _load_groups() -> list[dict[str, Any]]:
    """Read every adapter's FIELD_CONTRACTS + _last_seen_raw at request time.

    Returns a list of dicts shaped for the template:
        [
            {
                "source_name": "ha_fordpass",
                "rows": [
                    {"contract": FieldContract, "last_seen": dict | None},
                    ...
                ],
            },
            ...
        ]
    """
    out: list[dict[str, Any]] = []
    for source_name, module_path in _ADAPTER_MODULES:
        module = importlib.import_module(module_path)
        contracts: list[FieldContract] = list(getattr(module, "FIELD_CONTRACTS", []))
        last_seen: dict[str, dict[str, Any]] = dict(getattr(module, "_last_seen_raw", {}))
        rows = [
            {"contract": c, "last_seen": last_seen.get(_contract_key(c))}
            for c in sorted(contracts, key=lambda x: (x.source_entity_pattern, x.source_attribute))
        ]
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
        "admin/data_sources.html",
        {
            "request": request,
            "active_page": "data_sources",
            "active_vehicle": active_vehicle,
            "all_vehicles": all_vehicles,
            "groups": _load_groups(),
        },
    )
