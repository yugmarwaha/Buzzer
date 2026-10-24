"""Test-only: remove one integration-test quiz and everything it created.

The app deliberately has no whole-quiz delete (results must survive), so the integration
test teardown uses this script instead of an HTTP route (docs/design-host-admin.md §9):

    docker compose exec -T backend python -m scripts.cleanup_test_quiz --game-id 42
    docker compose exec -T backend python -m scripts.cleanup_test_quiz --image-id 7 --image-id 8

With `--game-id` it removes the quiz's Redis room state, scores, snapshots, sessions,
questions, legacy grants, and the quiz itself — and nothing else (courses, rosters, access,
users stay until `--sweep`).
It refuses to run outside development / stress-test mode, and refuses any quiz whose
course isn't named `Test Course <8 hex chars>` (the fixture's naming), so it can't touch
real data.

`--image-id` (repeatable, T8) removes exactly the listed images after the quiz, with the
same test-course check. Images have no foreign key between versions, so any order works.

`--sweep` runs at the start and end of every integration test session (see
`tests/integration/conftest.py`). It removes every course the suite creates — semester
`Test` and named `Test Course <8 hex>` or `Real-looking course <6 hex>` — with all their
rows, then the users the suite creates (the shared `t4_*` accounts, `t4?<6 hex>` NetID
users, `testplayer<n>.<6 hex>@example.com` guests). A user still linked to any other data
is kept and reported, never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models.course import Course, CourseRoster, UserCourseAccess
from app.models.game import Game, Question, UserGameAccess
from app.models.image import Image
from app.models.session import GameSession, SessionQuestion, SessionScore
from app.models.user import User
from app.redis_client import close_redis, get_redis
from app.services import state_service as state

TEST_COURSE = re.compile(r"^Test Course [0-9a-f]{8}$")
# test_cleanup_script_refuses_non_test_course makes this one; only --sweep removes it.
GUARD_COURSE = re.compile(r"^Real-looking course [0-9a-f]{6}$")
TEST_SEMESTER = "Test"
SHARED_USERNAMES = ("t4_host_a", "t4_host_b", "t4_host_c", "t4_player", "t4_player2")
TEST_NETID = re.compile(r"^t4[a-z][0-9a-f]{6}$")
TEST_GUEST_EMAIL = re.compile(r"^testplayer\d+\.[0-9a-f]{6}@example\.com$")


def is_test_course_name(name: str) -> bool:
    return bool(TEST_COURSE.match(name))


def is_sweepable_course(course: Course) -> bool:
    return course.semester == TEST_SEMESTER and bool(
        TEST_COURSE.match(course.name) or GUARD_COURSE.match(course.name)
    )


def is_test_user(user: User) -> bool:
    if user.role == "ADMIN":
        return False
    if user.username in SHARED_USERNAMES:
        return True
    if user.netid and TEST_NETID.match(user.netid):
        return True
    return (
        user.role == "GUEST"
        and user.email is not None
        and bool(TEST_GUEST_EMAIL.match(user.email))
    )


async def cleanup(
    game_id: int | None, image_ids: list[int], sweep: bool = False
) -> int:
    if not settings.is_development:
        print("Refusing: only runs in development mode.")
        return 2

    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine) as db:
            if sweep:
                return await _sweep(db)
            if game_id is not None:
                code = await _cleanup_game(db, game_id)
                if code:
                    return code
            return await _cleanup_images(db, image_ids)
    finally:
        await close_redis()
        await engine.dispose()


async def _cleanup_images(db: AsyncSession, image_ids: list[int]) -> int:
    for image_id in image_ids:
        course_name = (
            await db.execute(
                select(Course.name)
                .join(Image, Image.course_id == Course.id)
                .where(Image.id == image_id)
            )
        ).scalar_one_or_none()
        if course_name is None:
            print(f"Image {image_id} not found; nothing to do.")
            continue
        if not TEST_COURSE.match(course_name):
            print(
                f"Refusing: image {image_id} is in course {course_name!r}, "
                "not a test course."
            )
            return 3
        await db.execute(delete(Image).where(Image.id == image_id))
        await db.commit()
        print(f"Removed test image {image_id}.")
    return 0


async def _cleanup_game(
    db: AsyncSession,
    game_id: int,
    course_ok: Callable[[Course], bool] = lambda c: is_test_course_name(c.name),
) -> int:
    game = await db.get(Game, game_id)
    if game is None:
        print(f"Game {game_id} not found; nothing to do.")
        return 0
    course = await db.get(Course, game.course_id)
    if course is None or not course_ok(course):
        name = course.name if course else None
        print(f"Refusing: game {game_id} is in course {name!r}, not a test course.")
        return 3

    sessions = (
        await db.execute(
            select(GameSession.id, GameSession.room_code).where(
                GameSession.game_id == game_id
            )
        )
    ).all()
    ids = await _delete_sessions(db, sessions)
    await db.execute(delete(Question).where(Question.game_id == game_id))
    await db.execute(delete(UserGameAccess).where(UserGameAccess.game_id == game_id))
    await db.execute(delete(Game).where(Game.id == game_id))
    await db.commit()
    print(f"Removed test quiz {game_id} ({len(ids)} sessions).")
    return 0


async def _delete_sessions(db: AsyncSession, sessions) -> list[str]:
    """Delete (session id, room code) rows with their Redis state, scores and snapshots."""
    redis = await get_redis()
    for sid, code in sessions:
        await state.delete_room_state(redis, code, sid)
    ids = [sid for sid, _code in sessions]
    if ids:
        await db.execute(delete(SessionScore).where(SessionScore.session_id.in_(ids)))
        await db.execute(
            delete(SessionQuestion).where(SessionQuestion.session_id.in_(ids))
        )
        await db.execute(delete(GameSession).where(GameSession.id.in_(ids)))
    return ids


async def _sweep(db: AsyncSession) -> int:
    courses = (
        (await db.execute(select(Course).where(Course.semester == TEST_SEMESTER)))
        .scalars()
        .all()
    )
    course_ids = [c.id for c in courses if is_sweepable_course(c)]

    if course_ids:
        game_ids = (
            (await db.execute(select(Game.id).where(Game.course_id.in_(course_ids))))
            .scalars()
            .all()
        )
        for gid in game_ids:
            code = await _cleanup_game(db, gid, course_ok=is_sweepable_course)
            if code:
                return code
        # Sessions can point at a course without a quiz of that course.
        leftover = (
            await db.execute(
                select(GameSession.id, GameSession.room_code).where(
                    GameSession.course_id.in_(course_ids)
                )
            )
        ).all()
        await _delete_sessions(db, leftover)
        await db.execute(delete(Image).where(Image.course_id.in_(course_ids)))
        await db.execute(
            delete(CourseRoster).where(CourseRoster.course_id.in_(course_ids))
        )
        await db.execute(
            delete(UserCourseAccess).where(UserCourseAccess.course_id.in_(course_ids))
        )
        await db.execute(delete(Course).where(Course.id.in_(course_ids)))
        await db.commit()

    candidates = [
        u
        for u in (await db.execute(select(User).where(User.role != "ADMIN")))
        .scalars()
        .all()
        if is_test_user(u)
    ]
    removed = kept = 0
    for user in candidates:
        if await _user_has_links(db, user.id):
            print(f"Kept test-looking user {user.id}: still linked to other data.")
            kept += 1
            continue
        await db.execute(delete(User).where(User.id == user.id))
        removed += 1
    await db.commit()
    print(
        f"Swept {len(course_ids)} test courses and {removed} test users ({kept} kept)."
    )
    return 0


async def _user_has_links(db: AsyncSession, user_id: str) -> bool:
    checks = (
        select(SessionScore.id).where(SessionScore.user_id == user_id),
        select(GameSession.id).where(GameSession.host_user_id == user_id),
        select(UserCourseAccess.user_id).where(UserCourseAccess.user_id == user_id),
        select(UserGameAccess.user_id).where(UserGameAccess.user_id == user_id),
        select(Image.id).where(Image.uploaded_by == user_id),
    )
    for query in checks:
        if (await db.execute(query.limit(1))).first() is not None:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game-id", type=int)
    parser.add_argument("--image-id", type=int, action="append", default=[])
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    if args.sweep and (args.game_id is not None or args.image_id):
        parser.error("--sweep takes no other options")
    if not args.sweep and args.game_id is None and not args.image_id:
        parser.error("give --game-id, --image-id, --sweep, or both ids")
    return asyncio.run(cleanup(args.game_id, args.image_id, args.sweep))


if __name__ == "__main__":
    sys.exit(main())
