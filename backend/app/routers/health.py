from datetime import datetime, timezone

import structlog
from fastapi import APIRouter
from sqlalchemy import text

from ..database import DbSession
from ..redis_client import get_redis

router = APIRouter(tags=["health"])
logger = structlog.get_logger()


@router.get("/health")
async def health_check(db: DbSession):
    mysql_status = "disconnected"
    redis_status = "disconnected"
    active_rooms = 0
    active_players = 0

    try:
        await db.execute(text("SELECT 1"))
        mysql_status = "connected"
    except Exception as e:
        logger.error("health_mysql_fail", error=str(e))

    try:
        redis = await get_redis()
        await redis.ping()
        redis_status = "connected"

        # Count active rooms and players from Redis
        room_keys = await redis.keys("room:*")
        active_rooms = len(room_keys)

        player_counts = []
        for key in room_keys:
            count = await redis.scard(f"{key}:players")
            player_counts.append(count)
        active_players = sum(player_counts)
    except Exception as e:
        logger.error("health_redis_fail", error=str(e))

    overall = (
        "healthy"
        if mysql_status == "connected" and redis_status == "connected"
        else "degraded"
    )

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {
            "mysql": mysql_status,
            "redis": redis_status,
        },
        "activeRooms": active_rooms,
        "activePlayers": active_players,
    }
