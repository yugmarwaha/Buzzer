"""Past Quizzes for signed-in players (T4 design §6 Player routes; extra feature).

`require_user` rejects guests. Only finished sessions (COMPLETED / ABANDONED) that the
caller actually attempted are visible, so a live game can never be read here. Questions an
abandoned game never reached are returned as `{order_index, played: false}` only — no
prompt, options, or answer.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common.dependencies import require_user
from ..common.exceptions import NotFoundError
from ..database import DbSession
from ..models.course import Course
from ..models.game import Game
from ..models.session import GameSession, SessionQuestion, SessionScore
from ..models.user import User
from ..schemas.player import PastQuizDetail, PastQuizItem
from ..services import game_service, image_service

router = APIRouter(prefix="/player", tags=["player"])

_FINISHED = ("COMPLETED", "ABANDONED")


async def _items(db: AsyncSession, user: User, session_id: str | None = None):
    scored = (
        select(
            SessionScore.session_id,
            func.sum(SessionScore.points_awarded).label("score"),
        )
        .where(SessionScore.user_id == user.id)
        .group_by(SessionScore.session_id)
        .subquery()
    )
    # Out of the questions actually reached, so an abandoned game isn't scored against
    # questions nobody was asked.
    max_score = (
        select(
            SessionQuestion.session_id,
            func.sum(SessionQuestion.points_value).label("max_score"),
        )
        .where(SessionQuestion.asked_at.is_not(None))
        .group_by(SessionQuestion.session_id)
        .subquery()
    )
    stmt = (
        select(
            GameSession,
            Game.title,
            Course.name,
            scored.c.score,
            max_score.c.max_score,
        )
        .join(scored, scored.c.session_id == GameSession.id)
        .join(Game, Game.id == GameSession.game_id)
        .join(Course, Course.id == GameSession.course_id)
        .outerjoin(max_score, max_score.c.session_id == GameSession.id)
        .where(GameSession.status.in_(_FINISHED))
        .order_by(GameSession.completed_at.desc(), GameSession.created_at.desc())
    )
    if session_id is not None:
        stmt = stmt.where(GameSession.id == session_id)
    return [
        (
            s,
            {
                "session_id": s.id,
                "game_title": title,
                "course_name": course_name,
                "status": s.status,
                "completed_at": s.completed_at,
                "score": float(score or 0),
                "max_score": float(max_pts or 0),
            },
        )
        for s, title, course_name, score, max_pts in (await db.execute(stmt)).all()
    ]


@router.get("/past-quizzes", response_model=list[PastQuizItem])
async def past_quizzes(
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> list[dict]:
    return [item for _s, item in await _items(db, user)]


@router.get("/past-quizzes/{session_id}", response_model=PastQuizDetail)
async def past_quiz_detail(
    session_id: str,
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> dict:
    rows = await _items(db, user, session_id)
    if not rows:
        # Not finished, not attempted by this player, or doesn't exist: same answer.
        raise NotFoundError(f"Past quiz {session_id} not found")
    session, item = rows[0]

    questions = []
    for r in await game_service.get_player_question_rows(db, session, user.id):
        if not r["played"]:
            questions.append({"order_index": r["orderIndex"], "played": False})
            continue
        questions.append(
            {
                "order_index": r["orderIndex"],
                "played": True,
                "prompt": r["prompt"],
                "type": r["type"],
                "gradingType": r["gradingType"],
                "config": r["config"],
                "playerAnswer": r["playerAnswer"],
                "pointsAwarded": r["pointsAwarded"],
                "maxPoints": r["maxPoints"],
                "isCorrect": r["isCorrect"],
                "answerReveal": r["answerReveal"],
            }
        )
    # T8: only played questions' images; nothing about unreached questions is exposed.
    image_urls = await image_service.image_urls(
        db, [q["config"] for q in questions if q["played"]]
    )
    return {**item, "questions": questions, "image_urls": image_urls}
