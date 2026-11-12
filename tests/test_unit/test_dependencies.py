"""The request session commits on a clean return and rolls back on an exception."""

import pytest

from web import dependencies


class _Session:
    def __init__(self):
        self.calls: list[str] = []

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")

    async def close(self):
        self.calls.append("close")


class _Factory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        await self.session.close()
        return False


@pytest.fixture
def session(monkeypatch):
    fake = _Session()
    monkeypatch.setattr(dependencies, "AsyncSessionLocal", _Factory(fake))
    return fake


async def test_get_db_commits_on_clean_return(session):
    gen = dependencies.get_db()
    assert await gen.__anext__() is session
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert session.calls == ["commit", "close"]


async def test_get_db_rolls_back_on_exception(session):
    gen = dependencies.get_db()
    await gen.__anext__()
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("route failed"))
    assert session.calls == ["rollback", "close"]
