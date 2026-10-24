"""
T4 integration tests: course-owned quizzes, host/admin access, rooms, downloads, and
Past Quizzes (docs/design-host-admin.md §13).

Runs against the live Docker stack like the rest of the suite. Shared users come from the
`t4_users` fixture; every quiz a test creates is removed afterwards by the test-only
`scripts.cleanup_test_quiz` (the API has no whole-quiz delete).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid

import httpx
import pytest

from .conftest import _compose_cmd, _REPO_ROOT, cleanup_test_quiz, create_guest_tokens
from .engine.socket_client import TestSocketClient

T = 10.0  # HTTP timeout
EV = 10.0  # socket event timeout


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Stack helpers (MySQL / Redis inside the compose project under test)
# ---------------------------------------------------------------------------


def sql(query: str) -> str:
    r = subprocess.run(
        _compose_cmd()
        + [
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" buzzer -N -B -e "$0"',
            query,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def redis_cli(*args: str) -> str:
    r = subprocess.run(
        _compose_cmd() + ["exec", "-T", "redis", "redis-cli", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def room_key_exists(code: str) -> bool:
    return redis_cli("EXISTS", f"room:{code}") == "1"


def session_keys(sid: str) -> list[str]:
    out = redis_cli("--scan", "--pattern", f"session:{sid}:*")
    return [k for k in out.splitlines() if k]


def score_count(sid: str) -> int:
    return int(sql(f"SELECT COUNT(*) FROM session_scores WHERE session_id='{sid}'"))


async def eventually(check, timeout: float = 5.0, interval: float = 0.1):
    """Poll `check()` until it returns a truthy value (returned) or the timeout passes."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = check()
        if value or asyncio.get_running_loop().time() >= deadline:
            return value
        await asyncio.sleep(interval)


async def next_of(client: TestSocketClient, events: tuple[str, ...], timeout: float = EV):
    """Wait for whichever of `events` arrives first; returns (event, payload)."""
    tasks = {asyncio.ensure_future(client.wait_for(e, timeout)): e for e in events}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    t = done.pop()
    return tasks[t], t.result()


def lock_waiters() -> int:
    """Transactions currently blocked on a row lock in the stack's MySQL."""
    return int(sql("SELECT COUNT(*) FROM information_schema.innodb_trx WHERE trx_state='LOCK WAIT'"))


class GameRowLock:
    """Holds `SELECT ... FOR UPDATE` on one games row (or, with `table`, one row of another
    table keyed by id) from its own MySQL connection, so a test can line requests up behind
    the lock, change rows, and then release them all. Requests queued this way already
    hold a REPEATABLE READ snapshot (their auth lookup ran first), so anything committed
    here is exactly what a stale read would miss."""

    def __init__(self, gid: int | str, table: str = "games"):
        self.gid = gid
        self.table = table
        self.proc: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> "GameRowLock":
        self.proc = await asyncio.create_subprocess_exec(
            *_compose_cmd(),
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" buzzer -N -B -n 2>/dev/null',
            cwd=_REPO_ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        key = self.gid if isinstance(self.gid, int) else f"'{self.gid}'"
        await self.run(f"BEGIN; SELECT id FROM {self.table} WHERE id={key} FOR UPDATE;")
        return self

    async def run(self, statements: str) -> None:
        """Run statements in the locking transaction; returns once they have executed."""
        assert self.proc and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(f"{statements} SELECT 'sync-marker';\n".encode())
        await self.proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), EV)
            assert line, "mysql client exited"
            if line.strip() == b"sync-marker":
                return

    async def wait_for_waiters(self, n: int) -> None:
        assert await eventually(lambda: lock_waiters() >= n, EV), f"expected {n} lock waiters"

    async def __aexit__(self, *exc) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(b"COMMIT;\n")
        await self.proc.stdin.drain()
        self.proc.stdin.close()
        await asyncio.wait_for(self.proc.wait(), EV)


def visible_questions(base: str, token: str, gid: int) -> list[str]:
    """Prompts of a quiz's questions as a fresh request sees them."""
    r = httpx.get(f"{base}/api/host/games/{gid}/questions", headers=H(token), timeout=T)
    return [q["prompt"] for q in r.json()]


def backend_grace_seconds() -> int:
    """The running backend's host-disconnect grace (settings.HOST_GRACE_SECONDS)."""
    r = subprocess.run(
        _compose_cmd()
        + [
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            "from app.config import settings; print(settings.HOST_GRACE_SECONDS)",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip().splitlines()[-1])


# The disconnect-timer test waits out the real grace period, so it only runs against a
# backend started with a short one (the isolated test stack sets HOST_GRACE_SECONDS=3).
MAX_GRACE_FOR_TEST = 10


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_course(base_url: str, admin_token: str) -> int:
    r = httpx.post(
        f"{base_url}/api/admin/courses",
        json={"name": f"Test Course {uuid.uuid4().hex[:8]}", "semester": "Test"},
        headers=H(admin_token),
        timeout=T,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(base_url, admin_token, user_id, course_id, role):
    r = httpx.post(
        f"{base_url}/api/admin/users/{user_id}/course-access",
        json={"course_id": course_id, "role": role},
        headers=H(admin_token),
        timeout=T,
    )
    assert r.status_code == 204, r.text


@pytest.fixture()
def t4(base_url, admin_token, t4_users):
    """Two fresh test courses with the shared users granted as in §13."""
    c1 = _mk_course(base_url, admin_token)
    c2 = _mk_course(base_url, admin_token)
    u = t4_users
    _grant(base_url, admin_token, u["t4_host_a"]["id"], c1, "HOST")
    _grant(base_url, admin_token, u["t4_host_b"]["id"], c1, "HOST")
    _grant(base_url, admin_token, u["t4_host_c"]["id"], c2, "HOST")
    _grant(base_url, admin_token, u["t4_player"]["id"], c1, "PLAYER")
    _grant(base_url, admin_token, u["t4_player2"]["id"], c1, "PLAYER")
    created: list[int] = []
    ctx = {
        "base": base_url,
        "admin": admin_token,
        "a": u["t4_host_a"]["token"],
        "b": u["t4_host_b"]["token"],
        "c": u["t4_host_c"]["token"],
        "player": u["t4_player"]["token"],
        "player_id": u["t4_player"]["id"],
        "player2": u["t4_player2"]["token"],
        "a_id": u["t4_host_a"]["id"],
        "c1": c1,
        "c2": c2,
        "created": created,
    }
    yield ctx
    for gid in created:
        cleanup_test_quiz(gid)


def mc(prompt: str, points: float = 1.0) -> dict:
    return {
        "type": "multiple_choice",
        "grading_type": "ACCURACY",
        "prompt": prompt,
        "config": {"options": ["A", "B", "C"]},
        "answer_data": {"answer_points": [points, 0, 0]},
        "points_value": points,
        "time_limit_seconds": 30,
    }


def mk_quiz(t4, token: str, course_id: int, prompts=("Q1", "Q2")) -> tuple[int, list[int]]:
    base = t4["base"]
    r = httpx.post(
        f"{base}/api/host/courses/{course_id}/games",
        json={"title": f"T4 quiz {uuid.uuid4().hex[:6]}"},
        headers=H(token),
        timeout=T,
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    t4["created"].append(gid)
    qids = []
    for p in prompts:
        r = httpx.post(
            f"{base}/api/host/games/{gid}/questions", json=mc(p), headers=H(token), timeout=T
        )
        assert r.status_code == 201, r.text
        qids.append(r.json()["id"])
    return gid, qids


def start_room(t4, token: str, gid: int) -> tuple[str, str]:
    r = httpx.post(
        f"{t4['base']}/api/game/rooms", json={"game_id": gid}, headers=H(token), timeout=T
    )
    assert r.status_code == 201, r.text
    return r.json()["room_code"], r.json()["session_id"]


async def join(base, token, code, role, label) -> TestSocketClient:
    c = TestSocketClient(base, token, label)
    await c.connect()
    await c.emit("join_room", {"room_code": code, "role": role})
    await c.wait_for("sync_state", EV)
    return c


async def ask_and_answer(host, player, choice: int | None = 0) -> int:
    """Host advances to the next question; the player answers (or not). Returns qid."""
    await host.emit("host_advance", {})
    q = await player.wait_for("new_question", EV)
    await host.wait_for("new_question", EV)
    if choice is not None:
        await player.emit(
            "submit_answer",
            {"question_id": q["questionId"], "answer_data": {"selectedIndex": choice}},
        )
        await player.wait_for("answer_received", EV)
    return q["questionId"]


async def play_full(t4, host_token, player_token, gid, n_questions) -> str:
    """Run a complete game where the player answers every question with option A."""
    code, sid = start_room(t4, host_token, gid)
    host = await join(t4["base"], host_token, code, "HOST", "host")
    player = await join(t4["base"], player_token, code, "PLAYER", "player")
    try:
        for _ in range(n_questions):
            await ask_and_answer(host, player)
            await host.emit("host_advance", {})  # → RESULTS
            await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})  # → GAME_OVER
        await host.wait_for("game_over", EV)
        await player.wait_for("game_over", EV)
    finally:
        await host.disconnect()
        await player.disconnect()
    return sid


def end(t4, token, sid) -> httpx.Response:
    return httpx.post(f"{t4['base']}/api/host/sessions/{sid}/end", headers=H(token), timeout=T)


# ---------------------------------------------------------------------------
# Access control and parent ownership
# ---------------------------------------------------------------------------


def test_course_host_access(t4):
    base, c1, c2 = t4["base"], t4["c1"], t4["c2"]
    gid, qids = mk_quiz(t4, t4["a"], c1)

    # Host A works in course 1 …
    assert httpx.get(f"{base}/api/host/courses/{c1}/games", headers=H(t4["a"])).status_code == 200
    r = httpx.put(f"{base}/api/host/games/{gid}", json={"title": "Renamed"}, headers=H(t4["a"]))
    assert r.status_code == 200 and r.json()["title"] == "Renamed"
    assert httpx.post(
        f"{base}/api/host/games/{gid}/questions/reorder",
        json={"order": list(reversed(qids))},
        headers=H(t4["a"]),
    ).status_code == 204
    assert httpx.get(f"{base}/api/host/games/{gid}/export", headers=H(t4["a"])).status_code == 200
    # … and is refused in course 2.
    assert httpx.get(f"{base}/api/host/courses/{c2}/games", headers=H(t4["a"])).status_code == 403
    assert httpx.post(
        f"{base}/api/host/courses/{c2}/games", json={"title": "x"}, headers=H(t4["a"])
    ).status_code == 403

    # Host C (other course) can't touch course 1's quiz at all.
    assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(t4["c"])).status_code == 403
    assert httpx.put(
        f"{base}/api/host/games/{gid}", json={"title": "x"}, headers=H(t4["c"])
    ).status_code == 403

    # Players get 403 on /host/* (guests: test_guest_gets_403_on_host_routes).
    for token in (t4["player"],):
        assert httpx.get(f"{base}/api/host/courses/{c1}/games", headers=H(token)).status_code == 403
        assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(token)).status_code == 403
        assert httpx.get(f"{base}/api/host/courses/{c1}/roster", headers=H(token)).status_code == 403
        assert httpx.get(f"{base}/api/host/courses/{c1}/sessions", headers=H(token)).status_code == 403

    # Admin passes everywhere.
    for path in (
        f"/api/host/courses/{c1}/games",
        f"/api/host/courses/{c2}/games",
        f"/api/host/games/{gid}",
        f"/api/host/courses/{c1}/roster",
        f"/api/host/courses/{c1}/rooms",
        f"/api/host/courses/{c1}/sessions",
        f"/api/admin/games/{gid}",
        f"/api/admin/games/{gid}/questions",
    ):
        assert httpx.get(f"{base}{path}", headers=H(t4["admin"])).status_code == 200, path

    # Hosts get 403 on admin-only routes.
    assert httpx.get(f"{base}/api/admin/users", headers=H(t4["a"])).status_code == 403
    assert httpx.post(
        f"{base}/api/admin/courses", json={"name": "x", "semester": "y"}, headers=H(t4["a"])
    ).status_code == 403
    assert httpx.post(
        f"{base}/api/admin/users/{t4['a_id']}/course-access",
        json={"course_id": c2, "role": "HOST"},
        headers=H(t4["a"]),
    ).status_code == 403


