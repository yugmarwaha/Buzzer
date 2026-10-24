"""
Phase 5 — python-socketio WebSocket gateway.

Handles all real-time game communication between Host and Player clients.

Architecture notes:
- Each game room maps to a socket.io room named by its 6-char room code.
- Host sockets additionally join a "{room_code}:host" room for host-only events.
- Each player's socket joins a "user:{user_id}" room so the server can send
  personalised payloads (e.g. their score/rank) without exposing other players'
  data to the whole room.
- In-memory dicts track per-sid context, question timers, and host-abandon tasks.
  These are per-process; with the Redis manager, socket.io routes events correctly
  across instances while local task bookkeeping stays on the owning process.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import socketio
import structlog
from sqlalchemy import func, select

from ..common.exceptions import ConflictError
from ..config import settings
from ..database import AsyncSessionLocal
from ..models.game import Question
from ..models.session import GameSession, SessionQuestion, SessionScore
from ..redis_client import get_redis
from ..schemas.admin import NEW_TYPES
from ..services import game_service, image_service
from ..services import state_service as state
from ..services.auth_service import get_user_by_id
from . import events as E
from .middleware import authenticate_socket

logger = structlog.get_logger()

# Host-disconnect grace period (default 5 min; HOST_GRACE_SECONDS env overrides it)
_HOST_GRACE_SECONDS = settings.HOST_GRACE_SECONDS

# ---------------------------------------------------------------------------
# Server instantiation
# ---------------------------------------------------------------------------

# Use the Redis pubsub manager only in production (multi-instance scaling).
# In development the in-memory manager is simpler and more reliable — the
# AsyncRedisManager's pubsub listener drops connections under idle load, which
# silently breaks room-targeted emits.
_client_manager = (
    None if settings.is_development else socketio.AsyncRedisManager(settings.REDIS_URL)
)

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.cors_origins_list,
    client_manager=_client_manager,
    logger=False,
    engineio_logger=False,
)
socket_app = socketio.ASGIApp(sio, socketio_path="socket.io")

# ---------------------------------------------------------------------------
# Per-process in-memory state
# ---------------------------------------------------------------------------

# sid → {user_id, role, room_code, session_id}
_sid_ctx: dict[str, dict[str, Any]] = {}

# session_id → asyncio.Task (host disconnect abandon countdown)
_abandon_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]

# session_id → asyncio.Task (question time-limit countdown)
_timer_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _db():
    """Async DB session with auto-commit / auto-rollback."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _host_room(room_code: str) -> str:
    return f"{room_code}:host"


def _user_room(user_id: str) -> str:
    return f"user:{user_id}"


async def _question_payload(db, q: Question, number: int, total: int) -> dict:
    """Build the client-safe NEW_QUESTION payload (no answer_data). `imageUrls` maps the
    config's image IDs to URLs (T8 §5); clients never build URLs from IDs."""
    payload: dict = {
        "questionId": q.id,
        "type": q.type,
        "gradingType": q.grading_type,
        "prompt": q.prompt,
        "config": q.config,
        "timeLimitSeconds": q.time_limit_seconds,
        "pointsValue": q.points_value,
        "orderIndex": q.order_index,
        "questionNumber": number,
        "totalQuestions": total,
        "imageUrls": await image_service.image_urls(db, [q.config]),
    }
    if q.type == "fill_in_the_blank":
        payload["editDistance"] = q.answer_data.get("editDistance", 0)
    return payload


async def _host_question_payload(db, current_q: dict | None) -> dict | None:
    """Convert the compact Redis current-question record into a full client payload."""
    if not current_q:
        return None
    question = await db.get(Question, current_q["question_id"])
    if not question:
        return None
    payload = await _question_payload(
        db, question, current_q["question_number"], current_q["total_questions"]
    )
    payload["startedAt"] = current_q[
        "started_at"
    ]  # ISO timestamp so client can compute remaining time
    return payload


def _answer_reveal(q: Question) -> dict:
    """
    Derive a client-safe correct-answer reveal from answer_data.
    answer_data is NEVER forwarded directly — only derived facts are sent.
    """
    if q.grading_type == "COMPLETENESS":
        return {"type": "completeness"}
    if q.type == "multiple_choice":
        pts: list[int] = q.answer_data.get("answer_points", [])
        correct = [i for i, p in enumerate(pts) if p >= q.points_value]
        return {"type": "multiple_choice", "correctIndices": correct}
    if q.type == "true_false":
        pts_map: dict = q.answer_data.get("answer_points", {})
        correct_true = pts_map.get("true", 0) >= q.points_value
        return {"type": "true_false", "correctValue": correct_true}
    if q.type == "fill_in_the_blank":
        return {
            "type": "fill_in_the_blank",
            "acceptedAnswers": q.answer_data.get("acceptedAnswers", []),
            "editDistance": q.answer_data.get("editDistance", 0),
        }
    if q.type == "multi_select":
        return {
            "type": "multi_select",
            "answerPoints": q.answer_data.get("answer_points", []),
        }
    if q.type in NEW_TYPES:
        # Same shape as game-over and Past Quizzes; one definition, not another copy.
        return game_service.answer_reveal(q)
    return {}


async def _emit_error(sid: str, message: str) -> None:
    await sio.emit(E.ERROR, {"message": message}, to=sid)


# ---------------------------------------------------------------------------
# Payload builders shared by the live broadcasts and SYNC_STATE (page reload/reconnect)
# ---------------------------------------------------------------------------


async def _question_points(db, session_id: str, question_id: int) -> dict[str, int]:
    """{user_id: points_awarded} for one question. Selects columns, not ORM objects (see
    _on_host_advance_impl: loading SessionScore objects can trip the scores backref)."""
    rows = await db.execute(
        select(SessionScore.user_id, SessionScore.points_awarded).where(
            SessionScore.session_id == session_id,
            SessionScore.question_id == question_id,
        )
    )
    return {r.user_id: r.points_awarded for r in rows}


def _rank_of(leaderboard: list[dict], user_id: str) -> int:
    """Standard competition rank (equal totals share a rank: 1, 1, 3): one more than the
    number of players with a higher total. A player with no score rows has 0 points."""
    scores = {row["user_id"]: row["score"] for row in leaderboard}
    mine = scores.get(user_id, 0)
    return 1 + sum(1 for score in scores.values() if score > mine)


async def _host_results_payload(
    db, redis, session_id: str, question_id: int
) -> dict | None:
    """The host's QUESTION_RESULTS payload, rebuilt from MySQL/Redis."""
    question = await db.get(Question, question_id)
    if not question:
        return None
    q_scores = await _question_points(db, session_id, question_id)
    players = await state.get_all_players(redis, session_id)
    return {
        "questionId": question.id,
        "answerReveal": _answer_reveal(question),
        "answerDistribution": await state.get_answer_dist(
            redis, session_id, question.id
        ),
        "totalAnswered": len(q_scores),
        "totalPlayers": len(players),
        "imageUrls": await image_service.image_urls(db, [question.config]),
    }


async def _player_results_payload(
    db, redis, session_id: str, question_id: int, user_id: str
) -> dict | None:
    """One player's QUESTION_RESULTS payload, rebuilt from MySQL/Redis."""
    question = await db.get(Question, question_id)
    if not question:
        return None
    q_scores = await _question_points(db, session_id, question_id)
    leaderboard = await game_service.get_leaderboard(db, session_id)
    players = await state.get_all_players(redis, session_id)
    player = await state.get_player(redis, session_id, user_id)
    return {
        "questionId": question.id,
        "answerReveal": _answer_reveal(question),
        "yourPoints": q_scores.get(user_id, 0),
        "yourScore": player.get("score", 0) if player else 0,
        "yourRank": _rank_of(leaderboard, user_id),
        "playerCount": len(players),
        "imageUrls": await image_service.image_urls(db, [question.config]),
    }


async def _host_game_over_payload(
    db, session_id: str, leaderboard: list[dict], player_count: int
) -> dict:
    """The host's GAME_OVER payload: anonymous scores + per-question summary."""
    # Max possible score from the session's snapshot, not the live quiz
    max_possible_result = await db.execute(
        select(func.sum(SessionQuestion.points_value)).where(
            SessionQuestion.session_id == session_id
        )
    )
    summary = await game_service.get_host_question_summary(db, session_id, player_count)
    return {
        "scores": [row["score"] for row in leaderboard],
        "playerCount": player_count,
        "maxPossibleScore": int(max_possible_result.scalar_one() or 0),
        "questionSummary": summary,
        # From the snapshot configs, so old image versions resolve too (T8 §5).
        "imageUrls": await image_service.image_urls(db, [q["config"] for q in summary]),
    }


