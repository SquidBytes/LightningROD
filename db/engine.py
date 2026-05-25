"""Async SQLAlchemy engine and session factory."""
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


# Dialect captured at engine construction so the listener can no-op cleanly
# on PostgreSQL without inspecting the engine on every connection.
_DIALECT_IS_SQLITE = engine.sync_engine.dialect.name == "sqlite"


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Enable SQLite safety/performance PRAGMAs on each DBAPI connection."""
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
