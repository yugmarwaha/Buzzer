"""Course-first host API (T4 design §6 Host routes).

Every route checks HOST on the course the record belongs to (admins pass everywhere), and
child ids are loaded through their parent. Business rules live in the services so the
admin compatibility routes share them.
"""

from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..common.dependencies import require_user
from ..database import DbSession
from ..models.course import CourseRoster
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.admin import (
    GameCreate,
    GameUpdate,
    QuestionCreate,
    QuestionReorder,
    QuestionUpdate,
    RosterEntryPatch,
    RosterEntryResponse,
    RosterImportPayload,
    RosterUploadResult,
)
from ..schemas.images import MAX_IMPORT_BYTES, ImageItem
from ..schemas.host import (
    ActiveRoom,
    EndRoomResult,
    GameSummary,
    ImportResult,
    QuestionItem,
    SessionItem,
)
from ..services import access_service as access
from ..services import game_service, image_service, quiz_service, roster_service
from ..services.export_service import build_canvas_csv, build_session_csv
from ..services.report_service import build_session_report

router = APIRouter(prefix="/host", tags=["host"])

CurrentUser = Annotated[User, Depends(require_user)]
DB = DbSession


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/games", response_model=list[GameSummary])
async def list_games(
    course_id: int, user: CurrentUser, db: DB, archived: bool = False
) -> list[dict]:
    await access.assert_course_host(db, user, course_id)
    return await quiz_service.list_course_games(db, course_id, archived=archived)


@router.post("/courses/{course_id}/games", response_model=GameSummary, status_code=201)
async def create_game(
    course_id: int, body: GameCreate, user: CurrentUser, db: DB
) -> dict:
    await access.assert_course_host(db, user, course_id)
    # T8 design §8: quiz creation/import take the course lock first, so an image replace
    # in this course sees every quiz.
    await access.lock_course(db, course_id)
    game = await quiz_service.create_game(db, course_id, body)
    return await quiz_service.game_summary(db, game)


@router.post(
    "/courses/{course_id}/games/import", response_model=ImportResult, status_code=201
)
async def import_game(
    course_id: int,
    file: Annotated[UploadFile, File(description="buzzer/game JSON bundle")],
    user: CurrentUser,
    db: DB,
) -> dict:
    await access.assert_course_host(db, user, course_id)
    raw = await image_service.read_upload(file, MAX_IMPORT_BYTES, what="File")
    # T8 design §8-§9: import_bundle decodes the images first, then takes the course
    # lock itself before writing, so an image replace in this course sees every quiz.
    game = await quiz_service.import_bundle(db, course_id, raw, user.id)
    return {"game_id": game.id}


@router.get("/games/{game_id}", response_model=GameSummary)
async def get_game(game_id: int, user: CurrentUser, db: DB) -> dict:
    game = await access.get_game_for_host(db, user, game_id)
    return await quiz_service.game_summary(db, game)


@router.put("/games/{game_id}", response_model=GameSummary)
async def update_game(
    game_id: int, body: GameUpdate, user: CurrentUser, db: DB
) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.update_game(db, game, body)
    return await quiz_service.game_summary(db, game)


@router.post("/games/{game_id}/archive", response_model=GameSummary)
async def archive_game(game_id: int, user: CurrentUser, db: DB) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.archive_game(db, game)
    return await quiz_service.game_summary(db, game)


@router.post("/games/{game_id}/restore", response_model=GameSummary)
async def restore_game(game_id: int, user: CurrentUser, db: DB) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.restore_game(db, game)
    return await quiz_service.game_summary(db, game)


@router.get("/games/{game_id}/export")
async def export_game(game_id: int, user: CurrentUser, db: DB) -> StreamingResponse:
    game = await access.get_game_for_host(db, user, game_id)
    filename, content = await quiz_service.export_bundle(db, game)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@router.get("/games/{game_id}/questions", response_model=list[QuestionItem])
async def list_questions(game_id: int, user: CurrentUser, db: DB) -> list[dict]:
    game = await access.get_game_for_host(db, user, game_id)
    return await quiz_service.list_questions(db, game)


@router.post("/games/{game_id}/questions", response_model=QuestionItem, status_code=201)
async def create_question(
    game_id: int, body: QuestionCreate, user: CurrentUser, db: DB
) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    return await quiz_service.create_question(db, game, body)


@router.put("/games/{game_id}/questions/{question_id}", response_model=QuestionItem)
async def update_question(
    game_id: int, question_id: int, body: QuestionUpdate, user: CurrentUser, db: DB
) -> dict:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    return await quiz_service.update_question(db, game, question_id, body)


@router.delete("/games/{game_id}/questions/{question_id}", status_code=204)
async def delete_question(
    game_id: int, question_id: int, user: CurrentUser, db: DB
) -> None:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.delete_question(db, game, question_id)


@router.post("/games/{game_id}/questions/reorder", status_code=204)
async def reorder_questions(
    game_id: int, body: QuestionReorder, user: CurrentUser, db: DB
) -> None:
    game = await access.get_game_for_host(db, user, game_id, for_update=True)
    await quiz_service.reorder_questions(db, game, body.order)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/roster", response_model=list[RosterEntryResponse])