async def _player_game_over_payload(
    db,
    session_id: str,
    user_id: str,
    score: float,
    leaderboard: list[dict],
    player_count: int,
) -> dict:
    """One player's GAME_OVER payload: final score/rank + personal question recap."""
    summary = await game_service.get_player_question_summary(db, session_id, user_id)
    return {
        "yourFinalScore": score,
        "yourFinalRank": _rank_of(leaderboard, user_id),
        "playerCount": player_count,
        "questionSummary": summary,
        "imageUrls": await image_service.image_urls(db, [q["config"] for q in summary]),
    }


def _question_in_sync(room_state: dict | None, current_q: dict | None) -> bool:
    """True if room_state and current_q describe the same question. They are separate
    Redis keys written one after the other (an advance writes room_state, then current_q),
    and room_state is a read-modify-write blob that a concurrent handler can overwrite
    with an older copy, so a reader can see one key's new value next to the other's old
    one."""
    return bool(
        room_state
        and current_q
        and room_state.get("question_index", -1)
        == current_q.get("question_number", 0) - 1
    )


def _revealable_question_id(
    room_state: dict | None, current_q: dict | None
) -> int | None:
    """The question whose results (correct answer included) a sync may rebuild, or None.
    Only when the room is in RESULTS *for current_q*: the RESULTS phase records the
    question it belongs to (results_question_id), and the index must match too, so a
    stale RESULTS blob next to a newer open question never reveals that question."""
    if not room_state or room_state.get("question_phase") != "RESULTS":
        return None
    if not _question_in_sync(room_state, current_q):
        return None
    qid = current_q["question_id"]
    return qid if room_state.get("results_question_id") == qid else None


async def _read_live_state(
    redis, session_id: str, room_code: str
) -> tuple[dict | None, dict | None]:
    """(current_q, room_state), read in that order. Must be called after the socket has
    entered its rooms, so any change made after these reads also reaches it as an event.
    Reading current_q first matters: an advance writes room_state before current_q, so a
    new current_q is never paired with the room_state from before that advance."""
    current_q = await state.get_current_question(redis, session_id)
    room_state = await state.get_room_state(redis, room_code)
    return current_q, room_state


async def _host_sync_payload(db, redis, session, room_code: str) -> dict:
    """SYNC_STATE for a HOST socket. Carries everything the current host screen shows, so
    a page reload restores it: answered count while a question is open, the results chart
    in RESULTS, and the game-over summary once the game is COMPLETED. Reads state itself
    (see _read_live_state); results only when _revealable_question_id allows them.

    The session status is read fresh here too, after the socket entered its rooms: the
    final advance commits COMPLETED before it emits game_over, so a host that joins
    during it either gets that broadcast or sees COMPLETED here (never neither)."""
    status = await game_service.fresh_session_status(db, session.id) or session.status
    current_q, room_state = await _read_live_state(redis, session.id, room_code)
    players = await state.get_all_players(redis, session.id)
    question_phase = room_state.get("question_phase") if room_state else None
    in_sync = _question_in_sync(room_state, current_q)
    payload: dict = {
        "role": "HOST",
        "status": status,
        "questionPhase": question_phase,
        "questionIndex": room_state.get("question_index", -1) if room_state else -1,
        "players": players,
        "playerCount": len(players),
        # No answer data in it; an out-of-sync one is replaced by the next new_question.
        "currentQuestion": await _host_question_payload(db, current_q),
        "questionLocked": room_state.get("question_locked", False)
        if room_state
        else False,
        "timerRemainingSeconds": room_state.get("timer_remaining_seconds")
        if room_state
        else None,
        "answeredCount": 0,
        "allAnswered": False,
        "questionResults": None,
        "gameOver": None,
    }
    if status == "IN_PROGRESS" and in_sync:
        qid = current_q["question_id"]
        payload["answeredCount"] = await state.get_answered_count(
            redis, session.id, qid
        )
        if question_phase == "QUESTION":
            payload["allAnswered"] = await state.all_players_answered(
                redis, session.id, qid
            )
        reveal_qid = _revealable_question_id(room_state, current_q)
        if reveal_qid is not None:
            payload["questionResults"] = await _host_results_payload(
                db, redis, session.id, reveal_qid
            )
    elif status == "COMPLETED":
        leaderboard = await game_service.get_leaderboard(db, session.id)
        payload["gameOver"] = await _host_game_over_payload(
            db, session.id, leaderboard, len(players) or len(leaderboard)
        )
    return payload


async def _player_sync_extras(
    db, redis, session_id: str, room_state: dict | None, user_id: str, current_q
) -> dict:
    """Extra SYNC_STATE fields for a PLAYER socket, so a reload can restore the screen:
    the question phase, and this player's results while the room is in RESULTS. Pass a
    (current_q, room_state) pair from _read_live_state: results (which carry the correct
    answer) are built only for _revealable_question_id, never for an open question.

    Also, for the current question when the two keys agree (results revealable, or the
    question open and already answered by this player): `question`, the client-safe
    new_question payload (no answer data), and `yourAnswer`, {questionId, answerData}
    with this player's OWN submitted answer, so the results screen's "You answered" line
    survives a reload. Never another player's answer; nothing about the correct one."""
    question_phase = room_state.get("question_phase") if room_state else None
    extras: dict = {
        "questionPhase": question_phase,
        "questionResults": None,
        "question": None,
        "yourAnswer": None,
    }
    reveal_qid = _revealable_question_id(room_state, current_q)
    if reveal_qid is not None:
        extras["questionResults"] = await _player_results_payload(
            db, redis, session_id, reveal_qid, user_id
        )
    if not _question_in_sync(room_state, current_q) or question_phase not in (
        "QUESTION",
        "RESULTS",
    ):
        return extras
    qid = current_q["question_id"]
    answer_data = await _own_answer(db, session_id, qid, user_id)
    if answer_data is None and reveal_qid is None:
        return extras  # open and unanswered: the new_question push follows instead
    question = await db.get(Question, qid)
    if question:
        extras["question"] = await _question_payload(
            db, question, current_q["question_number"], current_q["total_questions"]
        )
    if answer_data is not None:
        extras["yourAnswer"] = {"questionId": qid, "answerData": answer_data}
    return extras


async def _own_answer(
    db, session_id: str, question_id: int, user_id: str
) -> dict | None:
    """The answer_data this user submitted for one question, or None. Filtered by user in
    SQL and again here, so it can only ever return the caller's own answer."""
    rows = await db.execute(
        select(
            SessionScore.user_id, SessionScore.question_id, SessionScore.answer_data
        ).where(
            SessionScore.session_id == session_id,
            SessionScore.question_id == question_id,
            SessionScore.user_id == user_id,
        )
    )
    for r in rows:
        if r.user_id == user_id and r.question_id == question_id:
            return r.answer_data or {}
    return None


async def _emit_player_game_over_sync(
    db, redis, sid: str, session_id: str, user_id: str
) -> bool:
    """Send a PLAYER the game-over SYNC_STATE for a COMPLETED room, but only if they took
    part (a Redis player record or a leaderboard row); their own summary, nobody else's.
    Returns False (nothing sent) for anyone else."""
    leaderboard = await game_service.get_leaderboard(db, session_id)
    player_data = await state.get_player(redis, session_id, user_id)
    lb_row = next((r for r in leaderboard if r["user_id"] == user_id), None)
    if not (player_data or lb_row):
        return False
    players = await state.get_all_players(redis, session_id)
    score = player_data.get("score", 0) if player_data else lb_row["score"]
    game_over = await _player_game_over_payload(
        db, session_id, user_id, score, leaderboard, len(players) or len(leaderboard)
    )
    await sio.emit(
        E.SYNC_STATE,
        {
            "role": "PLAYER",
            "status": "COMPLETED",
            "playerCount": game_over["playerCount"],
            "gameOver": game_over,
        },
        to=sid,
    )
    return True


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------


