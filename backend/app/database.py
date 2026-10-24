from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Use this (never a bare Depends(get_db)) for every request's session. scope="function"
# (FastAPI >= 0.121) runs get_db's commit before the response is sent; the default
# "request" scope commits only after, so a client could see 201 for a write the next
# request can't read yet. Mixing the two would also give one request two sessions.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
