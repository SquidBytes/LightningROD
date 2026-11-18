#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["resvg-py"]
# ///
"""Render website/og-image.png from website/og-image.svg.

Usage:
    uv run scripts/render_og_image.py          # re-render the PNG
    python scripts/render_og_image.py --check  # exit 1 if the PNG is stale

The PNG stores the SHA-256 of the SVG it was rendered from, so --check needs
no renderer.

Used by:
    - .githooks/pre-commit              (blocks a commit with a stale PNG)
    - .github/workflows/og-image.yml    (same check on dev and main)
"""
import hashlib
import struct
import sys
import zlib
from pathlib import Path

WEBSITE = Path(__file__).resolve().parent.parent / "website"
SVG = WEBSITE / "og-image.svg"
PNG = WEBSITE / "og-image.png"
KEY = b"og-svg-sha256"
IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def svg_hash() -> bytes:
    return hashlib.sha256(SVG.read_bytes()).hexdigest().encode()


def text_chunk(value: bytes) -> bytes:
    body = b"tEXt" + KEY + b"\x00" + value
    return struct.pack(">I", len(body) - 4) + body + struct.pack(">I", zlib.crc32(body))


def render() -> int:
    import resvg_py

    png = bytes(resvg_py.svg_to_bytes(svg_path=str(SVG), width=1200, height=630))
    if not png.endswith(IEND):
        print("ERROR: renderer returned an unexpected PNG", file=sys.stderr)
        return 1
    PNG.write_bytes(png[: -len(IEND)] + text_chunk(svg_hash()) + IEND)
    print(f"Rendered {PNG.relative_to(WEBSITE.parent)}")
    return 0


def check() -> int:
    if PNG.exists() and KEY + b"\x00" + svg_hash() in PNG.read_bytes():
        return 0
    print(
        "ERROR: website/og-image.png is out of date with website/og-image.svg.\n"
        "Run: uv run scripts/render_og_image.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv[1:] else render())