async def _question_timer_task(
    session_id: str,
    room_code: str,
    question_id: int,
    seconds: float,
) -> None:
    """Fire ANSWER_PHASE_ENDED to the host when the question time limit expires,
    then auto-lock the question after a short pause."""
    try:
        await asyncio.sleep(seconds)
        redis = await get_redis()
        answered = await state.get_answered_count(redis, session_id, question_id)
        total = await state.get_player_count(redis, session_id)
        await sio.emit(
            E.ANSWER_PHASE_ENDED,
            {
                "questionId": question_id,
                "answeredCount": answered,
                "totalPlayers": total,
            },
            to=_host_room(room_code),
        )
        logger.info(
            "question_timer_expired", session_id=session_id, question_id=question_id
        )

        # Brief pause so any in-flight answers can land, then auto-lock
        await asyncio.sleep(0.5)
        current_q = await state.get_current_question(redis, session_id)
        if current_q and current_q["question_id"] == question_id:

            def auto_lock(rs: dict) -> bool:
                # Re-checked under the room lock: still this question, open, unlocked.
                if (
                    rs.get("question_phase") != "QUESTION"
                    or rs.get("question_locked")
                    or not _question_in_sync(rs, current_q)
                ):
                    return False
                rs["question_locked"] = True
                rs["timer_remaining_seconds"] = 0.0
                return True

            if await state.update_room_state(redis, room_code, auto_lock):
                await sio.emit(
                    E.QUESTION_LOCKED,
                    {"questionId": question_id, "remainingSeconds": 0.0},
                    to=room_code,
                )
                logger.info(
                    "question_auto_locked",
                    session_id=session_id,
                    question_id=question_id,
                )
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(
            "question_timer_task_error", session_id=session_id, question_id=question_id
        )
    finally:
        # Only remove our own entry. task.cancel() is async — the CancelledError
        # is delivered at the next yield, which may be after a replacement task has
        # already been stored under the same session_id. A blind pop() here would
        # evict the new task, making it impossible to cancel later and allowing
        # this timer to fire ANSWER_PHASE_ENDED for the wrong question.
        if _timer_tasks.get(session_id) is asyncio.current_task():
            _timer_tasks.pop(session_id, None)


async def _auto_unlock_for_host(
    redis, session_id: str, room_code: str, host_sid: str | None = None
) -> None:
    """Unlock a question that was auto-locked because the host disconnected.
    Only acts when room_state carries question_locked_by_disconnect=True. If no time was
    left (the host dropped between timer expiry and the auto-lock), the question stays
    locked: unlocking it with no timer would leave it open forever. With host_sid, also
    records the joining host's sid in the same locked update.

    Takes the room lock itself (callers must not hold it). Inside it there are only Redis
    reads/writes and timer bookkeeping: the room state is re-read there, current_q is
    rewritten with the virtual start time before room_state is unlocked, and any older
    timer is cancelled and the new one registered, as on the host-unlock path."""
    remaining = 0.0
    current_q = None
    unlocked = False
    async with state.room_state_lock(redis, room_code):
        room_state = await state.get_room_state(redis, room_code)
        if room_state is None:
            return
        if host_sid is not None:
            room_state["host_sid"] = host_sid
        if not room_state.pop("question_locked_by_disconnect", None):
            if host_sid is not None:
                await state.set_room_state(redis, room_code, room_state)
            return
        if float(room_state.get("timer_remaining_seconds") or 0.0) <= 0:
            room_state["question_locked"] = True
            room_state["timer_remaining_seconds"] = 0.0
            await state.set_room_state(redis, room_code, room_state)
            logger.info("question_kept_locked_host_reconnect", session_id=session_id)
            return

        remaining = float(room_state.pop("timer_remaining_seconds", 0.0))
        room_state["question_locked"] = False
        current_q = await state.get_current_question(redis, session_id)
        if current_q and remaining > 0:
            virtual_start = datetime.now(timezone.utc) - timedelta(
                seconds=current_q["time_limit_seconds"] - remaining
            )
            await state.set_current_question(
                redis,
                session_id,
                current_q["question_id"],
                virtual_start,
                current_q["time_limit_seconds"],
                current_q["question_number"],
                current_q["total_questions"],
            )
            unlocked = True
        await state.set_room_state(redis, room_code, room_state)
        if unlocked:
            # Registered while the lock is held, as on the host-unlock path: an advance
            # (which cancels the timer under the same lock) can't miss it, and an older
            # timer for this session is cancelled first.
            _cancel_timer(session_id)
            _timer_tasks[session_id] = asyncio.ensure_future(
                _question_timer_task(
                    session_id, room_code, current_q["question_id"], remaining
                )
            )

    if current_q:
        await sio.emit(
            E.QUESTION_UNLOCKED,
            {"questionId": current_q["question_id"], "remainingSeconds": remaining},
            to=room_code,
        )
        logger.info(
            "question_auto_unlocked_host_reconnect",
            session_id=session_id,
            remaining_seconds=remaining,
        )


def _cancel_timer(session_id: str) -> None:
    """Cancel this session's question timer, if any. Synchronous, so it can run inside a
    room-state update (under the room lock)."""
    timer = _timer_tasks.pop(session_id, None)
    if timer:
        timer.cancel()


def _host_socket_connected(session_id: str) -> bool:
    """True if a HOST socket (host or admin) for this session is still registered on
    this process — e.g. a second tab, or a reconnect on a new sid whose old sid only
    times out later."""
    return any(
        c["role"] == "HOST" and c["session_id"] == session_id for c in _sid_ctx.values()
    )


async def _host_abandon_task(session_id: str, room_code: str) -> None:
    """End the game if the host does not reconnect within the grace period. Uses the same
    end_room path as the HTTP routes: empty rooms are removed, rooms with answers become
    ABANDONED, and the room-ended listener notifies clients."""
    try:
        await asyncio.sleep(_HOST_GRACE_SECONDS)
        # The grace period is over: deregister so a host reconnecting from here on can't
        # cancel this task halfway through end_room (the end goes ahead; the host gets
        # game_abandoned like everyone else).
        if _abandon_tasks.get(session_id) is asyncio.current_task():
            _abandon_tasks.pop(session_id, None)
        async with _db() as db:
            redis = await get_redis()
            session = await db.get(GameSession, session_id)
            if session and session.status == "IN_PROGRESS":
                try:
                    outcome = await game_service.end_room(db, redis, session_id)
                    logger.warning(
                        "game_ended_host_timeout",
                        session_id=session_id,
                        outcome=outcome,
                    )
                except ConflictError:
                    pass  # ended by someone else meanwhile
    except asyncio.CancelledError:
        pass
    finally:
        if _abandon_tasks.get(session_id) is asyncio.current_task():
            _abandon_tasks.pop(session_id, None)


async def _on_room_ended(room_code: str, session_id: str) -> None:
    """Room-ended listener, registered with game_service at import time (design §6).

    Runs after end_room has committed and cleared Redis, inside end_room's shielded
    cleanup task. It still skips the task it is running in, so it can never cancel
    itself; the disconnect timer deregisters itself before calling end_room.
    """
    current = asyncio.current_task()
    for tasks in (_timer_tasks, _abandon_tasks):
        task = tasks.get(session_id)
        if task is not None and task is not current:
            tasks.pop(session_id, None)
            task.cancel()
    for sid, ctx in list(_sid_ctx.items()):
        if ctx.get("session_id") == session_id:
            _sid_ctx.pop(sid, None)
    # Host sockets are also members of room_code, so one emit reaches everyone.
    await sio.emit(E.GAME_ABANDONED, {}, to=room_code)
    await sio.close_room(room_code)
    await sio.close_room(_host_room(room_code))


game_service.register_room_ended_listener(_on_room_ended)


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


def _client_ip(environ: dict) -> str:
    """Extract real client IP from the environ python-socketio passes to connect handlers.

    python-socketio uses a WSGI-style environ dict (HTTP_* headers, REMOTE_ADDR)
    even in ASGI mode. Fall back to ASGI scope fields in case that changes.
    """
    # WSGI-style: nginx sets X-Real-IP which becomes HTTP_X_REAL_IP
    for key in ("HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP"):
        val = environ.get(key, "")
        if val:
            return val.split(",")[0].strip()
    # WSGI-style direct address
    remote = environ.get("REMOTE_ADDR", "")
    if remote:
        return remote
    # ASGI scope fallback: headers as list of (bytes, bytes)
    for name, value in environ.get("headers", []):
        name_b = name if isinstance(name, bytes) else name.encode()
        if name_b.lower() in (b"x-forwarded-for", b"x-real-ip"):
            val = value.decode() if isinstance(value, bytes) else value
            return val.split(",")[0].strip()
    # ASGI scope fallback: client tuple
    client = environ.get("client")
    if client:
        return client[0]
    return "unknown"


