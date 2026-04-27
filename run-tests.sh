#!/usr/bin/env bash
set -euo pipefail

# LightningROD Test Runner
# Starts the test Postgres container (postgres backend only) and runs pytest.
#
# Usage:
#   ./run-tests.sh                                   # Run PG suite with defaults (-x --tb=short)
#   ./run-tests.sh --backend=postgres                # Explicit PG backend (default)
#   ./run-tests.sh --backend=sqlite                  # SQLite dialect-compat suite
#   ./run-tests.sh --backend=all                     # Both backends, fail-fast
#   ./run-tests.sh -m db                             # Run only DB-marked tests
#   ./run-tests.sh -m query                          # Run only query tests
#   ./run-tests.sh tests/test_api/                   # Run API integration tests
#   ./run-tests.sh -k "test_sessions"                # Run tests matching name pattern
#   ./run-tests.sh --no-header -q                    # Quiet output

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- --backend selector ---
BACKEND="postgres"
PYTEST_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --backend=*) BACKEND="${arg#*=}" ;;
        *)           PYTEST_ARGS+=("$arg") ;;
    esac
done

export TEST_BACKEND="$BACKEND"

# Resolve python: venv if available, else system python3.
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

case "$BACKEND" in
    postgres)
        # Fall through to existing docker-compose + pytest flow below.
        ;;
    sqlite)
        echo "Running SQLite dialect-compat suite..."
        if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
            PYTEST_ARGS=("-x" "--tb=short" "tests/test_dialect_sqlite/")
        fi
        # SQLite uses a tmpfile DB — skip docker-compose pre-flight entirely.
        exec "$PYTHON" -m pytest "${PYTEST_ARGS[@]}"
        ;;
    all)
        # Chain backends sequentially; fail-fast.
        TEST_BACKEND=postgres "$0" --backend=postgres "${PYTEST_ARGS[@]}" || exit 1
        TEST_BACKEND=sqlite   "$0" --backend=sqlite   "${PYTEST_ARGS[@]}" || exit 1
        exit 0
        ;;
    *)
        echo "Unknown backend: $BACKEND (expected: postgres|sqlite|all)" >&2
        exit 1
        ;;
esac

# --- Postgres path: docker-compose + pytest flow ---

# Start test DB container
echo "Starting test database..."
docker compose -f docker/docker-compose.test.yml up -d test-db

# Wait for healthcheck
echo "Waiting for test DB to be ready..."
retries=0
max_retries=30
until docker compose -f docker/docker-compose.test.yml exec test-db pg_isready -U lightningrod_test -d lightningrod_test 2>/dev/null; do
    retries=$((retries + 1))
    if [ "$retries" -ge "$max_retries" ]; then
        echo "ERROR: Test DB failed to start after ${max_retries} attempts"
        exit 1
    fi
    sleep 1
done
echo "Test DB ready."

# Run pytest, passing through PYTEST_ARGS (default: -x --tb=short)
if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
    "$PYTHON" -m pytest -x --tb=short
else
    "$PYTHON" -m pytest "${PYTEST_ARGS[@]}"
fi
