"""
Unit tests for the room-state lock (lost updates), the game-over ordering on the final
advance, and the player's own answer in SYNC_STATE. No DB, Redis or server: a small fake
async Redis and stubbed services.

Run with:
    cd tests/unit && PYTHONPATH=../../backend pytest test_room_state_lock.py -v
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import game_service
from app.services import state_service as state
from app.websocket import events as E
from app.websocket import gateway as gw


class FakeRedis:
    """The Redis calls these paths use, with PX/EX expiry and the compare-and-delete
    release script. Every call yields first, so concurrent coroutines interleave between
    a read and the write that follows it (the lost-update window)."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[object, float | None]] = {}
        self.sets: dict[str, set[str]] = {}

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _live(self, key: str):
        item = self.data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and self._now() >= expires:
            del self.data[key]
            return None
        return value

    async def get(self, key):
        await asyncio.sleep(0)
        return self._live(key)

    async def set(self, key, value, nx=False, ex=None, px=None):
        await asyncio.sleep(0)
        if nx and self._live(key) is not None:
            return None
        ttl = px / 1000 if px is not None else ex
        self.data[key] = (value, self._now() + ttl if ttl is not None else None)
        return True

    async def eval(self, script, numkeys, key, token):
        await asyncio.sleep(0)
        assert "GET" in script and "DEL" in script  # the compare-and-delete release
        if self._live(key) == token:
            del self.data[key]
            return 1
        return 0

    async def expire(self, key, seconds):
        return True

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def sadd(self, key, *members):
        # Like Redis: returns how many members were new.
        s = self.sets.setdefault(key, set())
        added = len(set(members) - s)
        s.update(members)
        return added

    async def srem(self, key, *members):
        s = self.sets.get(key, set())
        removed = len(s & set(members))
        s.difference_update(members)
        return removed

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)
            self.sets.pop(k, None)

    async def sismember(self, key, member):
        return member in self.sets.get(key, set())

    async def hgetall(self, key):
        return {}


ROOM = "ABCDE1"


# ---------------------------------------------------------------------------
# Gap 3: mutual exclusion, TTL, owner-only release
# ---------------------------------------------------------------------------


async def test_concurrent_increments_all_land():
    redis = FakeRedis()
    await state.set_room_state(redis, ROOM, {"n": 0})

    def inc(rs: dict) -> None:
        rs["n"] += 1

    await asyncio.gather(
        *(state.update_room_state(redis, ROOM, inc) for _ in range(40))
    )
    assert (await state.get_room_state(redis, ROOM))["n"] == 40


async def test_without_lock_the_same_increments_lose_updates():
    # Control: the unlocked read-modify-write this replaces does lose updates here.
    redis = FakeRedis()
    await state.set_room_state(redis, ROOM, {"n": 0})

    async def unlocked_inc() -> None:
        rs = await state.get_room_state(redis, ROOM)
        rs["n"] += 1
        await state.set_room_state(redis, ROOM, rs)

    await asyncio.gather(*(unlocked_inc() for _ in range(40)))
    assert (await state.get_room_state(redis, ROOM))["n"] < 40


async def test_two_writers_of_different_fields_both_survive():
    # e.g. a lock toggle and an advance: neither may write back the other's old copy.
    redis = FakeRedis()
    await state.set_room_state(redis, ROOM, {"question_phase": "QUESTION"})
    await asyncio.gather(
        state.update_room_state(
            redis, ROOM, lambda rs: rs.update(question_locked=True)
        ),
        state.update_room_state(redis, ROOM, lambda rs: rs.update(host_sid="sid-2")),
    )
    rs = await state.get_room_state(redis, ROOM)
    assert rs["question_locked"] is True and rs["host_sid"] == "sid-2"


async def test_lock_is_exclusive_while_held():
    redis = FakeRedis()
    inside = 0
    peak = 0

    async def worker() -> None:
        nonlocal inside, peak
        async with state.room_state_lock(redis, ROOM) as held:
            assert held
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.001)
            inside -= 1

    await asyncio.gather(*(worker() for _ in range(10)))
    assert peak == 1


async def test_declined_mutation_and_missing_room_write_nothing():
    redis = FakeRedis()
    assert await state.update_room_state(redis, ROOM, lambda rs: None) is None
    assert await state.get_room_state(redis, ROOM) is None  # never recreated
    await state.set_room_state(redis, ROOM, {"question_phase": "RESULTS"})

    def lock_if_open(rs: dict) -> bool:
        if rs.get("question_phase") != "QUESTION":
            return False
        rs["question_locked"] = True
        return True

    assert await state.update_room_state(redis, ROOM, lock_if_open) is None
    assert await state.get_room_state(redis, ROOM) == {"question_phase": "RESULTS"}


