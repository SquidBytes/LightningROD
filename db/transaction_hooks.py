"""Undo in-memory side effects when a session's transaction rolls back."""

from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_KEY = "on_rollback"


def on_rollback(db: AsyncSession, fn: Callable[[], None]) -> None:
    """Run ``fn`` if the current transaction or savepoint rolls back; forgotten once it commits."""
    sync = db.sync_session
    # get_transaction() is always the root, so a savepoint is looked up first.
    tx = sync.get_nested_transaction() or sync.get_transaction() or sync.begin()
    db.info.setdefault(_KEY, []).append((tx, fn))


def _under(tx: SessionTransaction | None, rolled_back: SessionTransaction) -> bool:
    """Whether ``tx`` is ``rolled_back`` itself or a savepoint inside it."""
    while tx is not None:
        if tx is rolled_back:
            return True
        tx = tx.parent
    return False


@event.listens_for(Session, "after_commit")
def _forget(session: Session) -> None:
    if not session.in_nested_transaction():  # a savepoint release is not a commit
        session.info.pop(_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _undo(session: Session, previous: SessionTransaction) -> None:
    hooks = session.info.get(_KEY, [])
    session.info[_KEY] = [(tx, fn) for tx, fn in hooks if not _under(tx, previous)]
    for tx, fn in reversed(hooks):  # unwind newest first
        if _under(tx, previous):
            fn()