def test_guest_gets_403_on_host_routes(t4):
    base = t4["base"]
    gid, _ = mk_quiz(t4, t4["a"], t4["c1"])
    code, _sid = start_room(t4, t4["a"], gid)
    guest = create_guest_tokens(base, code, 1)[0]
    assert httpx.get(f"{base}/api/host/courses/{t4['c1']}/games", headers=H(guest)).status_code == 403
    assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(guest)).status_code == 403


def test_old_game_grant_alone_gives_no_access(t4):
    gid, _ = mk_quiz(t4, t4["a"], t4["c1"])
    c_id = sql("SELECT id FROM users WHERE username='t4_host_c'")
    sql(f"INSERT IGNORE INTO user_game_access (user_id, game_id) VALUES ('{c_id}', {gid})")
    r = httpx.get(f"{t4['base']}/api/host/games/{gid}", headers=H(t4["c"]))
    assert r.status_code == 403
    r = httpx.post(f"{t4['base']}/api/game/rooms", json={"game_id": gid}, headers=H(t4["c"]))
    assert r.status_code == 403


def test_parent_ownership(t4):
    base = t4["base"]
    g1, q1 = mk_quiz(t4, t4["a"], t4["c1"])
    g2, q2 = mk_quiz(t4, t4["admin"], t4["c2"], prompts=("Other course question",))

    for prefix, token in (("/api/host", t4["a"]), ("/api/admin", t4["admin"])):
        r = httpx.put(
            f"{base}{prefix}/games/{g1}/questions/{q2[0]}",
            json={"prompt": "hijacked"},
            headers=H(token),
        )
        assert r.status_code == 404, (prefix, r.text)
        r = httpx.delete(f"{base}{prefix}/games/{g1}/questions/{q2[0]}", headers=H(token))
        assert r.status_code == 404, (prefix, r.text)
        r = httpx.post(
            f"{base}{prefix}/games/{g1}/questions/reorder",
            json={"order": q1[:-1] + [q2[0]]},
            headers=H(token),
        )
        assert r.status_code == 409, (prefix, r.text)
    # The refused reorders left course 1's order as it was …
    qs1 = httpx.get(f"{base}/api/host/games/{g1}/questions", headers=H(t4["admin"])).json()
    assert [(q["id"], q["order_index"]) for q in qs1] == [(q, i) for i, q in enumerate(q1)]
    # … and the course 2 question is untouched.
    qs = httpx.get(f"{base}/api/host/games/{g2}/questions", headers=H(t4["admin"])).json()
    assert [q["prompt"] for q in qs] == ["Other course question"]

    # Roster entry of course 2 via course 1.
    r = httpx.post(
        f"{base}/api/admin/courses/{t4['c2']}/roster/import",
        json={"rows": [{"netid": f"t4r{uuid.uuid4().hex[:6]}", "full_name": "Rosa", "email": "r@example.com"}]},
        headers=H(t4["admin"]),
    )
    assert r.status_code == 200, r.text
    entry = httpx.get(f"{base}/api/admin/courses/{t4['c2']}/roster", headers=H(t4["admin"])).json()[0]
    for prefix, token in (("/api/host", t4["a"]), ("/api/admin", t4["admin"])):
        r = httpx.patch(
            f"{base}{prefix}/courses/{t4['c1']}/roster/{entry['id']}",
            json={"is_active": False},
            headers=H(token),
        )
        assert r.status_code == 404, (prefix, r.text)
    after = httpx.get(f"{base}/api/admin/courses/{t4['c2']}/roster", headers=H(t4["admin"])).json()[0]
    assert after["is_active"] is True


async def test_retired_endpoints(t4):
    base, admin = t4["base"], t4["admin"]
    gid, _ = mk_quiz(t4, t4["a"], t4["c1"], prompts=("Q1",))
    assert httpx.delete(f"{base}/api/admin/games/{gid}", headers=H(admin)).status_code == 405
    uid = t4["a_id"]
    assert httpx.post(
        f"{base}/api/admin/users/{uid}/game-access", json={"game_id": gid}, headers=H(admin)
    ).status_code == 404
    # A real grant row (the old table is kept for the migration): the retired DELETE
    # route is gone (404), so the row is untouched.
    sql(f"INSERT IGNORE INTO user_game_access (user_id, game_id) VALUES ('{uid}', {gid})")
    grant = f"SELECT COUNT(*) FROM user_game_access WHERE user_id='{uid}' AND game_id={gid}"
    assert sql(grant) == "1"
    r = httpx.delete(f"{base}/api/admin/users/{uid}/game-access/{gid}", headers=H(admin))
    assert r.status_code == 404 and r.json()["detail"] == "Not Found", r.text
    assert sql(grant) == "1"
    assert httpx.get(f"{base}/api/game/my-games", headers=H(admin)).status_code == 404
    assert httpx.get(f"{base}/api/admin/sessions", headers=H(admin)).status_code == 404
    # A real finished session: the host-side download works, the retired admin routes
    # are route-not-found (FastAPI's generic 404, not a "session not found").
    sid = await play_full(t4, t4["a"], t4["player"], gid, 1)
    assert httpx.get(f"{base}/api/host/sessions/{sid}/export", headers=H(admin)).status_code == 200
    for kind in ("export", "report"):
        r = httpx.get(f"{base}/api/admin/sessions/{sid}/{kind}", headers=H(admin))
        assert r.status_code == 404 and r.json()["detail"] == "Not Found", (kind, r.text)
    # The admin user page shows a read-only course summary instead of game grants.
    body = httpx.get(f"{base}/api/admin/users/{uid}", headers=H(admin)).json()
    assert "game_access" not in body
    assert any(c["course_id"] == t4["c1"] and c["role"] == "HOST" for c in body["course_summary"])


# ---------------------------------------------------------------------------
# Rooms, lock, archive, question delete
# ---------------------------------------------------------------------------


def test_room_course_comes_from_quiz(t4):
    base = t4["base"]
    gid, _ = mk_quiz(t4, t4["a"], t4["c1"])
    code, _sid = start_room(t4, t4["a"], gid)
    assert httpx.get(f"{base}/api/game/rooms/{code}", headers=H(t4["a"])).json()["course_id"] == t4["c1"]
    r = httpx.post(
        f"{base}/api/game/rooms", json={"game_id": gid, "course_id": t4["c2"]}, headers=H(t4["a"])
    )
    assert r.status_code == 422
    assert httpx.post(f"{base}/api/game/rooms", json={"game_id": gid}, headers=H(t4["c"])).status_code == 403
    assert httpx.post(
        f"{base}/api/admin/games", json={"title": "no course"}, headers=H(t4["admin"])
    ).status_code == 422