async def test_crashed_holder_is_freed_by_ttl():
    redis = FakeRedis()
    # A handler that took the lock and died: it never releases.
    await redis.set(state._room_lock_key(ROOM), "dead", nx=True, px=50)
    loop = asyncio.get_running_loop()
    start = loop.time()
    async with state.room_state_lock(redis, ROOM, wait_s=1.0) as held:
        assert held
    assert loop.time() - start >= 0.04  # had to wait for the TTL


async def test_bounded_wait_gives_up_and_proceeds_unlocked():
    redis = FakeRedis()
    await redis.set(state._room_lock_key(ROOM), "other", nx=True, px=10_000)
    async with state.room_state_lock(redis, ROOM, wait_s=0.05) as held:
        assert held is False
    # Giving up must not release someone else's lock.
    assert await redis.get(state._room_lock_key(ROOM)) == "other"


async def test_release_only_by_owner_token():
    redis = FakeRedis()
    key = state._room_lock_key(ROOM)
    async with state.room_state_lock(redis, ROOM, ttl_ms=30) as held:
        assert held
        await asyncio.sleep(0.05)  # our TTL runs out while we still "hold" it
        # Another handler takes the now-free lock.
        assert await redis.set(key, "theirs", nx=True, px=10_000)
    # Our release (compare-and-delete) must leave their lock alone.
    assert await redis.get(key) == "theirs"


async def test_lock_is_released_after_an_exception():
    redis = FakeRedis()
    with pytest.raises(RuntimeError):
        async with state.room_state_lock(redis, ROOM):
            raise RuntimeError("boom")
    assert await redis.get(state._room_lock_key(ROOM)) is None


# ---------------------------------------------------------------------------
# Gap 3 in the gateway: a lock click can't overwrite RESULTS
# ---------------------------------------------------------------------------


async def test_lock_click_after_results_writes_nothing(monkeypatch):
    redis = FakeRedis()
    await state.set_room_state(
        redis,
        ROOM,
        {"question_phase": "RESULTS", "question_index": 0, "results_question_id": 11},
    )
    await state.set_current_question(
        redis, "s1", 11, gw.datetime.now(gw.timezone.utc), 30, 1, 3
    )
    emits: list = []

    async def fake_emit(event, payload, to=None):
        emits.append(event)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(gw, "get_redis", fake_get_redis)
    monkeypatch.setattr(gw.sio, "emit", fake_emit)
    monkeypatch.setitem(
        gw._sid_ctx,
        "host-sid",
        {"user_id": "h", "role": "HOST", "room_code": ROOM, "session_id": "s1"},
    )
    await gw._on_lock_question_impl("host-sid")
    rs = await state.get_room_state(redis, ROOM)
    assert rs["question_phase"] == "RESULTS" and "question_locked" not in rs
    assert emits == []


# ---------------------------------------------------------------------------
# Gap 1: the final advance persists COMPLETED before it emits game_over
# ---------------------------------------------------------------------------


def _patch_db(monkeypatch, db) -> None:
    @asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(gw, "_db", fake_db)


