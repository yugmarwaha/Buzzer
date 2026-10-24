"""
Unit tests for answer and advance races in the gateway: a double submit from one player,
an answer that lands after the question closed, two concurrent advances, an advance that
can't get the room lock, and the host-reconnect auto-unlock timer. No DB, Redis or
server: the fake async Redis from test_room_state_lock and small fake DB sessions.

Run with:
    cd tests/unit && PYTHONPATH=../../backend pytest test_answer_races.py -v
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import game_service
from app.services import state_service as state
from app.websocket import events as E
from app.websocket import gateway as gw

from .test_room_state_lock import ROOM, FakeRedis

SESSION = "s1"
QID = 11


class HashRedis(FakeRedis):
    """FakeRedis plus the hash calls the answer distribution uses."""

    def __init__(self) -> None:
        super().__init__()
        self.hashes: dict[str, dict[str, int]] = {}

    async def hincrby(self, key, field, amount):
        await asyncio.sleep(0)
        h = self.hashes.setdefault(key, {})
        h[field] = h.get(field, 0) + amount
        return h[field]

    async def hgetall(self, key):
        return {k: str(v) for k, v in self.hashes.get(key, {}).items()}


QUESTION = SimpleNamespace(
    id=QID,
    type="multiple_choice",
    grading_type="ACCURACY",
    prompt="2+2?",
    config={"options": ["3", "4"]},
    time_limit_seconds=30,
    points_value=100,
    order_index=0,
    answer_data={"answer_points": [0, 100]},
)


class AnswerDb:
    """A DB session for the submit path. Rows are only kept on commit, so a rollback
    drops this session's pending insert. on_flush runs during the flush (to simulate a
    host action while the insert is in flight)."""

    def __init__(self, committed: list, on_flush=None) -> None:
        self.committed = committed
        self.pending: list = []
        self.on_flush = on_flush

    async def get(self, model, key):
        await asyncio.sleep(0)
        if model is gw.GameSession:
            return SimpleNamespace(id=SESSION, status="IN_PROGRESS")
        return QUESTION if key == QID else None

    def add(self, row) -> None:
        self.pending.append(row)

    async def flush(self) -> None:
        await asyncio.sleep(0)
        if self.on_flush:
            await self.on_flush()

    async def execute(self, stmt):
        await asyncio.sleep(0)
        return SimpleNamespace(scalar_one_or_none=lambda: "IN_PROGRESS")

    async def rollback(self) -> None:
        self.pending.clear()

    async def commit(self) -> None:
        self.committed.extend(self.pending)
        self.pending.clear()


async def _open_question(redis, locked: bool = False) -> None:
    await state.set_room_state(
        redis,
        ROOM,
        {
            "status": "IN_PROGRESS",
            "question_phase": "QUESTION",
            "question_index": 0,
            "question_ids": [QID, 12],
            "question_locked": locked,
        },
    )
    await state.set_current_question(
        redis, SESSION, QID, gw.datetime.now(gw.timezone.utc), 30, 1, 2
    )


@pytest.fixture
def wired(monkeypatch):
    """Patch the gateway's Redis, emit and DB factory. Returns (redis, emits, rows,
    set_on_flush)."""
    redis = HashRedis()
    emits: list[tuple] = []
    rows: list = []
    hooks: dict = {"on_flush": None}

    async def fake_get_redis():
        return redis

    async def fake_emit(event, payload, to=None):
        emits.append((event, payload, to))

    @asynccontextmanager
    async def fake_db():
        db = AnswerDb(rows, hooks["on_flush"])
        yield db
        await db.commit()

    monkeypatch.setattr(gw, "get_redis", fake_get_redis)
    monkeypatch.setattr(gw.sio, "emit", fake_emit)
    monkeypatch.setattr(gw, "_db", fake_db)
    monkeypatch.setitem(
        gw._sid_ctx,
        "p-sid",
        {"user_id": "u1", "role": "PLAYER", "room_code": ROOM, "session_id": SESSION},
    )
    monkeypatch.setitem(
        gw._sid_ctx,
        "host-sid",
        {"user_id": "h", "role": "HOST", "room_code": ROOM, "session_id": SESSION},
    )
    yield redis, emits, rows, hooks
    gw._cancel_timer(SESSION)


ANSWER = {"question_id": QID, "answer_data": {"selectedIndex": 1}}


# ---------------------------------------------------------------------------
# Double submit: two concurrent submits from one player score once
# ---------------------------------------------------------------------------


async def test_concurrent_double_submit_scores_once(wired):
    redis, emits, rows, _ = wired
    await _open_question(redis)
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)

    await asyncio.gather(
        gw.on_submit_answer("p-sid", dict(ANSWER)),
        gw.on_submit_answer("p-sid", dict(ANSWER)),
    )

    assert len(rows) == 1  # one SessionScore row
    received = [p for e, p, _ in emits if e == E.ANSWER_RECEIVED]
    assert len(received) == 2
    assert sum(1 for p in received if p.get("alreadyAnswered")) == 1
    assert (await state.get_player(redis, SESSION, "u1"))["score"] == 100
    assert await state.get_answer_dist(redis, SESSION, QID) == {"1": 1}


async def test_rejected_answer_releases_the_claim(wired):
    # A submit refused after the claim (here: the question closes mid-write) must not
    # leave the player marked as answered.
    redis, emits, rows, hooks = wired
    await _open_question(redis)
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)

    async def lock_now():
        await state.update_room_state(
            redis, ROOM, lambda rs: rs.update(question_locked=True)
        )

    hooks["on_flush"] = lock_now
    await gw.on_submit_answer("p-sid", dict(ANSWER))
    assert not await state.has_answered(redis, SESSION, QID, "u1")


# ---------------------------------------------------------------------------
# Late answer: the question closes while the answer is being written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("close", ["lock", "results"])
async def test_answer_closed_during_write_is_rolled_back(wired, close):
    redis, emits, rows, hooks = wired
    await _open_question(redis)
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)

    async def close_question():
        if close == "lock":
            change = {"question_locked": True}
        else:
            change = {"question_phase": "RESULTS", "results_question_id": QID}
        await state.update_room_state(redis, ROOM, lambda rs: rs.update(change))

    hooks["on_flush"] = close_question
    await gw.on_submit_answer("p-sid", dict(ANSWER))

    assert rows == []  # rolled back, no score row
    assert [e for e, _, _ in emits] == [E.ERROR]
    assert "closed" in emits[0][1]["message"].lower()
    assert (await state.get_player(redis, SESSION, "u1"))["score"] == 0
    assert await state.get_answer_dist(redis, SESSION, QID) == {}
    assert not await state.has_answered(redis, SESSION, QID, "u1")


async def test_submit_to_locked_question_is_refused_before_any_write(wired):
    redis, emits, rows, _ = wired
    await _open_question(redis, locked=True)
    await gw.on_submit_answer("p-sid", dict(ANSWER))
    assert rows == [] and emits[0][0] == E.ERROR
    assert "locked" in emits[0][1]["message"].lower()


# ---------------------------------------------------------------------------
# Advance QUESTION -> RESULTS: one winner, results read after RESULTS is set
# ---------------------------------------------------------------------------


class AdvanceDb:
    async def get(self, model, key):
        await asyncio.sleep(0)
        if model is gw.GameSession:
            return SimpleNamespace(id=SESSION, status="IN_PROGRESS")
        return QUESTION if key == QID else None

    async def commit(self) -> None:
        pass


@pytest.fixture
def advance_stubs(wired, monkeypatch):
    redis, emits, rows, hooks = wired
    phases_at_read: list = []

    @asynccontextmanager
    async def fake_db():
        yield AdvanceDb()

    async def in_progress(db, sid):
        return "IN_PROGRESS"

    async def wait_writes(db, sid):
        phases_at_read.append((await state.get_room_state(redis, ROOM))["question_phase"])

    async def empty_leaderboard(db, sid):
        return []

    async def no_points(db, sid, qid):
        return {}

    async def host_sync(db, r, session, code):
        return {"sync": True}

    monkeypatch.setattr(gw, "_db", fake_db)
    monkeypatch.setattr(game_service, "fresh_session_status", in_progress)
    monkeypatch.setattr(game_service, "wait_for_answer_writes", wait_writes)
    monkeypatch.setattr(game_service, "get_leaderboard", empty_leaderboard)
    monkeypatch.setattr(gw, "_question_points", no_points)
    monkeypatch.setattr(gw, "_host_sync_payload", host_sync)
    return redis, emits, phases_at_read


async def test_concurrent_advances_show_results_once(advance_stubs):
    redis, emits, phases_at_read = advance_stubs
    await _open_question(redis)
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)

    # {} advances skip the claim, so only the compare-and-set under the lock decides.
    await asyncio.gather(
        gw._on_host_advance_impl("host-sid", {}),
        gw._on_host_advance_impl("host-sid", {}),
    )

    host_results = [
        e for e, _, to in emits if e == E.QUESTION_RESULTS and to == gw._host_room(ROOM)
    ]
    assert len(host_results) == 1
    assert [e for e, _, _ in emits].count(E.SYNC_STATE) == 1  # the loser resyncs
    rs = await state.get_room_state(redis, ROOM)
    assert rs["question_phase"] == "RESULTS" and rs["question_index"] == 0
    # The answers were read only after the room left QUESTION.
    assert phases_at_read == ["RESULTS"]


async def test_advance_refuses_without_the_room_lock(advance_stubs, monkeypatch):
    redis, emits, phases_at_read = advance_stubs
    await _open_question(redis)
    monkeypatch.setattr(state, "ROOM_LOCK_WAIT_S", 0.05)
    # Another handler holds the room lock for longer than the wait.
    await redis.set(state._room_lock_key(ROOM), "other", nx=True, px=10_000)

    await gw._on_host_advance_impl("host-sid", {})

    assert [e for e, _, _ in emits] == [E.ERROR]
    assert "busy" in emits[0][1]["message"].lower()
    rs = await state.get_room_state(redis, ROOM)
    assert rs["question_phase"] == "QUESTION"  # nothing written unlocked
    assert phases_at_read == []


# ---------------------------------------------------------------------------
# _auto_unlock_for_host: timer started under the lock, older timer cancelled
# ---------------------------------------------------------------------------


async def test_auto_unlock_replaces_timer_under_the_lock(wired, monkeypatch):
    redis, emits, _, _ = wired
    await _open_question(redis, locked=True)
    await state.update_room_state(
        redis,
        ROOM,
        lambda rs: rs.update(question_locked_by_disconnect=True, timer_remaining_seconds=12.0),
    )
    old = asyncio.ensure_future(asyncio.sleep(100))
    gw._timer_tasks[SESSION] = old
    lock_held_at_start: list[bool] = []

    def fake_timer(session_id, room_code, question_id, seconds):
        lock_held_at_start.append(state._room_lock_key(ROOM) in redis.data)
        return asyncio.sleep(100)

    monkeypatch.setattr(gw, "_question_timer_task", fake_timer)

    await gw._auto_unlock_for_host(redis, SESSION, ROOM, host_sid="new-host")
    await asyncio.sleep(0)

    assert old.cancelled()
    new = gw._timer_tasks[SESSION]
    assert new is not old and not new.done()
    assert lock_held_at_start == [True]
    rs = await state.get_room_state(redis, ROOM)
    assert rs["question_locked"] is False and rs["host_sid"] == "new-host"
    assert "question_locked_by_disconnect" not in rs
    assert [e for e, _, _ in emits] == [E.QUESTION_UNLOCKED]
    assert emits[0][1]["remainingSeconds"] == 12.0