_WS_RATE_LIMIT = 10  # max connections per window
_WS_RATE_WINDOW = 10  # seconds


@sio.event
async def connect(sid: str, _environ: dict, auth: dict | None) -> bool | None:
    """
    Authenticate the connecting socket.  The client must pass a valid JWT:
        const socket = io({ auth: { token: accessToken } });
    Returns False to reject the connection.
    """
    # Rate-limit WebSocket connections per IP using Redis (production only —
    # in dev all traffic shares a Docker-internal IP, which would trip the limit).
    # Bypass: if STRESS_TEST_KEY is set and the auth dict carries a matching
    # stress_key, skip rate limiting for this connection.
    if not settings.is_development:
        _stress_bypass = (
            bool(settings.STRESS_TEST_KEY)
            and isinstance(auth, dict)
            and auth.get("stress_key") == settings.STRESS_TEST_KEY
        )
        if not _stress_bypass:
            try:
                ip = _client_ip(_environ)
                redis = await get_redis()
                rate_key = f"ws_rate:{ip}"
                count = await redis.incr(rate_key)
                if count == 1:
                    await redis.expire(rate_key, _WS_RATE_WINDOW)
                if count > _WS_RATE_LIMIT:
                    logger.warning("socket_rate_limited", ip=ip, count=count)
                    return False
            except Exception:
                # Redis unavailable — skip rate limit and let JWT auth decide.
                logger.warning(
                    "ws_rate_limit_redis_error",
                    reason="Redis unavailable, skipping rate limit",
                )

    async with _db() as db:
        try:
            user = await authenticate_socket(auth, db)
        except ValueError as exc:
            logger.warning("socket_auth_failed", reason=str(exc))
            return False

    await sio.save_session(sid, {"user_id": user.id})
    logger.info("socket_connected", sid=sid, user_id=user.id)


@sio.event
async def disconnect(sid: str) -> None:
    ctx = _sid_ctx.pop(sid, None)
    if not ctx:
        return

    room_code = ctx["room_code"]
    session_id = ctx["session_id"]
    user_id = ctx["user_id"]
    role = ctx["role"]

    redis = await get_redis()

    if role == "HOST":
        # Another host socket for this room is still here: the host hasn't left.
        if _host_socket_connected(session_id):
            logger.info("host_socket_closed_other_connected", session_id=session_id)
            return
        room_state = await state.get_room_state(redis, room_code)
        if room_state and room_state.get("status") == "IN_PROGRESS":
            # Auto-lock the active question so the timer pauses while the host is away
            if room_state.get("question_phase") == "QUESTION" and not room_state.get(
                "question_locked"
            ):
                current_q = await state.get_current_question(redis, session_id)
                remaining = 0.0

                def disconnect_lock(rs: dict) -> bool:
                    # Under the room lock: re-check that no host joined meanwhile and the
                    # question is still this one, open and unlocked; the remaining time is
                    # computed here so it matches the moment of the write.
                    nonlocal remaining
                    if (
                        _host_socket_connected(session_id)
                        or rs.get("question_phase") != "QUESTION"
                        or rs.get("question_locked")
                        or not _question_in_sync(rs, current_q)
                    ):
                        return False
                    started_at = datetime.fromisoformat(current_q["started_at"])
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                    remaining = max(0.0, current_q["time_limit_seconds"] - elapsed)
                    timer = _timer_tasks.pop(session_id, None)
                    if timer:
                        timer.cancel()
                    rs["question_locked"] = True
                    rs["timer_remaining_seconds"] = remaining
                    rs["question_locked_by_disconnect"] = True
                    return True

                if current_q and await state.update_room_state(
                    redis, room_code, disconnect_lock
                ):
                    await sio.emit(
                        E.QUESTION_LOCKED,
                        {
                            "questionId": current_q["question_id"],
                            "remainingSeconds": remaining,
                        },
                        to=room_code,
                    )
                    logger.info(
                        "question_auto_locked_host_disconnect",
                        session_id=session_id,
                        remaining_seconds=remaining,
                    )

            if _host_socket_connected(session_id):
                # A host joined while this handler was awaiting: undo our auto-lock
                # (if any) the way a joining host does, and don't start the timer.
                await _auto_unlock_for_host(redis, session_id, room_code)
                return
            # Register the timer before the next await (the emit), so a host joining
            # from here on finds it in _abandon_tasks and cancels it. Cancelling any
            # previous entry keeps a stray timer from being orphaned.
            previous = _abandon_tasks.pop(session_id, None)
            if previous:
                previous.cancel()
            task = asyncio.create_task(_host_abandon_task(session_id, room_code))
            _abandon_tasks[session_id] = task
            abandon_at = (
                datetime.now(timezone.utc) + timedelta(seconds=_HOST_GRACE_SECONDS)
            ).isoformat()
            await sio.emit(E.HOST_DISCONNECTED, {"abandonAt": abandon_at}, to=room_code)
            logger.warning("host_disconnected_grace_started", session_id=session_id)
    else:
        await state.set_player_connected(redis, session_id, user_id, False)
        player_count = await state.get_player_count(redis, session_id)
        await sio.emit(
            E.PLAYER_LEFT,
            {"userId": user_id, "playerCount": player_count},
            to=_host_room(room_code),
        )
        logger.info("player_disconnected", user_id=user_id, room=room_code)


# ---------------------------------------------------------------------------
# JOIN_ROOM — initial join
# ---------------------------------------------------------------------------


