"""
Edge-case scenarios.

TIMER_EXPIRY_* — require --fast (time_limit_seconds=2).  The test runner
checks the `requires_fast` flag and skips these when --fast is not set.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.scoring import GameScenario, PlayerScript, QuestionSpec


# ---------------------------------------------------------------------------
# Helpers re-imported for convenience
# ---------------------------------------------------------------------------

MC_ACCURACY = QuestionSpec(
    type="multiple_choice",
    grading_type="ACCURACY",
    prompt="What is the capital of France?",
    config={"options": ["Berlin", "Paris", "Rome", "Madrid"]},
    answer_data={"answer_points": [0, 1000, 0, 0]},
    points_value=1000,
)

TF_ACCURACY = QuestionSpec(
    type="true_false",
    grading_type="ACCURACY",
    prompt="The Nile is the longest river in the world.",
    config={},
    answer_data={"answer_points": {"true": 1000, "false": 0}},
    points_value=1000,
)

TF_COMPLETENESS = QuestionSpec(
    type="true_false",
    grading_type="COMPLETENESS",
    prompt="Do you prefer summer over winter?",
    config={},
    answer_data={"answer_points": {"true": 1000, "false": 1000}},
    points_value=1000,
)

# ---------------------------------------------------------------------------
# Edge-case GameScenario subclass — adds requires_fast flag
# ---------------------------------------------------------------------------

@dataclass
class EdgeScenario(GameScenario):
    """GameScenario extended with a flag for tests that need --fast mode."""
    requires_fast: bool = False
    uses_timer_expiry: bool = False


# ---------------------------------------------------------------------------
# All-skip scenario
# ---------------------------------------------------------------------------

ALL_SKIP = EdgeScenario(
    name="all_players_skip_all_questions",
    description="Every player skips every question. All scores should be 0.",
    questions=[MC_ACCURACY, TF_ACCURACY, TF_COMPLETENESS],
    player_scripts=[
        PlayerScript(responses=[None, None, None]),
        PlayerScript(responses=[None, None, None]),
    ],
)


# ---------------------------------------------------------------------------
# Single question, single player — minimal game
# ---------------------------------------------------------------------------

MINIMAL = EdgeScenario(
    name="minimal_one_question_one_player",
    description="Smallest possible game: 1 question, 1 player, correct answer.",
    questions=[MC_ACCURACY],
    player_scripts=[
        PlayerScript(responses=[{"selectedIndex": 1}]),
    ],
)


# ---------------------------------------------------------------------------
# Late-join scenario: player 2 has None for early questions
# (simulates joining after a question has already been answered)
# ---------------------------------------------------------------------------

LATE_JOIN = EdgeScenario(
    name="late_joining_player",
    description=(
        "Player 1 answers all questions; player 2 misses the first question "
        "(simulating a late join). Both should see all questions on game-over screen."
    ),
    questions=[MC_ACCURACY, TF_ACCURACY, TF_COMPLETENESS],
    player_scripts=[
        PlayerScript(responses=[
            {"selectedIndex": 1},   # correct
            {"selectedValue": True}, # correct
            {"selectedValue": False}, # any
        ]),
        PlayerScript(responses=[
            None,                    # missed first question
            {"selectedValue": True}, # correct
            {"selectedValue": True}, # any
        ]),
    ],
)


# ---------------------------------------------------------------------------
# Timer-expiry scenario (requires --fast)
# ---------------------------------------------------------------------------

TIMER_EXPIRY = EdgeScenario(
    name="timer_expiry_no_answers",
    description=(
        "No player answers; host waits for timer to fire. "
        "Requires --fast (time_limit_seconds=2)."
    ),
    questions=[TF_ACCURACY],
    player_scripts=[
        PlayerScript(responses=[None]),
        PlayerScript(responses=[None]),
    ],
    requires_fast=True,
    uses_timer_expiry=True,
)


# ---------------------------------------------------------------------------
# Timer-expiry scenario WITH partial answers (requires --fast)
# ---------------------------------------------------------------------------

TIMER_EXPIRY_PARTIAL = EdgeScenario(
    name="timer_expiry_partial_answers",
    description=(
        "Player 0 answers correctly before the timer fires; player 1 does not answer. "
        "Verifies that answered scores are preserved and unanswered players get 0 "
        "when the server-side timer forces the phase end. Requires --fast."
    ),
    questions=[MC_ACCURACY],
    player_scripts=[
        PlayerScript(responses=[{"selectedIndex": 1}]),  # correct before timer
        PlayerScript(responses=[None]),                   # timer fires before this player answers
    ],
    requires_fast=True,
    uses_timer_expiry=True,
)


SCENARIOS: list[EdgeScenario] = [ALL_SKIP, MINIMAL, LATE_JOIN, TIMER_EXPIRY, TIMER_EXPIRY_PARTIAL]