def test_active_room_lock(t4):
    base, a = t4["base"], t4["a"]
    gid, qids = mk_quiz(t4, a, t4["c1"])
    _code, sid = start_room(t4, a, gid)

    assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(a)).json()["has_active_room"] is True
    assert httpx.put(f"{base}/api/host/games/{gid}", json={"title": "x"}, headers=H(a)).status_code == 409
    assert httpx.post(f"{base}/api/host/games/{gid}/questions", json=mc("new"), headers=H(a)).status_code == 409
    assert httpx.put(
        f"{base}/api/host/games/{gid}/questions/{qids[0]}", json={"prompt": "x"}, headers=H(a)
    ).status_code == 409
    assert httpx.post(
        f"{base}/api/host/games/{gid}/questions/reorder", json={"order": qids[::-1]}, headers=H(a)
    ).status_code == 409
    assert httpx.delete(f"{base}/api/host/games/{gid}/questions/{qids[0]}", headers=H(a)).status_code == 409
    assert httpx.post(f"{base}/api/host/games/{gid}/archive", headers=H(a)).status_code == 409

    assert end(t4, a, sid).json() == {"outcome": "removed"}
    assert httpx.put(f"{base}/api/host/games/{gid}", json={"title": "ok"}, headers=H(a)).status_code == 200
    assert httpx.post(f"{base}/api/host/games/{gid}/questions", json=mc("new"), headers=H(a)).status_code == 201


async def test_edits_waiting_on_room_creation_are_refused(t4):
    """A room committed while quiz edits wait on the game-row lock must refuse every one
    of them (design §6). The test holds the row lock itself, lines the edits up behind
    it, commits a LOBBY room for the quiz exactly as create_room does, then releases the
    lock: each edit's active-room check has to see the new room, not its older snapshot.
    (Plain concurrent requests can't show this: whichever order they land in, the room's
    snapshot is only taken at its first host_advance.)"""
    base, a = t4["base"], t4["a"]
    gid, qids = mk_quiz(t4, a, t4["c1"], prompts=("original 1", "original 2"))
    host_id = t4["a_id"]
    session_id = str(uuid.uuid4())
    room_code = "Z" + uuid.uuid4().hex[:5].upper()
    async with httpx.AsyncClient(base_url=base, headers=H(a), timeout=30) as client:
        edits = {
            "update game": lambda: client.put(f"/api/host/games/{gid}", json={"title": "x"}),
            "create question": lambda: client.post(f"/api/host/games/{gid}/questions", json=mc("new")),
            "update question": lambda: client.put(
                f"/api/host/games/{gid}/questions/{qids[0]}", json={"prompt": "edited"}
            ),
            "delete question": lambda: client.delete(f"/api/host/games/{gid}/questions/{qids[1]}"),
            "reorder": lambda: client.post(
                f"/api/host/games/{gid}/questions/reorder", json={"order": qids[::-1]}
            ),
            "archive": lambda: client.post(f"/api/host/games/{gid}/archive"),
        }
        async with GameRowLock(gid) as lock:
            pending = [asyncio.ensure_future(fire()) for fire in edits.values()]
            await lock.wait_for_waiters(len(pending))
            await lock.run(
                "INSERT INTO game_sessions (id, room_code, game_id, course_id, host_user_id, status) "
                f"VALUES ('{session_id}', '{room_code}', {gid}, {t4['c1']}, '{host_id}', 'LOBBY');"
            )
        results = await asyncio.gather(*pending)
    for name, r in zip(edits, results):
        assert r.status_code == 409, (name, r.status_code, r.text)
    qs = httpx.get(f"{base}/api/host/games/{gid}/questions", headers=H(a), timeout=T).json()
    assert [(q["id"], q["prompt"], q["order_index"]) for q in qs] == [
        (qids[0], "original 1", 0),
        (qids[1], "original 2", 1),
    ]
    game = httpx.get(f"{base}/api/host/games/{gid}", headers=H(a), timeout=T).json()
    assert game["archived_at"] is None and game["has_active_room"] is True


