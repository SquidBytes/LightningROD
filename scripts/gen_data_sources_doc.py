"""Auto-generate docs/data-sources.md from adapter FIELD_CONTRACTS registries.

Usage:
    uv run python scripts/gen_data_sources_doc.py          # write file
    uv run python scripts/gen_data_sources_doc.py --check  # fail if out of sync

CI: run with --check on every PR. Exits 1 with a diff if the committed
docs/data-sources.md does not match what the current registries would
generate.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

# Make the project root importable so `from web.services...` works when this
# script is run from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.services.units.contracts import FieldContract  # noqa: E402

# Explicit manifest of known adapters. Append a tuple here when a new adapter
# lands. The registry remains the source of truth; this list only enumerates
# which modules to read.
_ADAPTER_MODULES: list[tuple[str, str]] = [
    ("ha_fordpass", "web.services.sources.ha_fordpass.adapter"),
]

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data-sources.md"


def discover_adapters() -> list[tuple[str, list[FieldContract]]]:
    """Return [(source_name, contracts), ...] sorted by source_name."""
    import importlib

    out: list[tuple[str, list[FieldContract]]] = []
    for source_name, module_path in _ADAPTER_MODULES:
        module = importlib.import_module(module_path)
        contracts = list(getattr(module, "FIELD_CONTRACTS", []))
        out.append((source_name, contracts))
    out.sort(key=lambda pair: pair[0])
    return out


def render_markdown(groups: list[tuple[str, list[FieldContract]]]) -> str:
    """Pure function: groups -> deterministic markdown string."""
    lines: list[str] = [
        "# Data Sources",
        "",
        "**AUTO-GENERATED — DO NOT EDIT.** Run `uv run python scripts/gen_data_sources_doc.py` to refresh.",
        "",
        "Every field ingested by LightningROD is declared in a source adapter's",
        "`FIELD_CONTRACTS` registry. This page is a reflection of that registry at",
        "commit time and serves as the observable unit contract.",
        "",
    ]
    for source_name, contracts in groups:
        lines.append(f"## {source_name}")
        lines.append("")
        if not contracts:
            lines.append("_No contracts registered._")
            lines.append("")
            continue
        lines.append("| Source Entity | Source Attribute | Source Unit | DB Table | DB Column | Target Unit | Notes |")
        lines.append("|---|---|---|---|---|---|---|")
        sorted_contracts = sorted(
            contracts,
            key=lambda c: (c.source_locator.pattern, c.source_attribute),
        )
        for c in sorted_contracts:
            # Escape pipe characters in notes so they don't break the table.
            notes = (c.notes or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{c.source_locator.pattern}` "
                f"| `{c.source_attribute}` "
                f"| `{c.source_unit}` "
                f"| `{c.target_db_table}` "
                f"| `{c.target_db_column}` "
                f"| `{c.target_unit}` "
                f"| {notes} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if committed docs/data-sources.md is out of sync with the registry.",
    )
    args = parser.parse_args(argv)

    groups = discover_adapters()
    rendered = render_markdown(groups)

    if args.check:
        existing = _OUTPUT_PATH.read_text() if _OUTPUT_PATH.exists() else ""
        if existing == rendered:
            print(f"OK: {_OUTPUT_PATH} is in sync with FIELD_CONTRACTS.")
            return 0
        print(f"DRIFT: {_OUTPUT_PATH} does not match the current FIELD_CONTRACTS registry.")
        diff = difflib.unified_diff(
            existing.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{_OUTPUT_PATH} (committed)",
            tofile=f"{_OUTPUT_PATH} (would generate)",
        )
        sys.stdout.writelines(diff)
        print()
        print("Run `uv run python scripts/gen_data_sources_doc.py` to refresh, then commit.")
        return 1

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(rendered)
    print(f"Wrote {_OUTPUT_PATH} ({len(groups)} source(s), "
          f"{sum(len(c) for _, c in groups)} contract(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
