from __future__ import annotations

import io
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common.dependencies import require_admin
from ..common.exceptions import ConflictError, NotFoundError
from ..database import DbSession
from ..models.course import Course, CourseRoster, UserCourseAccess
from ..models.game import Game
from ..models.session import SessionScore
from ..models.user import User
from ..schemas.admin import (
    AdminCourse,
    AdminGameCreate,
    CourseAccessGrant,
    CourseCreate,
    CourseResponse,
    CourseUpdate,
    GameResponse,
    GameUpdate,
    QuestionCreate,
    QuestionReorder,
    QuestionResponse,
    QuestionUpdate,
    RosterEntryPatch,
    RosterEntryResponse,
    RosterImportPayload,
    RosterUploadResult,
    UserCreate,
    UserResponse,
    UserUpdate,
    UserWithAccessResponse,
)
from ..schemas.images import MAX_IMPORT_BYTES
from ..services import access_service as access
from ..services import image_service, quiz_service, roster_service
from ..services.auth_service import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])
logger = structlog.get_logger()

AdminUser = Annotated[User, Depends(require_admin)]
DB = DbSession


async def _quiz_counts(db: AsyncSession) -> dict[int, tuple[int, int]]:
    """course_id → (active quiz count, archived quiz count)."""
    rows = await db.execute(
        select(
            Game.course_id, Game.archived_at.is_(None), func.count(Game.id)
        ).group_by(Game.course_id, Game.archived_at.is_(None))
    )
    counts: dict[int, list[int]] = {}
    for course_id, is_active, n in rows.all():
        pair = counts.setdefault(course_id, [0, 0])
        pair[0 if is_active else 1] += n
    return {k: (v[0], v[1]) for k, v in counts.items()}


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------


@router.get("/courses", response_model=list[AdminCourse])
async def list_courses(_: AdminUser, db: DB) -> list[dict]:
    courses = (
        (await db.execute(select(Course).order_by(Course.semester.desc(), Course.name)))
        .scalars()
        .all()
    )
    hosts: dict[int, list[dict]] = {}
    for course_id, user in (
        await db.execute(
            select(UserCourseAccess.course_id, User)
            .join(User, User.id == UserCourseAccess.user_id)
            .where(UserCourseAccess.role == "HOST")
            .order_by(User.display_name)
        )
    ).all():
        hosts.setdefault(course_id, []).append(
            {
                "user_id": user.id,
                "display_name": user.display_name
                or user.username
                or user.netid
                or user.id,
            }
        )
    counts = await _quiz_counts(db)
    return [
        {
            "id": c.id,
            "name": c.name,
            "semester": c.semester,
            "created_at": c.created_at,
            "hosts": hosts.get(c.id, []),
            "active_quiz_count": counts.get(c.id, (0, 0))[0],
            "archived_quiz_count": counts.get(c.id, (0, 0))[1],
        }
        for c in courses
    ]


@router.post("/courses", response_model=CourseResponse, status_code=201)
async def create_course(body: CourseCreate, _: AdminUser, db: DB) -> Course:
    course = Course(name=body.name, semester=body.semester)
    db.add(course)
    await db.flush()
    await db.refresh(course)
    await db.commit()
    logger.info("course_created", course_id=course.id, name=body.name)
    return course


@router.get("/courses/{course_id}", response_model=CourseResponse)
async def get_course(course_id: int, _: AdminUser, db: DB) -> Course:
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundError(f"Course {course_id} not found")
    return course


@router.put("/courses/{course_id}", response_model=CourseResponse)
async def update_course(
    course_id: int, body: CourseUpdate, _: AdminUser, db: DB
) -> Course:
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundError(f"Course {course_id} not found")
    if body.name is not None:
        course.name = body.name
    if body.semester is not None:
        course.semester = body.semester
    return course


# ---------------------------------------------------------------------------
# Compatibility routes (seed script, tests, README curl examples).
# They call the same services as /api/host/*, so they share every guard and
# parent-ownership check. The Admin app itself uses the Host app for this work.
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/roster", response_model=list[RosterEntryResponse])
async def list_roster(course_id: int, user: AdminUser, db: DB) -> list[CourseRoster]:
    await access.assert_course_host(db, user, course_id)
    return await roster_service.list_roster(db, course_id)


@router.post("/courses/{course_id}/roster", response_model=RosterUploadResult)
async def upload_roster(
    course_id: int,
    file: Annotated[UploadFile, File(description="Canvas gradebook CSV export")],
    user: AdminUser,
    db: DB,
) -> RosterUploadResult:
    await access.assert_course_host(db, user, course_id)
    return await roster_service.process_roster_csv(db, course_id, await file.read())