async def test_concurrent_question_creates_get_contiguous_order(t4):
    """N question creates queued on one quiz's row lock each append at the end: every
    order_index is unique and they stay contiguous (the MAX read is a locking read)."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("Q0",))
    n = 8
    async with httpx.AsyncClient(base_url=base, headers=H(a), timeout=30) as client:
        async with GameRowLock(gid) as lock:
            pending = [
                asyncio.ensure_future(
                    client.post(f"/api/host/games/{gid}/questions", json=mc(f"C{i}"))
                )
                for i in range(n)
            ]
            await lock.wait_for_waiters(n)
        results = await asyncio.gather(*pending)
    assert [r.status_code for r in results] == [201] * n, [r.text for r in results]
    qs = httpx.get(f"{base}/api/host/games/{gid}/questions", headers=H(a), timeout=T).json()
    assert sorted(q["order_index"] for q in qs) == list(range(n + 1))


def test_archive_and_restore(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    assert httpx.post(f"{base}/api/host/games/{gid}/archive", headers=H(t4["c"])).status_code == 403
    r = httpx.post(f"{base}/api/host/games/{gid}/archive", headers=H(a))
    assert r.status_code == 200 and r.json()["archived_at"] is not None

    active = httpx.get(f"{base}/api/host/courses/{t4['c1']}/games", headers=H(a)).json()
    archived = httpx.get(f"{base}/api/host/courses/{t4['c1']}/games?archived=true", headers=H(a)).json()
    assert gid not in [g["id"] for g in active]
    assert gid in [g["id"] for g in archived]

    assert httpx.post(f"{base}/api/game/rooms", json={"game_id": gid}, headers=H(a)).status_code == 409
    assert httpx.put(f"{base}/api/host/games/{gid}", json={"title": "x"}, headers=H(a)).status_code == 409
    assert httpx.post(f"{base}/api/host/games/{gid}/questions", json=mc("x"), headers=H(a)).status_code == 409
    assert httpx.get(f"{base}/api/host/games/{gid}/export", headers=H(a)).status_code == 200
    assert httpx.post(f"{base}/api/host/games/{gid}/archive", headers=H(a)).status_code == 409

    assert httpx.post(f"{base}/api/host/games/{gid}/restore", headers=H(a)).status_code == 200
    assert httpx.post(f"{base}/api/host/games/{gid}/restore", headers=H(a)).status_code == 409
    _code, sid = start_room(t4, a, gid)
    assert httpx.post(f"{base}/api/host/games/{gid}/archive", headers=H(a)).status_code == 409
    end(t4, a, sid)


async def test_question_delete_rules(t4):
    base, a = t4["base"], t4["a"]
    gid, qids = mk_quiz(t4, a, t4["c1"], prompts=("Q1", "Q2", "Q3"))
    assert httpx.delete(f"{base}/api/host/games/{gid}/questions/{qids[1]}", headers=H(a)).status_code == 204
    qs = httpx.get(f"{base}/api/host/games/{gid}/questions", headers=H(a)).json()
    assert [q["order_index"] for q in qs] == [0, 1]
    assert [q["prompt"] for q in qs] == ["Q1", "Q3"]

    sid = await play_full(t4, a, t4["player"], gid, 2)
    qs = httpx.get(f"{base}/api/host/games/{gid}/questions", headers=H(a)).json()
    assert all(q["has_answers"] for q in qs)
    assert httpx.delete(f"{base}/api/host/games/{gid}/questions/{qids[0]}", headers=H(a)).status_code == 409
    report = httpx.get(f"{base}/api/host/sessions/{sid}/report", headers=H(a)).text
    assert "Q1" in report


# ---------------------------------------------------------------------------
# Ending rooms
# ---------------------------------------------------------------------------


def test_only_starter_or_admin_can_end(t4):
    base = t4["base"]
    gid, _ = mk_quiz(t4, t4["a"], t4["c1"])
    code, sid = start_room(t4, t4["a"], gid)
    rooms_b = httpx.get(f"{base}/api/host/courses/{t4['c1']}/rooms", headers=H(t4["b"])).json()
    mine = [r for r in rooms_b if r["session_id"] == sid][0]
    assert mine["can_end"] is False and mine["host_display_name"] == "t4_host_a"
    assert end(t4, t4["b"], sid).status_code == 403
    assert httpx.delete(f"{base}/api/game/sessions/{sid}", headers=H(t4["b"])).status_code == 403
    assert end(t4, t4["c"], sid).status_code == 403
    rooms_admin = httpx.get(f"{base}/api/host/courses/{t4['c1']}/rooms", headers=H(t4["admin"])).json()
    assert [r for r in rooms_admin if r["session_id"] == sid][0]["can_end"] is True
    assert end(t4, t4["admin"], sid).json() == {"outcome": "removed"}
    # Double end of a *removed* room: the row is gone, so 404 (an abandoned one is 409,
    # see test_end_in_progress_room_keeps_answers).
    assert end(t4, t4["admin"], sid).status_code == 404
    assert end(t4, t4["a"], sid).status_code == 404
    assert not room_key_exists(code)


async def test_end_lobby_room_notifies_players(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        assert end(t4, a, sid).json() == {"outcome": "removed"}
        await player.wait_for("game_abandoned", EV)
    finally:
        await player.disconnect()
    assert httpx.get(f"{base}/api/game/rooms/{code}", headers=H(a)).status_code == 404
    assert not room_key_exists(code)


async def test_end_in_progress_room_keeps_answers(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    late = await join(base, t4["player2"], code, "PLAYER", "player2")
    try:
        qid = await ask_and_answer(host, player)
        await late.wait_for("new_question", EV)
        assert end(t4, a, sid).json() == {"outcome": "abandoned"}
        for c in (player, late, host):
            await c.wait_for("game_abandoned", EV)
        # A *fresh* late answer (player2 never answered qid) is not accepted: the ended
        # room's sockets are detached, so it gets no acknowledgement and no score. A late
        # advance writes nothing either.
        late.drain("error", "answer_received")
        await late.emit("submit_answer", {"question_id": qid, "answer_data": {"selectedIndex": 0}})
        await host.emit("host_advance", {})
        with pytest.raises(asyncio.TimeoutError):
            await late.wait_for("answer_received", 1.0)
        assert score_count(sid) == 1
        assert not await eventually(lambda: room_key_exists(code) or session_keys(sid), 1.0)
    finally:
        for c in (host, player, late):
            await c.disconnect()

    assert sql(f"SELECT status, completed_at IS NOT NULL FROM game_sessions WHERE id='{sid}'") == "ABANDONED\t1"
    assert score_count(sid) == 1
    assert not room_key_exists(code)
    assert session_keys(sid) == []
    sessions = httpx.get(f"{base}/api/host/courses/{t4['c1']}/sessions", headers=H(t4["b"])).json()
    assert [s["status"] for s in sessions if s["session_id"] == sid] == ["ABANDONED"]
    past = httpx.get(f"{base}/api/player/past-quizzes", headers=H(t4["player"])).json()
    assert [p["status"] for p in past if p["session_id"] == sid] == ["ABANDONED"]
    # Double end of an abandoned room: exactly 409 (the row is kept).
    assert end(t4, a, sid).status_code == 409
    assert end(t4, t4["admin"], sid).status_code == 409


async def test_end_racing_fresh_answer_and_advance(t4):
    """End an IN_PROGRESS room while a fresh answer and a host_advance are in flight. Each
    run: the answer is either acknowledged (and scored, before the end) or refused with an
    error (and not scored); the session ends ABANDONED with no Redis state left behind."""
    base, a = t4["base"], t4["a"]
    for _ in range(5):
        gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("R1", "R2"))
        code, sid = start_room(t4, a, gid)
        host = await join(base, a, code, "HOST", "host")
        player = await join(base, t4["player"], code, "PLAYER", "player")
        racer = await join(base, t4["player2"], code, "PLAYER", "player2")
        try:
            qid = await ask_and_answer(host, player)  # one answer → end abandons
            await racer.wait_for("new_question", EV)
            async with httpx.AsyncClient(base_url=base, headers=H(a), timeout=T) as client:
                resp, _, _ = await asyncio.gather(
                    client.post(f"/api/host/sessions/{sid}/end"),
                    racer.emit(
                        "submit_answer",
                        {"question_id": qid, "answer_data": {"selectedIndex": 0}},
                    ),
                    host.emit("host_advance", {}),
                )
            assert resp.status_code == 200 and resp.json() == {"outcome": "abandoned"}, resp.text
            for c in (host, player, racer):
                await c.wait_for("game_abandoned", EV)
            # Acknowledged (scored before the end), refused with an error, or — once the
            # room's sockets are detached — no reply at all. Never scored after the end.
            try:
                ev, payload = await next_of(racer, ("error", "answer_received"), 2.0)
            except asyncio.TimeoutError:
                ev, payload = "no reply", {}
            if ev == "answer_received":
                assert not payload.get("alreadyAnswered")
                assert score_count(sid) == 2  # accepted before the end, and kept
            elif ev == "error":
                assert score_count(sid) == 1  # refused: no score written
            else:
                # No ack: the answer may have committed just before the end detached the
                # socket (the ack emit then goes nowhere), or not at all. Either is fine.
                assert score_count(sid) in (1, 2)
            expected_scores = score_count(sid)
        finally:
            for c in (host, player, racer):
                await c.disconnect()
        assert sql(f"SELECT status FROM game_sessions WHERE id='{sid}'") == "ABANDONED"
        assert not await eventually(lambda: room_key_exists(code) or session_keys(sid), 1.0)
        await asyncio.sleep(0.5)
        assert score_count(sid) == expected_scores <= 2  # nothing trickles in afterwards


@pytest.mark.parametrize("with_answer", [True, False], ids=["answers", "empty"])
async def test_host_disconnect_timer_ends_room(t4, with_answer):
    """Host leaves mid-game and doesn't return: after HOST_GRACE_SECONDS the timer runs
    end_room. With answers → ABANDONED; without → removed. Either way connected players
    still get game_abandoned (the listener didn't cancel the timer's own task)."""
    grace = backend_grace_seconds()
    if grace > MAX_GRACE_FOR_TEST:
        pytest.skip(
            f"backend HOST_GRACE_SECONDS={grace}; run against a stack started with "
            f"HOST_GRACE_SECONDS<={MAX_GRACE_FOR_TEST} (the isolated test stack uses 3)"
        )
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        await ask_and_answer(host, player, 0 if with_answer else None)
        await host.disconnect()
        await player.wait_for("host_disconnected", EV)
        await player.wait_for("game_abandoned", grace + EV)
    finally:
        await host.disconnect()
        await player.disconnect()

    if with_answer:
        assert sql(
            f"SELECT status, completed_at IS NOT NULL FROM game_sessions WHERE id='{sid}'"
        ) == "ABANDONED\t1"
        assert score_count(sid) == 1
    else:
        assert sql(f"SELECT COUNT(*) FROM game_sessions WHERE id='{sid}'") == "0"
    assert not room_key_exists(code)
    assert await eventually(lambda: not session_keys(sid), 2.0)


def room_state(code: str) -> dict:
    raw = redis_cli("GET", f"room:{code}")
    return json.loads(raw) if raw else {}


@pytest.mark.parametrize("second_join", ["join_room", "rejoin_room"], ids=["two-tabs", "reconnect"])
async def test_closing_one_of_two_host_sockets_is_not_a_disconnect(t4, second_join):
    """Two HOST sockets for one room (two tabs, or a reconnect on a new sid whose old sid
    drops later): closing one must not lock the question, emit host_disconnected, or
    start the abandon timer — the room is still IN_PROGRESS after the grace period."""
    grace = backend_grace_seconds()
    if grace > MAX_GRACE_FOR_TEST:
        pytest.skip(
            f"backend HOST_GRACE_SECONDS={grace}; run against a stack started with "
            f"HOST_GRACE_SECONDS<={MAX_GRACE_FOR_TEST} (the isolated test stack uses 3)"
        )
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    old = await join(base, a, code, "HOST", "host-old")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    new = TestSocketClient(base, a, "host-new")
    try:
        await ask_and_answer(old, player, None)
        await new.connect()
        payload = {"room_code": code, "role": "HOST"} if second_join == "join_room" else {"room_code": code}
        await new.emit(second_join, payload)
        await new.wait_for("sync_state", EV)

        await old.disconnect()
        with pytest.raises(asyncio.TimeoutError):
            await player.wait_for("host_disconnected", grace + 2.0)
        state_now = room_state(code)
        assert state_now.get("question_locked") is False, state_now
        assert not state_now.get("question_locked_by_disconnect"), state_now
        assert sql(f"SELECT status FROM game_sessions WHERE id='{sid}'") == "IN_PROGRESS"

        # The remaining socket still drives the game.
        await new.emit("host_advance", {})
        await new.wait_for("question_results", EV)

        # Once the last host socket goes, the normal disconnect path runs.
        await new.disconnect()
        await player.wait_for("host_disconnected", EV)
        await player.wait_for("game_abandoned", grace + EV)
    finally:
        for c in (old, new, player):
            await c.disconnect()
    assert await eventually(lambda: sql(f"SELECT COUNT(*) FROM game_sessions WHERE id='{sid}'") == "0", 2.0)
    assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(a)).json()["has_active_room"] is False


def test_stale_room_is_listed_and_can_be_ended(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    redis_cli("DEL", f"room:{code}")  # simulate TTL expiry / restart
    rooms = httpx.get(f"{base}/api/host/courses/{t4['c1']}/rooms", headers=H(a)).json()
    row = [r for r in rooms if r["session_id"] == sid][0]
    assert row["live"] is False and row["player_count"] is None
    assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(a)).json()["has_active_room"] is True
    assert end(t4, a, sid).json() == {"outcome": "removed"}
    assert httpx.get(f"{base}/api/host/games/{gid}", headers=H(a)).json()["has_active_room"] is False


async def test_rooms_tab_answered_count(t4):
    """§5 Rooms tab: `answered` comes from MySQL and counts distinct players who answered."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)

    def row() -> dict:
        rooms = httpx.get(f"{base}/api/host/courses/{t4['c1']}/rooms", headers=H(t4["b"]), timeout=T)
        assert rooms.status_code == 200, rooms.text
        return [r for r in rooms.json() if r["session_id"] == sid][0]

    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    player2 = await join(base, t4["player2"], code, "PLAYER", "player2")
    try:
        before = row()
        assert (before["answered_count"], before["has_answers"]) == (0, False)
        await ask_and_answer(host, player)  # Q1: player answers
        await player2.wait_for("new_question", EV)
        assert await eventually(lambda: row()["answered_count"] == 1)
        assert row()["has_answers"] is True
        await host.emit("host_advance", {})  # → RESULTS
        await host.wait_for("question_results", EV)
        await ask_and_answer(host, player)  # Q2: the same player again
        assert not await eventually(lambda: row()["answered_count"] != 1, 1.0)
        assert score_count(sid) == 2  # two answers, still one distinct player
    finally:
        for c in (host, player, player2):
            await c.disconnect()
    assert end(t4, a, sid).json() == {"outcome": "abandoned"}


