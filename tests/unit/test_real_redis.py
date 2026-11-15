"""
Concurrency tests against a real Redis server (the other unit tests use a fake).

They prove the claims the in-memory fake can only model: SET NX PX locking, the
compare-and-delete release script, the capped-join script and SADD claims behave as
intended when many coroutines on several connections hit one Redis at once.

Set REDIS_TEST_URL to run them (they are skipped otherwise). The database it names is
FLUSHED before every test, so point it at a throwaway server or a spare db index:

    docker run --rm -d -p 6390:6379 redis:7.0-alpine
    cd tests/unit && REDIS_TEST_URL=redis://localhost:6390/15 \\
        PYTHONPATH=../../backend pytest test_real_redis.py -v
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from redis.asyncio import from_url
from redis.exceptions import MaxConnectionsError

from app.redis_client import create_redis
from app.services import state_service as state

REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL", "")

pytestmark = pytest.mark.skipif(
    not REDIS_TEST_URL, reason="REDIS_TEST_URL is not set (needs a real Redis)"
)

ROOM = "ABCDE1"
SESSION = "s-real"
WRITERS = 40
CLIENTS = 4  # separate connections, as separate worker processes would have


@pytest_asyncio.fixture
async def clients():
    """CLIENTS independent Redis clients on a freshly flushed database."""
    conns = [from_url(REDIS_TEST_URL, decode_responses=True) for _ in range(CLIENTS)]
    await conns[0].flushdb()
    yield conns
    await conns[0].flushdb()
    for c in conns:
        await c.aclose()


def _spread(clients, n):
    """n client picks, round-robin over the connections."""
    return [clients[i % len(clients)] for i in range(n)]


# ---------------------------------------------------------------------------
# Room-state lock
# ---------------------------------------------------------------------------


async def test_concurrent_increments_all_land(clients):
    await state.set_room_state(clients[0], ROOM, {"n": 0})

    def inc(rs: dict) -> None:
        rs["n"] += 1

    await asyncio.gather(
        *(state.update_room_state(c, ROOM, inc) for c in _spread(clients, WRITERS))
    )
    assert (await state.get_room_state(clients[0], ROOM))["n"] == WRITERS


async def test_without_lock_the_same_increments_lose_updates(clients):
    # Control: the unlocked read-modify-write the lock replaces loses updates here too.
    await state.set_room_state(clients[0], ROOM, {"n": 0})

    async def unlocked_inc(c) -> None:
        rs = await state.get_room_state(c, ROOM)
        rs["n"] += 1
        await state.set_room_state(c, ROOM, rs)

    await asyncio.gather(*(unlocked_inc(c) for c in _spread(clients, WRITERS)))
    assert (await state.get_room_state(clients[0], ROOM))["n"] < WRITERS


async def test_lock_is_exclusive_across_connections(clients):
    inside = 0
    peak = 0

    async def worker(c) -> None:
        nonlocal inside, peak
        async with state.room_state_lock(c, ROOM) as held:
            assert held
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.002)
            inside -= 1

    await asyncio.gather(*(worker(c) for c in _spread(clients, 20)))
    assert peak == 1


async def test_expired_holder_cannot_release_the_next_holders_lock(clients):
    a, b = clients[0], clients[1]
    async with state.room_state_lock(a, ROOM, ttl_ms=50) as held_a:
        assert held_a
        await asyncio.sleep(0.12)  # a's TTL runs out while it still "holds" the lock
        async with state.room_state_lock(b, ROOM, ttl_ms=2000) as held_b:
            assert held_b
            token_b = await b.get("lock:room:" + ROOM)
            assert token_b
    # b released its own lock; a's late release (on leaving the outer block) is a no-op.
    assert await a.get("lock:room:" + ROOM) is None

    # And directly: a's release while b holds must leave b's token in place.
    async with state.room_state_lock(b, ROOM, ttl_ms=2000):
        token_b = await b.get("lock:room:" + ROOM)
        assert (
            await a.eval(state._RELEASE_LOCK_LUA, 1, "lock:room:" + ROOM, "not-b") == 0
        )
        assert await b.get("lock:room:" + ROOM) == token_b


async def test_require_lock_refuses_to_write_when_the_room_is_busy(clients):
    await state.set_room_state(clients[0], ROOM, {"n": 0})
    async with state.room_state_lock(clients[0], ROOM, ttl_ms=3000):
        state.ROOM_LOCK_WAIT_S, saved = 0.05, state.ROOM_LOCK_WAIT_S
        try:
            with pytest.raises(state.RoomLockBusyError):
                await state.update_room_state(
                    clients[1], ROOM, lambda rs: rs.update(n=99), require_lock=True
                )
        finally:
            state.ROOM_LOCK_WAIT_S = saved
    assert (await state.get_room_state(clients[0], ROOM))["n"] == 0


# ---------------------------------------------------------------------------
# Player limit (max_players)
# ---------------------------------------------------------------------------


async def test_concurrent_joins_never_exceed_the_limit(clients):
    limit, joiners = 25, 100
    results = await asyncio.gather(
        *(
            state.add_player(c, SESSION, f"u{i}", f"P{i}", True, max_players=limit)
            for i, c in enumerate(_spread(clients, joiners))
        )
    )
    assert sum(results) == limit
    assert await state.get_player_count(clients[0], SESSION) == limit
    # A refused player left nothing behind; an admitted one has its record.
    for i, admitted in enumerate(results):
        assert (await state.get_player(clients[0], SESSION, f"u{i}") is not None) == (
            admitted
        )


async def test_full_room_still_lets_a_seated_player_back_in(clients):
    c = clients[0]
    assert await state.add_player(c, SESSION, "u1", "A", True, max_players=1)
    assert not await state.add_player(c, SESSION, "u2", "B", True, max_players=1)
    assert await state.add_player(c, SESSION, "u1", "A", True, max_players=1)
    assert await state.get_player_count(c, SESSION) == 1


async def test_a_freed_seat_can_be_taken(clients):
    c = clients[0]
    assert await state.add_player(c, SESSION, "u1", "A", True, max_players=1)
    await state.remove_player(c, SESSION, "u1")
    assert await state.add_player(c, SESSION, "u2", "B", True, max_players=1)


async def test_no_limit_admits_everyone(clients):
    results = await asyncio.gather(
        *(state.add_player(clients[0], SESSION, f"u{i}", "P", True) for i in range(30))
    )
    assert all(results)
    assert await state.get_player_count(clients[0], SESSION) == 30


# ---------------------------------------------------------------------------
# Exactly-once claims
# ---------------------------------------------------------------------------


async def test_concurrent_answer_claims_have_one_winner(clients):
    wins = await asyncio.gather(
        *(state.claim_answer(c, SESSION, 7, "u1") for c in _spread(clients, 50))
    )
    assert sum(wins) == 1
    assert await state.get_answered_count(clients[0], SESSION, 7) == 1


async def test_concurrent_advance_claims_have_one_winner(clients):
    wins = await asyncio.gather(
        *(state.claim_advance(c, SESSION, "QUESTION", 3) for c in _spread(clients, 50))
    )
    assert sum(wins) == 1


async def test_concurrent_score_updates_all_count(clients):
    await state.add_player(clients[0], SESSION, "u1", "A", True)
    await asyncio.gather(
        *(
            state.update_player_score(c, SESSION, "u1", 10)
            for c in _spread(clients, WRITERS)
        )
    )
    assert (await state.get_player(clients[0], SESSION, "u1"))["score"] == 10 * WRITERS


# ---------------------------------------------------------------------------
# Host presence shared between workers
# ---------------------------------------------------------------------------


async def test_host_presence_is_visible_from_every_connection(clients):
    worker_a, worker_b = clients[0], clients[1]
    assert not await state.host_connected(worker_b, SESSION)
    await state.add_host_socket(worker_a, SESSION, "sid-a")
    assert await state.host_connected(worker_b, SESSION)
    # A second host socket on another worker keeps the host "here" when the first goes.
    await state.add_host_socket(worker_b, SESSION, "sid-b")
    await state.remove_host_socket(worker_a, SESSION, "sid-a")
    assert await state.host_connected(worker_a, SESSION)
    await state.remove_host_socket(worker_b, SESSION, "sid-b")
    assert not await state.host_connected(worker_a, SESSION)


async def test_host_away_mark_is_cleared_once(clients):
    await state.set_host_away(clients[0], SESSION, 30)
    cleared = await asyncio.gather(
        *(state.clear_host_away(c, SESSION) for c in _spread(clients, 8))
    )
    assert sum(cleared) == 1


async def test_ending_a_room_removes_presence_keys(clients):
    c = clients[0]
    await state.set_room_state(c, ROOM, {"status": "IN_PROGRESS"})
    await state.add_host_socket(c, SESSION, "sid-a")
    await state.set_host_away(c, SESSION, 30)
    await state.delete_room_state(c, ROOM, SESSION)
    assert await c.keys("*") == []


# ---------------------------------------------------------------------------
# Connection pool under a burst (found by scripts/load_test.py --think-max 0)
# ---------------------------------------------------------------------------

BURST = 300
POOL = 5


async def test_a_burst_larger_than_the_pool_waits_instead_of_failing(clients):
    redis = create_redis(REDIS_TEST_URL, max_connections=POOL, wait_seconds=10)
    try:
        wins = await asyncio.gather(
            *(state.claim_answer(redis, SESSION, 7, f"u{i}") for i in range(BURST))
        )
    finally:
        await redis.aclose()
    assert sum(wins) == BURST
    assert await state.get_answered_count(clients[0], SESSION, 7) == BURST


async def test_the_default_pool_drops_the_same_burst(clients):
    # Control: this is what the app's client did before create_redis.
    redis = from_url(REDIS_TEST_URL, decode_responses=True, max_connections=POOL)
    try:
        results = await asyncio.gather(
            *(state.claim_answer(redis, SESSION, 7, f"u{i}") for i in range(BURST)),
            return_exceptions=True,
        )
    finally:
        await redis.aclose()
    assert any(isinstance(r, MaxConnectionsError) for r in results)
    assert await state.get_answered_count(clients[0], SESSION, 7) < BURST
