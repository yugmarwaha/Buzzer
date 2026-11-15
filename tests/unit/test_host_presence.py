"""
Unit tests for host presence shared through Redis (several backend workers).

"Is a host connected?" is answered from the session:{id}:hosts set, not from one
process's memory, so these tests model a second worker by writing to that set without
touching this process's _sid_ctx. No DB, Redis or server: the fake Redis of
test_room_state_lock.py and stubbed services.

Run with:
    cd tests/unit && PYTHONPATH=../../backend pytest test_host_presence.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import game_service
from app.services import state_service as state
from app.websocket import events as E
from app.websocket import gateway as gw

from .test_room_state_lock import ROOM, FakeRedis, _patch_db

SESSION = "s1"


class CountingRedis(FakeRedis):
    """FakeRedis whose DEL reports how many keys it removed, like Redis."""

    async def delete(self, *keys):
        removed = sum(1 for k in keys if k in self.data or k in self.sets)
        await super().delete(*keys)
        return removed


@pytest.fixture
def env(monkeypatch):
    """A room with an open question, one host socket on this process, and captured emits."""
    redis = CountingRedis()
    emits: list[tuple] = []

    async def fake_get_redis():
        return redis

    async def fake_emit(event, payload, to=None):
        emits.append((event, to))

    monkeypatch.setattr(gw, "get_redis", fake_get_redis)
    monkeypatch.setattr(gw.sio, "emit", fake_emit)
    monkeypatch.setattr(gw, "_abandon_tasks", {})
    monkeypatch.setattr(gw, "_timer_tasks", {})
    monkeypatch.setattr(gw, "_sid_ctx", {})
    return SimpleNamespace(redis=redis, emits=emits)


async def _open_question(redis) -> None:
    await state.set_room_state(
        redis,
        ROOM,
        {"status": "IN_PROGRESS", "question_phase": "QUESTION", "question_index": 0},
    )
    await state.set_current_question(
        redis, SESSION, 11, gw.datetime.now(gw.timezone.utc), 30, 1, 3
    )


async def _host_joins_here(redis, sid: str) -> None:
    session = SimpleNamespace(id=SESSION, room_code=ROOM)
    await gw._register_host_socket(redis, sid, "h", session)


async def _stop_tasks() -> None:
    for task in list(gw._abandon_tasks.values()):
        task.cancel()
    await asyncio.sleep(0)


async def test_host_on_another_worker_keeps_the_room_running(env):
    await _open_question(env.redis)
    await _host_joins_here(env.redis, "sid-here")
    # A second host socket that lives on another worker: only Redis knows it.
    await state.add_host_socket(env.redis, SESSION, "sid-elsewhere")

    await gw.disconnect("sid-here")

    rs = await state.get_room_state(env.redis, ROOM)
    assert not rs.get("question_locked")
    assert env.emits == []
    assert gw._abandon_tasks == {}
    assert await state.host_connected(env.redis, SESSION)


async def test_last_host_socket_leaving_locks_and_starts_the_grace_period(env):
    await _open_question(env.redis)
    await _host_joins_here(env.redis, "sid-here")

    await gw.disconnect("sid-here")
    try:
        rs = await state.get_room_state(env.redis, ROOM)
        assert rs["question_locked"] is True
        assert rs["question_locked_by_disconnect"] is True
        assert 0 < rs["timer_remaining_seconds"] <= 30
        assert [e for e, _ in env.emits] == [E.QUESTION_LOCKED, E.HOST_DISCONNECTED]
        assert SESSION in gw._abandon_tasks
        assert not await state.host_connected(env.redis, SESSION)
        assert await env.redis.get(state._host_away_key(SESSION)) == "1"
    finally:
        await _stop_tasks()


async def test_host_returning_on_another_worker_tells_the_players(env):
    # The grace timer runs on the worker the host left, so the worker the host comes
    # back through has no task to cancel; the mark in Redis still makes it announce.
    await state.set_host_away(env.redis, SESSION, 60)

    await gw._end_host_absence(env.redis, SESSION, ROOM)
    assert env.emits == [(E.HOST_RECONNECTED, ROOM)]

    # Nothing to announce when no one was told the host left.
    env.emits.clear()
    await gw._end_host_absence(env.redis, SESSION, ROOM)
    assert env.emits == []


async def _run_grace_timer(monkeypatch, env) -> list[str]:
    ended: list[str] = []

    async def end_room(db, redis, session_id):
        ended.append(session_id)
        return "ABANDONED"

    async def get(model, session_id):
        return SimpleNamespace(id=session_id, status="IN_PROGRESS")

    _patch_db(monkeypatch, SimpleNamespace(get=get))
    monkeypatch.setattr(gw, "_HOST_GRACE_SECONDS", 0)
    monkeypatch.setattr(game_service, "end_room", end_room)
    await gw._host_abandon_task(SESSION, ROOM)
    return ended


async def test_grace_timer_ends_the_room_when_no_host_came_back(monkeypatch, env):
    assert await _run_grace_timer(monkeypatch, env) == [SESSION]


async def test_grace_timer_spares_a_room_whose_host_is_on_another_worker(
    monkeypatch, env
):
    # The host reconnected through a worker that could not cancel this task.
    await state.add_host_socket(env.redis, SESSION, "sid-elsewhere")
    assert await _run_grace_timer(monkeypatch, env) == []


async def test_host_joining_while_the_disconnect_runs_is_not_announced_as_gone(
    monkeypatch, env
):
    await _open_question(env.redis)
    await _host_joins_here(env.redis, "sid-old")
    real_set_away = state.set_host_away

    async def set_away_then_host_joins(redis, session_id, ttl):
        await real_set_away(redis, session_id, ttl)
        # Another worker registers the returning host right here.
        await state.add_host_socket(redis, session_id, "sid-new")

    monkeypatch.setattr(state, "set_host_away", set_away_then_host_joins)

    await gw.disconnect("sid-old")
    await asyncio.sleep(0)

    events = [e for e, _ in env.emits]
    assert E.HOST_DISCONNECTED not in events
    assert gw._abandon_tasks == {}
    assert await env.redis.get(state._host_away_key(SESSION)) is None
    # The auto-lock taken on the way was undone the way a joining host undoes it.
    rs = await state.get_room_state(env.redis, ROOM)
    assert rs["question_locked"] is False
    assert events == [E.QUESTION_LOCKED, E.QUESTION_UNLOCKED]
    gw._cancel_timer(SESSION)