@sio.on(E.JOIN_ROOM)
async def on_join_room(sid: str, data: dict) -> None:
    """
    data = {"room_code": str, "role": "HOST" | "PLAYER"}
    Validates access, registers the socket in the appropriate rooms, and
    emits SYNC_STATE back to the joining client.
    """
    session_data = await sio.get_session(sid)
    user_id: str = session_data.get("user_id", "")

    room_code = str(data.get("room_code", "")).upper().strip()
    role = str(data.get("role", "")).upper()

    if not room_code or role not in ("HOST", "PLAYER"):
        await _emit_error(sid, "Invalid join_room payload")
        return

    async with _db() as db:
        redis = await get_redis()
        user = await get_user_by_id(db, user_id)
        if not user:
            await _emit_error(sid, "User not found")
            return

        try:
            session, room_state = await game_service.get_session_by_code(
                db, redis, room_code
            )
        except Exception as exc:
            await _emit_error(sid, str(exc))
            return

        if role == "HOST":
            if user.id != session.host_user_id and user.role != "ADMIN":
                await _emit_error(sid, "Not authorised as host for this session")
                return

            # Cancel any pending abandon grace period
            task = _abandon_tasks.pop(session.id, None)
            if task:
                task.cancel()
                # Players were told host_disconnected; clear their banner.
                await sio.emit(E.HOST_RECONNECTED, {}, to=room_code)

            await sio.enter_room(sid, room_code)
            await sio.enter_room(sid, _host_room(room_code))
            _sid_ctx[sid] = {
                "user_id": user.id,
                "role": "HOST",
                "room_code": room_code,
                "session_id": session.id,
            }

            # Now that the socket is in its rooms: record its sid and undo a disconnect
            # auto-lock, in one locked read-modify-write of the current room state.
            await _auto_unlock_for_host(redis, session.id, room_code, host_sid=sid)

            await sio.emit(
                E.SYNC_STATE,
                await _host_sync_payload(db, redis, session, room_code),
                to=sid,
            )
            logger.info("host_joined", sid=sid, room=room_code, session_id=session.id)

        else:  # PLAYER
            if session.status == "COMPLETED":
                # A player who took part (e.g. reloading the game-over screen) gets the
                # final summary back; nobody new is let in, and no rooms are joined.
                if await _emit_player_game_over_sync(
                    db, redis, sid, session.id, user.id
                ):
                    return
            if session.status not in ("LOBBY", "IN_PROGRESS"):
                await _emit_error(sid, "This game is not accepting players")
                return

            try:
                display_name = await game_service.authorise_player(db, user, session)
            except Exception as exc:
                await _emit_error(sid, str(exc))
                return

            existing = await state.get_player(redis, session.id, user.id)
            if existing:
                await state.set_player_connected(redis, session.id, user.id, True)
            else:
                await state.add_player(
                    redis,
                    session.id,
                    user.id,
                    display_name,
                    is_guest=(user.role == "GUEST"),
                )

            await sio.enter_room(sid, room_code)
            await sio.enter_room(sid, _user_room(user.id))
            _sid_ctx[sid] = {
                "user_id": user.id,
                "role": "PLAYER",
                "room_code": room_code,
                "session_id": session.id,
            }

            # Read the status again now that the socket is in its rooms. The final advance
            # commits COMPLETED before it emits game_over, so either this read sees
            # COMPLETED (the game-over sync below) or game_over is emitted after it and
            # reaches this socket's user room (it targets the players read after the
            # commit, which include this one). A join can't fall between the two.
            status = await game_service.fresh_session_status(db, session.id)
            if status not in ("LOBBY", "IN_PROGRESS"):
                _sid_ctx.pop(sid, None)
                await sio.leave_room(sid, room_code)
                await sio.leave_room(sid, _user_room(user.id))
                if not existing:
                    # Undo our add: a player new to the room only now must not count as
                    # having taken part (nor recreate keys of an ended room).
                    await state.remove_player(redis, session.id, user.id)
                if status == "COMPLETED" and await _emit_player_game_over_sync(
                    db, redis, sid, session.id, user.id
                ):
                    return
                await _emit_error(sid, "This game is not accepting players")
                return

            player_count = await state.get_player_count(redis, session.id)
            player_data = await state.get_player(redis, session.id, user.id)

            # Notify host of the new player
            await sio.emit(
                E.PLAYER_JOINED,
                {
                    "userId": user.id,
                    "displayName": display_name,
                    "playerCount": player_count,
                },
                to=_host_room(room_code),
            )

            # All state reads happen now, after entering the rooms (the room_state read
            # above may be stale; see _read_live_state). If the two keys disagree, an
            # advance is in flight: send the safe state and let its events follow.
            current_q, room_state = await _read_live_state(redis, session.id, room_code)
            in_sync = _question_in_sync(room_state, current_q)
            has_answered = False
            if in_sync:
                has_answered = await state.has_answered(
                    redis, session.id, current_q["question_id"], user.id
                )

            question_phase = room_state.get("question_phase") if room_state else None
            question_locked = (
                room_state.get("question_locked", False) if room_state else False
            )

            await sio.emit(
                E.SYNC_STATE,
                {
                    "role": "PLAYER",
                    "status": status,
                    "playerCount": player_count,
                    "yourScore": player_data.get("score", 0) if player_data else 0,
                    "currentQuestion": current_q if in_sync else None,
                    "hasAnswered": has_answered,
                    "questionLocked": question_locked,
                    **await _player_sync_extras(
                        db, redis, session.id, room_state, user.id, current_q
                    ),
                },
                to=sid,
            )

            # Late-join (or reload): if a question is open and this player hasn't
            # answered it, push it with the remaining time. A locked question is sent
            # too (flagged, so the client shows it locked) so that a later unlock
            # lets the player answer.
            if (
                status == "IN_PROGRESS"
                and question_phase == "QUESTION"
                and in_sync
                and not has_answered
            ):
                question = await db.get(Question, current_q["question_id"])
                if question:
                    if question_locked:
                        frozen = float(room_state.get("timer_remaining_seconds") or 0)
                        remaining = max(1, int(-(-frozen // 1)))  # ceil, at least 1
                    else:
                        started_at = datetime.fromisoformat(current_q["started_at"])
                        if started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=timezone.utc)
                        elapsed = int(
                            (datetime.now(timezone.utc) - started_at).total_seconds()
                        )
                        remaining = max(5, current_q["time_limit_seconds"] - elapsed)
                    payload = await _question_payload(
                        db,
                        question,
                        current_q["question_number"],
                        current_q["total_questions"],
                    )
                    payload["timeLimitSeconds"] = remaining
                    payload["questionLocked"] = bool(question_locked)
                    await sio.emit(E.NEW_QUESTION, payload, to=sid)

            await state.refresh_room_ttl(redis, room_code)
            logger.info("player_joined", sid=sid, room=room_code, user_id=user.id)


# ---------------------------------------------------------------------------
# REJOIN_ROOM — reconnection after network interruption
# ---------------------------------------------------------------------------


@sio.on(E.REJOIN_ROOM)
async def on_rejoin_room(sid: str, data: dict) -> None:
    """
    data = {"room_code": str, "role": "HOST" | "PLAYER"}
    Re-establishes room membership using the JWT already validated at connect time, with
    the same per-role permission checks as join_room. `role` may be omitted by older
    clients: it then defaults to HOST only for the session's starter (the one user who was
    a host without claiming it) and PLAYER for everyone else, so an admin who joined as a
    player never silently becomes host on reconnect — an admin host must send role HOST.
    """
    session_data = await sio.get_session(sid)
    user_id: str = session_data.get("user_id", "")

    room_code = str(data.get("room_code", "")).upper().strip()
    raw_role = data.get("role")
    role = str(raw_role).upper() if raw_role is not None else None
    if not room_code or not user_id or role not in (None, "HOST", "PLAYER"):
        await _emit_error(sid, "Invalid rejoin_room payload")
        return

    async with _db() as db:
        redis = await get_redis()
        user = await get_user_by_id(db, user_id)
        if not user:
            await _emit_error(sid, "User not found")
            return

        try:
            session, room_state = await game_service.get_session_by_code(
                db, redis, room_code
            )
        except Exception as exc:
            await _emit_error(sid, str(exc))
            return

        if role is None:
            role = "HOST" if user.id == session.host_user_id else "PLAYER"
        is_host = role == "HOST"

        if is_host:
            if user.id != session.host_user_id and user.role != "ADMIN":
                await _emit_error(sid, "Not authorised as host for this session")
                return

            task = _abandon_tasks.pop(session.id, None)
            if task:
                task.cancel()
                logger.info("host_reconnected_abandon_cancelled", session_id=session.id)
                await sio.emit(E.HOST_RECONNECTED, {}, to=room_code)

            await sio.enter_room(sid, room_code)
            await sio.enter_room(sid, _host_room(room_code))
            _sid_ctx[sid] = {
                "user_id": user.id,
                "role": "HOST",
                "room_code": room_code,
                "session_id": session.id,
            }

            # Now that the socket is in its rooms: record its sid and undo a disconnect
            # auto-lock, in one locked read-modify-write of the current room state.
            await _auto_unlock_for_host(redis, session.id, room_code, host_sid=sid)

            await sio.emit(
                E.SYNC_STATE,
                await _host_sync_payload(db, redis, session, room_code),
                to=sid,
            )
            logger.info("host_rejoined", sid=sid, room=room_code, session_id=session.id)

        else:
            # Same player check as join_room, every time (e.g. a since-deactivated
            # roster entry can't rejoin). A player already in the room may still rejoin
            # a finished game to see the final state; a new one may not.
            try:
                display_name = await game_service.authorise_player(db, user, session)
            except Exception as exc:
                await _emit_error(sid, str(exc))
                return
            player_data = await state.get_player(redis, session.id, user.id)
            if not player_data:
                if session.status not in ("LOBBY", "IN_PROGRESS"):
                    await _emit_error(sid, "This game is not accepting players")
                    return
                await state.add_player(
                    redis,
                    session.id,
                    user.id,
                    display_name,
                    is_guest=(user.role == "GUEST"),
                )
                player_data = await state.get_player(redis, session.id, user.id)
                # A first-time player via rejoin is new to the host, as in join_room.
                await sio.emit(
                    E.PLAYER_JOINED,
                    {
                        "userId": user.id,
                        "displayName": display_name,
                        "playerCount": await state.get_player_count(redis, session.id),
                    },
                    to=_host_room(room_code),
                )
            else:
                await state.set_player_connected(redis, session.id, user.id, True)

            await sio.enter_room(sid, room_code)
            await sio.enter_room(sid, _user_room(user.id))
            _sid_ctx[sid] = {
                "user_id": user.id,
                "role": "PLAYER",
                "room_code": room_code,
                "session_id": session.id,
            }

            # Fresh, consistent reads after entering the rooms (as in join_room).
            current_q, room_state = await _read_live_state(redis, session.id, room_code)
            in_sync = _question_in_sync(room_state, current_q)
            has_answered = False
            if in_sync:
                has_answered = await state.has_answered(
                    redis, session.id, current_q["question_id"], user.id
                )

            player_count = await state.get_player_count(redis, session.id)
            question_locked = (
                room_state.get("question_locked", False) if room_state else False
            )

            await sio.emit(
                E.SYNC_STATE,
                {
                    "role": "PLAYER",
                    "status": session.status,
                    "playerCount": player_count,
                    "yourScore": player_data.get("score", 0) if player_data else 0,
                    "currentQuestion": current_q if in_sync else None,
                    "hasAnswered": has_answered,
                    "questionLocked": question_locked,
                    **await _player_sync_extras(
                        db, redis, session.id, room_state, user.id, current_q
                    ),
                },
                to=sid,
            )

        await state.refresh_room_ttl(redis, room_code)
        logger.info(
            "client_rejoined", sid=sid, room=room_code, user_id=user_id, is_host=is_host
        )


# ---------------------------------------------------------------------------
# HOST_ADVANCE — host drives all phase transitions
# ---------------------------------------------------------------------------


@sio.on(E.HOST_ADVANCE)
async def on_host_advance(sid: str, _data: dict) -> None:
    """
    The host sends HOST_ADVANCE to move the game forward.

    State machine:
      question_phase == None (LOBBY)  →  start game, emit NEW_QUESTION (phase → QUESTION)
      question_phase == "QUESTION"    →  show results, emit QUESTION_RESULTS (phase → RESULTS)
      question_phase == "RESULTS"     →  next question or end game (phase → QUESTION | COMPLETED)

    Optional data = {"from_phase": "LOBBY" | "QUESTION" | "RESULTS", "question_index": int}
    (question_index may be omitted, then only the phase is checked): the screen the host
    advanced from. If the room has already moved past it (a double click, an auto-advance
    racing a manual one, a second host tab) or another advance from the same screen is
    already running (atomic claim, state.claim_advance), the advance is ignored instead of
    skipping a question, and the host gets a fresh SYNC_STATE so its screen catches up
    (e.g. after a lost update left it a phase behind). Clients that send {} (scripts,
    tests) are not checked.
    """
    try:
        await _on_host_advance_impl(sid, _data if isinstance(_data, dict) else {})
    except Exception:
        logger.exception("host_advance_unhandled_error", sid=sid)


def _stale_advance(data: dict, room_state: dict) -> bool:
    """True if the host advanced from a screen the room has already left."""
    if "from_phase" not in data:
        return False
    sent_phase = data.get("from_phase")
    phase = room_state.get("question_phase")
    if (None if sent_phase == "LOBBY" else sent_phase) != phase:
        return True
    sent_index = data.get("question_index")
    return (
        phase is not None
        and isinstance(sent_index, int)
        and sent_index != room_state.get("question_index", -1)
    )


def _phase_unchanged(room_state: dict, phase: str | None, index: int) -> bool:
    """True if the room is still on the screen (phase, question index) an advance started
    from. Checked under the room lock right before the advance writes, so a concurrent
    advance (e.g. two {} advances from scripts, which skip the claim) can't apply twice."""
    return (
        room_state.get("question_phase") == phase
        and room_state.get("question_index", -1) == index
    )


async def _advance_lost_race(db, redis, sid: str, session, room_code: str) -> None:
    """The room moved on while this advance did its DB work: write nothing and resync the
    host, as for a stale advance."""
    logger.info("host_advance_lost_race", session_id=session.id)
    await sio.emit(
        E.SYNC_STATE, await _host_sync_payload(db, redis, session, room_code), to=sid
    )


async def _emit_room_busy(sid: str, session_id: str) -> None:
    """The room lock could not be taken in time (state.ROOM_LOCK_WAIT_S). An advance does
    not fall back to an unlocked write, which could overwrite a concurrent change; it
    writes nothing and asks the host to try again."""
    logger.warning("host_advance_room_busy", session_id=session_id)
    await _emit_error(sid, "The room is busy. Try again in a few seconds.")


async def _on_host_advance_impl(sid: str, data: dict) -> None:
    ctx = _sid_ctx.get(sid)
    if not ctx or ctx["role"] != "HOST":
        return

    room_code = ctx["room_code"]
    session_id = ctx["session_id"]

    async with _db() as db:
        redis = await get_redis()
        session = await db.get(GameSession, session_id)
        room_state = await state.get_room_state(redis, room_code)

        if not session or not room_state:
            await _emit_error(sid, "Session state not found")
            return

        if "from_phase" in data and (
            _stale_advance(data, room_state)
            or not await state.claim_advance(
                redis,
                session_id,
                room_state.get("question_phase") or "LOBBY",
                room_state.get("question_index", -1),
            )
        ):
            logger.info(
                "host_advance_stale_ignored",
                session_id=session_id,
                sent=data,
                phase=room_state.get("question_phase"),
                index=room_state.get("question_index", -1),
            )
            # Bring this host's screen up to date; otherwise a host left a phase behind
            # would keep sending an advance that is ignored every time.
            await sio.emit(
                E.SYNC_STATE,
                await _host_sync_payload(db, redis, session, room_code),
                to=sid,
            )
            return

        question_phase = room_state.get("question_phase")
        question_index: int = room_state.get("question_index", -1)
        question_ids: list[int] = room_state.get("question_ids", [])

        # Cancel any running question timer regardless of which phase we're in
        timer = _timer_tasks.pop(session_id, None)
        if timer:
            timer.cancel()

        # ── Phase: QUESTION → RESULTS ────────────────────────────────────────
        if question_phase == "QUESTION":
            current_q_state = await state.get_current_question(redis, session_id)
            if not current_q_state:
                await _emit_error(sid, "No current question found")
                return

            question = await db.get(Question, current_q_state["question_id"])
            if not question:
                await _emit_error(sid, "Question record missing")
                return

            # Move to RESULTS before reading the answers and before emitting: an answer
            # whose write is still in flight then either lands before the reads (see
            # wait_for_answer_writes below) or is rolled back by its still_open check, so
            # the counts and scores are complete. The host may also send its next
            # host_advance as soon as question_results arrives, and it must not still see
            # QUESTION. The room may have been ended meanwhile; don't recreate it.
            if await game_service.fresh_session_status(db, session_id) != "IN_PROGRESS":
                return

            def enter_results(rs: dict) -> bool:
                # Compare-and-set under the room lock: only if the room is still on the
                # question this advance started from (a concurrent advance or a stale
                # copy can't be overwritten, and lock/timer writes made meanwhile are
                # kept until this replaces them).
                if not _phase_unchanged(rs, question_phase, question_index):
                    return False
                _cancel_timer(session_id)  # e.g. one an unlock started meanwhile
                rs["question_phase"] = "RESULTS"
                # The question these results belong to: a sync reveals only this one.
                rs["results_question_id"] = question.id
                rs.pop("question_locked", None)
                rs.pop("timer_remaining_seconds", None)
                rs.pop("question_locked_by_disconnect", None)
                return True

            try:
                entered = await state.update_room_state(
                    redis, room_code, enter_results, require_lock=True
                )
            except state.RoomLockBusyError:
                await _emit_room_busy(sid, session_id)
                return
            if not entered:
                await _advance_lost_race(db, redis, sid, session, room_code)
                return

            await game_service.wait_for_answer_writes(db, session_id)
            reveal = _answer_reveal(question)
            answer_dist = await state.get_answer_dist(redis, session_id, question.id)
            leaderboard = await game_service.get_leaderboard(db, session_id)
            players = await state.get_all_players(redis, session_id)

            # Per-question points for all players (single column query)
            q_scores = await _question_points(db, session_id, question.id)
            image_urls = await image_service.image_urls(db, [question.config])

            # Bar-chart results → host (no player names)
            await sio.emit(
                E.QUESTION_RESULTS,
                {
                    "questionId": question.id,
                    "answerReveal": reveal,
                    "answerDistribution": answer_dist,
                    "totalAnswered": len(q_scores),
                    "totalPlayers": len(players),
                    "imageUrls": image_urls,
                },
                to=_host_room(room_code),
            )

            # Personal result → every player (privacy: no other names/scores exposed)
            # Use the Redis player list so zero-score players aren't skipped.
            for player in players:
                uid = player["user_id"]
                await sio.emit(
                    E.QUESTION_RESULTS,
                    {
                        "questionId": question.id,
                        "answerReveal": reveal,
                        "yourPoints": q_scores.get(uid, 0),
                        "yourScore": player.get("score", 0),
                        "yourRank": _rank_of(leaderboard, uid),
                        "playerCount": len(players),
                        "imageUrls": image_urls,
                    },
                    to=_user_room(uid),
                )

            logger.info(
                "question_results_shown", session_id=session_id, question_id=question.id
            )
            return

        # ── Phase: RESULTS or None (Lobby) → advance/start ──────────────────
        if question_phase in (None, "RESULTS"):
            next_index = question_index + 1

            if question_phase is None:
                # Game has not started yet — transition LOBBY → IN_PROGRESS. A start
                # whose advance failed after start_game committed (room busy, an error
                # before question 0 was written) left IN_PROGRESS with no question:
                # continue to question 0 from the snapshot instead of starting again.
                try:
                    if session.status == "IN_PROGRESS":
                        snapshot = await game_service.load_snapshot(db, session)
                        question_ids = [q.question_id for q in snapshot]
                    else:
                        questions = await game_service.start_game(db, redis, session)
                        question_ids = [q.id for q in questions]
                except Exception as exc:
                    await _emit_error(sid, str(exc))
                    return
                next_index = 0

            if next_index >= len(question_ids):
                # All questions exhausted → end game. Compare-and-set: if the room was
                # ended meanwhile this is a no-op and nothing is emitted or written.
                if not await game_service.complete_game(db, redis, session):
                    return

                # COMPLETED is committed in MySQL (complete_game) and mirrored into Redis
                # here, all BEFORE any game_over emit. join_room reads the status after
                # its socket entered its rooms, so a joiner either sees COMPLETED (and
                # gets the game-over sync) or is already in its rooms when these emits
                # go out; it can't miss both.
                def mark_completed(rs: dict) -> None:
                    rs["question_phase"] = "COMPLETED"
                    rs["status"] = "COMPLETED"

                await state.update_room_state(redis, room_code, mark_completed)

                leaderboard = await game_service.get_leaderboard(db, session_id)
                players = await state.get_all_players(redis, session_id)

                # Anonymous score list + per-question summary to the host
                await sio.emit(
                    E.GAME_OVER,
                    await _host_game_over_payload(
                        db, session_id, leaderboard, len(players)
                    ),
                    to=_host_room(room_code),
                )

                # Per-player GAME_OVER with their personal summary
                # Iterate Redis player list so players with 0 points aren't skipped.
                for player in players:
                    uid = player["user_id"]
                    await sio.emit(
                        E.GAME_OVER,
                        await _player_game_over_payload(
                            db,
                            session_id,
                            uid,
                            player.get("score", 0),
                            leaderboard,
                            len(players),
                        ),
                        to=_user_room(uid),
                    )

                logger.info("game_completed_via_gateway", session_id=session_id)
                return

            # Load and broadcast the next question
            question = await db.get(Question, question_ids[next_index])
            if not question:
                await _emit_error(
                    sid, f"Question {question_ids[next_index]} not found in DB"
                )
                return

            # Re-check in a fresh transaction right before the Redis writes, so an
            # end_room that committed meanwhile can't be undone (design §6).
            if await game_service.fresh_session_status(db, session_id) != "IN_PROGRESS":
                return
            await game_service.mark_question_asked(db, session_id, question.id)
            # Commit now: if an emit or Redis write below throws, the question was still
            # broadcast and asked_at must survive the rollback.
            await db.commit()
            # mark_question_asked may have waited on end_room's row locks and resumed only
            # after end_room committed; re-check so its Redis cleanup isn't undone.
            if await game_service.fresh_session_status(db, session_id) != "IN_PROGRESS":
                return

            # Compare-and-set under the room lock, then current_q, in that order (readers
            # read current_q first; see _read_live_state). Only fast Redis writes inside.
            started_at = datetime.now(timezone.utc)
            async with state.room_state_lock(redis, room_code) as held:
                fresh = (
                    None if not held else await state.get_room_state(redis, room_code)
                )
                if fresh is None or not _phase_unchanged(
                    fresh, question_phase, question_index
                ):
                    fresh = None
                else:
                    if question_phase is None:
                        # start_game set IN_PROGRESS; keep it explicit (disconnect()
                        # only starts the host grace timer for IN_PROGRESS rooms).
                        fresh["status"] = "IN_PROGRESS"
                    fresh["question_index"] = next_index
                    fresh["question_ids"] = question_ids
                    fresh["question_phase"] = "QUESTION"
                    fresh["question_locked"] = False
                    fresh.pop("results_question_id", None)
                    fresh.pop("timer_remaining_seconds", None)
                    fresh.pop("question_locked_by_disconnect", None)
                    await state.set_room_state(redis, room_code, fresh)
                    await state.set_current_question(
                        redis,
                        session_id,
                        question.id,
                        started_at,
                        question.time_limit_seconds,
                        next_index + 1,
                        len(question_ids),
                    )
                    # Start the question timer while the lock is held, so a lock click
                    # right after this write always finds (and pauses) it.
                    _cancel_timer(session_id)
                    _timer_tasks[session_id] = asyncio.create_task(
                        _question_timer_task(
                            session_id,
                            room_code,
                            question.id,
                            question.time_limit_seconds,
                        )
                    )
            if not held:
                # Never write the next question without the lock (see _emit_room_busy).
                await _emit_room_busy(sid, session_id)
                return
            if fresh is None:
                await _advance_lost_race(db, redis, sid, session, room_code)
                return

            payload = await _question_payload(
                db, question, next_index + 1, len(question_ids)
            )
            _t_emit = datetime.now(timezone.utc)
            await sio.emit(E.NEW_QUESTION, payload, to=room_code)

            await state.refresh_room_ttl(redis, room_code)

            _log_kw: dict = {
                "session_id": session_id,
                "question_id": question.id,
                "index": next_index,
            }
            if settings.is_stress_test_mode:
                _log_kw["emit_at"] = _t_emit.isoformat()
                _log_kw["emit_ms"] = int(
                    (datetime.now(timezone.utc) - _t_emit).total_seconds() * 1000
                )
            logger.info("question_started", **_log_kw)


# ---------------------------------------------------------------------------
# SUBMIT_ANSWER — player submits their answer
# ---------------------------------------------------------------------------


async def _answer_window_open(
    redis, room_code: str, session_id: str, question_id: int
) -> bool:
    """True if question_id is still the open, unlocked question of the room. Re-checked
    after the answer's DB flush (see game_service.record_answer)."""
    room_state = await state.get_room_state(redis, room_code)
    if (
        not room_state
        or room_state.get("question_phase") != "QUESTION"
        or room_state.get("question_locked")
    ):
        return False
    current_q = await state.get_current_question(redis, session_id)
    return bool(current_q and current_q["question_id"] == question_id)


@sio.on(E.SUBMIT_ANSWER)
async def on_submit_answer(sid: str, data: dict) -> None:
    """
    data = {"question_id": int, "answer_data": dict, "answer_time_ms": int | None}
    Scores the answer, persists to MySQL, updates Redis, and notifies client + host.
    """
    ctx = _sid_ctx.get(sid)
    if not ctx or ctx["role"] != "PLAYER":
        return

    user_id = ctx["user_id"]
    room_code = ctx["room_code"]
    session_id = ctx["session_id"]

    question_id = data.get("question_id")
    answer_data = data.get("answer_data") or {}
    answer_time_ms = data.get("answer_time_ms")

    if not isinstance(question_id, int):
        await _emit_error(sid, "question_id must be an integer")
        return

    async with _db() as db:
        redis = await get_redis()

        room_state = await state.get_room_state(redis, room_code)
        if not room_state or room_state.get("question_phase") != "QUESTION":
            if settings.is_stress_test_mode:
                logger.warning(
                    "submit_answer_no_active_question",
                    sid=sid,
                    session_id=session_id,
                    phase=room_state.get("question_phase") if room_state else None,
                )
            await _emit_error(sid, "No active question")
            return

        if room_state.get("question_locked"):
            await _emit_error(sid, "Question is locked")
            return

        current_q_state = await state.get_current_question(redis, session_id)
        if not current_q_state or current_q_state["question_id"] != question_id:
            if settings.is_stress_test_mode:
                logger.warning(
                    "submit_answer_question_mismatch",
                    sid=sid,
                    session_id=session_id,
                    expected=current_q_state["question_id"]
                    if current_q_state
                    else None,
                    received=question_id,
                )
            await _emit_error(sid, "Question ID does not match the active question")
            return

        # Idempotent: acknowledge but don't double-score
        if await state.has_answered(redis, session_id, question_id, user_id):
            await sio.emit(
                E.ANSWER_RECEIVED,
                {"questionId": question_id, "alreadyAnswered": True},
                to=sid,
            )
            return

        session = await db.get(GameSession, session_id)
        if not session or session.status != "IN_PROGRESS":
            await _emit_error(sid, "Session is not in progress")
            return

        question = await db.get(Question, question_id)
        if not question:
            await _emit_error(sid, "Question not found")
            return

        if question.type == "multi_select":
            indices = answer_data.get("selectedIndices")
            if not isinstance(indices, list) or not all(
                isinstance(i, int) for i in indices
            ):
                await _emit_error(
                    sid,
                    "multi_select answer must include selectedIndices as a list of integers",
                )
                return
            num_opts = len(question.config.get("options", []))
            if any(not (0 <= i < num_opts) for i in indices):
                await _emit_error(
                    sid, "selectedIndices contains an out-of-bounds index"
                )
                return
            if len(set(indices)) != len(indices):
                await _emit_error(sid, "selectedIndices contains a duplicate index")
                return

        # T7 types: reject a malformed answer before scoring; no score row is written.
        if question.type in NEW_TYPES:
            problem = game_service.validate_answer(question, answer_data)
            if problem:
                await _emit_error(sid, problem)
                return

        # Claim the one answer atomically (SADD). The has_answered check above is only a
        # fast path: two concurrent submits from one player can both pass it, but only
        # one wins this claim. The claim is released if the answer is not recorded.
        if not await state.claim_answer(redis, session_id, question_id, user_id):
            await sio.emit(
                E.ANSWER_RECEIVED,
                {"questionId": question_id, "alreadyAnswered": True},
                to=sid,
            )
            return

        async def still_open() -> bool:
            return await _answer_window_open(redis, room_code, session_id, question_id)

        result = None  # set once record_answer has updated the Redis score/dist
        try:
            result = await game_service.record_answer(
                db,
                redis,
                session_id,
                user_id,
                question,
                answer_data,
                answer_time_ms,
                still_open=still_open,
            )
            # Commit before any emit, so the results (wait_for_answer_writes) and the
            # player see the same answers.
            await db.commit()
        except ConflictError as exc:
            # The room was ended, or the question closed, while the answer was being
            # written (rolled back).
            await state.release_answer(redis, session_id, question_id, user_id)
            await _emit_error(sid, exc.message)
            return
        except BaseException:
            # The commit failed after record_answer counted the answer in Redis: undo
            # that too, or a resubmit (the claim is released) would count twice.
            if result is not None:
                await game_service.revert_answer(
                    redis, session_id, user_id, question, answer_data, result
                )
            await state.release_answer(redis, session_id, question_id, user_id)
            raise

        player_data = await state.get_player(redis, session_id, user_id)
        total_score = (
            player_data.get("score", 0) if player_data else result.points_awarded
        )

        # Notify the answering player
        await sio.emit(
            E.ANSWER_RECEIVED,
            {
                "questionId": question_id,
                "isCorrect": result.is_correct,
                "pointsAwarded": result.points_awarded,
                "totalScore": total_score,
            },
            to=sid,
        )

        # Notify host of updated answer count
        answered_count = await state.get_answered_count(redis, session_id, question_id)
        player_count = await state.get_player_count(redis, session_id)
        await sio.emit(
            E.ANSWER_STATUS,
            {
                "questionId": question_id,
                "userId": user_id,
                "answeredCount": answered_count,
                "totalPlayers": player_count,
            },
            to=_host_room(room_code),
        )

        # If all players have answered, notify host immediately (cancel timer)
        if await state.all_players_answered(redis, session_id, question_id):
            timer = _timer_tasks.pop(session_id, None)
            if timer:
                timer.cancel()
            await sio.emit(
                E.ANSWER_PHASE_ENDED,
                {
                    "questionId": question_id,
                    "answeredCount": answered_count,
                    "totalPlayers": player_count,
                    "allAnswered": True,
                },
                to=_host_room(room_code),
            )

        await state.refresh_room_ttl(redis, room_code)
        _ans_log: dict = {
            "session_id": session_id,
            "user_id": user_id,
            "question_id": question_id,
            "is_correct": result.is_correct,
        }
        if settings.is_stress_test_mode:
            _ans_log["points_awarded"] = result.points_awarded
            _ans_log["answer_time_ms"] = answer_time_ms
        logger.info("answer_recorded", **_ans_log)


# ---------------------------------------------------------------------------
# HOST_LOCK_QUESTION — host closes answer submission for the current question
# ---------------------------------------------------------------------------


@sio.on(E.HOST_LOCK_QUESTION)
async def on_lock_question(sid: str, _data: dict) -> None:
    """
    Toggles question_locked in room state. Also pauses/resumes the server-side
    timer task so that locking stops the countdown and unlocking resumes it.
    """
    try:
        await _on_lock_question_impl(sid)
    except Exception:
        logger.exception("lock_question_unhandled_error", sid=sid)


async def _on_lock_question_impl(sid: str) -> None:
    ctx = _sid_ctx.get(sid)
    if not ctx or ctx["role"] != "HOST":
        return

    room_code = ctx["room_code"]
    session_id = ctx["session_id"]
    redis = await get_redis()

    # The whole toggle is one locked read-modify-write with only Redis calls inside, so
    # it can't write QUESTION+locked over a RESULTS (or the next question) written by an
    # advance meanwhile: if the room has left this open question, nothing is written.
    # Emits happen after the lock is released.
    async with state.room_state_lock(redis, room_code):
        room_state = await state.get_room_state(redis, room_code)
        if not room_state or room_state.get("question_phase") != "QUESTION":
            return
        current_q = await state.get_current_question(redis, session_id)
        if not current_q or not _question_in_sync(room_state, current_q):
            return

        new_locked = not room_state.get("question_locked", False)
        room_state["question_locked"] = new_locked
        if new_locked:
            started_at = datetime.fromisoformat(current_q["started_at"])
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
            remaining = max(0.0, current_q["time_limit_seconds"] - elapsed)
            room_state["timer_remaining_seconds"] = remaining
            _cancel_timer(session_id)
            await state.set_room_state(redis, room_code, room_state)
        else:
            remaining = float(room_state.pop("timer_remaining_seconds", 0.0) or 0.0)
            room_state.pop("question_locked_by_disconnect", None)
            if remaining > 0:
                # Shift started_at forward so that the next lock recomputes remaining
                # correctly, regardless of how long this question has been paused.
                # Invariant: now - started_at == time_limit - remaining (active elapsed).
                virtual_start = datetime.now(timezone.utc) - timedelta(
                    seconds=current_q["time_limit_seconds"] - remaining
                )
                await state.set_current_question(
                    redis,
                    session_id,
                    current_q["question_id"],
                    virtual_start,
                    current_q["time_limit_seconds"],
                    current_q["question_number"],
                    current_q["total_questions"],
                )
            await state.set_room_state(redis, room_code, room_state)
            if remaining > 0:
                # Registered while the lock is held, so an advance (which cancels the
                # timer under the same lock) can't miss it.
                _cancel_timer(session_id)
                _timer_tasks[session_id] = asyncio.ensure_future(
                    _question_timer_task(
                        session_id, room_code, current_q["question_id"], remaining
                    )
                )

    event = E.QUESTION_LOCKED if new_locked else E.QUESTION_UNLOCKED
    await sio.emit(
        event,
        {"questionId": current_q["question_id"], "remainingSeconds": remaining},
        to=room_code,
    )
    logger.info(
        "question_locked_by_host" if new_locked else "question_unlocked_by_host",
        session_id=session_id,
        question_id=current_q["question_id"],
        remaining_seconds=remaining,
    )
