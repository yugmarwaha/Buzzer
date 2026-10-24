"""
Redis-backed game state management with MySQL fallback.

Key namespaces:
  room:{code}                    → room metadata JSON          TTL: 90 min (reset on any activity)
  session:{id}:players           → Set of active user_ids
  session:{id}:player:{user_id}  → player metadata JSON
  session:{id}:question          → current question state JSON
  session:{id}:answered:{qid}    → Set of user_ids who answered question qid
  session:{id}:dist:{qid}        → Hash of answer_key → count (e.g. "0"→5, "true"→8)
  advance:{id}:{phase}:{index}   → host_advance claim (SET NX), TTL: 5 s
  lock:room:{code}               → room-state RMW lock (SET NX PX, owner token), TTL: 3 s
  lock:player:{id}:{user_id}     → player-JSON RMW lock (same scheme), TTL: 1 s

The session:{id}:* keys carry SESSION_KEY_TTL, reset on every write. It is a backstop so
keys orphaned by an ended room (see end_room) can't live forever; it is longer than any
real game so a live room never loses them while its room key is kept alive by activity.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.session import GameSession, SessionScore
from ..models.game import Question

logger = structlog.get_logger()

ROOM_TTL = 90 * 60  # 90 minutes in seconds
SESSION_KEY_TTL = 24 * 60 * 60  # 24 hours: backstop for session:{id}:* keys


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _room_key(code: str) -> str:
    return f"room:{code}"


def _players_key(session_id: str) -> str:
    return f"session:{session_id}:players"


def _player_key(session_id: str, user_id: str) -> str:
    return f"session:{session_id}:player:{user_id}"


def _question_key(session_id: str) -> str:
    return f"session:{session_id}:question"


def _answered_key(session_id: str, question_id: int) -> str:
    return f"session:{session_id}:answered:{question_id}"


def _dist_key(session_id: str, question_id: int) -> str:
    return f"session:{session_id}:dist:{question_id}"


# ---------------------------------------------------------------------------
# Room state
# ---------------------------------------------------------------------------


async def set_room_state(redis: Redis, code: str, data: dict) -> None:
    await redis.set(_room_key(code), json.dumps(data), ex=ROOM_TTL)


async def get_room_state(redis: Redis, code: str) -> dict | None:
    raw = await redis.get(_room_key(code))
    return json.loads(raw) if raw else None


# Read-modify-write of the room blob: several handlers (advance, lock, timer, disconnect,
# host join, start_game) each read the JSON, change a few fields and write it back, so
# without a lock one can overwrite a newer write (e.g. QUESTION+locked over RESULTS).
# Every such update goes through room_state_lock / update_room_state.
ROOM_LOCK_TTL_MS = 3000  # a crashed holder frees the room after this
ROOM_LOCK_WAIT_S = 4.0  # > TTL, so an expired lock is always picked up before giving up
_ROOM_LOCK_RETRY_S = (0.005, 0.05)  # first and maximum backoff between attempts

# Delete the key only if it still holds our token: a holder whose TTL ran out must not
# release a lock some other handler has taken since.
_RELEASE_LOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


def _room_lock_key(code: str) -> str:
    # Not under room:* so the room-count and health scans never see it.
    return f"lock:room:{code}"


def _player_lock_key(session_id: str, user_id: str) -> str:
    return f"lock:player:{session_id}:{user_id}"


@asynccontextmanager
async def room_state_lock(
    redis: Redis,
    code: str,
    ttl_ms: int = ROOM_LOCK_TTL_MS,
    wait_s: float | None = None,
) -> AsyncIterator[bool]:
    """Short-lived per-room lock (SET NX PX + compare-and-delete release).

    Yields True when held. Inside it do only fast Redis reads/writes of this room's state:
    no DB queries, emits or sleeps (they would hold every other handler of the room up),
    and never take it again (it is not re-entrant: a nested acquire waits for itself and
    then times out). If it can't be acquired within wait_s it logs and yields False, and
    the caller proceeds unlocked (the pre-lock behavior) rather than stalling the game.
    wait_s defaults to ROOM_LOCK_WAIT_S, read at call time. Callers that must not write
    unlocked check the yielded value (or use update_room_state(require_lock=True))."""
    async with _short_lock(
        redis,
        _room_lock_key(code),
        ttl_ms,
        wait_s,
        "room_state_lock_timeout",
        room=code,
    ) as held:
        yield held


@asynccontextmanager
async def _short_lock(
    redis: Redis,
    key: str,
    ttl_ms: int,
    wait_s: float | None,
    timeout_event: str,
    **log_fields,
) -> AsyncIterator[bool]:
    """The SET NX PX lock behind room_state_lock and the player-JSON lock."""
    if wait_s is None:
        wait_s = ROOM_LOCK_WAIT_S
    token = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    delay, max_delay = _ROOM_LOCK_RETRY_S
    acquired = False
    while True:
        if await redis.set(key, token, nx=True, px=ttl_ms):
            acquired = True
            break
        if loop.time() >= deadline:
            logger.warning(timeout_event, wait_s=wait_s, **log_fields)
            break
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)
    try:
        yield acquired
    finally:
        if acquired:
            await redis.eval(_RELEASE_LOCK_LUA, 1, key, token)


class RoomLockBusyError(Exception):
    """update_room_state(require_lock=True) could not get the room lock in time."""


async def update_room_state(
    redis: Redis,
    code: str,
    mutate: Callable[[dict], bool | None],
    require_lock: bool = False,
) -> dict | None:
    """Locked read-modify-write of the room blob. `mutate` changes the dict in place; it
    must be synchronous and do no I/O (it runs while the lock is held). It returns False
    to skip the write (e.g. the room has moved on and the change no longer applies).
    Returns the written state, or None if the room key is gone (never recreated) or
    mutate declined. With require_lock=True it does not fall back to an unlocked write
    when the lock wait times out: it writes nothing and raises RoomLockBusyError."""
    async with room_state_lock(redis, code) as held:
        if require_lock and not held:
            raise RoomLockBusyError(code)
        room_state = await get_room_state(redis, code)
        if room_state is None or mutate(room_state) is False:
            return None
        await set_room_state(redis, code, room_state)
        return room_state


async def refresh_room_ttl(redis: Redis, code: str) -> None:
    """Reset the 90-minute inactivity timer. Called on every WebSocket message."""
    await redis.expire(_room_key(code), ROOM_TTL)


async def delete_room_state(redis: Redis, code: str, session_id: str) -> None:
    """Clean up all keys for a completed/abandoned room."""
    keys = [_room_key(code), _players_key(session_id), _question_key(session_id)]
    answered_keys = await redis.keys(f"session:{session_id}:answered:*")
    dist_keys = await redis.keys(f"session:{session_id}:dist:*")
    player_keys = await redis.keys(f"session:{session_id}:player:*")
    all_keys = keys + answered_keys + dist_keys + player_keys
    if all_keys:
        await redis.delete(*all_keys)


ADVANCE_CLAIM_TTL = 5  # seconds


async def claim_advance(
    redis: Redis, session_id: str, from_phase: str, question_index: int
) -> bool:
    """Atomically claim the advance away from one screen (phase + question index).
    True only for the first caller within ADVANCE_CLAIM_TTL; a concurrent duplicate
    advance from the same screen gets False. SET NX is a single Redis command, so two
    handlers running at once can't both win."""
    key = f"advance:{session_id}:{from_phase}:{question_index}"
    return bool(await redis.set(key, "1", nx=True, ex=ADVANCE_CLAIM_TTL))


