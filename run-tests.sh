#!/usr/bin/env bash
set -euo pipefail

# LightningROD Test Runner
# Starts the test Postgres container and runs pytest.
#
# Usage:
#   ./run-tests.sh                        # Run all tests with defaults (-x --tb=short)
#   ./run-tests.sh -m db                  # Run only DB-marked tests
#   ./run-tests.sh -m query               # Run only query tests
#   ./run-tests.sh tests/test_api/        # Run API integration tests
#   ./run-tests.sh -k "test_sessions"     # Run tests matching name pattern
#   ./run-tests.sh --no-header -q         # Quiet output

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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

# Run pytest, passing through all arguments (default: -x --tb=short)
# Use venv python if available, else system python3
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

if [ $# -eq 0 ]; then
    $PYTHON -m pytest -x --tb=short
else
    $PYTHON -m pytest "$@"
fi
