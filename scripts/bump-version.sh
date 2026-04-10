#!/usr/bin/env bash
# bump-version.sh — single-command version bump across every file that tracks
# the LightningROD version.
#
# Usage:
#   scripts/bump-version.sh 0.3.1            # set an explicit version
#   scripts/bump-version.sh --show           # print the current version
#
# What it touches:
#   pyproject.toml         (authoritative [project].version)
#   package.json           (npm "version" field)
#   .env                   (LIGHTNINGROD_VERSION, consumed by docker-compose)
#
# After bumping, rebuild with `docker compose build` (or the Makefile
# equivalent) to produce lightningrod-web:<new-version>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

pyproject="${ROOT}/pyproject.toml"
package_json="${ROOT}/package.json"
env_file="${ROOT}/.env"

current_version() {
    grep -E '^version\s*=\s*"' "${pyproject}" | head -1 | sed -E 's/.*"([^"]+)".*/\1/'
}

if [[ $# -eq 0 || "${1:-}" == "--show" ]]; then
    echo "Current version: $(current_version)"
    exit 0
fi

new_version="$1"

if ! [[ "${new_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]]; then
    echo "Error: version must look like MAJOR.MINOR.PATCH (optionally -prerelease)" >&2
    echo "Got: ${new_version}" >&2
    exit 1
fi

echo "Bumping version: $(current_version) -> ${new_version}"

# pyproject.toml — only touch the [project].version line, not any dep pins
python3 - "$pyproject" "$new_version" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
new = sys.argv[2]
text = path.read_text()
# Match the first 'version = "x.y.z"' inside the [project] table
new_text, n = re.subn(
    r'(?ms)(^\[project\].*?^version\s*=\s*")[^"]+(")',
    lambda m: m.group(1) + new + m.group(2),
    text,
    count=1,
)
if n != 1:
    sys.exit("Could not update pyproject.toml version")
path.write_text(new_text)
PY
echo "  updated pyproject.toml"

# package.json — update the "version" field
python3 - "$package_json" "$new_version" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
new = sys.argv[2]
data = json.loads(path.read_text())
data["version"] = new
path.write_text(json.dumps(data, indent=2) + "\n")
PY
echo "  updated package.json"

# .env — set or add LIGHTNINGROD_VERSION=<new>
if [[ -f "${env_file}" ]]; then
    if grep -q '^LIGHTNINGROD_VERSION=' "${env_file}"; then
        # macOS BSD sed needs -i '' but gnu sed wants -i. Use a temp-file dance
        # to stay portable.
        tmp="$(mktemp)"
        sed 's|^LIGHTNINGROD_VERSION=.*|LIGHTNINGROD_VERSION='"${new_version}"'|' \
            "${env_file}" > "${tmp}"
        mv "${tmp}" "${env_file}"
    else
        printf '\nLIGHTNINGROD_VERSION=%s\n' "${new_version}" >> "${env_file}"
    fi
    echo "  updated .env"
else
    echo "  .env not found — skipped (create it from .env.example)"
fi

echo
echo "Done. Rebuild images with:"
echo "  docker compose build"
echo "  # or:"
echo "  docker compose -f docker-compose.standalone.yml build"
