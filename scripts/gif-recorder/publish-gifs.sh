#!/usr/bin/env bash
# publish-gifs.sh — promote freshly recorded gifs into docs/assets/images/
# and refresh version annotations in markdown GIF captions.
#
# Recordings live in scripts/gif-recorder/output/ as lr_<scene>-<tag>.gif
# (the tag is the recording branch). This script picks the gifs whose tag
# matches the current milestone, copies each to docs/assets/images/lr_<scene>.gif
# (stable filename so README/docs links never break), and then rewrites every
# `v0.X` annotation in markdown gif alt-text to the current full version from
# pyproject.toml.
#
# Usage:
#   scripts/gif-recorder/publish-gifs.sh                # use auto-detected tag (current branch)
#   scripts/gif-recorder/publish-gifs.sh --tag v0.4     # promote v0.4-tagged gifs
#   scripts/gif-recorder/publish-gifs.sh --dry-run      # show what would change, do nothing
#   scripts/gif-recorder/publish-gifs.sh --no-readme    # copy gifs but leave markdown alone
#
# The script never commits or pushes — it only changes files in the working
# tree so you can review the diff before committing.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$HERE/../.." && pwd)"
SRC_DIR="${GIF_OUT_DIR:-$HERE/output}"
DEST_DIR="$APP_ROOT/docs/assets/images"
README="$APP_ROOT/README.md"
DOCS_DIR="$APP_ROOT/docs"

DRY_RUN=0
UPDATE_README=1
TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --tag=*) TAG="${1#--tag=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-readme|--no-docs) UPDATE_README=0; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Resolve tag: explicit > current branch.
if [[ -z "$TAG" ]]; then
  TAG="$(git -C "$APP_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  TAG="${TAG//\//-}"
fi
if [[ -z "$TAG" ]]; then
  echo "Could not determine tag (not in a git repo? pass --tag explicitly)" >&2
  exit 1
fi

# Read current full version from pyproject.toml ([project].version).
VERSION="$(grep -E '^version\s*=\s*"' "$APP_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
if [[ -z "$VERSION" ]]; then
  echo "Could not read version from pyproject.toml" >&2
  exit 1
fi

echo "Publishing tag=${TAG}, version=v${VERSION}"
echo "  source: $SRC_DIR"
echo "  dest:   $DEST_DIR"
[[ "$DRY_RUN" -eq 1 ]] && echo "  (dry run — no files will change)"

# Collect candidate gifs.
shopt -s nullglob
candidates=("$SRC_DIR"/lr_*-"${TAG}".gif)
shopt -u nullglob

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No gifs found matching: $SRC_DIR/lr_*-${TAG}.gif" >&2
  echo "Record some first: ./scripts/gif-recorder/record.sh" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"

copied=()
for src in "${candidates[@]}"; do
  base="$(basename "$src")"               # lr_overview-v0.4.gif
  stem="${base%-${TAG}.gif}"              # lr_overview
  dest="$DEST_DIR/${stem}.gif"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would copy: $base -> $(realpath --relative-to="$APP_ROOT" "$dest")"
  else
    cp "$src" "$dest"
    echo "  copied: $base -> $(realpath --relative-to="$APP_ROOT" "$dest")"
  fi
  copied+=("$stem")
done

# Update markdown version annotations in gif captions. Targets patterns like
# `![cost page v0.2](docs/assets/images/lr_costs.gif)` and rewrites the
# version token to `v${VERSION}`. The replacement only touches lines that have
# both `![` and `.gif)`, so other version mentions in prose are left alone.
if [[ "$UPDATE_README" -eq 1 ]]; then
  markdown_files=()
  [[ -f "$README" ]] && markdown_files+=("$README")
  if [[ -d "$DOCS_DIR" ]]; then
    while IFS= read -r file; do
      markdown_files+=("$file")
    done < <(find "$DOCS_DIR" -type f -name "*.md" | sort)
  fi

  if [[ ${#markdown_files[@]} -eq 0 ]]; then
    echo "  (no markdown files found — skipping caption update)"
  else
    if [[ "$DRY_RUN" -eq 1 ]]; then
      python3 - "$VERSION" "${markdown_files[@]}" <<'PY'
import re
import sys
from pathlib import Path

version = sys.argv[1]
paths = [Path(p) for p in sys.argv[2:]]
pat = re.compile(r"(!\[[^]]*?)\bv\d+\.\d+(?:\.\d+)?(\b[^]]*\]\([^)]+\.gif\))")
total = 0
for path in paths:
    src = path.read_text()
    hits = pat.findall(src)
    total += len(hits)
    if hits:
        print(f"  would rewrite {len(hits)} caption(s) in {path}")
print(f"  would rewrite {total} markdown caption(s) to v{version}")
PY
    else
      python3 - "$VERSION" "${markdown_files[@]}" <<'PY'
import re
import sys
from pathlib import Path

version = sys.argv[1]
paths = [Path(p) for p in sys.argv[2:]]
pat = re.compile(r"(!\[[^]]*?)\bv\d+\.\d+(?:\.\d+)?(\b[^]]*\]\([^)]+\.gif\))")
total = 0
for path in paths:
    src = path.read_text()
    new, n = pat.subn(rf"\1v{version}\2", src)
    if n:
        path.write_text(new)
        print(f"  rewrote {n} caption(s) in {path}")
    total += n
print(f"  rewrote {total} markdown caption(s) to v{version}")
PY
    fi
  fi
fi

echo
echo "Done. Review the changes:"
echo "  git -C \"$APP_ROOT\" status"
echo "  git -C \"$APP_ROOT\" diff -- docs/assets/images README.md docs"
