"""
Unit tests for scoring and ranking rules: multi_select counts each option once (and the
gateway refuses repeated indices), isCorrect agrees with the answer reveal, tied totals
share a rank, the fill-in distribution key, the player-JSON lock, and the Redis undo of
an answer whose commit failed. No DB, Redis or server.

Run with:
    cd tests/unit && PYTHONPATH=../../backend pytest test_scoring_rules.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import game_service
from app.services import state_service as state
from app.websocket import events as E
from app.websocket import gateway as gw

from . import test_answer_races as races
from .test_answer_races import QID, SESSION, AnswerDb, HashRedis, _open_question, wired  # noqa: F401


def _q(qtype: str, answer_data: dict, points_value: float = 100, **extra):
    return SimpleNamespace(
        id=QID,
        type=qtype,
        grading_type="ACCURACY",
        points_value=points_value,
        answer_data=answer_data,
        config=extra.get("config", {}),
    )


MULTI = _q(
    "multi_select",
    {"answer_points": [50, 50, -20]},
    config={"options": ["a", "b", "c"]},
)


# ---------------------------------------------------------------------------
# multi_select: each option counts once
# ---------------------------------------------------------------------------


def test_multi_select_repeated_index_scores_once():
    once = game_service.calculate_score(MULTI, {"selectedIndices": [0]})
    repeated = game_service.calculate_score(MULTI, {"selectedIndices": [0] * 1000})
    assert repeated.points_awarded == once.points_awarded == 50
    both = game_service.calculate_score(MULTI, {"selectedIndices": [0, 1, 0, 1]})
    assert both.points_awarded == 100 and both.is_correct


def test_multi_select_distribution_counts_each_option_once():
    keys = game_service._answer_dist_keys(MULTI, {"selectedIndices": [1, 0, 1, 1]})
    assert keys == ["1", "0"]


async def test_gateway_refuses_duplicate_indices(wired, monkeypatch):  # noqa: F811
    redis, emits, rows, _ = wired
    monkeypatch.setattr(races, "QUESTION", MULTI)
    await _open_question(redis)
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)

    await gw.on_submit_answer(
        "p-sid", {"question_id": QID, "answer_data": {"selectedIndices": [0, 0]}}
    )

    assert rows == []
    assert [e for e, _, _ in emits] == [E.ERROR]
    assert "duplicate" in emits[0][1]["message"].lower()
    assert not await state.has_answered(redis, SESSION, QID, "u1")


# ---------------------------------------------------------------------------
# isCorrect matches the reveal (p >= points_value) and is never true at 0 points
# ---------------------------------------------------------------------------


def test_multiple_choice_correct_matches_reveal():
    q = _q("multiple_choice", {"answer_points": [0, 120, 100]})
    reveal = game_service.answer_reveal(q)["correctIndices"]
    for i in range(3):
        result = game_service.calculate_score(q, {"selectedIndex": i})
        assert result.is_correct == (i in reveal)


@pytest.mark.parametrize(
    "q, answer",
    [
        (_q("multiple_choice", {"answer_points": [0, 0]}, 0), {"selectedIndex": 0}),
        (
            _q("true_false", {"answer_points": {"true": 0, "false": 0}}, 0),
            {"selectedValue": True},
        ),
        (
            _q("fill_in_the_blank", {"acceptedAnswers": ["paris"]}, 0),
            {"text": "london"},
        ),
    ],
)
def test_zero_point_question_is_never_correct(q, answer):
    result = game_service.calculate_score(q, answer)
    assert result.points_awarded == 0 and not result.is_correct


# ---------------------------------------------------------------------------
# Ties share a rank (1, 1, 3)
# ---------------------------------------------------------------------------


def test_tied_totals_share_a_rank():
    leaderboard = [
        {"user_id": "a", "score": 30.0},
        {"user_id": "b", "score": 30.0},
        {"user_id": "c", "score": 10.0},
        {"user_id": "d", "score": 0.0},
    ]
    ranks = {u: gw._rank_of(leaderboard, u) for u in "abcd"}
    assert ranks == {"a": 1, "b": 1, "c": 3, "d": 4}
    # No score rows: 0 points, tied with d.
    assert gw._rank_of(leaderboard, "e") == 4


async def test_results_and_game_over_use_the_same_rank(monkeypatch):
    leaderboard = [{"user_id": "a", "score": 5.0}, {"user_id": "b", "score": 5.0}]

    async def no_summary(db, sid, uid):
        return []

    async def no_urls(db, configs):
        return {}

    monkeypatch.setattr(game_service, "get_player_question_summary", no_summary)
    monkeypatch.setattr(gw.image_service, "image_urls", no_urls)
    payload = await gw._player_game_over_payload(
        None, SESSION, "b", 5.0, leaderboard, 2
    )
    assert payload["yourFinalRank"] == 1


# ---------------------------------------------------------------------------
# Fill-in distribution key: one normalization, live and game-over
# ---------------------------------------------------------------------------


def test_fill_in_key_collapses_inner_whitespace():
    q = _q("fill_in_the_blank", {"acceptedAnswers": ["new york"]})
    assert game_service._answer_dist_keys(q, {"text": "  New   York "}) == ["new york"]


# ---------------------------------------------------------------------------
# Player JSON: a connect write can't restore an old score
# ---------------------------------------------------------------------------


async def test_score_and_connected_writes_do_not_overwrite_each_other():
    redis = HashRedis()
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)
    await asyncio.gather(
        state.update_player_score(redis, SESSION, "u1", 100),
        state.set_player_connected(redis, SESSION, "u1", False),
    )
    player = await state.get_player(redis, SESSION, "u1")
    assert player["score"] == 100 and player["is_connected"] is False


# ---------------------------------------------------------------------------
# A failed commit undoes the Redis score and distribution
# ---------------------------------------------------------------------------


class HdelRedis(HashRedis):
    async def hdel(self, key, *fields):
        h = self.hashes.get(key, {})
        for f in fields:
            h.pop(f, None)


async def test_revert_answer_undoes_redis_updates():
    redis = HdelRedis()
    await state.add_player(redis, SESSION, "u1", "Tester", is_guest=True)
    answer = {"selectedIndices": [0, 1]}
    result = await game_service.record_answer(
        AnswerDb([]), redis, SESSION, "u1", MULTI, answer, None
    )
    assert (await state.get_player(redis, SESSION, "u1"))["score"] == 100

    await game_service.revert_answer(redis, SESSION, "u1", MULTI, answer, result)

    assert (await state.get_player(redis, SESSION, "u1"))["score"] == 0
    assert await state.get_answer_dist(redis, SESSION, QID) == {}
