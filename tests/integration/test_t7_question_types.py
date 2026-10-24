"""
T7 integration tests: the pin_drop and closest_guess question types
(docs/plans/t7-new-question-types.md §10).

Runs against the live Docker stack like the rest of the suite. Every quiz a test creates
lives in a fresh `Test Course <hex>` and is removed afterwards by the test-only
`scripts.cleanup_test_quiz` (the API has no whole-quiz delete).
"""

from __future__ import annotations

import csv
import io
import json
import uuid

import httpx
import pytest

from .conftest import cleanup_test_quiz, create_guest_tokens
from .engine.socket_client import TestSocketClient
from .test_t4_host_admin import (
    EV,
    H,
    T,
    _grant,
    _mk_course,
    eventually,
    join,
    next_of,
    redis_cli,
    sql,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def t7(base_url, admin_token, t4_users):
    """A fresh test course; the shared signed-in player is granted PLAYER on it so Past
    Quizzes can be checked. The admin acts as host (admins are HOST on every course)."""
    course = _mk_course(base_url, admin_token)
    player = t4_users["t4_player"]
    _grant(base_url, admin_token, player["id"], course, "PLAYER")
    created: list[int] = []
    yield {
        "base": base_url,
        "admin": admin_token,
        "course": course,
        "player": player["token"],
        "created": created,
    }
    for gid in created:
        cleanup_test_quiz(gid)


def pin(target=(3, -2), grading="ACCURACY", points=5.0, **over) -> dict:
    q = {
        "type": "pin_drop",
        "grading_type": grading,
        "prompt": "Mark the point",
        "config": {},
        "answer_data": {"target": {"x": target[0], "y": target[1]}}
        if grading == "ACCURACY"
        else {},
        "points_value": points,
        "time_limit_seconds": 60,
    }
    return {**q, **over}


def guess(correct=330, full=10, partial=50, grading="ACCURACY", points=10.0, unit="m", **over) -> dict:
    q = {
        "type": "closest_guess",
        "grading_type": grading,
        "prompt": "How tall is it?",
        "config": {"unit": unit} if unit else {},
        "answer_data": {"correct": correct, "fullTolerance": full, "partialTolerance": partial}
        if grading == "ACCURACY"
        else {},
        "points_value": points,
        "time_limit_seconds": 60,
    }
    return {**q, **over}


def mk_quiz(t7, questions: list[dict]) -> tuple[int, list[int]]:
    base = t7["base"]
    r = httpx.post(
        f"{base}/api/host/courses/{t7['course']}/games",
        json={"title": f"T7 quiz {uuid.uuid4().hex[:6]}"},
        headers=H(t7["admin"]),
        timeout=T,
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    t7["created"].append(gid)
    qids = []
    for q in questions:
        r = httpx.post(f"{base}/api/host/games/{gid}/questions", json=q, headers=H(t7["admin"]), timeout=T)
        assert r.status_code == 201, r.text
        qids.append(r.json()["id"])
    return gid, qids


def create_q(t7, gid: int, body: dict) -> httpx.Response:
    return httpx.post(f"{t7['base']}/api/host/games/{gid}/questions", json=body, headers=H(t7["admin"]), timeout=T)


def update_q(t7, gid: int, qid: int, body: dict) -> httpx.Response:
    return httpx.put(
        f"{t7['base']}/api/host/games/{gid}/questions/{qid}", json=body, headers=H(t7["admin"]), timeout=T
    )


def get_q(t7, gid: int, qid: int) -> dict:
    r = httpx.get(f"{t7['base']}/api/host/games/{gid}/questions", headers=H(t7["admin"]), timeout=T)
    assert r.status_code == 200, r.text
    return next(q for q in r.json() if q["id"] == qid)


def scores_for(sid: str, qid: int) -> int:
    return int(sql(f"SELECT COUNT(*) FROM session_scores WHERE session_id='{sid}' AND question_id={qid}"))


def open_room(t7, gid: int) -> tuple[str, str]:
    r = httpx.post(f"{t7['base']}/api/game/rooms", json={"game_id": gid}, headers=H(t7["admin"]), timeout=T)
    assert r.status_code == 201, r.text
    return r.json()["room_code"], r.json()["session_id"]


async def play(t7, gid: int, answers: list[list], *, signed_in_player: bool = False) -> dict:
    """Run a whole game. answers[q][p] is player p's answer_data for question q (None = no
    answer). Players are guests created for this room; with `signed_in_player`, player 0 is
    the shared signed-in player instead (for Past Quizzes). Returns per-question player
    replies, the host's live results, both game-over payloads and the session id."""
    base = t7["base"]
    code, sid = open_room(t7, gid)
    n = len(answers[0])
    players = create_guest_tokens(base, code, n - 1 if signed_in_player else n)
    if signed_in_player:
        players = [t7["player"], *players]
    host = await join(base, t7["admin"], code, "HOST", "host")
    clients: list[TestSocketClient] = []
    for i, tok in enumerate(players):
        clients.append(await join(base, tok, code, "PLAYER", f"p{i}"))
    out: dict = {"sid": sid, "replies": [], "results": [], "qids": []}
    try:
        for per_player in answers:
            await host.emit("host_advance", {})
            qid = (await host.wait_for("new_question", EV))["questionId"]
            out["qids"].append(qid)
            replies = []
            for c, ans in zip(clients, per_player):
                await c.wait_for("new_question", EV)
                if ans is None:
                    replies.append(None)
                    continue
                await c.emit("submit_answer", {"question_id": qid, "answer_data": ans})
                replies.append(await next_of(c, ("answer_received", "error")))
            out["replies"].append(replies)
            await host.emit("host_advance", {})  # -> RESULTS
            out["results"].append(await host.wait_for("question_results", EV))
            for c in clients:
                await c.wait_for("question_results", EV)
        await host.emit("host_advance", {})  # -> GAME_OVER
        out["host_over"] = await host.wait_for("game_over", EV)
        out["player_over"] = [await c.wait_for("game_over", EV) for c in clients]
    finally:
        await host.disconnect()
        for c in clients:
            await c.disconnect()
    return out


def awarded(reply) -> float:
    event, payload = reply
    assert event == "answer_received", f"expected a scored answer, got {event}: {payload}"
    return payload["pointsAwarded"]


def rejected(reply) -> bool:
    return reply is not None and reply[0] == "error"


# ---------------------------------------------------------------------------
# 1. Authoring: create, export, import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pin(target=(6, 0)),
        pin(answer_data={"target": {"x": 1.0, "y": 0}}),
        pin(answer_data={"target": {"x": True, "y": 0}}),
        pin(config={"imageId": 1}),
        pin(points=0),
        pin(grading="COMPLETENESS", answer_data={"target": {"x": 0, "y": 0}}),
        guess(full=60, partial=50),
        guess(full=-1),
        guess(correct=True),
        guess(correct=1e13),
        guess(unit="x" * 21),
        guess(unit=" m"),
        guess(config={"unit": ""}),
        guess(config={"unit": "m", "extra": 1}),
        guess(points=0),
        guess(grading="COMPLETENESS", answer_data={"correct": 1, "fullTolerance": 0, "partialTolerance": 0}),
    ],
    ids=[
        "pin-out-of-range", "pin-float", "pin-bool", "pin-config-key", "pin-zero-points",
        "pin-completeness-with-target", "guess-P<F", "guess-negative-F", "guess-bool",
        "guess-too-large", "guess-unit-too-long", "guess-unit-spaces", "guess-unit-empty",
        "guess-config-key", "guess-zero-points", "guess-completeness-with-data",
    ],
)
async def test_create_rejects_invalid_shapes(t7, body):
    gid, _ = mk_quiz(t7, [])
    r = create_q(t7, gid, body)
    assert r.status_code == 422, r.text


