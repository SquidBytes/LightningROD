#!/usr/bin/env bash
# Convert a Playwright video (webm) to an optimized GIF using a 2-pass palette.
#
# Usage:
#   convert-to-gif.sh <input.webm> <output.gif> [fps] [width] [head_trim_sec]
#
# Defaults: fps=15, width=960, head_trim_sec=0. The head_trim drops the first
# N seconds — useful because Playwright videos start at context creation, so
# the leading frames usually contain page-render settle time, not content.
set -euo pipefail

IN="${1:?input webm path required}"
OUT="${2:?output gif path required}"
FPS="${3:-15}"
WIDTH="${4:-960}"
HEAD_TRIM="${5:-0}"

if [[ ! -f "$IN" ]]; then
  echo "input not found: $IN" >&2
  exit 1
fi

PALETTE="$(mktemp -t gifpalette.XXXXXX.png)"
trap 'rm -f "$PALETTE"' EXIT

SS_ARGS=()
if [[ "$HEAD_TRIM" != "0" ]]; then
  SS_ARGS=(-ss "$HEAD_TRIM")
fi

# Pass 1: build an adaptive palette from the source video.
ffmpeg -hide_banner -loglevel error -y "${SS_ARGS[@]}" -i "$IN" \
  -vf "fps=${FPS},scale=${WIDTH}:-2:flags=lanczos,palettegen=stats_mode=diff" \
  "$PALETTE"

# Pass 2: encode GIF using the palette with dithering tuned for UI motion.
ffmpeg -hide_banner -loglevel error -y "${SS_ARGS[@]}" -i "$IN" -i "$PALETTE" \
  -lavfi "fps=${FPS},scale=${WIDTH}:-2:flags=lanczos[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  "$OUT"

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