async def test_start_after_end_writes_nothing(t4):
    """A host_advance (game start) arriving after the room ended recreates nothing."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    try:
        assert end(t4, a, sid).status_code == 200
        await host.wait_for("game_abandoned", EV)
        await host.emit("host_advance", {})
        with pytest.raises(asyncio.TimeoutError):
            await host.wait_for("new_question", 1.0)
        assert not await eventually(lambda: room_key_exists(code) or session_keys(sid), 1.0)
    finally:
        await host.disconnect()
    assert not room_key_exists(code)
    assert sql(f"SELECT COUNT(*) FROM game_sessions WHERE id='{sid}'") == "0"


async def test_start_game_racing_end_of_lobby_room(t4):
    """start_game (first host_advance) and the end of a LOBBY room race. Whichever wins,
    the empty room is removed, players and host get game_abandoned, and the room key is
    never recreated."""
    base, a = t4["base"], t4["a"]
    for _ in range(5):
        gid, _ = mk_quiz(t4, a, t4["c1"])
        code, sid = start_room(t4, a, gid)
        host = await join(base, a, code, "HOST", "host")
        player = await join(base, t4["player"], code, "PLAYER", "player")
        try:
            async with httpx.AsyncClient(base_url=base, headers=H(a), timeout=T) as client:
                resp, _ = await asyncio.gather(
                    client.post(f"/api/host/sessions/{sid}/end"),
                    host.emit("host_advance", {}),
                )
            assert resp.status_code == 200 and resp.json() == {"outcome": "removed"}, resp.text
            await host.wait_for("game_abandoned", EV)
            await player.wait_for("game_abandoned", EV)
        finally:
            await host.disconnect()
            await player.disconnect()
        assert sql(f"SELECT COUNT(*) FROM game_sessions WHERE id='{sid}'") == "0"
        assert not await eventually(lambda: room_key_exists(code) or session_keys(sid), 1.0)


# ---------------------------------------------------------------------------
# Downloads, guest merge, snapshot, Past Quizzes
# ---------------------------------------------------------------------------


async def test_downloads_and_snapshot(t4):
    base, a = t4["base"], t4["a"]
    gid, qids = mk_quiz(t4, a, t4["c1"], prompts=("Original prompt one", "Original prompt two"))
    sid = await play_full(t4, a, t4["player"], gid, 2)

    # Any host of the course (B didn't run it) can download; another course's host can't.
    raw = httpx.get(f"{base}/api/host/sessions/{sid}/export?format=raw", headers=H(t4["b"]))
    assert raw.status_code == 200
    rows = list(csv.reader(io.StringIO(raw.text)))
    assert rows[0] == ["Player", "Q1", "Q2", "Total"]
    assert float(rows[1][-1]) == 2.0
    assert httpx.get(f"{base}/api/host/sessions/{sid}/report", headers=H(t4["b"])).status_code == 200
    assert httpx.get(f"{base}/api/game/sessions/{sid}/export", headers=H(t4["b"])).status_code == 200
    assert httpx.get(f"{base}/api/host/sessions/{sid}/export", headers=H(t4["c"])).status_code == 403
    assert httpx.get(f"{base}/api/host/sessions/{sid}/report", headers=H(t4["c"])).status_code == 403

    # Edit after the session: downloads and Past Quizzes still show what was asked.
    assert httpx.put(
        f"{base}/api/host/games/{gid}/questions/{qids[0]}",
        json={"prompt": "Edited later", "points_value": 5},
        headers=H(a),
    ).status_code == 200
    report = httpx.get(f"{base}/api/host/sessions/{sid}/report", headers=H(a)).text
    assert "Original prompt one" in report and "Edited later" not in report
    raw_after = httpx.get(f"{base}/api/host/sessions/{sid}/export?format=raw", headers=H(a))
    assert raw_after.status_code == 200
    assert list(csv.reader(io.StringIO(raw_after.text))) == rows
    canvas = httpx.get(
        f"{base}/api/host/sessions/{sid}/export",
        params={"format": "canvas", "per_question": "true", "roster_only": "false"},
        headers=H(a),
    )
    assert canvas.status_code == 200
    crow = list(csv.reader(io.StringIO(canvas.text)))
    assert crow[0][5].startswith("Q1: Original prompt one") and "Edited later" not in canvas.text
    assert crow[1] == ["Points Possible", "", "", "", "", "1.0", "1.0"]  # not the edited 5
    detail = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t4["player"])).json()
    assert detail["questions"][0]["prompt"] == "Original prompt one"
    assert detail["questions"][0]["maxPoints"] == 1.0
    assert detail["max_score"] == 2.0


async def test_download_totals_agree(t4):
    """The HTML report's per-player figures and both Canvas exports agree with the raw
    CSV totals for the same finished session (two players, different scores, a skip)."""
    base, a = t4["base"], t4["a"]
    r = httpx.post(
        f"{base}/api/host/courses/{t4['c1']}/games",
        json={"title": f"T4 totals {uuid.uuid4().hex[:6]}"},
        headers=H(a),
        timeout=T,
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    t4["created"].append(gid)
    for prompt, pts in (("Totals Q1", 2.0), ("Totals Q2", 1.0), ("Totals Q3", 3.0)):
        r = httpx.post(
            f"{base}/api/host/games/{gid}/questions", json=mc(prompt, pts), headers=H(a), timeout=T
        )
        assert r.status_code == 201, r.text

    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    p1 = await join(base, t4["player"], code, "PLAYER", "player")
    p2 = await join(base, t4["player2"], code, "PLAYER", "player2")
    # player: all correct (2+1+3 = 6); player2: wrong, right, no answer (0+1+0 = 1).
    plan = [(0, 1), (0, 0), (0, None)]
    try:
        for c1, c2 in plan:
            await host.emit("host_advance", {})
            q = await host.wait_for("new_question", EV)
            for client, choice in ((p1, c1), (p2, c2)):
                await client.wait_for("new_question", EV)
                if choice is not None:
                    await client.emit(
                        "submit_answer",
                        {"question_id": q["questionId"], "answer_data": {"selectedIndex": choice}},
                    )
                    await client.wait_for("answer_received", EV)
            await host.emit("host_advance", {})  # → RESULTS
            await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})  # → GAME_OVER
        await host.wait_for("game_over", EV)
    finally:
        for c in (host, p1, p2):
            await c.disconnect()

    def get(path: str, **params) -> str:
        resp = httpx.get(f"{base}/api/host/sessions/{sid}/{path}", params=params, headers=H(a), timeout=T)
        assert resp.status_code == 200, resp.text
        return resp.text

    raw_rows = list(csv.reader(io.StringIO(get("export", format="raw"))))
    assert raw_rows[0] == ["Player", "Q1", "Q2", "Q3", "Total"]
    raw = {row[0]: float(row[-1]) for row in raw_rows[1:]}
    assert raw == {"t4_player": 6.0, "t4_player2": 1.0}
    # Each raw total is the sum of its own question cells.
    for row in raw_rows[1:]:
        assert sum(float(v) for v in row[1:-1] if v != "") == float(row[-1])

    single = list(csv.reader(io.StringIO(get("export", format="canvas", roster_only="false"))))
    assert single[1][5] == "6.0"  # Points Possible = every asked question
    assert {row[0]: float(row[5]) for row in single[2:]} == raw

    per_q = list(
        csv.reader(
            io.StringIO(get("export", format="canvas", per_question="true", roster_only="false"))
        )
    )
    assert per_q[1][5:] == ["2.0", "1.0", "3.0"]
    assert {row[0]: sum(float(v) for v in row[5:] if v != "") for row in per_q[2:]} == raw

    # The HTML report is anonymised: players, average, high score, and histogram counts.
    report = get("report")

    def stat(label: str) -> str:
        m = re.search(
            rf'<div class="stat-label">{label}</div>\s*<div class="stat-value">([^<]*)</div>',
            report,
        )
        assert m, label
        return m.group(1)

    totals = list(raw.values())
    assert int(stat("Players")) == len(totals)
    assert float(stat("Average Score")) == round(sum(totals) / len(totals), 1)
    assert float(stat("High Score")) == max(totals)
    assert float(stat("Max Possible")) == 6.0
    counts = re.findall(r'<span class="hist-count">(\d*)</span>', report)
    assert sum(int(c) for c in counts if c) == len(totals)


async def test_abandoned_downloads_and_unplayed_questions(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("Reached question", "Never reached question"))
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        await ask_and_answer(host, player)
    finally:
        await host.disconnect()
        await player.disconnect()
    assert end(t4, a, sid).json() == {"outcome": "abandoned"}

    raw = httpx.get(f"{base}/api/host/sessions/{sid}/export?format=raw", headers=H(a)).text
    row = list(csv.reader(io.StringIO(raw)))[1]
    assert row[1] == "1.0" and row[2] == ""
    report = httpx.get(f"{base}/api/host/sessions/{sid}/report", headers=H(a)).text
    assert "Reached question" in report and "Never reached question" not in report

    # Canvas: the unasked question's "Points Possible" and score cells are blank, and the
    # single-column format counts only asked questions.
    per_q = httpx.get(
        f"{base}/api/host/sessions/{sid}/export",
        params={"format": "canvas", "per_question": "true", "roster_only": "false"},
        headers=H(a),
    ).text
    prow = list(csv.reader(io.StringIO(per_q)))
    assert prow[1] == ["Points Possible", "", "", "", "", "1.0", ""]
    assert prow[2][5:] == ["1.0", ""]
    single = httpx.get(
        f"{base}/api/host/sessions/{sid}/export",
        params={"format": "canvas", "roster_only": "false"},
        headers=H(a),
    ).text
    srow = list(csv.reader(io.StringIO(single)))
    assert srow[1][5] == "1.0" and srow[2][5] == "1.0"

    detail = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t4["player"])).json()
    assert detail["status"] == "ABANDONED"
    assert detail["questions"][1] == {"order_index": 1, "played": False}
    assert "Never reached question" not in json.dumps(detail)
    # Max score counts only the asked question (1 of 2), in the list and the detail.
    assert detail["max_score"] == 1.0 and detail["score"] == 1.0
    items = httpx.get(f"{base}/api/player/past-quizzes", headers=H(t4["player"])).json()
    assert [(p["score"], p["max_score"]) for p in items if p["session_id"] == sid] == [(1.0, 1.0)]


async def test_downloads_refused_until_session_finished(t4):
    """CSV/HTML downloads of a LOBBY or IN_PROGRESS session → 409 on every route."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)

    def downloads() -> list[int]:
        return [
            httpx.get(f"{base}{path}", headers=H(token), timeout=T).status_code
            for token in (a, t4["admin"])
            for path in (
                f"/api/host/sessions/{sid}/export",
                f"/api/host/sessions/{sid}/export?format=raw",
                f"/api/host/sessions/{sid}/export?format=canvas&per_question=true",
                f"/api/host/sessions/{sid}/report",
                f"/api/game/sessions/{sid}/export",
            )
        ]

    assert downloads() == [409] * 10  # LOBBY
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        await ask_and_answer(host, player)
        assert downloads() == [409] * 10  # IN_PROGRESS
    finally:
        await host.disconnect()
        await player.disconnect()
    assert end(t4, a, sid).json() == {"outcome": "abandoned"}
    assert downloads() == [200] * 10  # ABANDONED


