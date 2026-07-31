#!/usr/bin/env bash
# tag-release.sh — prepare a LightningROD release in one command.
#
# Promotes [Unreleased] in CHANGELOG.md to [X.Y.Z] - <today>, bumps the
# version across pyproject.toml / package.json / .env, commits, and creates an
# annotated tag.
#
# Usage:
#   scripts/tag-release.sh 0.3.1
#
# Preconditions (all hard-fail):
#   - Working tree is clean (no uncommitted changes)
#   - CHANGELOG.md has a non-empty [Unreleased] section with no
#     placeholder markers (TBD / TODO / XXX / FIXME / <!-- ... -->)
#   - The target version does not already exist in CHANGELOG.md
#
# Undo-before-push is cheap:
#   git tag -d v0.3.1
#   git reset --hard HEAD~1

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <version>" >&2
    echo "Example: $0 0.3.1" >&2
    exit 2
fi
VERSION="$1"

if ! [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]]; then
    echo "Error: version must match MAJOR.MINOR.PATCH[-prerelease], got: ${VERSION}" >&2
    exit 1
fi

TAG="v${VERSION}"
TODAY="$(date +%Y-%m-%d)"

# --- 1. Working tree must be clean --------------------------------------
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working tree not clean. Commit or stash changes first." >&2
    git status --short >&2
    exit 1
fi

# --- 2. CHANGELOG.md must exist -----------------------------------------
if [[ ! -f CHANGELOG.md ]]; then
    echo "Error: CHANGELOG.md not found. Create it first (see DEV_CI_README.md)." >&2
    exit 1
fi

# --- 3. Validate [Unreleased] and promote it to [VERSION] - TODAY -------
python3 - "${VERSION}" "${TODAY}" <<'PY'
import re
import sys
from pathlib import Path

version, today = sys.argv[1], sys.argv[2]
path = Path("CHANGELOG.md")
text = path.read_text()

# Refuse if this version already exists.
if re.search(rf"^## \[{re.escape(version)}\]", text, re.MULTILINE):
    print(f"ERROR: CHANGELOG.md already has a [{version}] section.", file=sys.stderr)
    sys.exit(1)

# Find [Unreleased] block. Terminator matches any "## " heading (not just
# "## [") so a trailing "## Development" section at the bottom of the
# file doesn't leak into the captured body.
m = re.search(
    r"^## \[Unreleased\]\s*\n(.*?)(?=\n## |\Z)",
    text,
    re.MULTILINE | re.DOTALL,
)
if not m:
    print("ERROR: CHANGELOG.md has no [Unreleased] section.", file=sys.stderr)
    sys.exit(1)

body = m.group(1)

# Strip empty subsection headers before checking emptiness.
stripped = re.sub(r"^### \w+\s*(?=\n### |\Z)", "", body, flags=re.MULTILINE).strip()
if not stripped:
    print(
        "ERROR: CHANGELOG.md [Unreleased] section is empty.\n"
        "Add entries under ### Added / Changed / Fixed / Removed before tagging.",
        file=sys.stderr,
    )
    sys.exit(1)

for sign in ("TBD", "TODO", "XXX", "FIXME", "<!--"):
    if sign in body:
        print(
            f"ERROR: CHANGELOG.md [Unreleased] still contains placeholder '{sign}'.",
            file=sys.stderr,
        )
        sys.exit(1)

# Promote: insert a fresh empty [Unreleased] on top, and rename the old
# [Unreleased] heading to [VERSION] - TODAY.
fresh = (
    "## [Unreleased]\n\n"
    "### Added\n\n"
    "### Changed\n\n"
    "### Fixed\n\n"
    "### Removed\n\n"
)
new_text = re.sub(
    r"^## \[Unreleased\]",
    fresh + f"## [{version}] - {today}",
    text,
    count=1,
    flags=re.MULTILINE,
)
path.write_text(new_text)
print(f"Promoted [Unreleased] → [{version}] - {today}")
PY

# --- 4. Bump version in pyproject.toml / package.json / .env -------------
scripts/bump-version.sh "${VERSION}"

# --- 5. Stage everything the bump + promotion touched --------------------
git add CHANGELOG.md pyproject.toml package.json README.md docs
[[ -f package-lock.json ]] && git add package-lock.json
[[ -f uv.lock ]] && git add uv.lock
# .env is typically gitignored for LightningROD; bump-version.sh touches
# it for local docker compose builds, but we don't commit it.
git reset .env 2>/dev/null || true

# --- 6. Commit ----------------------------------------------------------
git commit -m "release: v${VERSION}"

# --- 7. Tag -------------------------------------------------------------
git tag -a "${TAG}" -m "LightningROD ${TAG}"

# --- 8. Print push instructions (do NOT push automatically) --------------
BRANCH="$(git branch --show-current)"
cat <<EOF

✓ Release prepared: ${TAG}

Review before pushing:
    git show HEAD                    # the release commit
    git show ${TAG}                  # the annotated tag
    sed -n '/## \[${VERSION}\]/,/## \[/p' CHANGELOG.md   # the notes that will publish

When you are ready, push with:
    git push origin ${BRANCH}
    git push origin ${TAG}

The second push (the tag) is what triggers .github/workflows/release.yml.

To undo without pushing:
    git tag -d ${TAG}
    git reset --hard HEAD~1
EOF
