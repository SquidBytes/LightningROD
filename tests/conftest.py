"""Root conftest: test engine, DB session with transaction rollback, Alembic migrations.

Backend selectable via TEST_BACKEND env var (default 'postgres'; 'sqlite' for
the dialect-compat suite — see tests/test_dialect_sqlite/).

CRITICAL: DATABASE_URL is set BEFORE any app imports to prevent the production
engine (db/engine.py) from connecting to the dev database.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _resolve_test_db_url() -> str:
    """Return the test DATABASE_URL for the active TEST_BACKEND.

    sqlite: tmpfile DB wiped at module load so each session starts clean.
    postgres (default): the dockerized test-db service URL.
    """
    backend = os.environ.get("TEST_BACKEND", "postgres")
    if backend == "sqlite":
        tmp = Path(tempfile.gettempdir()) / "lightningrod_test_dialect.db"
        tmp.unlink(missing_ok=True)
        return f"sqlite+aiosqlite:///{tmp}"
    return "postgresql+asyncpg://lightningrod_test:testpass@localhost:5433/lightningrod_test"


# DATABASE_URL must be set before any app imports so config.py + alembic
# env.py pick it up. E402 below is allowed by ruff because only os.environ
# assignments separate the import groups.
_BACKEND = os.environ.get("TEST_BACKEND", "postgres")
TEST_DB_URL = _resolve_test_db_url()
os.environ["DATABASE_URL"] = TEST_DB_URL

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

_migrations_done = False


def _attach_sqlite_pragmas(engine):
    """Mirror db/engine.py: install per-connection PRAGMAs on a fresh test engine.

    The fixture creates an isolated engine per test for transaction-rollback
    semantics; that engine bypasses db/engine.py's listener, so SQLite would
    default to foreign_keys=OFF and journal_mode=delete. Re-installing the
    same listener here keeps test behaviour aligned with production.
    """
    if engine.sync_engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA busy_timeout = 5000")
        finally:
            cursor.close()


def _run_alembic_migrations():
    """Run Alembic migrations via subprocess to avoid event loop conflicts.
    The Alembic env.py uses asyncio.run internally, which cannot be called
    from within an already-running event loop (as in an async pytest fixture).
    Running as a subprocess avoids this entirely.
    """
    global _migrations_done
    if _migrations_done:
        return

    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # If DB is unavailable (e.g. running unit tests without Docker), skip
        # migrations rather than aborting the entire test session. Tests that
        # actually need the DB will fail individually via missing fixtures.
        # Lowercase the stderr once and check a stable marker tuple — relying
        # on a single exact string is fragile across asyncpg/aiosqlite versions.
        stderr_lower = result.stderr.lower()
        connect_markers = (
            "connect call failed",
            "connectionrefusederror",
            "could not connect to server",
            "no such file or directory",  # aiosqlite missing parent dir
        )
        if any(marker in stderr_lower for marker in connect_markers):
            return
        raise RuntimeError(
            f"Alembic migration failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    _migrations_done = True


# Run migrations once at module load time (before any tests)
_run_alembic_migrations()


@pytest_asyncio.fixture
async def db_session():
    """Per-test DB session with transaction rollback isolation.

    Each test gets a fresh engine + connection + transaction that rolls back
    after the test completes, so no data persists between tests.

    Uses join_transaction_mode='create_savepoint' so that when the session
    does internal operations (like autoflush), it creates sub-savepoints
    within the test transaction rather than committing.
    """
    engine = create_async_engine(TEST_DB_URL, echo=False)
    _attach_sqlite_pragmas(engine)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        yield session

        await session.close()
        await trans.rollback()
    await engine.dispose()


@pytest.fixture(autouse=True)
def reset_factories():
    """Reset factory seed before each test for deterministic data generation."""
    from tests.factories import BaseFactory

    BaseFactory.reset_seed()
    yield
