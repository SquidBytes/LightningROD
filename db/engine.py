"""Module for engine."""
from urllib.parse import urlparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config import settings


def _is_sqlite(url: str) -> bool:
    return urlparse(url).scheme.startswith("sqlite")


_kwargs: dict = {"echo": settings.debug}
if _is_sqlite(settings.database_url):
    _kwargs["poolclass"] = NullPool
else:
    _kwargs["pool_pre_ping"] = True
    _kwargs["pool_recycle"] = 3600

engine = create_async_engine(settings.database_url, **_kwargs)


# Dialect captured at engine-construction time so the listener can no-op cleanly
# on PG without inspecting the engine each call. Using the public Dialect.name
# attribute is more robust than sniffing the DBAPI wrapper's __module__ string,
# which is "sqlalchemy.dialects.sqlite.aiosqlite" (NOT "aiosqlite") under
# SQLAlchemy 2.0's async adapter — startswith("aiosqlite") would silently fail.
_DIALECT_IS_SQLITE = engine.sync_engine.dialect.name == "sqlite"


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """No-op on PG, PRAGMAs on SQLite. Detected via engine dialect name."""
    if not _DIALECT_IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
