"""Root conftest: test engine, DB session with transaction rollback, Alembic migrations.

CRITICAL: Environment variables are set BEFORE any app imports to prevent
the production engine (db/engine.py) from connecting to the dev database.
"""

import os
import subprocess
import sys

# Set test environment BEFORE any app imports (Pitfall 3 from RESEARCH.md)
os.environ["POSTGRES_USER"] = "lightningrod_test"
os.environ["POSTGRES_PASSWORD"] = "testpass"
os.environ["POSTGRES_DB"] = "lightningrod_test"
os.environ["POSTGRES_HOST"] = "localhost"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

TEST_DB_URL = "postgresql+asyncpg://lightningrod_test:testpass@localhost:5433/lightningrod_test"

_migrations_done = False


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
    env["POSTGRES_USER"] = "lightningrod_test"
    env["POSTGRES_PASSWORD"] = "testpass"
    env["POSTGRES_DB"] = "lightningrod_test"
    env["POSTGRES_HOST"] = "localhost:5433"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
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
