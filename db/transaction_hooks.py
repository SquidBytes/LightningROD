"""Undo in-memory side effects when a session's transaction rolls back."""

from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_KEY = "on_rollback"


def on_rollback(db: AsyncSession, fn: Callable[[], None]) -> None:
    """Run ``fn`` if the current transaction rolls back; forgotten once it commits."""
    db.info.setdefault(_KEY, []).append(fn)


@event.listens_for(Session, "after_commit")
def _forget(session: Session) -> None:
    session.info.pop(_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _undo(session: Session, previous: SessionTransaction) -> None:
    if previous.nested:
        return  # a savepoint rollback leaves the outer transaction alive
    for fn in session.info.pop(_KEY, []):
        fn()