# ---------------------------------------------------------------------------
# Player management
# ---------------------------------------------------------------------------


async def add_player(
    redis: Redis,
    session_id: str,
    user_id: str,
    display_name: str,
    is_guest: bool,
    score: int = 0,
) -> None:
    player_data = {
        "user_id": user_id,
        "display_name": display_name,
        "is_guest": is_guest,
        "score": score,
        "is_connected": True,
    }
    await redis.sadd(_players_key(session_id), user_id)
    await redis.expire(_players_key(session_id), SESSION_KEY_TTL)
    await redis.set(
        _player_key(session_id, user_id), json.dumps(player_data), ex=SESSION_KEY_TTL
    )


# The player JSON holds both the score (answers) and is_connected (join/disconnect).
# Both writers read-modify-write it under this short per-player lock, so one can't
# write back an old copy of the other's field (e.g. a disconnect restoring an old score).
PLAYER_LOCK_TTL_MS = 1000


async def _update_player(
    redis: Redis, session_id: str, user_id: str, mutate: Callable[[dict], None]
) -> dict | None:
    """Locked read → sync mutate → write of one player's JSON. A missing key is not
    recreated (returns None). On a lock timeout it proceeds unlocked, like
    room_state_lock."""
    key = _player_key(session_id, user_id)
    async with _short_lock(
        redis,
        _player_lock_key(session_id, user_id),
        PLAYER_LOCK_TTL_MS,
        None,
        "player_state_lock_timeout",
        session_id=session_id,
        user_id=user_id,
    ):
        raw = await redis.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        mutate(data)
        await redis.set(key, json.dumps(data), ex=SESSION_KEY_TTL)
        return data


