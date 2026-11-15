from redis.asyncio import BlockingConnectionPool, Redis

from .config import settings

_redis: Redis | None = None


def create_redis(
    url: str,
    max_connections: int,
    wait_seconds: float,
) -> Redis:
    """A Redis client whose callers wait for a free connection.

    redis-py's default pool raises MaxConnectionsError as soon as all of its connections
    are in use. A burst of socket events (a whole room answering, or leaving, at the same
    moment) runs more handlers than that at once, and each failed handler was a dropped
    answer with no reply to the player. A blocking pool makes the extra handlers queue for
    a connection (up to wait_seconds) instead.
    """
    pool = BlockingConnectionPool.from_url(
        url,
        decode_responses=True,
        max_connections=max_connections,
        timeout=wait_seconds,
    )
    return Redis.from_pool(pool)


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = create_redis(
            settings.REDIS_URL,
            settings.REDIS_MAX_CONNECTIONS,
            settings.REDIS_POOL_WAIT_SECONDS,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
