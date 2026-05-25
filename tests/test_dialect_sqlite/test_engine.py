"""Engine + PRAGMA assertions on SQLite."""
import pytest
from sqlalchemy import text


@pytest.mark.db
async def test_dialect_is_sqlite(db_session):
    """Sanity check — under TEST_BACKEND=sqlite the bind dialect is SQLite."""
    assert db_session.bind.dialect.name == "sqlite"


@pytest.mark.db
async def test_pragma_foreign_keys_on(db_session):
    """PRAGMA foreign_keys must be ON per-connection."""
    result = await db_session.execute(text("PRAGMA foreign_keys"))
    assert result.scalar() == 1


@pytest.mark.db
async def test_pragma_journal_mode_wal(db_session):
    """PRAGMA journal_mode WAL set by db/engine.py connect-event listener."""
    result = await db_session.execute(text("PRAGMA journal_mode"))
    assert str(result.scalar()).lower() == "wal"


@pytest.mark.db
async def test_pragma_busy_timeout(db_session):
    """PRAGMA busy_timeout is set to 5000ms."""
    result = await db_session.execute(text("PRAGMA busy_timeout"))
    assert result.scalar() == 5000
