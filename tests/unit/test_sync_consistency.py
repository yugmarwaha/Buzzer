"""
Unit tests for the gateway's reload/advance consistency helpers: no DB, Redis or server.

Run with:
    cd tests/unit && PYTHONPATH=../../backend pytest test_sync_consistency.py -v
"""

from __future__ import annotations

import asyncio

from app.services import state_service as state
from app.websocket.gateway import (
    _question_in_sync,
    _revealable_question_id,
    _stale_advance,
)


def q(qid: int, number: int) -> dict:
    """A Redis current-question record (only the fields the helpers read)."""
    return {"question_id": qid, "question_number": number}


# ---------------------------------------------------------------------------
# _revealable_question_id: results (with the correct answer) only for RESULTS of current_q
# ---------------------------------------------------------------------------


def test_reveal_when_results_belong_to_current_question():
    room = {"question_phase": "RESULTS", "question_index": 0, "results_question_id": 11}
    assert _revealable_question_id(room, q(11, 1)) == 11


def test_no_reveal_while_question_open():
    room = {"question_phase": "QUESTION", "question_index": 0}
    assert _revealable_question_id(room, q(11, 1)) is None


def test_stale_results_blob_next_to_new_question_is_not_revealed():
    # Advance RESULTS(q11) -> QUESTION(q12) ran, but an older RESULTS copy of the room
    # blob was written back afterwards (lost update). current_q is the new open question.
    room = {"question_phase": "RESULTS", "question_index": 0, "results_question_id": 11}
    assert _revealable_question_id(room, q(12, 2)) is None


def test_matching_index_but_other_results_question_is_not_revealed():
    room = {"question_phase": "RESULTS", "question_index": 1, "results_question_id": 11}
    assert _revealable_question_id(room, q(12, 2)) is None


def test_results_without_recorded_question_are_not_revealed():
    # A RESULTS blob from before results_question_id existed: safe default is no reveal.
    room = {"question_phase": "RESULTS", "question_index": 0}
    assert _revealable_question_id(room, q(11, 1)) is None


def test_no_state_no_reveal():
    assert _revealable_question_id(None, q(11, 1)) is None
    assert _revealable_question_id({"question_phase": "RESULTS"}, None) is None


def test_question_in_sync_mid_advance():
    # An advance writes room_state (index 1) before current_q (still question 1 of N).
    assert not _question_in_sync({"question_index": 1}, q(11, 1))
    assert _question_in_sync({"question_index": 1}, q(12, 2))


# ---------------------------------------------------------------------------
# _stale_advance
# ---------------------------------------------------------------------------


def test_empty_payload_never_stale():
    assert not _stale_advance({}, {"question_phase": "RESULTS", "question_index": 3})


def test_phase_mismatch_is_stale():
    room = {"question_phase": "RESULTS", "question_index": 0}
    assert _stale_advance({"from_phase": "QUESTION", "question_index": 0}, room)


def test_index_mismatch_is_stale():
    room = {"question_phase": "QUESTION", "question_index": 2}
    assert _stale_advance({"from_phase": "QUESTION", "question_index": 1}, room)


def test_missing_index_checks_phase_only():
    room = {"question_phase": "QUESTION", "question_index": 2}
    assert not _stale_advance({"from_phase": "QUESTION"}, room)


def test_lobby_matches_no_phase():
    assert not _stale_advance({"from_phase": "LOBBY", "question_index": -1}, {})


# ---------------------------------------------------------------------------
# claim_advance: one winner for concurrent advances from the same screen
# ---------------------------------------------------------------------------


class _FakeRedis:
    """SET with NX/EX semantics, enough for claim_advance."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        await asyncio.sleep(0)  # let the other caller interleave
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


async def test_concurrent_claims_have_one_winner():
    redis = _FakeRedis()
    results = await asyncio.gather(
        state.claim_advance(redis, "s1", "QUESTION", 0),
        state.claim_advance(redis, "s1", "QUESTION", 0),
    )
    assert sorted(results) == [False, True]


async def test_claims_for_different_screens_are_independent():
    redis = _FakeRedis()
    assert await state.claim_advance(redis, "s1", "QUESTION", 0)
    assert await state.claim_advance(redis, "s1", "RESULTS", 0)
