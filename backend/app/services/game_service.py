from __future__ import annotations

import asyncio
import math
import secrets
import uuid
from datetime import datetime, timezone

import structlog
from rapidfuzz.distance import Levenshtein
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from ..common.exceptions import ConflictError, ForbiddenError, NotFoundError
from ..models.course import CourseRoster, UserCourseAccess
from ..models.game import Question
from ..models.session import GameSession, SessionQuestion, SessionScore
from ..models.user import User
from ..schemas.admin import NEW_TYPES, is_grid_int, is_guess_number
from ..schemas.game import ScoreResult
from . import state_service as state
from .access_service import get_game_for_host
from .quiz_service import assert_not_archived

logger = structlog.get_logger()

# Room-ended listeners (T4 design §6 "Ending a room"). The websocket gateway registers one
# at import time so HTTP-triggered ends reach connected clients without routers or
# services importing websocket/ (dependencies stay routers → services ← websocket).
RoomEndedListener = Callable[[str, str], Awaitable[None]]  # (room_code, session_id)
room_ended_listeners: list[RoomEndedListener] = []


def register_room_ended_listener(fn: RoomEndedListener) -> None:
    room_ended_listeners.append(fn)


# Character set from spec: unambiguous alphanumeric (no 0/O/1/I/L)
_ROOM_CHARSET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_MAX_CODE_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Room code generation
# ---------------------------------------------------------------------------


async def generate_room_code(redis: Redis) -> str:
    for _ in range(_MAX_CODE_ATTEMPTS):
        code = "".join(secrets.choice(_ROOM_CHARSET) for _ in range(6))
        if not await redis.exists(f"room:{code}"):
            return code
    raise ConflictError("Could not generate a unique room code — try again")


# ---------------------------------------------------------------------------
# Access validation helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


