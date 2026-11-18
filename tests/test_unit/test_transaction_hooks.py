"""on_rollback hooks follow the transaction or savepoint they were registered in."""

import pytest
from sqlalchemy import text

from db.transaction_hooks import on_rollback

pytestmark = [pytest.mark.unit, pytest.mark.db]


async def test_hook_runs_when_the_transaction_rolls_back(db_session):
    calls: list[str] = []
    await db_session.execute(text("SELECT 1"))
    on_rollback(db_session, lambda: calls.append("undo"))

    await db_session.rollback()

    assert calls == ["undo"]


async def test_hook_inside_a_savepoint_runs_when_only_the_savepoint_rolls_back(db_session):
    calls: list[str] = []
    await db_session.execute(text("SELECT 1"))
    on_rollback(db_session, lambda: calls.append("outer"))

    with pytest.raises(RuntimeError):
        async with db_session.begin_nested():
            on_rollback(db_session, lambda: calls.append("inner"))
            raise RuntimeError("boom")

    assert calls == ["inner"]
    await db_session.rollback()
    assert calls == ["inner", "outer"]


async def test_released_savepoint_hook_runs_when_the_outer_transaction_rolls_back(db_session):
    calls: list[str] = []
    async with db_session.begin_nested():
        on_rollback(db_session, lambda: calls.append("inner"))
    assert calls == []

    await db_session.rollback()

    assert calls == ["inner"]


async def test_hook_is_dropped_after_commit(db_session):
    calls: list[str] = []
    await db_session.execute(text("SELECT 1"))
    on_rollback(db_session, lambda: calls.append("undo"))

    await db_session.commit()
    await db_session.execute(text("SELECT 1"))
    await db_session.rollback()

    assert calls == []


async def test_hooks_unwind_newest_first(db_session):
    value = [None]
    await db_session.execute(text("SELECT 1"))
    on_rollback(db_session, lambda: value.__setitem__(0, None))
    on_rollback(db_session, lambda: value.__setitem__(0, "first"))

    await db_session.rollback()

    assert value[0] is None


async def test_rollback_session_runs_hooks_on_exit(db_session):
    from web.services.repair.base import rollback_session

    calls: list[str] = []
    async with rollback_session(engine=db_session.bind.engine) as session:
        await session.execute(text("SELECT 1"))
        on_rollback(session, lambda: calls.append("undo"))

    assert calls == ["undo"]
