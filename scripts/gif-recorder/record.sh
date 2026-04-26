#!/usr/bin/env bash
# Record one or more scenes and convert each to an optimized GIF.
#
# Usage:
#   record.sh                    # records every *.spec.ts in scenes/
#   record.sh overview           # records scenes/overview.spec.ts
#   record.sh overview costs     # records the listed scenes
#
# Env:
#   GIF_PORT       (default 8000) port the dev server is listening on
#   GIF_BASE_URL   (overrides GIF_PORT)
#   GIF_OUT_DIR    (default scripts/gif-recorder/output) where final .gif files land
#   GIF_KEEP_RAW   if set to 1, keep the raw webm artifacts under output/raw/
#   GIF_FPS        (default 15)   passed through to convert-to-gif.sh
#   GIF_WIDTH      (default 1280) passed through to convert-to-gif.sh
#   GIF_HEAD_TRIM  (default 1.5)  seconds to drop from the start of the video,
#                                 covers Playwright's pre-render settle window
#   GIF_TAG        (auto)         tag suffix appended to output filenames so
#                                 you can tell which milestone a recording
#                                 came from. Defaults to the current git
#                                 branch (sanitized). Set explicitly to
#                                 override, or set to empty to disable.
#
# Output filenames: lr_<scene>-<tag>.gif (e.g. lr_overview-v0.4.gif).
# These versioned files stay in output/ until publish-gifs.sh promotes them
# into docs/assets/images/ under stable names (lr_<scene>.gif).
#
# Per-scene overrides: a scene named foo can opt into custom values by exporting
# them in scenes/foo.env (sourced before that scene's conversion). Recognized
# keys: FPS, WIDTH, HEAD_TRIM.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$HERE/../.." && pwd)"
OUT_DIR="${GIF_OUT_DIR:-$HERE/output}"
RAW_DIR="$HERE/output/raw"
PORT="${GIF_PORT:-8000}"
BASE_URL="${GIF_BASE_URL:-http://localhost:$PORT}"

# Auto-derive a tag suffix from the current git branch unless GIF_TAG is set
# (use GIF_TAG="" to opt out of tagging entirely). Forward-slashes get
# replaced with dashes so feature/foo doesn't break filenames.
if [[ -z "${GIF_TAG+x}" ]]; then
  if branch="$(git -C "$APP_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
    GIF_TAG="${branch//\//-}"
  else
    GIF_TAG=""
  fi
fi
TAG_SUFFIX=""
[[ -n "$GIF_TAG" ]] && TAG_SUFFIX="-${GIF_TAG}"

mkdir -p "$OUT_DIR" "$RAW_DIR"

# Sanity check: make sure the app is reachable before we spin up Playwright.
if ! curl -fs -o /dev/null --max-time 3 "$BASE_URL/"; then
  echo "Dev server not reachable at $BASE_URL"  >&2
  echo "Start it first, e.g.:"                    >&2
  echo "  uv run uvicorn web.main:app --port $PORT" >&2
  exit 1
fi

cd "$APP_ROOT"

# Build the list of scene files.
scenes=()
if [[ $# -eq 0 ]]; then
  while IFS= read -r f; do scenes+=("$f"); done < <(find "$HERE/scenes" -maxdepth 1 -name "*.spec.ts" -type f | sort)
else
  for name in "$@"; do
    f="$HERE/scenes/${name}.spec.ts"
    if [[ ! -f "$f" ]]; then
      echo "no scene: $f" >&2; exit 1
    fi
    scenes+=("$f")
  done
fi

# Wipe prior raw output so we don't pick up stale videos.
rm -rf "$RAW_DIR"
mkdir -p "$RAW_DIR"

CONFIG="$HERE/playwright.config.ts"

for scene in "${scenes[@]}"; do
  name="$(basename "$scene" .spec.ts)"
  echo "=== recording: $name ==="
  GIF_BASE_URL="$BASE_URL" npx playwright test --config "$CONFIG" "$scene"

  # Playwright drops video at output/raw/<spec-name>-<title>-chromium/video.webm.
  webm="$(find "$RAW_DIR" -path "*${name}*" -name "video.webm" -type f | head -n1)"
  if [[ -z "$webm" ]]; then
    echo "no video found for $name under $RAW_DIR" >&2
    exit 1
  fi

  # Per-scene overrides via sidecar env file.
  FPS="${GIF_FPS:-15}"
  WIDTH="${GIF_WIDTH:-1280}"
  HEAD_TRIM="${GIF_HEAD_TRIM:-1.5}"
  if [[ -f "$HERE/scenes/${name}.env" ]]; then
    # shellcheck disable=SC1090
    source "$HERE/scenes/${name}.env"
  fi

  gif="$OUT_DIR/lr_${name}${TAG_SUFFIX}.gif"
  "$HERE/convert-to-gif.sh" "$webm" "$gif" "$FPS" "$WIDTH" "$HEAD_TRIM"
done

if [[ "${GIF_KEEP_RAW:-0}" != "1" ]]; then
  rm -rf "$RAW_DIR"
fi

echo "done. gifs in: $OUT_DIR"