async def test_game_over_max_score_comes_from_snapshot(t4):
    """A change to the live questions after start doesn't move the game-over max score.
    (The API refuses edits during a room, so the live row is changed directly.)"""
    base, a = t4["base"], t4["a"]
    gid, qids = mk_quiz(t4, a, t4["c1"], prompts=("M1", "M2"))
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        for _ in qids:
            await ask_and_answer(host, player)
            await host.emit("host_advance", {})
            await host.wait_for("question_results", EV)
        assert httpx.put(
            f"{base}/api/host/games/{gid}/questions/{qids[0]}",
            json={"points_value": 7},
            headers=H(a),
        ).status_code == 409
        sql(f"UPDATE questions SET points_value=7 WHERE id={qids[0]}")
        await host.emit("host_advance", {})
        over = await host.wait_for("game_over", EV)
        await player.wait_for("game_over", EV)
    finally:
        await host.disconnect()
        await player.disconnect()
    assert over["maxPossibleScore"] == 2
    items = httpx.get(f"{base}/api/player/past-quizzes", headers=H(t4["player"])).json()
    assert [p["max_score"] for p in items if p["session_id"] == sid] == [2.0]


async def test_guest_merge_is_starter_or_admin(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("only",))
    code, sid = start_room(t4, a, gid)
    guest = create_guest_tokens(base, code, 1)[0]
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, guest, code, "PLAYER", "guest")
    try:
        await ask_and_answer(host, player)
        await host.emit("host_advance", {})
        await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})
        await host.wait_for("game_over", EV)
    finally:
        await host.disconnect()
        await player.disconnect()

    for token, can in ((t4["b"], False), (a, True), (t4["admin"], True)):
        sessions = httpx.get(f"{base}/api/host/courses/{t4['c1']}/sessions", headers=H(token)).json()
        assert [s["can_merge_guests"] for s in sessions if s["session_id"] == sid] == [can]
    assert httpx.get(f"{base}/api/game/sessions/{sid}/guests", headers=H(t4["b"])).status_code == 403
    assert httpx.get(f"{base}/api/game/sessions/{sid}/guests", headers=H(a)).status_code == 200
    assert httpx.get(f"{base}/api/game/sessions/{sid}/guests", headers=H(t4["admin"])).status_code == 200


def netid_user(t4, on_roster: bool = True) -> tuple[str, str, str]:
    """A NetID user with an account (dev login), optionally on c1's roster.
    Returns (netid, token, user_id)."""
    base = t4["base"]
    netid = f"t4m{uuid.uuid4().hex[:6]}"
    if on_roster:
        r = httpx.post(
            f"{base}/api/host/courses/{t4['c1']}/roster/import",
            json={"rows": [{"netid": netid, "full_name": "Mia Merge", "email": f"{netid}@example.com"}]},
            headers=H(t4["a"]),
            timeout=T,
        )
        assert r.status_code == 200 and r.json()["imported"] == 1, r.text
    login = httpx.post(f"{base}/api/auth/login", json={"netid": netid}, timeout=T)
    assert login.status_code == 200, login.text
    uid = sql(f"SELECT id FROM users WHERE netid='{netid}'")
    return netid, login.json()["temp_token"], uid


async def play_one(t4, gid: int, player_tokens: list[str], code_sid=None) -> str:
    """One-question game on a fresh room where every player answers; returns session id."""
    base, a = t4["base"], t4["a"]
    code, sid = code_sid or start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    players = [await join(base, tok, code, "PLAYER", f"p{i}") for i, tok in enumerate(player_tokens)]
    try:
        await host.emit("host_advance", {})
        for p in players:
            q = await p.wait_for("new_question", EV)
            await p.emit("submit_answer", {"question_id": q["questionId"], "answer_data": {"selectedIndex": 0}})
            await p.wait_for("answer_received", EV)
        await host.emit("host_advance", {})
        await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})
        await host.wait_for("game_over", EV)
    finally:
        await host.disconnect()
        for p in players:
            await p.disconnect()
    return sid


def merge(t4, token, sid, guest_id, netid) -> httpx.Response:
    return httpx.post(
        f"{t4['base']}/api/game/sessions/{sid}/merge-guest",
        json={"guest_user_id": guest_id, "target_netid": netid},
        headers=H(token),
        timeout=T,
    )


def guest_id_in(t4, sid: str) -> str:
    guests = httpx.get(f"{t4['base']}/api/game/sessions/{sid}/guests", headers=H(t4["a"]), timeout=T).json()
    assert len(guests) == 1, guests
    return guests[0]["id"]


def scores_of(sid: str, uid: str) -> int:
    return int(sql(f"SELECT COUNT(*) FROM session_scores WHERE session_id='{sid}' AND user_id='{uid}'"))


async def test_guest_merge_moves_this_sessions_scores(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("only",))
    code, sid = start_room(t4, a, gid)
    guest = create_guest_tokens(base, code, 1)[0]
    await play_one(t4, gid, [guest], (code, sid))
    gid_user = guest_id_in(t4, sid)
    netid, _, uid = netid_user(t4)

    # Netid is normalised the way the roster stores it.
    r = merge(t4, a, sid, gid_user, f"  {netid.upper()} ")
    assert r.status_code == 204, r.text
    assert scores_of(sid, uid) == 1 and scores_of(sid, gid_user) == 0
    assert sql(f"SELECT COUNT(*) FROM users WHERE id='{gid_user}'") == "0"
    # A second merge of the same (now deleted) guest is 404.
    assert merge(t4, a, sid, gid_user, netid).status_code == 404


async def test_guest_merge_refusals(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("only",))
    netid, netid_token, uid = netid_user(t4)
    off_roster, _, _ = netid_user(t4, on_roster=False)

    code, sid = start_room(t4, a, gid)
    guest = create_guest_tokens(base, code, 1)[0]
    await play_one(t4, gid, [guest, netid_token], (code, sid))
    g = guest_id_in(t4, sid)

    code2, sid2 = start_room(t4, a, gid)
    other_guest = create_guest_tokens(base, code2, 1)[0]
    await play_one(t4, gid, [other_guest], (code2, sid2))
    g_other = guest_id_in(t4, sid2)

    # A guest from another session is 404 here, as is a non-guest.
    r = merge(t4, a, sid, g_other, netid)
    assert r.status_code == 404 and r.json()["message"], r.text
    assert merge(t4, a, sid, uid, netid).status_code == 404
    # A NetID that isn't on the course roster (even with an account) is refused.
    r = merge(t4, a, sid, g, off_roster)
    assert r.status_code == 404 and "roster" in r.json()["message"], r.text
    # A target who already answered in this session is 409.
    r = merge(t4, a, sid, g, netid)
    assert r.status_code == 409 and "already" in r.json()["message"], r.text
    # Nothing moved.
    assert scores_of(sid, g) == 1 and scores_of(sid2, g_other) == 1 and scores_of(sid, uid) == 1

    # The admin route keeps its pre-T4 behaviour: no session, all of the guest's scores move.
    admin_merge = f"{base}/api/admin/users/merge-guest"
    body = {"guest_user_id": g_other, "target_netid": netid}
    r = httpx.post(admin_merge, json=body, headers=H(t4["admin"]), timeout=T)
    assert r.status_code == 204, r.text
    assert scores_of(sid2, uid) == 1 and scores_of(sid2, g_other) == 0


async def test_guest_merge_keeps_other_sessions_scores(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("only",))
    code, sid = start_room(t4, a, gid)
    guest = create_guest_tokens(base, code, 1)[0]
    await play_one(t4, gid, [guest], (code, sid))
    sid2 = await play_one(t4, gid, [guest])  # the same guest plays a second session
    g = guest_id_in(t4, sid)
    assert guest_id_in(t4, sid2) == g
    netid, _, uid = netid_user(t4)

    assert merge(t4, a, sid, g, netid).status_code == 204
    assert scores_of(sid, uid) == 1 and scores_of(sid, g) == 0
    assert scores_of(sid2, g) == 1 and scores_of(sid2, uid) == 0
    assert sql(f"SELECT role FROM users WHERE id='{g}'") == "GUEST"


