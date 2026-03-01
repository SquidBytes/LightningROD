from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
