"""Past Quizzes response shapes (T4 design §6 Shapes)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PastQuizItem(BaseModel):
    session_id: str
    game_title: str
    course_name: str
    status: str
    completed_at: datetime | None
    score: float
    max_score: float


class PlayedQuestion(BaseModel):
    order_index: int
    played: Literal[True]
    prompt: str
    type: str
    gradingType: str
    config: dict
    playerAnswer: dict | None
    pointsAwarded: float
    maxPoints: float
    isCorrect: bool
    answerReveal: dict


class UnplayedQuestion(BaseModel):
    """Exactly this — nothing about a question the game never reached is exposed."""

    order_index: int
    played: Literal[False]


class PastQuizDetail(PastQuizItem):
    questions: list[PlayedQuestion | UnplayedQuestion]
    # T8: {"<image id>": "/api/images/<key>"} for the played questions' snapshot configs.
    image_urls: dict[str, str] = {}
