"""The shared pipeline must not know any data source or manufacturer."""

from __future__ import annotations

from pathlib import Path

PIPELINE = Path(__file__).parent.parent.parent / "web" / "services" / "pipeline"


def test_pipeline_imports_nothing_from_sources():
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(PIPELINE.glob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if line.lstrip().startswith(("from ", "import ")) and "web.services.sources" in line
    ]
    assert offenders == []