@router.post("/courses/{course_id}/roster/import", response_model=RosterUploadResult)
async def import_roster_rows(
    course_id: int, payload: RosterImportPayload, user: AdminUser, db: DB
) -> RosterUploadResult:
    await access.assert_course_host(db, user, course_id)
    rows = [
        {"netid": r.netid, "full_name": r.full_name, "email": r.email}
        for r in payload.rows
    ]
    return await roster_service.process_roster_rows(db, course_id, rows)


@router.patch(
    "/courses/{course_id}/roster/{roster_id}", response_model=RosterEntryResponse
)
async def patch_roster_entry(
    course_id: int, roster_id: int, body: RosterEntryPatch, user: AdminUser, db: DB
) -> CourseRoster:
    await access.assert_course_host(db, user, course_id)
    entry = await access.get_roster_entry_in_course(db, course_id, roster_id)
    return await roster_service.patch_roster_entry(db, entry, body)


@router.get("/games", response_model=list[GameResponse])
async def list_games(_: AdminUser, db: DB) -> list[Game]:
    return (await db.execute(select(Game).order_by(Game.title))).scalars().all()


@router.post("/games", response_model=GameResponse, status_code=201)
async def create_game(body: AdminGameCreate, user: AdminUser, db: DB) -> Game:
    await access.assert_course_host(db, user, body.course_id)
    # T8 design §8: quiz creation/import take the course lock first, so an image replace
    # in this course sees every quiz.
    await access.lock_course(db, body.course_id)
    return await quiz_service.create_game(db, body.course_id, body)


@router.get("/games/{game_id}", response_model=GameResponse)
async def get_game(game_id: int, user: AdminUser, db: DB) -> Game:
    return await access.get_game_for_host(db, user, game_id)


@router.put("/games/{game_id}", response_model=GameResponse)
async def update_game(game_id: int, body: GameUpdate, user: AdminUser, db: DB) -> Game:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    return await quiz_service.update_game(db, game, body)


@router.get("/games/{game_id}/questions", response_model=list[QuestionResponse])
async def list_questions(game_id: int, user: AdminUser, db: DB) -> list[dict]:
    game = await access.get_game_for_host(db, user, game_id)
    return await quiz_service.list_questions(db, game)


@router.post(
    "/games/{game_id}/questions", response_model=QuestionResponse, status_code=201
)
async def create_question(
    game_id: int, body: QuestionCreate, user: AdminUser, db: DB
) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    return await quiz_service.create_question(db, game, body)


@router.put("/games/{game_id}/questions/{question_id}", response_model=QuestionResponse)
async def update_question(
    game_id: int, question_id: int, body: QuestionUpdate, user: AdminUser, db: DB
) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    return await quiz_service.update_question(db, game, question_id, body)


@router.delete("/games/{game_id}/questions/{question_id}", status_code=204)
async def delete_question(
    game_id: int, question_id: int, user: AdminUser, db: DB
) -> None:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.delete_question(db, game, question_id)


@router.post("/games/{game_id}/questions/reorder", status_code=204)
async def reorder_questions(
    game_id: int, body: QuestionReorder, user: AdminUser, db: DB
) -> None:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.reorder_questions(db, game, body.order)


@router.get("/games/{game_id}/export")
async def export_game(game_id: int, user: AdminUser, db: DB) -> StreamingResponse:
    game = await access.get_game_for_host(db, user, game_id)
    filename, content = await quiz_service.export_bundle(db, game)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/games/import", status_code=201)
async def import_game(
    file: Annotated[UploadFile, File(description="buzzer/game JSON bundle")],
    course_id: Annotated[int, Form(description="Course the new quiz belongs to")],
    user: AdminUser,
    db: DB,
) -> dict:
    await access.assert_course_host(db, user, course_id)
    raw = await image_service.read_upload(file, MAX_IMPORT_BYTES, what="File")
    # T8 design §8-§9: import_bundle decodes the images first, then takes the course
    # lock itself before writing, so an image replace in this course sees every quiz.
    game = await quiz_service.import_bundle(db, course_id, raw, user.id)
    return {"game_id": game.id}


# ---------------------------------------------------------------------------
# Users (local accounts)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> list[User]:
    result = await db.execute(
        select(User).where(User.role != "GUEST").order_by(User.role, User.username)
    )
    return result.scalars().all()


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> User:
    # Enforce unique username
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Username '{body.username}' is already taken")

    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        display_name=body.display_name,
        email=str(body.email) if body.email else None,
        password_hash=hash_password(body.password),
        role="USER",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    await db.commit()
    logger.info("local_user_created", user_id=user.id, username=body.username)
    return user