async def test_concurrent_merges_into_one_target(t4):
    """Two guests of one session merged into the same NetID at once: exactly one wins.
    Both requests are lined up behind a lock on the target's user row, so the loser reads
    the target's answers only after the winner committed them."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("only",))
    code, sid = start_room(t4, a, gid)
    guests = create_guest_tokens(base, code, 2)
    await play_one(t4, gid, guests, (code, sid))
    listed = httpx.get(f"{base}/api/game/sessions/{sid}/guests", headers=H(a), timeout=T).json()
    g1, g2 = [g["id"] for g in listed]
    netid, _, uid = netid_user(t4)

    async with httpx.AsyncClient(timeout=T) as client:

        async def merge_async(guest_id: str) -> httpx.Response:
            return await client.post(
                f"{base}/api/game/sessions/{sid}/merge-guest",
                json={"guest_user_id": guest_id, "target_netid": netid},
                headers=H(a),
            )

        async with GameRowLock(uid, table="users") as lock:
            pending = asyncio.gather(merge_async(g1), merge_async(g2))
            await lock.wait_for_waiters(2)
        results = await pending

    assert sorted(r.status_code for r in results) == [204, 409], [r.text for r in results]
    assert scores_of(sid, uid) == 1
    assert scores_of(sid, g1) + scores_of(sid, g2) == 1


async def test_host_guest_merge_refused_until_session_finished(t4):
    """The host guest list and merge are 409 in LOBBY and IN_PROGRESS; a bad body is 422."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("only",))
    code, sid = start_room(t4, a, gid)
    guest = create_guest_tokens(base, code, 1)[0]
    netid, _, uid = netid_user(t4)
    guests_url = f"{base}/api/game/sessions/{sid}/guests"

    assert httpx.get(guests_url, headers=H(a), timeout=T).status_code == 409  # LOBBY
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, guest, code, "PLAYER", "guest")
    try:
        await ask_and_answer(host, player)
        g = sql(
            f"SELECT u.id FROM users u JOIN session_scores s ON s.user_id=u.id "
            f"WHERE s.session_id='{sid}' AND u.role='GUEST'"
        )
        for token in (a, t4["admin"]):  # IN_PROGRESS
            assert httpx.get(guests_url, headers=H(token), timeout=T).status_code == 409
            r = merge(t4, token, sid, g, netid)
            assert r.status_code == 409 and "finished" in r.json()["message"], r.text
        assert scores_of(sid, g) == 1 and scores_of(sid, uid) == 0
    finally:
        await host.disconnect()
        await player.disconnect()
    assert end(t4, a, sid).json() == {"outcome": "abandoned"}

    url = f"{base}/api/game/sessions/{sid}/merge-guest"
    for body in ({"guest_user_id": None, "target_netid": netid}, {"guest_user_id": g}):
        assert httpx.post(url, json=body, headers=H(a), timeout=T).status_code == 422
    assert guest_id_in(t4, sid) == g
    assert merge(t4, a, sid, g, netid).status_code == 204
    assert scores_of(sid, uid) == 1