async def list_roster(course_id: int, user: CurrentUser, db: DB) -> list[CourseRoster]:
    await access.assert_course_host(db, user, course_id)
    return await roster_service.list_roster(db, course_id)


@router.post("/courses/{course_id}/roster", response_model=RosterUploadResult)
async def upload_roster(
    course_id: int,
    file: Annotated[UploadFile, File(description="Canvas gradebook CSV export")],
    user: CurrentUser,
    db: DB,
) -> RosterUploadResult:
    await access.assert_course_host(db, user, course_id)
    return await roster_service.process_roster_csv(db, course_id, await file.read())


@router.post("/courses/{course_id}/roster/import", response_model=RosterUploadResult)
async def import_roster_rows(
    course_id: int, payload: RosterImportPayload, user: CurrentUser, db: DB
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
    course_id: int,
    roster_id: int,
    body: RosterEntryPatch,
    user: CurrentUser,
    db: DB,
) -> CourseRoster:
    await access.assert_course_host(db, user, course_id)
    entry = await access.get_roster_entry_in_course(db, course_id, roster_id)
    return await roster_service.patch_roster_entry(db, entry, body)


# ---------------------------------------------------------------------------
# Images (T8 design §6, §7)
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/images", response_model=list[ImageItem])
async def list_images(course_id: int, user: CurrentUser, db: DB) -> list[dict]:
    await access.assert_course_host(db, user, course_id)
    return await image_service.list_images(db, course_id)


@router.post("/courses/{course_id}/images", response_model=ImageItem, status_code=201)
async def upload_image(
    course_id: int,
    file: Annotated[
        UploadFile, File(description="PNG, JPEG, WebP or GIF, at most 5 MiB")
    ],
    user: CurrentUser,
    db: DB,
) -> dict:
    await access.assert_course_host(db, user, course_id)
    # Decode before locking: validation is slow and needs no lock (§4).
    loaded = await run_in_threadpool(
        image_service.load_image, await image_service.read_upload(file)
    )
    await access.lock_course(db, course_id)
    image = await image_service.create_image(
        db, course_id, user.id, loaded, file.filename
    )
    return image_service.image_item(image, 0, 0)


@router.put("/images/{image_id}", response_model=ImageItem, status_code=201)
async def replace_image(
    image_id: int,
    file: Annotated[
        UploadFile, File(description="PNG, JPEG, WebP or GIF, at most 5 MiB")
    ],
    user: CurrentUser,
    db: DB,
) -> dict:
    course_id = await image_service.course_of_image(db, image_id)
    await access.assert_course_host(db, user, course_id)
    loaded = await run_in_threadpool(
        image_service.load_image, await image_service.read_upload(file)
    )
    await access.lock_course(db, course_id)
    new = await image_service.replace_image(
        db, course_id, image_id, user.id, loaded, file.filename
    )
    return image_service.image_item(new, 0, 0)


@router.delete("/images/{image_id}", status_code=204)
async def delete_image(image_id: int, user: CurrentUser, db: DB) -> None:
    course_id = await image_service.course_of_image(db, image_id)
    await access.assert_course_host(db, user, course_id)
    await access.lock_course(db, course_id)
    await image_service.delete_image(db, course_id, image_id)


# ---------------------------------------------------------------------------
# Active rooms
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/rooms", response_model=list[ActiveRoom])
async def list_rooms(
    course_id: int, user: CurrentUser, db: DB, redis=Depends(get_redis)
) -> list[dict]:
    await access.assert_course_host(db, user, course_id)
    return await game_service.list_active_rooms(db, redis, course_id, user)


@router.post("/sessions/{session_id}/end", response_model=EndRoomResult)
async def end_room(
    session_id: str, user: CurrentUser, db: DB, redis=Depends(get_redis)
) -> dict:
    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_starter_or_admin(user, session)
    outcome = await game_service.end_room(db, redis, session_id)
    return {"outcome": outcome}


# ---------------------------------------------------------------------------
# Finished sessions and downloads
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/sessions", response_model=list[SessionItem])
async def list_sessions(course_id: int, user: CurrentUser, db: DB) -> list[dict]:
    await access.assert_course_host(db, user, course_id)
    return await game_service.list_finished_sessions(db, course_id, user)


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    user: CurrentUser,
    db: DB,
    format: str = Query("canvas", description="'canvas' or 'raw'"),
    title: str | None = Query(None, description="Assignment column title (Canvas)"),
    sis_domain: str = Query("", description="Domain appended to netid for SIS Login"),
    roster_only: bool = Query(True, description="Exclude players without a netid"),
    per_question: bool = Query(False, description="One column per question"),
) -> StreamingResponse:
    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_finished(session)
    if format == "canvas":
        filename, content = await build_canvas_csv(
            db,
            session_id,
            assignment_title=title,
            sis_domain=sis_domain,
            roster_only=roster_only,
            per_question=per_question,
        )
    else:
        filename, content = await build_session_csv(db, session_id)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions/{session_id}/report")
async def session_report(session_id: str, user: CurrentUser, db: DB) -> Response:
    session = await access.get_session_for_host(db, user, session_id)
    access.assert_session_finished(session)
    filename, content = await build_session_report(db, session_id)
    return Response(
        content=content,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
