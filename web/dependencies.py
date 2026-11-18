"""FastAPI dependency providers."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession]:
    """One transaction per request: commit on a clean return, roll back on an exception.

    Explicit commit rather than ``session.begin()`` so a route may commit early
    (before handing off to a service that opens its own session).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# scope="function" ends the transaction before the response is sent, so a
# failed commit becomes a 500 instead of landing after the client saw success.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
