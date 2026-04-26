#!/usr/bin/env python3
"""Extract a single release section from CHANGELOG.md by version.

Usage:
    scripts/extract-changelog.py 0.3.1

Prints the body of the "## [0.3.1]" section to stdout.
Exits non-zero if the section is missing, empty, or contains placeholder
markers (TBD / TODO / XXX / FIXME / HTML comments).

Used by:
    - .github/workflows/release.yml  (hard gate on publishing)
    - scripts/tag-release.sh         (local sanity check before tagging)
"""
import re
import sys
from pathlib import Path

PLACEHOLDER_SIGNS = ("TBD", "TODO", "XXX", "FIXME", "<!--")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract-changelog.py <version>", file=sys.stderr)
        return 2
    version = sys.argv[1]

    changelog = Path("CHANGELOG.md")
    if not changelog.exists():
        print(f"ERROR: CHANGELOG.md not found in {Path.cwd()}", file=sys.stderr)
        return 1

    text = changelog.read_text()

    # Match "## [0.3.1]" optionally followed by "- YYYY-MM-DD", capture
    # everything until the next level-2 heading or end-of-file. The
    # terminator matches any "## ..." so trailing non-version sections
    # (e.g. "## Development") at the bottom of CHANGELOG.md don't leak
    # into the extracted body.
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\](?:\s*-\s*[^\n]*)?\n(.*?)(?=\n## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        print(
            f"ERROR: CHANGELOG.md has no section for version {version}.\n"
            f"Add a '## [{version}] - YYYY-MM-DD' heading with entries, or\n"
            f"run scripts/tag-release.sh {version} to promote [Unreleased] for you.",
            file=sys.stderr,
        )
        return 1

    section = match.group(1).strip()

    # Strip empty subsection headings ("### Added" with nothing under it)
    # before checking for emptiness.
    stripped = re.sub(
        r"^### \w+\s*(?=\n### |\Z)", "", section, flags=re.MULTILINE
    ).strip()

    if not stripped:
        print(
            f"ERROR: CHANGELOG.md section [{version}] is empty — no entries "
            f"under any ### category.",
            file=sys.stderr,
        )
        return 1

    for sign in PLACEHOLDER_SIGNS:
        if sign in section:
            print(
                f"ERROR: CHANGELOG.md section [{version}] still contains "
                f"placeholder text '{sign}'.",
                file=sys.stderr,
            )
            return 1

    # Print the cleaned section (with populated subsection headers
    # preserved) so empty categories don't leak into the Release body.
    out = re.sub(
        r"^### \w+\s*(?=\n### |\Z)",
        "",
        section,
        flags=re.MULTILINE,
    ).strip()
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