async def test_final_advance_persists_completed_before_game_over(monkeypatch):
    redis = FakeRedis()
    session_id = "s1"
    await state.set_room_state(
        redis,
        ROOM,
        {
            "status": "IN_PROGRESS",
            "question_phase": "RESULTS",
            "question_index": 1,
            "question_ids": [11, 12],
            "results_question_id": 12,
        },
    )
    await state.add_player(redis, session_id, "u1", "Tester", is_guest=True)
    mysql = {"status": "IN_PROGRESS"}
    log: list[tuple] = []

    async def fake_get_redis():
        return redis

    async def complete_game(db, r, session):
        mysql["status"] = "COMPLETED"
        log.append(("mysql_completed",))
        return True

    async def fake_emit(event, payload, to=None):
        rs = await state.get_room_state(redis, ROOM)
        log.append(("emit", event, to, mysql["status"], rs["status"]))

    async def empty_leaderboard(db, sid):
        return []

    async def host_go(db, sid, lb, n):
        return {"scores": []}

    async def player_go(db, sid, uid, score, lb, n):
        return {"yourFinalScore": score}

    db = SimpleNamespace()

    async def db_get(model, key):
        return SimpleNamespace(id=session_id, status=mysql["status"])

    db.get = db_get
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(gw, "get_redis", fake_get_redis)
    monkeypatch.setattr(gw.sio, "emit", fake_emit)
    monkeypatch.setattr(game_service, "complete_game", complete_game)
    monkeypatch.setattr(game_service, "get_leaderboard", empty_leaderboard)
    monkeypatch.setattr(gw, "_host_game_over_payload", host_go)
    monkeypatch.setattr(gw, "_player_game_over_payload", player_go)
    monkeypatch.setitem(
        gw._sid_ctx,
        "host-sid",
        {"user_id": "h", "role": "HOST", "room_code": ROOM, "session_id": session_id},
    )

    await gw._on_host_advance_impl("host-sid", {})  # {} is never stale-checked

    emits = [e for e in log if e[0] == "emit"]
    assert log[0] == ("mysql_completed",)
    assert {e[2] for e in emits} == {gw._host_room(ROOM), gw._user_room("u1")}
    assert all(e[1] == E.GAME_OVER for e in emits)
    # Every game_over went out with COMPLETED already in MySQL and in Redis.
    assert all(e[3] == "COMPLETED" and e[4] == "COMPLETED" for e in emits)


