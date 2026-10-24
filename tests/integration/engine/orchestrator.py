"""
Game orchestrator: connects host + players via socket.io, drives the full
game loop, and returns each player's game_over payload for assertion.

The host always advances manually — we never block waiting for a timer to
fire.  The --fast flag (which sets time_limit_seconds=2 on questions) is
therefore only needed for edge-case tests that specifically test automatic
timer expiry.
"""
from __future__ import annotations

import asyncio
import logging

from .scoring import GameScenario
from .socket_client import TestSocketClient

logger = logging.getLogger(__name__)

# How long to wait for a socket event before the test fails
_EVENT_TIMEOUT = 15.0


async def run_game(
    base_url: str,
    host_token: str,
    player_tokens: list[str],
    room_code: str,
    scenario: GameScenario,
) -> list[dict]:
    """
    Drive a complete game session end-to-end.

    Returns a list of game_over payloads, one per player, in the same order
    as `player_tokens`.  Raises on any socket error or timeout.
    """
    host = TestSocketClient(base_url, host_token, "host")
    players = [
        TestSocketClient(base_url, t, f"player_{i}")
        for i, t in enumerate(player_tokens)
    ]
    all_clients = [host, *players]

    try:
        # ── Connect ────────────────────────────────────────────────────────
        await asyncio.gather(*[c.connect() for c in all_clients])

        # ── Join room ──────────────────────────────────────────────────────
        await host.emit("join_room", {"room_code": room_code, "role": "HOST"})
        # Stagger player joins slightly so the server doesn't see a thundering herd
        for p in players:
            await p.emit("join_room", {"room_code": room_code, "role": "PLAYER"})
            await asyncio.sleep(0.05)

        # Wait for all clients to receive their sync_state confirmation
        sync = await host.wait_for("sync_state", _EVENT_TIMEOUT)
        assert sync.get("status") in ("LOBBY", "IN_PROGRESS"), \
            f"Host sync_state had unexpected status: {sync}"
        await asyncio.gather(*[p.wait_for("sync_state", _EVENT_TIMEOUT) for p in players])

        logger.info("All clients joined room %s — starting game", room_code)

        # ── Game loop ──────────────────────────────────────────────────────
        # Host advances from LOBBY → first QUESTION
        await host.emit("host_advance", {})

        for q_idx, q_spec in enumerate(scenario.questions):
            logger.info("Question %d/%d (%s, %s)",
                        q_idx + 1, len(scenario.questions),
                        q_spec.type, q_spec.grading_type)

            # Wait for new_question on all clients
            await host.wait_for("new_question", _EVENT_TIMEOUT)
            new_q_payloads = await asyncio.gather(*[
                p.wait_for("new_question", _EVENT_TIMEOUT) for p in players
            ])

            question_id = new_q_payloads[0]["questionId"]

            # Submit player answers and wait for server confirmation before advancing.
            # answer_received is only emitted after record_answer() completes
            # (both DB write and Redis score update), so this is the reliable
            # synchronisation point instead of a fixed sleep.
            submitting_players = []
            for p_idx, (player, script) in enumerate(zip(players, scenario.player_scripts)):
                response = script.responses[q_idx]
                if response is not None:
                    await player.emit("submit_answer", {
                        "question_id": question_id,
                        "answer_data": response,
                        "answer_time_ms": 250 + p_idx * 50,
                    })
                    submitting_players.append(player)

            if submitting_players:
                # Yield to the event loop so the engineio write-loop task can
                # flush the outgoing queue before we block on answer_received.
                await asyncio.sleep(0.1)
                await asyncio.gather(*[
                    p.wait_for("answer_received", _EVENT_TIMEOUT)
                    for p in submitting_players
                ])

            # QUESTION → RESULTS
            await host.emit("host_advance", {})
            await host.wait_for("question_results", _EVENT_TIMEOUT)
            await asyncio.gather(*[
                p.wait_for("question_results", _EVENT_TIMEOUT) for p in players
            ])

            # RESULTS → next QUESTION (or GAME_OVER for the last question)
            await host.emit("host_advance", {})

        # ── Collect game_over payloads ─────────────────────────────────────
        await host.wait_for("game_over", _EVENT_TIMEOUT)
        game_over_payloads = await asyncio.gather(*[
            p.wait_for("game_over", _EVENT_TIMEOUT) for p in players
        ])

        logger.info("Game complete — collected %d player payloads", len(game_over_payloads))
        return list(game_over_payloads)

    finally:
        await asyncio.gather(
            *[c.disconnect() for c in all_clients],
            return_exceptions=True,
        )


async def run_game_with_timer_expiry(
    base_url: str,
    host_token: str,
    player_tokens: list[str],
    room_code: str,
    scenario: GameScenario,
    timer_seconds: int = 2,
) -> list[dict]:
    """
    Variant of run_game where the host does NOT advance after new_question —
    instead the test waits for the server-side timer to fire (answer_phase_ended),
    then the host advances to results.  Requires --fast (short time_limit_seconds).

    Use this for edge cases that specifically test timer-expiry behaviour.
    """
    host = TestSocketClient(base_url, host_token, "host")
    players = [
        TestSocketClient(base_url, t, f"player_{i}")
        for i, t in enumerate(player_tokens)
    ]
    all_clients = [host, *players]

    try:
        await asyncio.gather(*[c.connect() for c in all_clients])

        await host.emit("join_room", {"room_code": room_code, "role": "HOST"})
        for p in players:
            await p.emit("join_room", {"room_code": room_code, "role": "PLAYER"})
            await asyncio.sleep(0.05)

        await host.wait_for("sync_state", _EVENT_TIMEOUT)
        await asyncio.gather(*[p.wait_for("sync_state", _EVENT_TIMEOUT) for p in players])

        await host.emit("host_advance", {})

        for q_idx, q_spec in enumerate(scenario.questions):
            await host.wait_for("new_question", _EVENT_TIMEOUT)
            new_q_payloads = await asyncio.gather(*[
                p.wait_for("new_question", _EVENT_TIMEOUT) for p in players
            ])
            question_id = new_q_payloads[0]["questionId"]

            answer_tasks = []
            for p_idx, (player, script) in enumerate(zip(players, scenario.player_scripts)):
                response = script.responses[q_idx]
                if response is not None:
                    answer_tasks.append(player.emit("submit_answer", {
                        "question_id": question_id,
                        "answer_data": response,
                        "answer_time_ms": 250,
                    }))
            if answer_tasks:
                await asyncio.gather(*answer_tasks)
                # Yield so the engineio write-loop flushes outbound packets
                # before we block waiting for the timer to fire.
                await asyncio.sleep(0.1)

            # Wait for server timer to fire instead of advancing manually
            await host.wait_for("answer_phase_ended", timeout=timer_seconds + 5.0)

            # Now host advances to results
            await host.emit("host_advance", {})
            await host.wait_for("question_results", _EVENT_TIMEOUT)
            await asyncio.gather(*[
                p.wait_for("question_results", _EVENT_TIMEOUT) for p in players
            ])

            await host.emit("host_advance", {})

        await host.wait_for("game_over", _EVENT_TIMEOUT)
        game_over_payloads = await asyncio.gather(*[
            p.wait_for("game_over", _EVENT_TIMEOUT) for p in players
        ])

        return list(game_over_payloads)

    finally:
        await asyncio.gather(
            *[c.disconnect() for c in all_clients],
            return_exceptions=True,
        )