async def test_create_export_import_roundtrip(t7):
    questions = [
        pin(),
        pin(grading="COMPLETENESS", points=2),
        guess(),
        guess(grading="COMPLETENESS", unit=None),
        guess(correct=-40, full=0, partial=0, unit=None),
    ]
    gid, _ = mk_quiz(t7, questions)
    r = httpx.get(f"{t7['base']}/api/host/games/{gid}/export", headers=H(t7["admin"]), timeout=T)
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["version"] == 1
    assert [q["type"] for q in bundle["questions"]] == [q["type"] for q in questions]

    r = httpx.post(
        f"{t7['base']}/api/host/courses/{t7['course']}/games/import",
        files={"file": ("quiz.json", json.dumps(bundle).encode(), "application/json")},
        headers=H(t7["admin"]),
        timeout=T,
    )
    assert r.status_code == 201, r.text
    new_gid = r.json()["game_id"]
    t7["created"].append(new_gid)
    r = httpx.get(f"{t7['base']}/api/host/games/{new_gid}/questions", headers=H(t7["admin"]), timeout=T)
    imported = r.json()
    for want, got in zip(questions, imported):
        for key in ("type", "grading_type", "config", "answer_data", "points_value"):
            assert got[key] == want[key], (key, got, want)


# ---------------------------------------------------------------------------
# 2. Update validation (design §5.2, scoped to the new types)
# ---------------------------------------------------------------------------