async def update_player_score(
    redis: Redis, session_id: str, user_id: str, delta: int
) -> int:
    """Add delta to a player's score and return the new total."""
    data = await _update_player(
        redis,
        session_id,
        user_id,
        lambda d: d.update(score=d.get("score", 0) + delta),
    )
    return data["score"] if data else 0


async def set_player_connected(
    redis: Redis, session_id: str, user_id: str, connected: bool
) -> None:
    await _update_player(
        redis, session_id, user_id, lambda d: d.update(is_connected=connected)
    )


async def remove_player(redis: Redis, session_id: str, user_id: str) -> None:
    await redis.srem(_players_key(session_id), user_id)
    await redis.delete(_player_key(session_id, user_id))


async def get_player(redis: Redis, session_id: str, user_id: str) -> dict | None:
    raw = await redis.get(_player_key(session_id, user_id))
    return json.loads(raw) if raw else None


async def get_all_players(redis: Redis, session_id: str) -> list[dict]:
    user_ids = await redis.smembers(_players_key(session_id))
    players = []
    for uid in user_ids:
        raw = await redis.get(_player_key(session_id, uid))
        if raw:
            players.append(json.loads(raw))
    return sorted(players, key=lambda p: p.get("score", 0), reverse=True)


async def get_player_count(redis: Redis, session_id: str) -> int:
    return await redis.scard(_players_key(session_id))


# ---------------------------------------------------------------------------
# Current question state
# ---------------------------------------------------------------------------


async def set_current_question(
    redis: Redis,
    session_id: str,
    question_id: int,
    started_at: datetime,
    time_limit_seconds: int,
    question_number: int,
    total_questions: int,
) -> None:
    data = {
        "question_id": question_id,
        "started_at": started_at.isoformat(),
        "time_limit_seconds": time_limit_seconds,
        "question_number": question_number,
        "total_questions": total_questions,
    }
    await redis.set(_question_key(session_id), json.dumps(data), ex=SESSION_KEY_TTL)


async def get_current_question(redis: Redis, session_id: str) -> dict | None:
    raw = await redis.get(_question_key(session_id))
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Answer tracking
# ---------------------------------------------------------------------------


async def mark_answered(
    redis: Redis, session_id: str, question_id: int, user_id: str
) -> None:
    await redis.sadd(_answered_key(session_id, question_id), user_id)
    await redis.expire(_answered_key(session_id, question_id), SESSION_KEY_TTL)