@router.get("/users/guests", response_model=list[UserResponse])
async def list_guests(
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> list[User]:
    result = await db.execute(
        select(User).where(User.role == "GUEST").order_by(User.created_at.desc())
    )
    return result.scalars().all()


@router.get("/users/{user_id}", response_model=UserWithAccessResponse)
async def get_user(user_id: str, _: AdminUser, db: DB) -> dict:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found")

    access_rows = (
        await db.execute(
            select(UserCourseAccess, Course)
            .join(Course, Course.id == UserCourseAccess.course_id)
            .where(UserCourseAccess.user_id == user_id)
            .order_by(Course.semester.desc(), Course.name)
        )
    ).all()
    counts = await _quiz_counts(db)

    return {
        **{
            c: getattr(user, c)
            for c in [
                "id",
                "username",
                "netid",
                "display_name",
                "email",
                "role",
                "created_at",
                "last_login",
            ]
        },
        "course_access": [
            {"course_id": ca.course_id, "role": ca.role} for ca, _c in access_rows
        ],
        # Read-only: quiz access comes from course access (no per-quiz grants).
        "course_summary": [
            {
                "course_id": course.id,
                "name": course.name,
                "semester": course.semester,
                "role": ca.role,
                "active_quiz_count": counts.get(course.id, (0, 0))[0],
                "archived_quiz_count": counts.get(course.id, (0, 0))[1],
            }
            for ca, course in access_rows
        ],
    }


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    if body.username is not None:
        existing = await db.execute(
            select(User).where(User.username == body.username, User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError(f"Username '{body.username}' is already taken")
        user.username = body.username
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.email is not None:
        user.email = str(body.email)
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        user.role = body.role
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> None:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    if user.role == "ADMIN":
        raise ConflictError("Cannot delete an admin account")
    # Delete scores first — the session_scores.user_id column is NOT NULL so
    # SQLAlchemy's default SET-NULL cascade would fail without this.
    from sqlalchemy import delete as sa_delete
    from ..models.session import SessionScore

    await db.execute(sa_delete(SessionScore).where(SessionScore.user_id == user_id))
    await db.delete(user)


# ---------------------------------------------------------------------------
# Access management
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/course-access", status_code=204)
async def grant_course_access(
    user_id: str,
    body: CourseAccessGrant,
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> None:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    course = await db.get(Course, body.course_id)
    if not course:
        raise NotFoundError(f"Course {body.course_id} not found")

    result = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user_id,
            UserCourseAccess.course_id == body.course_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.role = body.role  # update role if access already exists
    else:
        db.add(
            UserCourseAccess(user_id=user_id, course_id=body.course_id, role=body.role)
        )


@router.delete("/users/{user_id}/course-access/{course_id}", status_code=204)
async def revoke_course_access(
    user_id: str,
    course_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> None:
    result = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user_id,
            UserCourseAccess.course_id == course_id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise NotFoundError(
            f"No course access record for user {user_id} / course {course_id}"
        )
    await db.delete(access)


# ---------------------------------------------------------------------------
# Guest merge
# ---------------------------------------------------------------------------


@router.post("/users/merge-guest", status_code=204)
async def merge_guest(
    body: dict,
    _: Annotated[User, Depends(require_admin)],
    db: DbSession,
) -> None:
    """Admin guest merge (unchanged from before T4, unlike the host route): moves all of the
    guest's scores to the user with `target_netid` and deletes the guest; if no such user exists
    yet, the guest record is promoted in place to that netid."""
    guest_user_id: str = body.get("guest_user_id", "")
    target_netid: str = body.get("target_netid", "")

    if not guest_user_id or not target_netid:
        raise ConflictError("guest_user_id and target_netid are required")

    guest = await db.get(User, guest_user_id)
    if not guest or guest.role != "GUEST":
        raise NotFoundError(f"Guest user {guest_user_id} not found")

    normalized_netid = target_netid.strip().lower()

    # Find the real user by netid
    result = await db.execute(select(User).where(User.netid == normalized_netid))
    real_user = result.scalar_one_or_none()

    if real_user:
        # Real user already has an account — re-attribute scores and delete the guest record
        scores_result = await db.execute(
            select(SessionScore).where(SessionScore.user_id == guest_user_id)
        )
        for score in scores_result.scalars().all():
            score.user_id = real_user.id
        await db.delete(guest)
        logger.info("guest_merged", guest_id=guest_user_id, real_user_id=real_user.id)
    else:
        # No account yet for this netid (student hasn't logged in via OAuth2) —
        # promote the guest record in-place so scores are retained without re-attribution
        guest.netid = normalized_netid
        guest.role = "USER"
        logger.info("guest_promoted", guest_id=guest_user_id, netid=normalized_netid)