async def test_update_validation_for_new_types(t7):
    gid, (pid, gq) = mk_quiz(t7, [pin(), guess()])
    before = get_q(t7, gid, pid)

    # Partial update with a bad target: 422 with a readable string detail, nothing saved.
    r = update_q(t7, gid, pid, {"answer_data": {"target": {"x": 9, "y": 0}}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail.startswith("Question invalid: ")
    assert "Value error, " not in detail and "target.x" in detail
    assert get_q(t7, gid, pid)["answer_data"] == before["answer_data"]

    # A valid partial update still works; omitted fields keep their stored values.
    r = update_q(t7, gid, pid, {"prompt": "Mark the vertex"})
    assert r.status_code == 200, r.text
    assert get_q(t7, gid, pid)["answer_data"] == before["answer_data"]

    # Grading-only change: the stored target no longer fits COMPLETENESS ...
    assert update_q(t7, gid, pid, {"grading_type": "COMPLETENESS"}).status_code == 422
    # ... and succeeds when the matching answer_data is sent with it.
    r = update_q(t7, gid, pid, {"grading_type": "COMPLETENESS", "answer_data": {}})
    assert r.status_code == 200, r.text

    # Turning a new-type question into an old type is checked too (stored type is new).
    r = update_q(t7, gid, gq, {"type": "multiple_choice"})
    assert r.status_code == 422
    assert "options" in r.json()["detail"]

    # Closest Guess rules apply on update as on create.
    r = update_q(t7, gid, gq, {"answer_data": {"correct": 1, "fullTolerance": 5, "partialTolerance": 2}})
    assert r.status_code == 422


async def test_update_of_existing_types_is_unchanged(t7):
    """Regression check: T7 scopes update validation to the new types, so the four original
    types keep their pre-T7 update behaviour (gap C1 stays deferred, design §9)."""
    mc = {
        "type": "multiple_choice",
        "grading_type": "ACCURACY",
        "prompt": "Pick A",
        "config": {"options": ["A", "B"]},
        "answer_data": {"answer_points": [1, 0]},
        "points_value": 1,
    }
    gid, (qid,) = mk_quiz(t7, [mc])
    assert update_q(t7, gid, qid, {"prompt": "Pick B"}).status_code == 200
    # Pre-T7 behaviour (C1): a partial update to an existing type is not structure-checked.
    assert update_q(t7, gid, qid, {"answer_data": {"answer_points": [1]}}).status_code == 200


# ---------------------------------------------------------------------------
# 3-6. Live games
# ---------------------------------------------------------------------------


async def test_pin_drop_live_game_and_results(t7):
    gid, _ = mk_quiz(t7, [pin(target=(3, -2), points=5), pin(grading="COMPLETENESS", points=2)])
    out = await play(
        t7,
        gid,
        [
            # ACCURACY: exact hit, miss, out of range, float coordinate
            [{"x": 3, "y": -2}, {"x": 3, "y": -1}, {"x": 6, "y": 0}, {"x": 1.0, "y": 0}],
            # COMPLETENESS: any valid point scores; bool and missing key are rejected
            [{"x": 0, "y": 0}, {"x": -5, "y": 5}, {"x": True, "y": 0}, {"x": 1}],
        ],
        signed_in_player=True,
    )
    sid, (q1, q2) = out["sid"], out["qids"]
    r1, r2 = out["replies"]

    assert awarded(r1[0]) == 5 and r1[0][1]["isCorrect"] is True
    assert awarded(r1[1]) == 0 and r1[1][1]["isCorrect"] is False
    assert rejected(r1[2]) and rejected(r1[3])
    assert awarded(r2[0]) == 2 and awarded(r2[1]) == 2
    assert rejected(r2[2]) and rejected(r2[3])
    # Rejected answers write no score row.
    assert scores_for(sid, q1) == 2 and scores_for(sid, q2) == 2

    res1, res2 = out["results"]
    assert res1["answerReveal"] == {"type": "pin_drop", "target": {"x": 3, "y": -2}}
    assert res1["answerDistribution"] == {"3,-2": 1, "3,-1": 1}
    assert res2["answerReveal"] == {"type": "completeness"}
    assert res2["answerDistribution"] == {"0,0": 1, "-5,5": 1}

    # Game-over summary is rebuilt from the snapshot and must agree with the live counts.
    summary = out["host_over"]["questionSummary"]
    assert summary[0]["answerDistribution"] == res1["answerDistribution"]
    assert summary[1]["answerDistribution"] == res2["answerDistribution"]

    # Past Quizzes (signed-in player).
    r = httpx.get(f"{t7['base']}/api/player/past-quizzes/{sid}", headers=H(t7["player"]), timeout=T)
    assert r.status_code == 200, r.text
    detail = r.json()["questions"]
    assert detail[0]["playerAnswer"] == {"x": 3, "y": -2}
    assert detail[0]["answerReveal"] == {"type": "pin_drop", "target": {"x": 3, "y": -2}}
    assert detail[1]["answerReveal"] == {"type": "completeness"}

    # HTML report: type badge and the ACCURACY summary line.
    r = httpx.get(f"{t7['base']}/api/host/sessions/{sid}/report", headers=H(t7["admin"]), timeout=T)
    assert r.status_code == 200
    assert "Pin Drop" in r.text and "Target (3, -2) · 1 correct" in r.text


async def test_closest_guess_live_game_and_results(t7):
    gid, _ = mk_quiz(
        t7,
        [
            guess(correct=330, full=10, partial=50, points=10),  # Q1 bands
            guess(correct=0, full=2, partial=2, points=10, unit=None),  # Q2 F == P
            guess(correct=-40, full=2, partial=10, points=10, unit=None),  # Q3 negative
            guess(correct=0, full=0, partial=10, points=10, unit=None),  # Q4 truncates to 0
            guess(correct=0, full=0, partial=1e12, points=10, unit=None),  # Q5 cap
            guess(grading="COMPLETENESS", points=3),  # Q6
        ],
    )
    out = await play(
        t7,
        gid,
        [
            [{"value": 340}, {"value": 350}, {"value": 380}, {"value": "abc"}],
            [{"value": 2}, {"value": 2.5}, {"value": True}, {"value": float("nan")}],
            [{"value": -45}, {"value": -40}, {"value": 1e13}, None],
            [{"value": 9.9995}, None, None, None],
            [{"value": 1e-8}, None, None, None],
            [{"value": 5}, {"value": "x"}, None, None],
        ],
    )
    q1, q2, q3, q4, q5, q6 = out["replies"]

    # Q1: e = F -> full; between -> linear partial; e = P -> 0; non-number rejected.
    assert awarded(q1[0]) == 10 and q1[0][1]["isCorrect"] is True
    assert awarded(q1[1]) == 7.5 and q1[1][1]["isCorrect"] is False
    assert awarded(q1[2]) == 0
    assert rejected(q1[3])
    # Q2: equal tolerances are a step; boolean and NaN are rejected.
    assert awarded(q2[0]) == 10 and awarded(q2[1]) == 0
    assert rejected(q2[2]) and rejected(q2[3])
    # Q3: negative correct value; over-limit value rejected.
    assert awarded(q3[0]) == 6.25 and awarded(q3[1]) == 10
    assert rejected(q3[2])
    # Q4: partial credit that truncates to 0 is a miss. Q5: capped one cent below full.
    assert awarded(q4[0]) == 0
    assert awarded(q5[0]) == 9.99 and q5[0][1]["isCorrect"] is False
    # Q6: COMPLETENESS scores any valid number, still rejects a non-number.
    assert awarded(q6[0]) == 3 and rejected(q6[1])

    res = out["results"]
    assert res[0]["answerDistribution"] == {"full": 1, "partial": 1, "miss": 1}
    assert res[0]["answerReveal"] == {
        "type": "closest_guess", "correct": 330, "fullTolerance": 10, "partialTolerance": 50, "unit": "m",
    }
    assert res[3]["answerDistribution"] == {"miss": 1}
    assert res[4]["answerDistribution"] == {"partial": 1}
    assert res[5]["answerDistribution"] == {} and res[5]["answerReveal"] == {"type": "completeness"}
    summary = out["host_over"]["questionSummary"]
    for live, over in zip(res, summary):
        assert over["answerDistribution"] == live["answerDistribution"]

    # CSV keeps the fractional award; HTML report has the band summary.
    sid = out["sid"]
    r = httpx.get(
        f"{t7['base']}/api/host/sessions/{sid}/export", params={"format": "raw"}, headers=H(t7["admin"]), timeout=T
    )
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    assert any("7.5" in row for row in rows[1:]), rows
    r = httpx.get(f"{t7['base']}/api/host/sessions/{sid}/report", headers=H(t7["admin"]), timeout=T)
    assert "Closest Guess" in r.text
    assert "Correct 330 m · full 1 · partial 1 · miss 1" in r.text


# ---------------------------------------------------------------------------
# 6. Existing behaviour, recorded (not changed by T7)
# ---------------------------------------------------------------------------


async def test_second_submit_and_submit_after_lock(t7):
    gid, _ = mk_quiz(t7, [pin()])
    code, _ = open_room(t7, gid)
    host = await join(t7["base"], t7["admin"], code, "HOST", "host")
    a_tok, b_tok = create_guest_tokens(t7["base"], code, 2)
    a = await join(t7["base"], a_tok, code, "PLAYER", "a")
    b = await join(t7["base"], b_tok, code, "PLAYER", "b")
    try:
        await host.emit("host_advance", {})
        qid = (await a.wait_for("new_question", EV))["questionId"]
        await b.wait_for("new_question", EV)
        await a.emit("submit_answer", {"question_id": qid, "answer_data": {"x": 3, "y": -2}})
        assert (await a.wait_for("answer_received", EV))["pointsAwarded"] == 5
        # A second sequential submit is acknowledged but not scored again.
        await a.emit("submit_answer", {"question_id": qid, "answer_data": {"x": 0, "y": 0}})
        assert (await a.wait_for("answer_received", EV)).get("alreadyAnswered") is True
        # After the host locks the question, a submit gets an error event (P1, design §9).
        await host.emit("host_lock_question", {})
        # The test client doesn't track question_locked; wait until the room state shows it.
        assert await eventually(
            lambda: json.loads(redis_cli("GET", f"room:{code}") or "{}").get("question_locked") is True
        )
        await b.emit("submit_answer", {"question_id": qid, "answer_data": {"x": 3, "y": -2}})
        event, payload = await next_of(b, ("answer_received", "error"))
        assert event == "error" and "locked" in payload["message"].lower()
    finally:
        for c in (host, a, b):
            await c.disconnect()
