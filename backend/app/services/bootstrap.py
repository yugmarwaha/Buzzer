"""
On first startup, create the initial admin user if ADMIN_USERNAME and
ADMIN_PASSWORD are set and no ADMIN account exists yet.

This runs once inside the lifespan startup hook. After the first admin
exists the env vars are ignored — remove them from .env if you like,
or leave them (they simply become a no-op).

On a fresh database the schema doesn't exist until `alembic upgrade head`
has been run, so this retries instead of crashing: startup blocks (with a
log line every few seconds) until migrations land, then completes on its
own. Crashing here wouldn't self-heal — under `uvicorn --reload` the
reloader process survives an app startup failure, so the container looks
"Up" while nothing is serving.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from ..config import settings
from ..database import AsyncSessionLocal
from ..models.user import User
from .auth_service import hash_password

logger = structlog.get_logger()

RETRY_SECONDS = 5


async def bootstrap_admin() -> None:
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        return

    while True:
        try:
            await _create_admin_if_missing()
            return
        except ProgrammingError:
            # Fresh database: the schema doesn't exist yet.
            logger.warning(
                "admin_bootstrap_waiting_for_schema",
                hint="run `docker compose run --rm backend alembic upgrade head` "
                "to create the schema; startup will finish on its own",
            )
        except OperationalError as exc:
            # Transient: deadlock with a concurrently running migration, or a
            # connection blip while MySQL settles.
            logger.warning("admin_bootstrap_retrying", error=str(exc.orig))
        await asyncio.sleep(RETRY_SECONDS)


async def _create_admin_if_missing() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.role == "ADMIN").limit(1))
        if result.scalars().first() is not None:
            return  # admin already exists

        admin = User(
            id=str(uuid.uuid4()),
            username=settings.ADMIN_USERNAME.lower(),
            display_name="Admin",
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            role="ADMIN",
        )
        db.add(admin)
        await db.commit()
        logger.info("admin_bootstrapped", username=settings.ADMIN_USERNAME.lower())
