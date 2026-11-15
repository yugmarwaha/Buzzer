"""
Player limit: a room admits at most the quiz's max_players (docs/realtime.md).

The limit is claimed atomically in Redis on join, so these tests send the joins at the
same time on purpose.
"""
from __future__ import annotations

import asyncio

import httpx

from .conftest import create_guest_tokens, create_questions, create_room
from .engine.socket_client import TestSocketClient
from .scenarios.all_question_types import MC_ACCURACY

LIMIT = 3
EXTRA = 4
ROOM_FULL = "This room is full"


class _OneQuestion:
    questions = [MC_ACCURACY]


def _room_with_limit(base_url, admin_token, game_setup, limit: int) -> str:
    game_id = game_setup["game_id"]
    r = httpx.put(
        f"{base_url}/api/admin/games/{game_id}",
        json={"max_players": limit},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    create_questions(base_url, admin_token, game_id, _OneQuestion, False)
    return create_room(base_url, admin_token, game_setup["course_id"], game_id)


async def _join(client: TestSocketClient, room_code: str) -> tuple[str, dict]:
    """Join as a player; returns ("sync_state" | "error", payload)."""
    await client.emit("join_room", {"room_code": room_code, "role": "PLAYER"})
    waits = {
        asyncio.ensure_future(client.wait_for(ev, 15.0)): ev
        for ev in ("sync_state", "error")
    }
    done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    first = done.pop()
    return waits[first], first.result()


async def test_concurrent_joins_stop_at_max_players(game_setup, base_url, admin_token):
    room_code = _room_with_limit(base_url, admin_token, game_setup, LIMIT)
    tokens = create_guest_tokens(base_url, room_code, LIMIT + EXTRA)
    host = TestSocketClient(base_url, admin_token, "host")
    players = [TestSocketClient(base_url, t, f"p{i}") for i, t in enumerate(tokens)]
    try:
        await asyncio.gather(host.connect(), *(p.connect() for p in players))
        await host.emit("join_room", {"room_code": room_code, "role": "HOST"})
        await host.wait_for("sync_state")

        outcomes = await asyncio.gather(*(_join(p, room_code) for p in players))

        admitted = [o for o in outcomes if o[0] == "sync_state"]
        refused = [o for o in outcomes if o[0] == "error"]
        assert len(admitted) == LIMIT
        assert len(refused) == EXTRA
        assert all(payload.get("message") == ROOM_FULL for _, payload in refused)
        # The host was told about the admitted players only.
        counts = [(await host.wait_for("player_joined"))["playerCount"] for _ in admitted]
        assert max(counts) == LIMIT
    finally:
        await asyncio.gather(
            host.disconnect(), *(p.disconnect() for p in players), return_exceptions=True
        )


async def test_seated_player_can_reconnect_to_a_full_room(
    game_setup, base_url, admin_token
):
    room_code = _room_with_limit(base_url, admin_token, game_setup, 1)
    seated_token, late_token = create_guest_tokens(base_url, room_code, 2)
    seated = TestSocketClient(base_url, seated_token, "seated")
    late = TestSocketClient(base_url, late_token, "late")
    again = TestSocketClient(base_url, seated_token, "seated-again")
    try:
        await asyncio.gather(seated.connect(), late.connect())
        assert (await _join(seated, room_code))[0] == "sync_state"
        kind, payload = await _join(late, room_code)
        assert (kind, payload.get("message")) == ("error", ROOM_FULL)

        # The seated player drops and comes back (join_room, then rejoin_room): the
        # room is still full, and the seat is still theirs.
        await seated.disconnect()
        await again.connect()
        assert (await _join(again, room_code))[0] == "sync_state"
        await again.emit("rejoin_room", {"room_code": room_code, "role": "PLAYER"})
        assert (await again.wait_for("sync_state"))["role"] == "PLAYER"

        # rejoin_room applies the same limit to a player who never had a seat.
        await late.emit("rejoin_room", {"room_code": room_code, "role": "PLAYER"})
        assert (await late.wait_for("error")).get("message") == ROOM_FULL
    finally:
        await asyncio.gather(
            seated.disconnect(), late.disconnect(), again.disconnect(),
            return_exceptions=True,
        )
