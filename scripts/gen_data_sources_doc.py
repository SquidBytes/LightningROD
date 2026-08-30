"""Auto-generate docs/data-sources.md from adapter DATA_MAP registries.

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

from web.services.sources.registry import REGISTRY  # noqa: E402
from web.services.units.data_map import DataMapping  # noqa: E402

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data-sources.md"


def discover_adapters() -> list[tuple[str, list[DataMapping]]]:
    """Return [(source_name, mappings), ...] sorted by source_name."""
    import importlib

    out: list[tuple[str, list[DataMapping]]] = []
    for source in REGISTRY:
        module = importlib.import_module(source.adapter_module)
        mappings = list(getattr(module, "DATA_MAP", []))
        out.append((source.source_name, mappings))
    out.sort(key=lambda pair: pair[0])
    return out


def render_markdown(groups: list[tuple[str, list[DataMapping]]]) -> str:
    """Pure function: groups -> deterministic markdown string."""
    lines: list[str] = [
        "# Data Sources",
        "",
        "**AUTO-GENERATED — DO NOT EDIT.** Run `uv run python scripts/gen_data_sources_doc.py` to refresh.",
        "",
        "Every field ingested by LightningROD is declared in a source adapter's",
        "`DATA_MAP` registry. This page reflects that registry at commit time and is",
        "the observable record of how each value is mapped and converted.",
        "",
    ]
    for source_name, mappings in groups:
        lines.append(f"## {source_name}")
        lines.append("")
        if not mappings:
            lines.append("_No mappings registered._")
            lines.append("")
            continue
        lines.append("| Source Entity | Source Field | Source Unit | DB Table | DB Column | DB Unit | Notes |")
        lines.append("|---|---|---|---|---|---|---|")
        sorted_mappings = sorted(
            mappings,
            key=lambda c: (c.source.pattern, c.source_field),
        )
        for c in sorted_mappings:
            # Escape pipe characters in notes so they don't break the table.
            notes = (c.notes or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{c.source.pattern}` "
                f"| `{c.source_field}` "
                f"| `{c.source_unit}` "
                f"| `{c.db_table}` "
                f"| `{c.db_column}` "
                f"| `{c.db_unit}` "
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
            print(f"OK: {_OUTPUT_PATH} is in sync with DATA_MAP.")
            return 0
        print(f"DRIFT: {_OUTPUT_PATH} does not match the current DATA_MAP registry.")
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
          f"{sum(len(c) for _, c in groups)} mapping(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
