"""Cross-dialect SQLAlchemy TypeDecorators."""
from sqlalchemy import JSON, types
from sqlalchemy.dialects.postgresql import JSONB


class JSONStorage(types.TypeDecorator):
    """JSONB on PostgreSQL, JSON on SQLite. No operator support — generic storage only."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB(astext_type=None))
        return dialect.type_descriptor(JSON())
