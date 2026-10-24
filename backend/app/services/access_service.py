"""Course-based access checks (T4 design §3, §6).

Access to a quiz, roster, or session always comes from the course the record belongs to,
read from the database — never from the request. A child record (question, roster entry)
is always loaded through its parent, so a host of one course can't reach another course's
data by guessing ids.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common.exceptions import ConflictError, ForbiddenError, NotFoundError
from ..models.course import Course, CourseRoster, UserCourseAccess
from ..models.game import Game, Question
from ..models.session import GameSession
from ..models.user import User


async def is_course_host(db: AsyncSession, user: User, course_id: int) -> bool:
    if user.role == "ADMIN":
        return True
    result = await db.execute(
        select(UserCourseAccess.user_id).where(
            UserCourseAccess.user_id == user.id,
            UserCourseAccess.course_id == course_id,
            UserCourseAccess.role == "HOST",
        )
    )
    return result.first() is not None


async def assert_course_host(db: AsyncSession, user: User, course_id: int) -> Course:
    """Admin or HOST on the course; 404 if the course is missing, 403 otherwise."""
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundError(f"Course {course_id} not found")
    if not await is_course_host(db, user, course_id):
        raise ForbiddenError("You do not have host access to this course")
    return course


async def lock_course(
    db: AsyncSession, course_id: int, *, shared: bool = False
) -> None:
    """Lock the course row: the first lock of every course-scoped mutation (T8 design §8,
    global order course -> quizzes -> questions -> images -> session rows). Exclusive for
    quiz/question/image writes; shared for room creation, so rooms don't block each other."""
    stmt = select(Course.id).where(Course.id == course_id).with_for_update(read=shared)
    if (await db.execute(stmt)).first() is None:
        raise NotFoundError(f"Course {course_id} not found")


async def get_game_for_host(
    db: AsyncSession,
    user: User,
    game_id: int,
    *,
    for_update: bool = False,
    shared_course_lock: bool = False,
) -> Game:
    """Load a quiz and check HOST on *its* course.

    `for_update` locks the course row first (exclusive, or shared with
    `shared_course_lock`), then the quiz row, which serializes room creation against edits
    (T4 §6) and keeps the T8 lock order (§8). A quiz's course never changes, so reading
    `course_id` before locking is safe.
    """
    if not for_update:
        game = (
            await db.execute(select(Game).where(Game.id == game_id))
        ).scalar_one_or_none()
        if not game:
            raise NotFoundError(f"Game {game_id} not found")
        if not await is_course_host(db, user, game.course_id):
            raise ForbiddenError("You do not have host access to this course")
        return game

    course_id = (
        await db.execute(select(Game.course_id).where(Game.id == game_id))
    ).scalar_one_or_none()
    if course_id is None:
        raise NotFoundError(f"Game {game_id} not found")
    if not await is_course_host(db, user, course_id):
        raise ForbiddenError("You do not have host access to this course")
    await lock_course(db, course_id, shared=shared_course_lock)
    game = (
        await db.execute(
            select(Game)
            .where(Game.id == game_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return game


async def get_session_for_host(
    db: AsyncSession, user: User, session_id: str
) -> GameSession:
    """Load a session and check HOST on the course it was played in."""
    session = await db.get(GameSession, session_id)
    if not session:
        raise NotFoundError(f"Session {session_id} not found")
    if not await is_course_host(db, user, session.course_id):
        raise ForbiddenError("You do not have host access to this course")
    return session


def assert_session_finished(session: GameSession) -> None:
    """Downloads (CSV, HTML) and the host guest list/merge are for finished sessions only
    (§3); a live room is 409."""
    if session.status not in ("COMPLETED", "ABANDONED"):
        raise ConflictError("This session hasn't finished yet")


async def get_question_in_game(
    db: AsyncSession, game: Game, question_id: int, *, for_update: bool = False
) -> Question:
    """`for_update` makes it a locking read, for callers already holding the game-row
    lock: it sees rows committed while they waited, not the older snapshot."""
    stmt = select(Question).where(
        Question.id == question_id, Question.game_id == game.id
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(stmt)
    question = result.scalar_one_or_none()
    if not question:
        raise NotFoundError(f"Question {question_id} not found in game {game.id}")
    return question


async def get_roster_entry_in_course(
    db: AsyncSession, course_id: int, roster_id: int
) -> CourseRoster:
    result = await db.execute(
        select(CourseRoster).where(
            CourseRoster.id == roster_id, CourseRoster.course_id == course_id
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError(f"Roster entry {roster_id} not found in course {course_id}")
    return entry


def assert_session_starter_or_admin(user: User, session: GameSession) -> None:
    if user.role == "ADMIN":
        return
    if session.host_user_id is None or session.host_user_id != user.id:
        raise ForbiddenError(
            "Only the host who started this session or an admin can do this"
        )