async def claim_answer(
    redis: Redis, session_id: str, question_id: int, user_id: str
) -> bool:
    """Atomically claim this player's one answer to a question (SADD on the answered
    set). True only for the first caller: a concurrent second submit gets False. The
    caller must release_answer if the answer is then not recorded."""
    key = _answered_key(session_id, question_id)
    added = await redis.sadd(key, user_id)
    await redis.expire(key, SESSION_KEY_TTL)
    return added == 1


async def release_answer(
    redis: Redis, session_id: str, question_id: int, user_id: str
) -> None:
    """Undo claim_answer when the answer was not recorded (rejected or failed)."""
    await redis.srem(_answered_key(session_id, question_id), user_id)


async def has_answered(
    redis: Redis, session_id: str, question_id: int, user_id: str
) -> bool:
    return await redis.sismember(_answered_key(session_id, question_id), user_id)


async def get_answered_count(redis: Redis, session_id: str, question_id: int) -> int:
    return await redis.scard(_answered_key(session_id, question_id))


async def increment_answer_dist(
    redis: Redis, session_id: str, question_id: int, answer_key: str
) -> None:
    """Increment the per-option counter for a question's answer distribution."""
    await redis.hincrby(_dist_key(session_id, question_id), answer_key, 1)
    await redis.expire(_dist_key(session_id, question_id), SESSION_KEY_TTL)


async def decrement_answer_dist(
    redis: Redis, session_id: str, question_id: int, answer_key: str
) -> None:
    """Undo increment_answer_dist (an answer whose commit failed). A counter that drops
    to 0 is removed, so the results show no empty entry."""
    key = _dist_key(session_id, question_id)
    if await redis.hincrby(key, answer_key, -1) <= 0:
        await redis.hdel(key, answer_key)


async def get_answer_dist(
    redis: Redis, session_id: str, question_id: int
) -> dict[str, int]:
    """Return the full answer distribution as {answer_key: count}."""
    raw = await redis.hgetall(_dist_key(session_id, question_id))
    return {k: int(v) for k, v in raw.items()}


async def all_players_answered(redis: Redis, session_id: str, question_id: int) -> bool:
    total = await get_player_count(redis, session_id)
    if total == 0:
        return False
    answered = await get_answered_count(redis, session_id, question_id)
    return answered >= total


# ---------------------------------------------------------------------------
# MySQL fallback — restore state after Redis loss
# ---------------------------------------------------------------------------


async def restore_from_mysql(
    db: AsyncSession, redis: Redis, session: GameSession
) -> dict:
    """
    Rebuild Redis state for a session from MySQL after a Redis restart/flush.
    Returns a state dict describing what was restored.
    """
    session_id = session.id

    # Restore cumulative scores per player
    score_rows = await db.execute(
        select(
            SessionScore.user_id,
            func.sum(SessionScore.points_awarded).label("total"),
        )
        .where(SessionScore.session_id == session_id)
        .group_by(SessionScore.user_id)
    )
    player_scores = {row.user_id: row.total for row in score_rows}

    # Determine the last answered question by order_index
    last_answered_result = await db.execute(
        select(Question.order_index)
        .join(SessionScore, SessionScore.question_id == Question.id)
        .where(SessionScore.session_id == session_id)
        .order_by(Question.order_index.desc())
        .limit(1)
    )
    last_order = last_answered_result.scalar_one_or_none()

    # Find the next question to resume on
    if last_order is not None:
        next_q_result = await db.execute(
            select(Question)
            .where(
                Question.game_id == session.game_id,
                Question.order_index > last_order,
            )
            .order_by(Question.order_index)
            .limit(1)
        )
        next_question = next_q_result.scalar_one_or_none()
    else:
        next_q_result = await db.execute(
            select(Question)
            .where(Question.game_id == session.game_id)
            .order_by(Question.order_index)
            .limit(1)
        )
        next_question = next_q_result.scalar_one_or_none()

    logger.warning(
        "redis_state_lost_restored_from_mysql",
        session_id=session_id,
        players_restored=len(player_scores),
        resumed_question_id=next_question.id if next_question else None,
    )

    return {
        "player_scores": player_scores,
        "next_question": next_question,
        "restored": True,
    }