async def create_room(
    db: AsyncSession,
    redis: Redis,
    host: User,
    game_id: int,
    course_id: int | None = None,
    max_rooms: int = 50,
) -> GameSession:
    # Access comes from the quiz's own course. The row lock serializes this against
    # concurrent edits/archives of the same quiz (design §6 Services). The course row is
    # locked first, shared (T8 design §8): the session insert below takes a foreign-key
    # shared lock on the course, which would otherwise come after the quiz lock.
    game = await get_game_for_host(
        db, host, game_id, for_update=True, shared_course_lock=True
    )
    if course_id is not None and course_id != game.course_id:
        raise HTTPException(
            status_code=422,
            detail="course_id does not match the quiz's course",
        )
    assert_not_archived(game)
    course_id = game.course_id

    # Enforce global room limit — count only LOBBY/IN_PROGRESS rooms.
    # COMPLETED/ABANDONED rooms may linger in Redis briefly for reconnection
    # but do not consume a concurrent-room slot.
    all_room_keys = await redis.keys("room:*")
    room_count = 0
    for key in all_room_keys:
        key_str = key if isinstance(key, str) else key.decode()
        if len(key_str) != 11:  # "room:" (5 chars) + 6-char code
            continue
        room_state_data = await state.get_room_state(redis, key_str[5:])
        if room_state_data and room_state_data.get("status") in (
            "LOBBY",
            "IN_PROGRESS",
        ):
            room_count += 1
    if room_count >= max_rooms:
        raise ConflictError(f"Maximum of {max_rooms} concurrent rooms reached")

    room_code = await generate_room_code(redis)

    session = GameSession(
        id=str(uuid.uuid4()),
        room_code=room_code,
        game_id=game_id,
        course_id=course_id,
        host_user_id=host.id,
        status="LOBBY",
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    # Store room state in Redis
    await state.set_room_state(
        redis,
        room_code,
        {
            "session_id": session.id,
            "game_id": game_id,
            "course_id": course_id,
            "host_user_id": host.id,
            "status": "LOBBY",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    logger.info(
        "room_created",
        room_code=room_code,
        session_id=session.id,
        host_id=host.id,
        game_id=game_id,
        course_id=course_id,
    )
    return session


async def get_session_by_code(
    db: AsyncSession, redis: Redis, room_code: str
) -> tuple[GameSession, dict | None]:
    """
    Return (session, redis_state). redis_state is None if Redis has no record
    (session may still be in MySQL as COMPLETED/ABANDONED).
    """
    redis_state = await state.get_room_state(redis, room_code)

    result = await db.execute(
        select(GameSession)
        .where(GameSession.room_code == room_code)
        .options(selectinload(GameSession.game))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise NotFoundError(f"Room '{room_code}' not found or has expired")

    return session, redis_state


async def start_game(
    db: AsyncSession, redis: Redis, session: GameSession
) -> list[Question]:
    """Transition LOBBY → IN_PROGRESS. Returns ordered question list."""
    if session.status != "LOBBY":
        raise ConflictError(f"Cannot start a game in '{session.status}' state")

    result = await db.execute(
        select(Question)
        .where(Question.game_id == session.game_id)
        .order_by(Question.order_index)
    )
    questions = result.scalars().all()
    if not questions:
        raise ConflictError(
            "This game has no questions — add questions before starting"
        )

    # Compare-and-set: if the room was ended (or removed) meanwhile, nothing changes and
    # we stop before writing any Redis state.
    result = await db.execute(
        update(GameSession)
        .where(GameSession.id == session.id, GameSession.status == "LOBBY")
        .values(status="IN_PROGRESS")
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise ConflictError("This room has already ended")

    # Snapshot what is about to be asked, in the same transaction.
    for q in questions:
        db.add(
            SessionQuestion(
                session_id=session.id,
                question_id=q.id,
                order_index=q.order_index,
                type=q.type,
                grading_type=q.grading_type,
                prompt=q.prompt,
                config=q.config,
                answer_data=q.answer_data,
                time_limit_seconds=q.time_limit_seconds,
                points_value=q.points_value,
            )
        )
    # Commit immediately so concurrent on_submit_answer handlers in separate DB
    # sessions see IN_PROGRESS rather than the unflushed LOBBY status.
    await db.commit()

    # Re-read in a fresh transaction right before the Redis write: an end_room that was
    # waiting on the row lock may have committed since, and its Redis cleanup must not be
    # undone (design §6). populate_existing also refreshes `session` in place.
    current = (
        await db.execute(
            select(GameSession)
            .where(GameSession.id == session.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if current is None or current.status != "IN_PROGRESS":
        raise ConflictError("This room has already ended")

    # Update Redis status (locked read-modify-write; a missing room key is not recreated)
    await state.update_room_state(
        redis, session.room_code, lambda rs: rs.update(status="IN_PROGRESS")
    )

    logger.info("game_started", session_id=session.id, question_count=len(questions))
    return list(questions)


async def complete_game(db: AsyncSession, redis: Redis, session: GameSession) -> bool:
    """IN_PROGRESS → COMPLETED as a compare-and-set, so it can never overwrite a room that
    was ended meanwhile. Returns False (and changes nothing) if the room already ended."""
    session_id = session.id
    result = await db.execute(
        update(GameSession)
        .where(GameSession.id == session_id, GameSession.status == "IN_PROGRESS")
        .values(status="COMPLETED", completed_at=_utcnow())
        .execution_options(synchronize_session=False)
    )
    # Commit so player join attempts racing with game_over see COMPLETED.
    await db.commit()
    if result.rowcount != 1:
        # end_room may have deleted the row, so there is nothing to refresh.
        logger.info("complete_game_noop_already_ended", session_id=session_id)
        return False
    await db.refresh(session)
    logger.info("game_completed", session_id=session_id)
    return True


async def fresh_session_status(db: AsyncSession, session_id: str) -> str | None:
    """Read the session status as of *now*. Committing first ends the REPEATABLE READ
    snapshot, so the read sees a concurrent end_room commit (design §6)."""
    await db.commit()
    result = await db.execute(
        select(GameSession.status)
        .where(GameSession.id == session_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def end_room(db: AsyncSession, redis: Redis, session_id: str) -> str:
    """The one way to end an active room (Rooms tab, live End game, admin, and the
    host-disconnect timer). Callers have already done the access checks.

    Order matters (design §6): lock → fresh answer check → abandon or remove → commit →
    only then clear Redis and notify listeners. Returns "abandoned" or "removed".
    """
    session = (
        await db.execute(
            select(GameSession)
            .where(GameSession.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not session or session.status not in ("LOBBY", "IN_PROGRESS"):
        await db.rollback()
        raise ConflictError("This room has already ended")
    room_code = session.room_code

    # Locking read: sees the latest committed answers, not this transaction's snapshot.
    has_answers = (
        await db.execute(
            select(SessionScore.id)
            .where(SessionScore.session_id == session_id)
            .limit(1)
            .with_for_update(read=True)
        )
    ).first() is not None

    if has_answers:
        await db.execute(
            update(GameSession)
            .where(
                GameSession.id == session_id,
                GameSession.status.in_(("LOBBY", "IN_PROGRESS")),
            )
            .values(status="ABANDONED", completed_at=_utcnow())
            .execution_options(synchronize_session=False)
        )
        outcome = "abandoned"
    else:
        # session_scores / session_questions cascade at the DB level.
        await db.execute(
            delete(GameSession)
            .where(GameSession.id == session_id)
            .execution_options(synchronize_session=False)
        )
        outcome = "removed"

    await db.commit()
    if outcome == "removed":
        db.expunge(session)

    # The commit is final, so the cleanup must run to completion even if the caller is
    # cancelled now (e.g. a host reconnect cancelling the disconnect timer): shield it in
    # its own task. The listeners therefore run in that task, not the caller's.
    task = asyncio.ensure_future(
        _after_room_ended(redis, room_code, session_id, outcome)
    )
    _after_end_tasks.add(task)
    task.add_done_callback(_after_end_tasks.discard)
    await asyncio.shield(task)
    if outcome == "abandoned":
        await db.refresh(session)
    return outcome


# Strong references so a shielded cleanup isn't garbage-collected if its caller is cancelled.
_after_end_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


async def _after_room_ended(
    redis: Redis, room_code: str, session_id: str, outcome: str
) -> None:
    """end_room step 6: only after a successful commit, clear Redis, then notify."""
    await state.delete_room_state(redis, room_code, session_id)
    for listener in list(room_ended_listeners):
        try:
            await listener(room_code, session_id)
        except Exception:
            logger.exception("room_ended_listener_error", session_id=session_id)
    logger.info("room_ended", session_id=session_id, outcome=outcome)


async def mark_question_asked(
    db: AsyncSession, session_id: str, question_id: int
) -> None:
    await db.execute(
        update(SessionQuestion)
        .where(
            SessionQuestion.session_id == session_id,
            SessionQuestion.question_id == question_id,
            SessionQuestion.asked_at.is_(None),
        )
        .values(asked_at=_utcnow())
        .execution_options(synchronize_session=False)
    )


async def load_snapshot(
    db: AsyncSession, session: GameSession
) -> list[SessionQuestion]:
    """The questions as asked in this session, in order. Never falls back to the live
    questions: a started session without a snapshot is an error (design §4)."""
    rows = (
        (
            await db.execute(
                select(SessionQuestion)
                .where(SessionQuestion.session_id == session.id)
                .order_by(SessionQuestion.order_index, SessionQuestion.question_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows and session.status != "LOBBY":
        logger.error("session_snapshot_missing", session_id=session.id)
        raise ConflictError("No record of this session's questions")
    return list(rows)


# ---------------------------------------------------------------------------
# Player authorisation at join time
# ---------------------------------------------------------------------------


async def authorise_player(db: AsyncSession, user: User, session: GameSession) -> str:
    """
    Check whether the player is permitted to join this session.
    Returns the display name to use for the player.
    Raises ForbiddenError / NotFoundError if not authorised.
    """
    if user.role == "GUEST":
        return user.display_name or "Guest"

    if user.role == "ADMIN":
        return user.display_name or user.username or "Admin"

    # OAuth2 user — check course roster
    if user.netid:
        result = await db.execute(
            select(CourseRoster).where(
                CourseRoster.course_id == session.course_id,
                CourseRoster.netid == user.netid,
                CourseRoster.is_active == True,  # noqa: E712
            )
        )
        roster_entry = result.scalar_one_or_none()
        if roster_entry:
            return roster_entry.full_name  # first name from CSV

    # Local account with explicit PLAYER role for this course
    result = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user.id,
            UserCourseAccess.course_id == session.course_id,
            UserCourseAccess.role == "PLAYER",
        )
    )
    if result.scalar_one_or_none():
        return user.display_name or user.username or "Player"

    # Local account with HOST role can also join as player
    result = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user.id,
            UserCourseAccess.course_id == session.course_id,
            UserCourseAccess.role == "HOST",
        )
    )
    if result.scalar_one_or_none():
        return user.display_name or user.username or "Host"

    raise ForbiddenError("You are not enrolled in the course roster for this game")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T7 types (pin_drop, closest_guess) — design docs/plans/t7-new-question-types.md
# ---------------------------------------------------------------------------

_GUESS_EPS = 1e-9


def validate_answer(question, answer_data) -> str | None:
    """Shape check for a T7 answer; returns an error message, or None if valid.
    Applies under both grading modes, before any points are awarded (design §2.5).
    `question` may be a live Question or a SessionQuestion snapshot."""
    if not isinstance(answer_data, dict):
        return "answer_data must be an object"
    if question.type == "pin_drop":
        if set(answer_data) != {"x", "y"} or not all(
            is_grid_int(answer_data[k]) for k in ("x", "y")
        ):
            return "pin_drop answer must be integers x and y from -5 to 5"
        return None
    if question.type == "closest_guess":
        if set(answer_data) != {"value"} or not is_guess_number(answer_data["value"]):
            return "closest_guess answer must be a finite number no larger than 1e12"
        return None
    return None


def closest_guess_result(value: float, answer_data: dict, points_value: float):
    """(award, band) for an ACCURACY closest_guess answer — the only place these rules
    live (design §3.2). Band follows the award: full <=> award == points_value,
    partial <=> 0 < award < points_value, miss <=> award == 0."""
    e = abs(value - answer_data["correct"])
    full = answer_data["fullTolerance"]
    partial = answer_data["partialTolerance"]
    if e <= full + _GUESS_EPS:
        return points_value, "full"
    if e >= partial - _GUESS_EPS:
        return 0.0, "miss"
    raw = points_value * (partial - e) / (partial - full)
    # Truncate to cents, then cap a cent below full: float division can round the ratio
    # to exactly 1.0, and partial credit must never equal full points.
    award = min(math.floor(raw * 100) / 100, points_value - 0.01)
    if award <= 0:
        return 0.0, "miss"
    return award, "partial"


def new_type_dist_key(question, answer_data: dict | None) -> str | None:
    """Answer-distribution key for a T7 answer, or None (no entry). `question` may be a
    live Question or a snapshot. Used by record_answer and get_host_question_summary."""
    if not answer_data or validate_answer(question, answer_data) is not None:
        return None
    if question.type == "pin_drop":
        return f"{answer_data['x']},{answer_data['y']}"
    if question.type == "closest_guess" and question.grading_type == "ACCURACY":
        _, band = closest_guess_result(
            answer_data["value"], question.answer_data, question.points_value
        )
        return band
    return None


def normalize_fill_in(text: str | None) -> str:
    """Lowercase and collapse whitespace. The one normalization of a fill-in answer, for
    grading and for the live and game-over distribution keys."""
    return " ".join((text or "").lower().split())


def calculate_score(question: Question, answer_data: dict) -> ScoreResult:
    """
    Calculate points for a player's answer.
    COMPLETENESS: any answer = full points, no answer = 0
    ACCURACY:     points come from answer_data.answer_points per selected option
    """
    if not answer_data:
        return ScoreResult(points_awarded=0, is_correct=False)

    # T7 types: an invalid answer scores nothing, under either grading mode.
    if question.type in NEW_TYPES and validate_answer(question, answer_data):
        return ScoreResult(points_awarded=0, is_correct=False)

    if question.grading_type == "COMPLETENESS":
        return ScoreResult(points_awarded=question.points_value, is_correct=True)

    # ACCURACY
    if question.type == "multiple_choice":
        selected = answer_data.get("selectedIndex")
        if selected is None:
            return ScoreResult(points_awarded=0, is_correct=False)
        pts_list: list[float] = question.answer_data.get("answer_points", [])
        if not (0 <= selected < len(pts_list)):
            return ScoreResult(points_awarded=0, is_correct=False)
        points = pts_list[selected]
        # Same rule as answer_reveal (p >= points_value); never correct for a 0-point
        # question, as for multi_select.
        return ScoreResult(
            points_awarded=points,
            is_correct=(points >= question.points_value and question.points_value > 0),
        )

    if question.type == "true_false":
        selected = answer_data.get("selectedValue")
        if selected is None:
            return ScoreResult(points_awarded=0, is_correct=False)
        pts_map: dict = question.answer_data.get("answer_points", {})
        key = "true" if selected else "false"
        points = pts_map.get(key, 0)
        return ScoreResult(
            points_awarded=points,
            is_correct=(points >= question.points_value and question.points_value > 0),
        )

    if question.type == "fill_in_the_blank":
        text = normalize_fill_in(answer_data.get("text"))
        if not text:
            return ScoreResult(points_awarded=0, is_correct=False)
        accepted = [
            normalize_fill_in(a)
            for a in question.answer_data.get("acceptedAnswers", [])
        ]
        answer_pts: list[float] = question.answer_data.get("answerPoints", [])
        max_dist = int(question.answer_data.get("editDistance", 0))
        best: float = 0.0
        for i, a in enumerate(accepted):
            if Levenshtein.distance(text, a) <= max_dist:
                pts = answer_pts[i] if i < len(answer_pts) else question.points_value
                best = max(best, pts)
        return ScoreResult(
            points_awarded=best,
            is_correct=(best >= question.points_value and question.points_value > 0),
        )

    if question.type == "multi_select":
        selected = answer_data.get("selectedIndices")
        if not isinstance(selected, list):
            return ScoreResult(points_awarded=0, is_correct=False)
        pts_list: list[float] = question.answer_data.get("answer_points", [])
        # Each option counts once: a repeated index must not add its points again.
        raw = sum(
            pts_list[i] for i in _unique_indices(selected) if 0 <= i < len(pts_list)
        )
        score = max(0.0, raw)
        return ScoreResult(
            points_awarded=score,
            is_correct=(score >= question.points_value and question.points_value > 0),
        )

    if question.type == "pin_drop":
        target = question.answer_data.get("target", {})
        hit = answer_data["x"] == target.get("x") and answer_data["y"] == target.get(
            "y"
        )
        return ScoreResult(
            points_awarded=question.points_value if hit else 0, is_correct=hit
        )

    if question.type == "closest_guess":
        award, band = closest_guess_result(
            answer_data["value"], question.answer_data, question.points_value
        )
        return ScoreResult(points_awarded=award, is_correct=(band == "full"))

    return ScoreResult(points_awarded=0, is_correct=False)


def _unique_indices(indices: list) -> list[int]:
    """The int entries of a multi_select selectedIndices list, each once, in order."""
    return list(dict.fromkeys(i for i in indices if isinstance(i, int)))


def _answer_dist_keys(question, answer_data: dict) -> list[str]:
    """Distribution keys one answer adds to (several for multi_select, each option once).
    `question` may be a live Question or a snapshot."""
    if question.type == "multiple_choice":
        idx = answer_data.get("selectedIndex")
        return [str(idx)] if idx is not None else []
    if question.type == "true_false":
        val = answer_data.get("selectedValue")
        return ["true" if val else "false"] if val is not None else []
    if question.type == "fill_in_the_blank":
        text = normalize_fill_in(answer_data.get("text"))
        return [text] if text else []
    if question.type == "multi_select":
        indices = answer_data.get("selectedIndices")
        if isinstance(indices, list):
            return [str(i) for i in _unique_indices(indices)]
        return []
    if question.type in NEW_TYPES:
        key = new_type_dist_key(question, answer_data)
        return [key] if key is not None else []
    return []


async def record_answer(
    db: AsyncSession,
    redis: Redis,
    session_id: str,
    user_id: str,
    question: Question,
    answer_data: dict,
    answer_time_ms: int | None,
    still_open: Callable[[], Awaitable[bool]] | None = None,
) -> ScoreResult:
    """
    Score an answer, write it to MySQL immediately, and update Redis score.
    Returns the score result.

    The caller has already claimed the answer (state.claim_answer) and commits right
    after this returns. still_open, if given, is checked after the flush (the insert
    then holds a shared lock on the session row until the commit, see
    wait_for_answer_writes): if the question closed meanwhile (lock, results), the insert
    is rolled back and ConflictError is raised, before any Redis score/dist update.
    The Redis updates happen before the commit, so the results (read once
    wait_for_answer_writes sees the commit) include them; if the caller's commit then
    fails, it must call revert_answer to undo them.
    """
    result = calculate_score(question, answer_data)

    # Persist to MySQL immediately (per-question persistence guarantee)
    score_row = SessionScore(
        session_id=session_id,
        user_id=user_id,
        question_id=question.id,
        points_awarded=result.points_awarded,
        answer_time_ms=answer_time_ms,
        is_correct=result.is_correct,
        answer_data=answer_data or None,
    )
    db.add(score_row)
    try:
        await db.flush()
    except IntegrityError:
        # Usually end_room removed an empty room (no answers yet) while this insert
        # waited, so the session row is gone and the FK check fails. Any other integrity
        # failure (e.g. the user row vanished) gets a generic message.
        await db.rollback()
        session_exists = (
            await db.execute(select(GameSession.id).where(GameSession.id == session_id))
        ).scalar_one_or_none()
        if session_exists is None:
            raise ConflictError("Session is not in progress")
        raise ConflictError("Could not record answer")

    # The caller's status check read this transaction's snapshot. If end_room committed
    # while the insert waited on the session-row lock, the room is gone from Redis:
    # reject the answer instead of recreating per-session keys. A locking read sees the
    # latest committed status (the FK check already holds a shared lock on the row).
    status = (
        await db.execute(
            select(GameSession.status)
            .where(GameSession.id == session_id)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if status != "IN_PROGRESS":
        await db.rollback()
        raise ConflictError("Session is not in progress")

    # A late answer: it passed the caller's phase/lock check, but the question was locked
    # or moved to RESULTS while it was being written.
    if still_open is not None and not await still_open():
        await db.rollback()
        raise ConflictError("Question is closed")

    # Update Redis running score and answered set
    await state.update_player_score(redis, session_id, user_id, result.points_awarded)
    await state.mark_answered(redis, session_id, question.id, user_id)

    # Increment per-option distribution counter for the results bar chart
    for dist_key in _answer_dist_keys(question, answer_data):
        await state.increment_answer_dist(redis, session_id, question.id, dist_key)

    return result


async def revert_answer(
    redis: Redis,
    session_id: str,
    user_id: str,
    question: Question,
    answer_data: dict,
    result: ScoreResult,
) -> None:
    """Undo record_answer's Redis score and distribution updates when the caller's
    commit failed, so a resubmit (the claim is released) does not count twice."""
    await state.update_player_score(redis, session_id, user_id, -result.points_awarded)
    for dist_key in _answer_dist_keys(question, answer_data):
        await state.decrement_answer_dist(redis, session_id, question.id, dist_key)


async def wait_for_answer_writes(db: AsyncSession, session_id: str) -> None:
    """Wait until answer writes in flight for this session have committed or rolled back,
    then start a fresh snapshot. Call it after the room has left QUESTION and before
    reading scores for the results. An answer insert holds a shared lock on the session
    row (FK check and record_answer's locking read) from its flush to its commit, so this
    exclusive locking read waits for it. An answer that flushes after this sees the closed
    question in its still_open check and rolls back. The lock is released at once."""
    await db.commit()
    await db.execute(
        select(GameSession.id).where(GameSession.id == session_id).with_for_update()
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


async def get_leaderboard(db: AsyncSession, session_id: str) -> list[dict]:
    """
    Aggregate session_scores from MySQL to produce a leaderboard.
    Used for QUESTION_RESULTS and GAME_OVER broadcasts.
    """
    from sqlalchemy import func as sqlfunc

    rows = await db.execute(
        select(
            SessionScore.user_id,
            sqlfunc.sum(SessionScore.points_awarded).label("total_score"),
        )
        .where(SessionScore.session_id == session_id)
        .group_by(SessionScore.user_id)
        .order_by(sqlfunc.sum(SessionScore.points_awarded).desc())
    )
    return [{"user_id": r.user_id, "score": float(r.total_score or 0)} for r in rows]


def answer_reveal(q) -> dict:
    """Client-safe correct-answer facts for a Question or SessionQuestion snapshot.
    answer_data itself is never sent to clients."""
    q_ans = q.answer_data or {}
    pts_val = q.points_value
    if q.grading_type == "COMPLETENESS":
        return {"type": "completeness"}
    if q.type == "multiple_choice":
        pts = q_ans.get("answer_points", [])
        return {
            "type": "multiple_choice",
            "correctIndices": [i for i, p in enumerate(pts) if p >= pts_val],
        }
    if q.type == "true_false":
        pts_map = q_ans.get("answer_points", {})
        return {"type": "true_false", "correctValue": pts_map.get("true", 0) >= pts_val}
    if q.type == "fill_in_the_blank":
        return {
            "type": "fill_in_the_blank",
            "acceptedAnswers": q_ans.get("acceptedAnswers", []),
            "editDistance": q_ans.get("editDistance", 0),
        }
    if q.type == "multi_select":
        return {"type": "multi_select", "answerPoints": q_ans.get("answer_points", [])}
    if q.type == "pin_drop":
        return {"type": "pin_drop", "target": q_ans.get("target")}
    if q.type == "closest_guess":
        return {
            "type": "closest_guess",
            "correct": q_ans.get("correct"),
            "fullTolerance": q_ans.get("fullTolerance"),
            "partialTolerance": q_ans.get("partialTolerance"),
            "unit": (q.config or {}).get("unit"),
        }
    return {}


async def get_player_question_rows(
    db: AsyncSession, session: GameSession, user_id: str
) -> list[dict]:
    """One row per snapshotted question with the player's answer (if any) and whether the
    question was ever reached (`played`)."""
    snapshot = await load_snapshot(db, session)
    scores = {
        s.question_id: s
        for s in (
            await db.execute(
                select(SessionScore).where(
                    SessionScore.session_id == session.id,
                    SessionScore.user_id == user_id,
                )
            )
        )
        .scalars()
        .all()
    }
    rows = []
    for q in snapshot:
        score = scores.get(q.question_id)
        rows.append(
            {
                "questionId": q.question_id,
                "orderIndex": q.order_index,
                "played": q.asked_at is not None,
                "prompt": q.prompt,
                "type": q.type,
                "gradingType": q.grading_type,
                "config": q.config or {},
                "pointsAwarded": score.points_awarded if score else 0,
                "maxPoints": q.points_value,
                "isCorrect": score.is_correct if score else False,
                "answerTimeMs": score.answer_time_ms if score else None,
                "playerAnswer": score.answer_data if score else None,
                "answerReveal": answer_reveal(q),
            }
        )
    return rows


async def get_player_question_summary(
    db: AsyncSession, session_id: str, user_id: str
) -> list[dict]:
    """Per-question breakdown for a single player — used in GAME_OVER payload."""
    session = await db.get(GameSession, session_id)
    if not session:
        return []
    return [
        {k: v for k, v in r.items() if k not in ("orderIndex", "played")}
        for r in await get_player_question_rows(db, session, user_id)
    ]


async def get_host_question_summary(
    db: AsyncSession, session_id: str, total_players: int
) -> list[dict]:
    """Per-question breakdown for the host game-over screen, from the snapshot.
    Returns prompt, answer reveal, distribution, and correct/answered counts."""
    from collections import defaultdict

    session = await db.get(GameSession, session_id)
    if not session:
        return []
    snapshot = await load_snapshot(db, session)
    scores_by_q: dict[int, list[SessionScore]] = defaultdict(list)
    for s in (
        await db.execute(
            select(SessionScore).where(SessionScore.session_id == session_id)
        )
    ).scalars():
        scores_by_q[s.question_id].append(s)

    result = []
    for qnum, q in enumerate(snapshot, start=1):
        scores = scores_by_q[q.question_id]
        q_type = q.type
        dist: dict[str, int] = {}
        correct_count = 0
        total_time = 0
        time_count = 0
        for s in scores:
            ans = s.answer_data
            if ans:
                # Same keys as the live distribution (record_answer).
                for k in _answer_dist_keys(q, ans):
                    dist[k] = dist.get(k, 0) + 1
            if s.is_correct:
                correct_count += 1
            if s.answer_time_ms is not None:
                total_time += s.answer_time_ms
                time_count += 1

        result.append(
            {
                "questionId": q.question_id,
                "questionNumber": qnum,
                "prompt": q.prompt,
                "type": q_type,
                "gradingType": q.grading_type,
                "config": q.config or {},
                "pointsValue": q.points_value,
                "answerReveal": answer_reveal(q),
                "answerDistribution": dist,
                "totalAnswered": len(scores),
                "totalPlayers": total_players,
                "correctCount": correct_count,
                "avgAnswerTimeMs": round(total_time / time_count)
                if time_count
                else None,
            }
        )

    return result


# ---------------------------------------------------------------------------
# Guest merge (host and admin routers share this)
# ---------------------------------------------------------------------------


async def merge_guest(
    db: AsyncSession, session: GameSession, guest_user_id: str, target_netid: str
) -> User:
    """Move one session's scores from a guest to a rostered NetID user.

    Only this session's scores move. The guest must have scores here (else 404); the netid
    must be active on the session's course roster (else 404) and belong to an existing user
    (else 409 — they haven't signed in yet); a target who already has scores in this session
    is 409. The guest row is deleted only once it has no scores left anywhere.
    Access checks are the caller's job. Returns the target user.
    """
    if not guest_user_id or not target_netid:
        raise ConflictError("guest_user_id and target_netid are required")

    # Lock order: guest row, then target row, and only then any session_scores rows. Two
    # merges into one target both wait on the target lock holding no score locks (a score
    # lock taken earlier would gap-lock the index the winner writes into: deadlock). The
    # score reads after the locks are locking reads: under REPEATABLE READ a plain SELECT
    # reads this transaction's snapshot, which can predate a merge that committed while
    # this one waited.
    guest = (
        await db.execute(select(User).where(User.id == guest_user_id).with_for_update())
    ).scalar_one_or_none()

    def guest_scores_stmt():
        return select(SessionScore).where(
            SessionScore.session_id == session.id,
            SessionScore.user_id == guest_user_id,
        )

    # Early, unlocked check so a wrong guest is reported before roster/account problems;
    # re-read with a lock below.
    has_scores = (
        await db.execute(guest_scores_stmt().limit(1))
    ).scalar_one_or_none() is not None
    if not guest or guest.role != "GUEST" or not has_scores:
        raise NotFoundError(f"Guest {guest_user_id} has no scores in this session")

    # Roster netids are stored stripped and lowercased (roster_service).
    netid = target_netid.strip().lower()
    on_roster = (
        await db.execute(
            select(CourseRoster.id).where(
                CourseRoster.course_id == session.course_id,
                CourseRoster.netid == netid,
                CourseRoster.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if on_roster is None:
        raise NotFoundError(f"NetID '{netid}' is not on this course's active roster")

    target = (
        await db.execute(select(User).where(User.netid == netid).with_for_update())
    ).scalar_one_or_none()
    if not target:
        raise ConflictError(
            f"NetID '{netid}' has no Buzzer account yet; merge after they sign in once"
        )

    guest_scores = (
        (await db.execute(guest_scores_stmt().with_for_update())).scalars().all()
    )
    if not guest_scores:
        raise NotFoundError(f"Guest {guest_user_id} has no scores in this session")

    already = (
        await db.execute(
            select(SessionScore.id)
            .where(
                SessionScore.session_id == session.id,
                SessionScore.user_id == target.id,
            )
            .limit(1)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if already is not None:
        raise ConflictError(
            f"NetID '{netid}' already has answers in this session; can't merge a guest into it"
        )

    for score in guest_scores:
        score.user_id = target.id
    await db.flush()

    remaining = (
        await db.execute(
            select(SessionScore.id)
            .where(SessionScore.user_id == guest.id)
            .limit(1)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if remaining is None:
        await db.delete(guest)
        await db.flush()

    logger.info(
        "guest_merged",
        guest_id=guest_user_id,
        real_user_id=target.id,
        session_id=session.id,
        guest_deleted=remaining is None,
    )
    return target


# ---------------------------------------------------------------------------
# Course session / room listings (host app)
# ---------------------------------------------------------------------------


def _display(u: User | None) -> str | None:
    if u is None:
        return None
    return u.display_name or u.username or u.netid or u.id


async def _session_rows(db: AsyncSession, sessions: list[GameSession]):
    from sqlalchemy import func as sqlfunc

    from ..models.game import Game

    ids = [s.id for s in sessions]
    game_ids = {s.game_id for s in sessions}
    host_ids = {s.host_user_id for s in sessions if s.host_user_id}
    titles = (
        dict(
            (
                await db.execute(
                    select(Game.id, Game.title).where(Game.id.in_(game_ids))
                )
            ).all()
        )
        if game_ids
        else {}
    )
    hosts = (
        {
            u.id: u
            for u in (
                await db.execute(select(User).where(User.id.in_(host_ids)))
            ).scalars()
        }
        if host_ids
        else {}
    )
    players = (
        dict(
            (
                await db.execute(
                    select(
                        SessionScore.session_id,
                        sqlfunc.count(SessionScore.user_id.distinct()),
                    )
                    .where(SessionScore.session_id.in_(ids))
                    .group_by(SessionScore.session_id)
                )
            ).all()
        )
        if ids
        else {}
    )
    return titles, hosts, players


async def list_finished_sessions(
    db: AsyncSession, course_id: int, user: User
) -> list[dict]:
    """COMPLETED sessions, plus ABANDONED ones that have answers, newest first."""
    has_scores = (
        select(SessionScore.id)
        .where(SessionScore.session_id == GameSession.id)
        .exists()
    )
    sessions = list(
        (
            await db.execute(
                select(GameSession)
                .where(
                    GameSession.course_id == course_id,
                    (GameSession.status == "COMPLETED")
                    | ((GameSession.status == "ABANDONED") & has_scores),
                )
                .order_by(
                    GameSession.completed_at.desc(), GameSession.created_at.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    titles, hosts, players = await _session_rows(db, sessions)
    return [
        {
            "session_id": s.id,
            "room_code": s.room_code,
            "status": s.status,
            "game_id": s.game_id,
            "game_title": titles.get(s.game_id, "Unknown"),
            "host_user_id": s.host_user_id,
            "host_display_name": _display(hosts.get(s.host_user_id)),
            "created_at": s.created_at,
            "completed_at": s.completed_at,
            "player_count": players.get(s.id, 0),
            "can_merge_guests": user.role == "ADMIN"
            or (s.host_user_id is not None and s.host_user_id == user.id),
        }
        for s in sessions
    ]


async def list_active_rooms(
    db: AsyncSession, redis: Redis, course_id: int, user: User
) -> list[dict]:
    """Every LOBBY/IN_PROGRESS session of the course, from MySQL. Redis only adds the live
    player count; a row with no Redis state is a stale room ("Not running")."""
    sessions = list(
        (
            await db.execute(
                select(GameSession)
                .where(
                    GameSession.course_id == course_id,
                    GameSession.status.in_(("LOBBY", "IN_PROGRESS")),
                )
                .order_by(GameSession.created_at)
            )
        )
        .scalars()
        .all()
    )
    titles, hosts, players = await _session_rows(db, sessions)
    rows = []
    for s in sessions:
        live = await state.get_room_state(redis, s.room_code) is not None
        rows.append(
            {
                "session_id": s.id,
                "room_code": s.room_code,
                "status": s.status,
                "game_id": s.game_id,
                "game_title": titles.get(s.game_id, "Unknown"),
                "host_user_id": s.host_user_id,
                "host_display_name": _display(hosts.get(s.host_user_id)),
                "created_at": s.created_at,
                "live": live,
                "player_count": await state.get_player_count(redis, s.id)
                if live
                else None,
                "has_answers": players.get(s.id, 0) > 0,
                "answered_count": players.get(s.id, 0),
                # The caller already passed the course-host check.
                "can_end": user.role == "ADMIN"
                or (s.host_user_id is not None and s.host_user_id == user.id),
            }
        )
    return rows
