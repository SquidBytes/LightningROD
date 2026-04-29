"""SQLite-only conftest. Skipped unless TEST_BACKEND=sqlite."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _enforce_sqlite_backend():
    """Skip the entire dialect-sqlite suite if not running with TEST_BACKEND=sqlite."""
    if os.environ.get("TEST_BACKEND") != "sqlite":
        pytest.skip(
            "dialect-sqlite tests only run with TEST_BACKEND=sqlite",
            allow_module_level=True,
        )
