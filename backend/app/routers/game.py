from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..common.dependencies import get_current_user, require_user
from ..config import settings
from ..database import DbSession
from ..models.course import Course, UserCourseAccess
from ..models.game import Game, Question
from ..models.session import GameSession
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.game import (
    ActiveSessionItem,
    GuestMergeRequest,
    MyCourseItem,
    RoomCreateRequest,
    RoomCreateResponse,
    RoomInfoResponse,
)
from ..services import access_service as access
from ..services import game_service

router = APIRouter(prefix="/game", tags=["game"])
logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Host resource discovery
# ---------------------------------------------------------------------------


@router.get("/my-courses", response_model=list[MyCourseItem])
async def my_courses(
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> list[dict]:
    """
    Returns courses the authenticated user can host.
    ADMIN sees all courses; USER sees courses with HOST role in user_course_access.
    """
    if user.role == "ADMIN":
        result = await db.execute(
            select(Course).order_by(Course.semester.desc(), Course.name)
        )
        return [
            {"id": c.id, "name": c.name, "semester": c.semester, "role": "HOST"}
            for c in result.scalars().all()
        ]

    result = await db.execute(
        select(Course, UserCourseAccess)
        .join(UserCourseAccess, UserCourseAccess.course_id == Course.id)
        .where(
            UserCourseAccess.user_id == user.id,
            UserCourseAccess.role == "HOST",
        )
        .order_by(Course.semester.desc(), Course.name)
    )
    return [
        {"id": c.id, "name": c.name, "semester": c.semester, "role": uca.role}
        for c, uca in result.all()
    ]


@router.get("/my-active-sessions", response_model=list[ActiveSessionItem])
async def my_active_sessions(
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> list[dict]:
    """Returns LOBBY/IN_PROGRESS sessions hosted by the current user."""
    result = await db.execute(
        select(GameSession)
        .where(
            GameSession.host_user_id == user.id,
            GameSession.status.in_(["LOBBY", "IN_PROGRESS"]),
        )
        .order_by(GameSession.created_at.desc())
    )
    sessions = result.scalars().all()

    rows = []
    for s in sessions:
        game = await db.get(Game, s.game_id)
        course = await db.get(Course, s.course_id)
        rows.append(
            {
                "session_id": s.id,
                "room_code": s.room_code,
                "status": s.status,
                "game_title": game.title if game else "Unknown",
                "course_id": s.course_id,
                "course_name": course.name if course else "Unknown",
                "course_semester": course.semester if course else "",
            }
        )
    return rows


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
    redis=Depends(get_redis),
) -> None:
    """Compatibility alias of POST /api/host/sessions/{id}/end: ends an *active* room.
    With answers it becomes ABANDONED (results kept); without, it is removed. Finished
    sessions can't be deleted (409)."""
    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_starter_or_admin(user, session)
    await game_service.end_room(db, redis, session_id)


# ---------------------------------------------------------------------------
# Room management
# ---------------------------------------------------------------------------


@router.post("/rooms", response_model=RoomCreateResponse, status_code=201)
async def create_room(
    body: RoomCreateRequest,
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
    redis=Depends(get_redis),
) -> dict:
    session = await game_service.create_room(
        db=db,
        redis=redis,
        host=user,
        game_id=body.game_id,
        course_id=body.course_id,  # optional; must match the quiz's course
        max_rooms=settings.MAX_ROOMS,
    )
    return {"room_code": session.room_code, "session_id": session.id}


@router.get("/rooms/{room_code}/ping")
async def ping_room(
    room_code: str,
    redis=Depends(get_redis),
) -> dict:
    """Public endpoint — no auth required. Confirms a room is joinable."""
    from ..services.state_service import get_room_state
    from ..common.exceptions import NotFoundError as _NF, ConflictError as _CE

    state = await get_room_state(redis, room_code.upper())
    if not state:
        raise _NF(f"Room '{room_code}' not found")
    if state.get("status") not in ("LOBBY", "IN_PROGRESS"):
        raise _CE("This game has already ended")
    return {"status": state.get("status")}


@router.get("/rooms/{room_code}", response_model=RoomInfoResponse)
async def get_room(
    room_code: str,
    _: Annotated[User, Depends(get_current_user)],
    db: DbSession,
    redis=Depends(get_redis),
) -> dict:
    """Get session metadata for reconnection. Any authenticated user can call this."""
    session, _ = await game_service.get_session_by_code(db, redis, room_code.upper())

    # Fetch related records for the response
    game = await db.get(Game, session.game_id)
    course = await db.get(Course, session.course_id)

    q_count_result = await db.execute(
        select(Question).where(Question.game_id == session.game_id)
    )
    question_count = len(q_count_result.scalars().all())

    return {
        "session_id": session.id,
        "room_code": session.room_code,
        "status": session.status,
        "game_title": game.title if game else "Unknown",
        "course_id": session.course_id,
        "course_name": course.name if course else "Unknown",
        "course_semester": course.semester if course else "",
        "question_count": question_count,
    }


# ---------------------------------------------------------------------------
# Post-game guest reconciliation (host-facing)
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/guests")
async def list_session_guests(
    session_id: str,
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> list[dict]:
    """List unmerged guest players from a finished session (409 while it's live)."""
    from ..models.session import SessionScore
    from ..models.user import User as UserModel

    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_starter_or_admin(user, session)
    access.assert_session_finished(session)

    result = await db.execute(
        select(UserModel)
        .join(SessionScore, SessionScore.user_id == UserModel.id)
        .where(
            SessionScore.session_id == session_id,
            UserModel.role == "GUEST",
        )
        .distinct()
    )
    guests = result.scalars().all()
    return [
        {
            "id": g.id,
            "display_name": g.display_name,
            "email": g.email,
        }
        for g in guests
    ]


@router.post("/sessions/{session_id}/merge-guest", status_code=204)
async def merge_guest_for_session(
    session_id: str,
    body: GuestMergeRequest,
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> None:
    """Host-initiated guest merge, scoped to this session (session starter or admin).
    Finished sessions only (409 while live, so a guest can't keep answering after it).
    Same rules as the admin endpoint: see `game_service.merge_guest`."""
    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_starter_or_admin(user, session)
    access.assert_session_finished(session)
    await game_service.merge_guest(db, session, body.guest_user_id, body.target_netid)


# ---------------------------------------------------------------------------
# Session score export
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/export")
async def export_session_scores(
    session_id: str,
    user: Annotated[User, Depends(require_user)],
    db: DbSession,
) -> StreamingResponse:
    """Download a finished session's scores as a CSV. Any host of the session's course, or
    an admin; a live room is 409."""
    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_finished(session)

    from ..services.export_service import build_session_csv

    filename, content = await build_session_csv(db, session_id)

    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