async def test_rejoin_as_new_player_notifies_host(t4):
    """A player whose first contact is rejoin_room (e.g. a reload before join landed) shows
    up on the host's screen like a join_room player does."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = TestSocketClient(base, t4["player"], "player-rejoin")
    try:
        await player.connect()
        await player.emit("rejoin_room", {"room_code": code, "role": "PLAYER"})
        assert (await player.wait_for("sync_state", EV))["role"] == "PLAYER"
        joined = await host.wait_for("player_joined", EV)
        assert joined["userId"] == t4["player_id"] and joined["playerCount"] == 1
    finally:
        await host.disconnect()
        await player.disconnect()
        end(t4, a, sid)


async def test_rejoin_room_keeps_the_claimed_role(t4):
    """rejoin_room applies join_room's per-role check: an admin who joined as a player stays
    a player (with role PLAYER, or with no role), and only the starter or an admin can rejoin
    as HOST."""
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"])
    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    admin_player = await join(base, t4["admin"], code, "PLAYER", "admin-player")
    clients = [host, admin_player]
    try:
        for payload in ({"room_code": code, "role": "PLAYER"}, {"room_code": code}):
            c = TestSocketClient(base, t4["admin"], "admin-rejoin")
            clients.append(c)
            await c.connect()
            await c.emit("rejoin_room", payload)
            assert (await c.wait_for("sync_state", EV))["role"] == "PLAYER"
            await c.emit("host_advance", {})
            with pytest.raises(asyncio.TimeoutError):
                await host.wait_for("new_question", 1.5)

        # A course host who didn't start the room can't claim HOST on rejoin.
        b = TestSocketClient(base, t4["b"], "b-rejoin")
        clients.append(b)
        await b.connect()
        await b.emit("rejoin_room", {"room_code": code, "role": "HOST"})
        assert "not authorised" in (await b.wait_for("error", EV))["message"].lower()

        # The starter rejoining with role HOST is still the host and drives the game.
        new_host = TestSocketClient(base, a, "host-rejoin")
        clients.append(new_host)
        await new_host.connect()
        await new_host.emit("rejoin_room", {"room_code": code, "role": "HOST"})
        assert (await new_host.wait_for("sync_state", EV))["role"] == "HOST"
        await new_host.emit("host_advance", {})
        await new_host.wait_for("new_question", EV)
    finally:
        for c in clients:
            await c.disconnect()
        end(t4, a, sid)


async def test_past_quizzes_visibility(t4):
    base, a = t4["base"], t4["a"]
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("P1",))
    sid = await play_full(t4, a, t4["player"], gid, 1)
    mine = httpx.get(f"{base}/api/player/past-quizzes", headers=H(t4["player"])).json()
    assert sid in [p["session_id"] for p in mine]
    # Another *player* of the same course who didn't play it can't see or read it.
    other = httpx.get(f"{base}/api/player/past-quizzes", headers=H(t4["player2"]))
    assert other.status_code == 200 and sid not in [p["session_id"] for p in other.json()]
    r = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t4["player2"]))
    assert r.status_code == 404
    # Nor can a host of the course (Past Quizzes is only the caller's own attempts).
    assert httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t4["b"])).status_code == 404

    # A live IN_PROGRESS session is never readable, even by a player in it.
    code, live_sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        await ask_and_answer(host, player)
        r = httpx.get(f"{base}/api/player/past-quizzes/{live_sid}", headers=H(t4["player"]))
        assert r.status_code == 404
    finally:
        await host.disconnect()
        await player.disconnect()
        end(t4, a, live_sid)



async def test_partial_credit_is_not_correct(t4):
    """Backend grading: a multi_select answer worth some but not all points is isCorrect
    false in the game-over questionSummary and in Past Quizzes; a full-points answer is true.
    (The player recap still shows any positive points as correct; that is display only.)"""
    base, a = t4["base"], t4["a"]
    r = httpx.post(
        f"{base}/api/host/courses/{t4['c1']}/games",
        json={"title": f"T4 partial {uuid.uuid4().hex[:6]}"},
        headers=H(a),
        timeout=T,
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    t4["created"].append(gid)
    multi = {
        "type": "multi_select",
        "grading_type": "ACCURACY",
        "prompt": "Pick both",
        "config": {"options": ["A", "B", "C"]},
        "answer_data": {"answer_points": [1, 1, 0]},
        "points_value": 2,
        "time_limit_seconds": 30,
    }
    for q in (multi, {**multi, "prompt": "Pick both again"}):
        r = httpx.post(f"{base}/api/host/games/{gid}/questions", json=q, headers=H(a), timeout=T)
        assert r.status_code == 201, r.text

    code, sid = start_room(t4, a, gid)
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        for picks in ([0], [0, 1]):  # partial (1 of 2), then full
            await host.emit("host_advance", {})
            q = await player.wait_for("new_question", EV)
            await host.wait_for("new_question", EV)
            await player.emit(
                "submit_answer",
                {"question_id": q["questionId"], "answer_data": {"selectedIndices": picks}},
            )
            await player.wait_for("answer_received", EV)
            await host.emit("host_advance", {})
            await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})
        over = await player.wait_for("game_over", EV)
    finally:
        await host.disconnect()
        await player.disconnect()

    summary = over["questionSummary"]
    assert [(s["pointsAwarded"], s["maxPoints"], s["isCorrect"]) for s in summary] == [
        (1, 2, False),
        (2, 2, True),
    ]
    detail = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t4["player"])).json()
    partial, full = detail["questions"]
    assert partial["isCorrect"] is False and 0 < partial["pointsAwarded"] < partial["maxPoints"]
    assert full["isCorrect"] is True and full["pointsAwarded"] == full["maxPoints"]


async def test_non_latin_title_downloads(t4):
    """A quiz titled in CJK exports and downloads (ASCII-safe Content-Disposition names)."""
    base, a = t4["base"], t4["a"]
    title = "测验 クイズ 퀴즈"
    gid, _ = mk_quiz(t4, a, t4["c1"], prompts=("CJK Q",))
    r = httpx.put(f"{base}/api/host/games/{gid}", json={"title": title}, headers=H(a), timeout=T)
    assert r.status_code == 200, r.text
    sid = await play_full(t4, a, t4["player"], gid, 1)

    for path, params in (
        (f"/api/host/games/{gid}/export", {}),
        (f"/api/admin/games/{gid}/export", {}),
        (f"/api/host/sessions/{sid}/export", {"format": "canvas", "roster_only": "false"}),
        (f"/api/host/sessions/{sid}/export", {"format": "canvas", "title": title}),
        (f"/api/host/sessions/{sid}/export", {"format": "raw"}),
        (f"/api/host/sessions/{sid}/report", {}),
        (f"/api/game/sessions/{sid}/export", {}),
    ):
        token = t4["admin"] if path.startswith("/api/admin") else a
        resp = httpx.get(f"{base}{path}", params=params, headers=H(token), timeout=T)
        assert resp.status_code == 200, (path, params, resp.text)
        disposition = resp.headers["content-disposition"]
        assert disposition.isascii() and 'filename="' in disposition, disposition
    bundle = httpx.get(f"{base}/api/host/games/{gid}/export", headers=H(a), timeout=T).json()
    assert bundle["game"]["title"] == title


def test_guest_has_no_past_quizzes(t4):
    base = t4["base"]
    gid, _ = mk_quiz(t4, t4["a"], t4["c1"])
    code, sid = start_room(t4, t4["a"], gid)
    guest = create_guest_tokens(base, code, 1)[0]
    assert httpx.get(f"{base}/api/player/past-quizzes", headers=H(guest)).status_code == 403
    end(t4, t4["a"], sid)


# ---------------------------------------------------------------------------
# Roster and import
# ---------------------------------------------------------------------------


async def test_roster_upload_and_deactivate(t4):
    base, a, c1 = t4["base"], t4["a"], t4["c1"]
    netid = f"t4n{uuid.uuid4().hex[:6]}"
    r = httpx.post(
        f"{base}/api/host/courses/{c1}/roster/import",
        json={"rows": [{"netid": netid, "full_name": "Nora Net", "email": f"{netid}@example.com"}]},
        headers=H(a),
    )
    assert r.status_code == 200 and r.json()["imported"] == 1, r.text
    entry = [e for e in httpx.get(f"{base}/api/host/courses/{c1}/roster", headers=H(a)).json() if e["netid"] == netid][0]

    # Dev-mode NetID login returns a temp token, which sockets accept like the OAuth flow.
    login = httpx.post(f"{base}/api/auth/login", json={"netid": netid}, timeout=T)
    assert login.status_code == 200, login.text
    netid_token = login.json()["temp_token"]

    gid, _ = mk_quiz(t4, a, c1)
    code, sid = start_room(t4, a, gid)
    ok = await join(base, netid_token, code, "PLAYER", "netid")
    await ok.disconnect()

    assert httpx.patch(
        f"{base}/api/host/courses/{c1}/roster/{entry['id']}", json={"is_active": False}, headers=H(a)
    ).status_code == 200
    blocked = TestSocketClient(base, netid_token, "netid-blocked")
    await blocked.connect()
    try:
        await blocked.emit("join_room", {"room_code": code, "role": "PLAYER"})
        err = await blocked.wait_for("error", EV)
        assert "roster" in err["message"].lower()
    finally:
        await blocked.disconnect()
        end(t4, a, sid)


def test_roster_patch_to_existing_netid_is_409(t4):
    base, c1 = t4["base"], t4["c1"]
    n1, n2 = f"t4d{uuid.uuid4().hex[:6]}", f"t4d{uuid.uuid4().hex[:6]}"
    r = httpx.post(
        f"{base}/api/host/courses/{c1}/roster/import",
        json={"rows": [
            {"netid": n, "full_name": n, "email": f"{n}@example.com"} for n in (n1, n2)
        ]},
        headers=H(t4["a"]),
    )
    assert r.status_code == 200, r.text
    second = [e for e in httpx.get(f"{base}/api/host/courses/{c1}/roster", headers=H(t4["a"])).json()
              if e["netid"] == n2][0]
    for prefix, token in (("/api/host", t4["a"]), ("/api/admin", t4["admin"])):
        for netid in (n1, f"  {n1.upper()} "):  # exact, and equal after normalising
            r = httpx.patch(
                f"{base}{prefix}/courses/{c1}/roster/{second['id']}",
                json={"netid": netid},
                headers=H(token),
            )
            assert r.status_code == 409, (prefix, netid, r.status_code, r.text)
    roster = httpx.get(f"{base}/api/host/courses/{c1}/roster", headers=H(t4["a"])).json()
    assert sorted(e["netid"] for e in roster if e["netid"] in (n1, n2)) == sorted([n1, n2])


def test_import_sample_file_into_course(t4):
    base = t4["base"]
    sample = pathlib.Path(_REPO_ROOT, "sample_games", "cs_first_day.json")
    files = {"file": ("cs_first_day.json", sample.read_bytes(), "application/json")}
    r = httpx.post(f"{base}/api/host/courses/{t4['c1']}/games/import", files=files, headers=H(t4["a"]))
    assert r.status_code == 201, r.text
    t4["created"].append(r.json()["game_id"])
    game = httpx.get(f"{base}/api/host/games/{r.json()['game_id']}", headers=H(t4["a"])).json()
    assert game["course_id"] == t4["c1"]
    assert game["question_count"] == len(json.loads(sample.read_text())["questions"])
    r = httpx.post(f"{base}/api/host/courses/{t4['c2']}/games/import", files=files, headers=H(t4["a"]))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Test cleanup safety
# ---------------------------------------------------------------------------


def test_cleanup_script_refuses_non_test_course(t4, base_url, admin_token):
    from .conftest import run_backend_script

    r = httpx.post(
        f"{base_url}/api/admin/courses",
        json={"name": f"Real-looking course {uuid.uuid4().hex[:6]}", "semester": "Test"},
        headers=H(admin_token),
    )
    cid = r.json()["id"]
    r = httpx.post(
        f"{base_url}/api/host/courses/{cid}/games", json={"title": "keep me"}, headers=H(admin_token)
    )
    gid = r.json()["id"]
    res = run_backend_script("scripts.cleanup_test_quiz", "--game-id", str(gid))
    assert res.returncode == 3, res.stdout + res.stderr
    assert httpx.get(f"{base_url}/api/host/games/{gid}", headers=H(admin_token)).status_code == 200
    # Leave it archived (quizzes can't be deleted).
    httpx.post(f"{base_url}/api/host/games/{gid}/archive", headers=H(admin_token))


def test_game_setup_refuses_non_local_stack(tmp_path):
    """`game_setup` skips before anything is created or cleaned when the base URL isn't a
    local stack and BUZZER_TEST_COMPOSE is unset. Runs a nested pytest on a copy of the
    conftest with a stub `docker` on PATH that records any call."""
    here = pathlib.Path(__file__).parent
    (tmp_path / "conftest.py").write_text((here / "conftest.py").read_text())
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_nested.py").write_text("def test_uses_game_setup(game_setup):\n    pass\n")
    calls = tmp_path / "docker_calls.log"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "docker"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {calls}\nexit 1\n')
    stub.chmod(0o755)

    env = {k: v for k, v in os.environ.items() if k != "BUZZER_TEST_COMPOSE"}
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    res = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
            "--rootdir", str(tmp_path), str(tmp_path / "test_nested.py"),
            "--base-url", "http://example.invalid:8000",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "1 skipped" in out and "not a local stack" in out, out
    assert not calls.exists(), calls.read_text()  # docker was never invoked


async def test_cleanup_removes_quiz_and_redis_but_keeps_course_and_users(t4):
    base, a, c1 = t4["base"], t4["a"], t4["c1"]
    gid, qids = mk_quiz(t4, a, c1)
    done_sid = await play_full(t4, a, t4["player"], gid, 2)  # a finished session
    code, sid = start_room(t4, a, gid)  # plus a live one holding Redis state
    host = await join(base, a, code, "HOST", "host")
    player = await join(base, t4["player"], code, "PLAYER", "player")
    try:
        await ask_and_answer(host, player)
        assert room_key_exists(code) and session_keys(sid)
        # Every session:{id}:* key carries the 24h backstop TTL.
        for key in session_keys(sid):
            assert 0 < int(redis_cli("TTL", key)) <= 24 * 60 * 60, key

        cleanup_test_quiz(gid)

        assert sql(f"SELECT COUNT(*) FROM games WHERE id={gid}") == "0"
        assert sql(f"SELECT COUNT(*) FROM questions WHERE game_id={gid}") == "0"
        assert sql(f"SELECT COUNT(*) FROM game_sessions WHERE game_id={gid}") == "0"
        for s in (sid, done_sid):
            assert sql(f"SELECT COUNT(*) FROM session_scores WHERE session_id='{s}'") == "0"
            assert sql(f"SELECT COUNT(*) FROM session_questions WHERE session_id='{s}'") == "0"
        assert not room_key_exists(code)
        assert session_keys(sid) == [] and session_keys(done_sid) == []
    finally:
        await host.disconnect()
        await player.disconnect()

    # The course, its access grants, and the users are untouched.
    assert sql(f"SELECT COUNT(*) FROM courses WHERE id={c1}") == "1"
    assert httpx.get(f"{base}/api/host/courses/{c1}/games", headers=H(a)).status_code == 200
    ids = "','".join([t4["a_id"], t4["player_id"]])
    assert sql(f"SELECT COUNT(*) FROM users WHERE id IN ('{ids}')") == "2"
    assert httpx.get(f"{base}/api/player/past-quizzes", headers=H(t4["player"])).status_code == 200
    # Disconnect handlers that ran after the cleanup recreated nothing.
    assert not await eventually(lambda: room_key_exists(code) or session_keys(sid), 1.0)
    # Idempotent: the fixture teardown's second run is a no-op.
    cleanup_test_quiz(gid)
