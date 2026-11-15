#!/usr/bin/env python3
"""
Load test: one full game with N simulated players, with latency percentiles and a
correctness check.

It creates its own test course, quiz and room, connects one host and N guest players
over WebSocket, plays every question, and measures what a player would feel:

  join        join_room sent            -> sync_state received
  fan-out     host_advance sent         -> new_question received (per player)
  answer      submit_answer sent        -> answer_received received
  results     host_advance sent         -> question_results received (per player)

It then checks that nothing was lost: every answer was acknowledged once, the host's
answered count matches, and each player's final score in game_over (read from MySQL)
equals the sum of the points the server acknowledged (kept in Redis during play).

With --over K it also sends K players more than the quiz's max_players and checks that
exactly K joins are refused with "This room is full".

All clients run in this one Python process, so the latencies include the client's own
event-loop time: they are an upper bound on what the server adds.

Usage:
    python scripts/load_test.py --players 250
    python scripts/load_test.py --players 250 --over 20 --json out.json
    python scripts/load_test.py --base-url http://localhost:8010 --players 100 --think-max 0

Prerequisites:
    pip install httpx "python-socketio[asyncio_client]" aiohttp
    A running stack, and ADMIN_USERNAME / ADMIN_PASSWORD in .env or the environment.
    Outside development mode, pass --stress-key (the server's STRESS_TEST_KEY) so the
    per-IP rate limits do not refuse the simulated players.

Cleanup (the data is ordinary test data, removed by the integration-test sweep):
    docker compose exec -T backend python -m scripts.cleanup_test_quiz --sweep
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import pathlib
import random
import sys
import time
import uuid

import httpx
import socketio

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
POINTS = 1000
OPTIONS = 4
CORRECT_INDEX = 1
EVENT_TIMEOUT = 60.0


def _env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    path = _REPO_ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(values_s: list[float]) -> dict:
    if not values_s:
        return {"count": 0}
    ms = [v * 1000 for v in values_s]
    return {
        "count": len(ms),
        "p50_ms": round(percentile(ms, 50), 1),
        "p95_ms": round(percentile(ms, 95), 1),
        "p99_ms": round(percentile(ms, 99), 1),
        "max_ms": round(max(ms), 1),
        "mean_ms": round(sum(ms) / len(ms), 1),
    }


class Client:
    """One socket.io connection that timestamps the events the test measures."""

    def __init__(self, base_url: str, token: str, stress_key: str, label: str):
        self.label = label
        self.base_url = base_url
        self.auth = {"token": token}
        if stress_key:
            self.auth["stress_key"] = stress_key
        self.sio = socketio.AsyncClient(
            logger=False, engineio_logger=False, reconnection=False
        )
        self.queues: dict[str, asyncio.Queue] = {}
        self.errors: list[str] = []
        self.acked_points = 0.0
        self.acks = 0
        for event in (
            "sync_state",
            "new_question",
            "question_results",
            "answer_received",
            "game_over",
            "answer_status",
            "error",
        ):
            self.queues[event] = asyncio.Queue()
            self.sio.on(event, self._handler(event))

    def _handler(self, event: str):
        async def handle(data=None):
            await self.queues[event].put((time.perf_counter(), data or {}))

        return handle

    async def connect(self) -> None:
        await self.sio.connect(
            self.base_url,
            socketio_path="/socket.io",
            auth=self.auth,
            transports=["websocket"],
            wait_timeout=30,
        )

    async def wait(self, event: str, timeout: float = EVENT_TIMEOUT):
        return await asyncio.wait_for(self.queues[event].get(), timeout)

    async def wait_or_error(self, event: str, timeout: float = EVENT_TIMEOUT):
        """The event, or the server's error event, whichever comes first."""
        want = asyncio.ensure_future(self.queues[event].get())
        err = asyncio.ensure_future(self.queues["error"].get())
        done, pending = await asyncio.wait(
            {want, err}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if not done:
            raise asyncio.TimeoutError(f"{self.label}: no {event} within {timeout}s")
        if want in done:
            return event, want.result()
        return "error", err.result()

    async def close(self) -> None:
        if self.sio.connected:
            await self.sio.disconnect()


async def _setup(args, http: httpx.AsyncClient) -> dict:
    env = _env_file()
    username = env.get("ADMIN_USERNAME") or os.environ.get("ADMIN_USERNAME", "admin")
    password = env.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
    r = await http.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    if r.status_code != 200:
        sys.exit(f"Admin login failed ({r.status_code}): {r.text}")
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    tag = uuid.uuid4().hex[:8]
    r = await http.post(
        "/api/admin/courses",
        json={"name": f"Test Course {tag}", "semester": "Test"},
        headers=headers,
    )
    r.raise_for_status()
    course_id = r.json()["id"]
    r = await http.post(
        "/api/admin/games",
        json={
            "course_id": course_id,
            "title": f"Load Test {tag}",
            "description": "scripts/load_test.py",
            "max_players": args.players,
        },
        headers=headers,
    )
    r.raise_for_status()
    game_id = r.json()["id"]
    answer_points = [0] * OPTIONS
    answer_points[CORRECT_INDEX] = POINTS
    for i in range(args.questions):
        r = await http.post(
            f"/api/admin/games/{game_id}/questions",
            json={
                "type": "multiple_choice",
                "grading_type": "ACCURACY",
                "prompt": f"Load test question {i + 1}",
                "config": {"options": [f"Option {n}" for n in range(OPTIONS)]},
                "answer_data": {"answer_points": answer_points},
                "points_value": POINTS,
                "time_limit_seconds": 120,
            },
            headers=headers,
        )
        r.raise_for_status()
    r = await http.post(
        "/api/game/rooms",
        json={"course_id": course_id, "game_id": game_id},
        headers=headers,
    )
    r.raise_for_status()
    return {
        "host_token": token,
        "room_code": r.json()["room_code"],
        "game_id": game_id,
        "course_id": course_id,
    }


async def _guest_tokens(args, http: httpx.AsyncClient, room: str, n: int) -> list[str]:
    sem = asyncio.Semaphore(args.concurrency)
    headers = {"X-Stress-Key": args.stress_key} if args.stress_key else {}

    async def one(i: int) -> str:
        uid = uuid.uuid4().hex[:6]
        async with sem:
            r = await http.post(
                "/api/auth/guest",
                json={
                    "display_name": f"TestPlayer{i}_{uid}",
                    "email": f"testplayer{i}.{uid}@example.com",
                    "room_code": room,
                },
                headers=headers,
            )
        if r.status_code != 200:
            sys.exit(f"Guest sign-in {i} failed ({r.status_code}): {r.text}")
        return r.json()["access_token"]

    return list(await asyncio.gather(*(one(i) for i in range(n))))


async def run(args) -> dict:
    rng = random.Random(args.seed)
    total_clients = args.players + args.over
    problems: list[str] = []

    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as http:
        setup = await _setup(args, http)
        room = setup["room_code"]
        print(f"room {room}: signing in {total_clients} guests...", flush=True)
        tokens = await _guest_tokens(args, http, room, total_clients)

    host = Client(args.base_url, setup["host_token"], args.stress_key, "host")
    clients = [
        Client(args.base_url, t, args.stress_key, f"p{i}") for i, t in enumerate(tokens)
    ]
    join_lat: list[float] = []
    fanout_lat: list[float] = []
    answer_lat: list[float] = []
    results_lat: list[float] = []
    refused = 0
    wall_start = time.perf_counter()

    try:
        await host.connect()
        await host.sio.emit("join_room", {"room_code": room, "role": "HOST"})
        await host.wait("sync_state")

        # ── Connect and join, at most --concurrency at a time ──────────────
        sem = asyncio.Semaphore(args.concurrency)
        players: list[Client] = []

        async def join(c: Client) -> None:
            nonlocal refused
            async with sem:
                await c.connect()
                t0 = time.perf_counter()
                await c.sio.emit("join_room", {"room_code": room, "role": "PLAYER"})
                kind, (t1, data) = await c.wait_or_error("sync_state")
            if kind == "error":
                if data.get("message") == "This room is full":
                    refused += 1
                else:
                    problems.append(f"{c.label}: join error {data}")
                return
            join_lat.append(t1 - t0)
            players.append(c)

        t_join = time.perf_counter()
        await asyncio.gather(*(join(c) for c in clients))
        join_wall = time.perf_counter() - t_join
        print(
            f"joined {len(players)}, refused {refused} in {join_wall:.1f}s", flush=True
        )
        if len(players) != args.players:
            problems.append(f"{len(players)} players joined, expected {args.players}")
        if refused != args.over:
            problems.append(f"{refused} joins refused as full, expected {args.over}")

        # ── Play every question ────────────────────────────────────────────
        answers_sent = 0
        answer_window_s = 0.0
        t_advance = time.perf_counter()
        await host.sio.emit("host_advance", {})
        for q_index in range(args.questions):

            async def play(c: Client, sent_at: float) -> None:
                nonlocal answers_sent
                t_q, question = await c.wait("new_question")
                fanout_lat.append(t_q - sent_at)
                await asyncio.sleep(rng.uniform(0, args.think_max))
                choice = (
                    CORRECT_INDEX
                    if rng.random() < args.accuracy
                    else rng.choice([i for i in range(OPTIONS) if i != CORRECT_INDEX])
                )
                t0 = time.perf_counter()
                await c.sio.emit(
                    "submit_answer",
                    {
                        "question_id": question["questionId"],
                        "answer_data": {"selectedIndex": choice},
                        "answer_time_ms": int((t0 - t_q) * 1000),
                    },
                )
                answers_sent += 1
                kind, (t1, data) = await c.wait_or_error("answer_received")
                if kind == "error":
                    problems.append(f"{c.label}: answer error {data}")
                    return
                answer_lat.append(t1 - t0)
                c.acks += 1
                c.acked_points += float(data.get("pointsAwarded") or 0)
                expected = POINTS if choice == CORRECT_INDEX else 0
                if data.get("pointsAwarded") != expected:
                    problems.append(
                        f"{c.label}: q{q_index} awarded {data.get('pointsAwarded')}, "
                        f"expected {expected}"
                    )

            t_window = time.perf_counter()
            await asyncio.gather(*(play(c, t_advance) for c in players))
            answer_window_s += time.perf_counter() - t_window

            # The host's live counter must reach every player. Its last update is sent
            # after the last player's reply, so wait for it instead of reading at once.
            answered = 0
            try:
                while answered < len(players):
                    _, status = await host.wait("answer_status", timeout=15.0)
                    answered = max(answered, status.get("answeredCount", 0))
            except asyncio.TimeoutError:
                problems.append(
                    f"q{q_index}: host saw {answered}/{len(players)} answered"
                )

            t_results = time.perf_counter()
            await host.sio.emit("host_advance", {})

            async def results(c: Client) -> None:
                t_r, _ = await c.wait("question_results")
                results_lat.append(t_r - t_results)

            await asyncio.gather(*(results(c) for c in players))
            t_advance = time.perf_counter()
            await host.sio.emit("host_advance", {})
            print(f"question {q_index + 1}/{args.questions} done", flush=True)

        # ── Game over: final scores come from MySQL ────────────────────────
        mismatched = 0
        for c in players:
            _, over = await c.wait("game_over")
            if c.acks != args.questions:
                problems.append(f"{c.label}: {c.acks}/{args.questions} answers acked")
            if abs(float(over.get("yourFinalScore") or 0) - c.acked_points) > 1e-6:
                mismatched += 1
        if mismatched:
            problems.append(
                f"{mismatched} players: final score differs from acknowledged points"
            )
        for c in [host, *players]:
            while not c.queues["error"].empty():
                _, err = c.queues["error"].get_nowait()
                problems.append(f"{c.label}: error event {err}")
    finally:
        await asyncio.gather(
            *(c.close() for c in [host, *clients]), return_exceptions=True
        )

    return {
        "base_url": args.base_url,
        "players": args.players,
        "extra_joins_over_limit": args.over,
        "joins_refused_room_full": refused,
        "questions": args.questions,
        "think_max_s": args.think_max,
        "answers_sent": answers_sent,
        "answers_acknowledged": len(answer_lat),
        "answers_per_second": round(len(answer_lat) / answer_window_s, 1)
        if answer_window_s
        else None,
        "join_wall_s": round(join_wall, 2),
        "total_wall_s": round(time.perf_counter() - wall_start, 2),
        "latency": {
            "join": summarize(join_lat),
            "question_fan_out": summarize(fanout_lat),
            "answer_round_trip": summarize(answer_lat),
            "results_fan_out": summarize(results_lat),
        },
        "problems": problems[:50],
        "problem_count": len(problems),
        "passed": not problems,
    }


def _print(report: dict) -> None:
    print()
    print(
        f"players {report['players']}  questions {report['questions']}  "
        f"answers {report['answers_acknowledged']}/{report['answers_sent']}  "
        f"refused-full {report['joins_refused_room_full']}/"
        f"{report['extra_joins_over_limit']}"
    )
    print(f"{'metric':<20}{'n':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}   (ms)")
    for name, s in report["latency"].items():
        if s["count"]:
            print(
                f"{name:<20}{s['count']:>7}{s['p50_ms']:>9}{s['p95_ms']:>9}"
                f"{s['p99_ms']:>9}{s['max_ms']:>9}"
            )
    print(f"answers/second while a question was open: {report['answers_per_second']}")
    if report["passed"]:
        print("PASS: no lost answers, no errors, final scores match")
    else:
        print(f"FAIL: {report['problem_count']} problem(s)")
        for p in report["problems"]:
            print("  -", p)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--players", type=int, default=100)
    ap.add_argument(
        "--over", type=int, default=0, help="extra joins beyond max_players"
    )
    ap.add_argument("--questions", type=int, default=5)
    ap.add_argument(
        "--think-max",
        type=float,
        default=3.0,
        help="each player waits 0..N s before answering (0 = all answer at once)",
    )
    ap.add_argument("--accuracy", type=float, default=0.6)
    ap.add_argument(
        "--concurrency", type=int, default=25, help="sign-ins/joins in flight at once"
    )
    ap.add_argument("--stress-key", default=os.environ.get("STRESS_TEST_KEY", ""))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", help="write the full report to this file")
    args = ap.parse_args()
    args.base_url = args.base_url.rstrip("/")

    report = asyncio.run(run(args))
    _print(report)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
