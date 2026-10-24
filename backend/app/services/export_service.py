"""Session score export — raw and Canvas-formatted CSV downloads."""

from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.course import Course, CourseRoster
from ..models.session import GameSession, SessionQuestion, SessionScore
from ..models.user import User
from .game_service import load_snapshot


def _cell(scores: dict, q: SessionQuestion, default: float = 0):
    """A player's points for one snapshotted question; empty if the question was never
    reached (abandoned game)."""
    if q.asked_at is None:
        return ""
    return scores.get(q.question_id, default)


async def build_session_csv(db: AsyncSession, session_id: str) -> tuple[str, bytes]:
    """
    Return (filename, csv_bytes) for a session score export.

    Columns: Player, Q1, Q2, …, Qn, Total
    Each row is one player; cells are points_awarded per question (0 if not answered).
    """
    session = await db.get(GameSession, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    # Fetch questions ordered by order_index
    questions = await load_snapshot(db, session)

    # Fetch all scores for the session
    s_result = await db.execute(
        select(SessionScore).where(SessionScore.session_id == session_id)
    )
    scores = s_result.scalars().all()

    # Collect unique player IDs maintaining first-seen order
    player_ids: list[str] = []
    seen: set[str] = set()
    for s in scores:
        if s.user_id not in seen:
            player_ids.append(s.user_id)
            seen.add(s.user_id)

    # Build score lookup: player_id → question_id → points_awarded
    score_map: dict[str, dict[int, int]] = {pid: {} for pid in player_ids}
    for s in scores:
        score_map[s.user_id][s.question_id] = s.points_awarded

    # Fetch display names
    name_map: dict[str, str] = {}
    if player_ids:
        u_result = await db.execute(select(User).where(User.id.in_(player_ids)))
        for u in u_result.scalars().all():
            name_map[u.id] = u.display_name or u.username or u.netid or u.id

    # Write CSV
    output = io.StringIO()
    writer = csv.writer(output)

    header = ["Player"] + [f"Q{i + 1}" for i in range(len(questions))] + ["Total"]
    writer.writerow(header)

    for pid in player_ids:
        row_scores = [_cell(score_map[pid], q) for q in questions]
        total = sum(v for v in row_scores if v != "")
        writer.writerow([name_map.get(pid, pid)] + row_scores + [total])

    # Filename based on session id (safe for all filesystems)
    filename = f"session_{session_id[:8]}_scores.csv"
    return filename, output.getvalue().encode()


async def build_canvas_csv(
    db: AsyncSession,
    session_id: str,
    *,
    assignment_title: str | None = None,
    sis_domain: str = "",
    roster_only: bool = True,
    per_question: bool = False,
) -> tuple[str, bytes]:
    """
    Return (filename, csv_bytes) in Canvas gradebook import format.

    Canvas matches students by SIS Login ID = netid (+ @sis_domain when provided).
    When roster_only=True, players without a netid (guests, local accounts) are skipped.
    When per_question=True, each question becomes its own assignment column.
    """
    session = await db.get(GameSession, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    questions = await load_snapshot(db, session)

    s_result = await db.execute(
        select(SessionScore).where(SessionScore.session_id == session_id)
    )
    scores = s_result.scalars().all()

    player_ids: list[str] = []
    seen: set[str] = set()
    for s in scores:
        if s.user_id not in seen:
            player_ids.append(s.user_id)
            seen.add(s.user_id)

    score_map: dict[str, dict[int, float]] = {pid: {} for pid in player_ids}
    for s in scores:
        score_map[s.user_id][s.question_id] = float(s.points_awarded)

    users: dict[str, User] = {}
    if player_ids:
        u_result = await db.execute(select(User).where(User.id.in_(player_ids)))
        for u in u_result.scalars().all():
            users[u.id] = u

    # Roster lets us use the exact name Canvas has on file
    r_result = await db.execute(
        select(CourseRoster).where(CourseRoster.course_id == session.course_id)
    )
    roster_by_netid: dict[str, CourseRoster] = {
        r.netid: r for r in r_result.scalars().all()
    }

    course = await db.get(Course, session.course_id)
    section = f"{course.name} {course.semester}" if course else ""

    title = assignment_title or "Quiz Score"

    def sis_login(netid: str) -> str:
        return f"{netid}@{sis_domain}" if (netid and sis_domain) else netid

    output = io.StringIO()
    writer = csv.writer(output)

    canvas_base = ["Student", "ID", "SIS User ID", "SIS Login ID", "Section"]

    if per_question:
        q_headers = [
            f"Q{i + 1}: {q.prompt[:50].rstrip()}" for i, q in enumerate(questions)
        ]
        writer.writerow(canvas_base + q_headers)
        # Unasked questions (abandoned game) stay blank, like their score cells.
        writer.writerow(
            ["Points Possible", "", "", "", ""]
            + [q.points_value if q.asked_at is not None else "" for q in questions]
        )
    else:
        writer.writerow(canvas_base + [title])
        max_score = sum(q.points_value for q in questions if q.asked_at is not None)
        writer.writerow(["Points Possible", "", "", "", "", max_score])

    for pid in player_ids:
        user = users.get(pid)
        if user is None:
            continue
        netid = user.netid or ""
        if roster_only and not netid:
            continue

        roster_entry = roster_by_netid.get(netid) if netid else None
        display = (
            roster_entry.full_name
            if roster_entry
            else user.display_name or user.username or netid or pid
        )

        q_scores = [_cell(score_map[pid], q, 0.0) for q in questions]

        if per_question:
            writer.writerow([display, "", "", sis_login(netid), section] + q_scores)
        else:
            total = sum(v for v in q_scores if v != "")
            writer.writerow([display, "", "", sis_login(netid), section, total])

    # ASCII only: the name goes into a Content-Disposition header, which must be latin-1.
    safe = "".join(
        c if (c.isascii() and c.isalnum()) or c in " _-" else "_" for c in title
    )
    filename = f"canvas_{safe}_{session_id[:8]}.csv"
    return filename, output.getvalue().encode()
