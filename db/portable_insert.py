"""Dialect-portable upsert helper."""
from typing import Any

from sqlalchemy.dialects.postgresql import Insert as _PgInsert
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import Insert as _SqliteInsert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.engine import Dialect

PortableInsert = _PgInsert | _SqliteInsert


def portable_insert(table: Any, *, dialect: Dialect) -> PortableInsert:
    """Return a dialect-appropriate Insert construct.

    Both PG and SQLite Insert expose:
      - .values(...)
      - .on_conflict_do_update(index_elements=..., set_=...)
      - .on_conflict_do_nothing(index_elements=...)
      - .excluded.<col>

    PG-only features (index_where=, constraint=, where=) are deliberately
    not supported here — branch in the call site if you reach for them.
    """
    if dialect.name == "postgresql":
        return _pg_insert(table)
    if dialect.name == "sqlite":
        return _sqlite_insert(table)
    raise NotImplementedError(
        f"portable_insert: unsupported dialect {dialect.name!r}"
    )