async def _run_player_join(monkeypatch, status_after_enter: str, existing: bool):
    """Drive on_join_room for a PLAYER whose first status read says IN_PROGRESS and whose
    read after entering the rooms says status_after_enter (the final advance committed in
    between). Returns the ordered call log and the emits."""
    redis = FakeRedis()
    session_id = "s1"
    if existing:
        await state.add_player(redis, session_id, "u1", "Tester", is_guest=True)
    await state.set_room_state(
        redis,
        ROOM,
        {
            "status": "IN_PROGRESS",
            "question_phase": "RESULTS",
            "question_index": 0,
            "results_question_id": 11,
        },
    )
    log: list = []
    session = SimpleNamespace(
        id=session_id, status="IN_PROGRESS", host_user_id="h", room_code=ROOM
    )

    async def fake_get_redis():
        return redis

    async def get_session(sid):
        return {"user_id": "u1"}

    async def get_user(db, uid):
        return SimpleNamespace(id="u1", role="GUEST")

    async def by_code(db, r, code):
        return session, await state.get_room_state(r, code)

    async def authorise(db, user, s):
        return "Tester"

    async def enter_room(sid, room):
        log.append(("enter", room))

    async def leave_room(sid, room):
        log.append(("leave", room))

    async def fresh_status(db, sid):
        log.append(("status_read", status_after_enter))
        return status_after_enter

    async def leaderboard(db, sid):
        return [{"user_id": "u1", "score": 100}] if existing else []

    async def player_go(db, sid, uid, score, lb, n):
        return {"yourFinalScore": score, "playerCount": n}

    async def fake_emit(event, payload, to=None):
        log.append(("emit", event, payload))

    _patch_db(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(gw, "get_redis", fake_get_redis)
    monkeypatch.setattr(gw.sio, "get_session", get_session)
    monkeypatch.setattr(gw.sio, "enter_room", enter_room)
    monkeypatch.setattr(gw.sio, "leave_room", leave_room)
    monkeypatch.setattr(gw.sio, "emit", fake_emit)
    monkeypatch.setattr(gw, "get_user_by_id", get_user)
    monkeypatch.setattr(game_service, "get_session_by_code", by_code)
    monkeypatch.setattr(game_service, "authorise_player", authorise)
    monkeypatch.setattr(game_service, "fresh_session_status", fresh_status)
    monkeypatch.setattr(game_service, "get_leaderboard", leaderboard)
    monkeypatch.setattr(gw, "_player_game_over_payload", player_go)

    await gw.on_join_room("p-sid", {"room_code": ROOM, "role": "PLAYER"})
    gw._sid_ctx.pop("p-sid", None)
    return log, redis


async def test_player_join_racing_final_advance_gets_game_over_sync(monkeypatch):
    log, _ = await _run_player_join(monkeypatch, "COMPLETED", existing=True)
    kinds = [e[0] for e in log]
    # The status is read only after the socket entered both of its rooms.
    assert kinds.index("status_read") > max(
        i for i, k in enumerate(kinds) if k == "enter"
    )
    syncs = [e[2] for e in log if e[0] == "emit" and e[1] == E.SYNC_STATE]
    assert len(syncs) == 1
    assert syncs[0]["status"] == "COMPLETED" and syncs[0]["gameOver"]
    assert not any(e[0] == "emit" and e[1] == E.PLAYER_JOINED for e in log)


async def test_player_join_before_final_commit_is_in_rooms_for_broadcast(monkeypatch):
    # Other order: the status read still sees IN_PROGRESS, so the commit (and so the
    # game_over emit) comes later, while this socket is already in its rooms.
    log, _ = await _run_player_join(monkeypatch, "IN_PROGRESS", existing=True)
    kinds = [e[0] for e in log]
    assert "leave" not in kinds
    assert kinds.index("status_read") > max(
        i for i, k in enumerate(kinds) if k == "enter"
    )


async def test_new_player_racing_final_advance_is_not_let_in(monkeypatch):
    log, redis = await _run_player_join(monkeypatch, "COMPLETED", existing=False)
    syncs = [e for e in log if e[0] == "emit" and e[1] == E.SYNC_STATE]
    errors = [e for e in log if e[0] == "emit" and e[1] == E.ERROR]
    assert syncs == [] and len(errors) == 1
    assert "u1" not in await redis.smembers("session:s1:players")


# ---------------------------------------------------------------------------
# Gap 2: the player's own answer in SYNC_STATE
# ---------------------------------------------------------------------------


QUESTION = SimpleNamespace(
    id=11,
    type="multiple_choice",
    grading_type="ACCURACY",
    prompt="2+2?",
    config={"options": ["3", "4"]},
    time_limit_seconds=30,
    points_value=100,
    order_index=0,
    answer_data={"answer_points": [0, 100]},
)


class FakeDb:
    """Returns score rows for several users regardless of the WHERE clause, so the test
    also proves the helper filters to the caller itself."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return [
            SimpleNamespace(
                user_id="other", question_id=11, answer_data={"selectedIndex": 0}
            ),
            SimpleNamespace(
                user_id="me", question_id=11, answer_data={"selectedIndex": 1}
            ),
        ]

    async def get(self, model, key):
        return QUESTION if key == 11 else None


@pytest.fixture
def stub_results(monkeypatch):
    async def results(db, redis, session_id, qid, user_id):
        return {"questionId": qid, "yourPoints": 100}

    monkeypatch.setattr(gw, "_player_results_payload", results)


CURRENT_Q = {"question_id": 11, "question_number": 1, "total_questions": 3}


async def test_results_sync_carries_only_the_callers_answer(stub_results):
    db = FakeDb()
    room = {"question_phase": "RESULTS", "question_index": 0, "results_question_id": 11}
    extras = await gw._player_sync_extras(db, None, "s1", room, "me", CURRENT_Q)
    assert extras["yourAnswer"] == {
        "questionId": 11,
        "answerData": {"selectedIndex": 1},
    }
    assert extras["questionResults"] == {"questionId": 11, "yourPoints": 100}
    assert extras["question"]["questionId"] == 11
    assert "answer_data" not in extras["question"]
    assert "answer_points" not in json.dumps(extras["question"])
    # The query itself is filtered by the caller's user id too.
    sql = str(db.statements[0])
    assert "session_scores.user_id =" in sql


async def test_open_answered_question_sync_has_answer_but_no_results(stub_results):
    room = {"question_phase": "QUESTION", "question_index": 0}
    extras = await gw._player_sync_extras(FakeDb(), None, "s1", room, "me", CURRENT_Q)
    assert extras["yourAnswer"]["answerData"] == {"selectedIndex": 1}
    assert extras["questionResults"] is None  # no reveal while the question is open


async def test_open_unanswered_question_sync_has_neither(stub_results):
    class NoRows(FakeDb):
        async def execute(self, stmt):
            return []

    room = {"question_phase": "QUESTION", "question_index": 0}
    extras = await gw._player_sync_extras(NoRows(), None, "s1", room, "me", CURRENT_Q)
    assert extras["yourAnswer"] is None and extras["question"] is None
    assert extras["questionResults"] is None


async def test_out_of_sync_keys_send_no_answer_or_question(stub_results):
    # room_state already on the next question's index, current_q still the old one.
    room = {"question_phase": "QUESTION", "question_index": 1}
    extras = await gw._player_sync_extras(FakeDb(), None, "s1", room, "me", CURRENT_Q)
    assert extras["yourAnswer"] is None and extras["question"] is None
