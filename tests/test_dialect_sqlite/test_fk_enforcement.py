"""Per-connection FK enforcement assertion (RESEARCH Pitfall 1).

SQLite enforces foreign keys only when ``PRAGMA foreign_keys = ON`` is set per
connection. db/engine.py installs a connect-event listener that runs the
PRAGMA on every aiosqlite handshake. Without it, FK violations would silently
succeed — exactly the failure mode this test guards against.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.mark.db
async def test_fk_violation_raises(db_session):
    """Insert into ev_charging_session.network_id pointing at a non-existent
    network row → IntegrityError. If FKs are off, the row would silently
    appear with a dangling reference.
    """
    bogus_network_id = 999_999_999
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                """
                INSERT INTO ev_charging_session
                    (device_id, network_id, is_complete, needs_review)
                VALUES (:dev, :nid, 0, 0)
                """
            ),
            {"dev": "FK_TEST_DEVICE", "nid": bogus_network_id},
        )
        # Force the FK check to fire now (savepoint/transaction context).
        await db_session.flush()
