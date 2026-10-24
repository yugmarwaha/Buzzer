"""Quiz → course map for the T4 migration (design doc §4).

Shared by `scripts/plan_game_courses.py` (propose / check) and the Alembic migration
(validate before any DDL). Everything here is read-only and runs on a *sync*
SQLAlchemy Connection (Alembic's connection, or `AsyncConnection.run_sync`).

It deliberately reads with raw SQL instead of the ORM models: it has to work on the
pre-migration schema, where `games.course_id` doesn't exist yet.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP_PATH = _BACKEND_ROOT / "migration_maps" / "game_course_map.json"

DEMO_GAME_TITLE = "Buzzer Demo"
DEMO_COURSE_NAME = "Demo Course"


def map_path() -> Path:
    """The map file location. `GAME_COURSE_MAP_PATH` exists only so tests can point at a
    scratch file instead of the team's real reviewed map."""
    override = os.environ.get("GAME_COURSE_MAP_PATH")
    return Path(override) if override else DEFAULT_MAP_PATH


def report_path(map_file: Path) -> Path:
    """The review report written next to a new map (game_course_map.report.txt)."""
    return map_file.with_name(map_file.stem + ".report.txt")


@dataclass
class GameRow:
    id: int
    title: str
    session_courses: list[int]
    proposal: int | None = None
    rule: str = ""
    hosts_gaining: list[str] = field(default_factory=list)
    grants_losing: list[str] = field(default_factory=list)


def _user_label(row) -> str:
    return row.display_name or row.username or row.netid or row.id


def load_games(conn: Connection) -> list[GameRow]:
    games = conn.execute(text("SELECT id, title FROM games ORDER BY id")).all()
    pairs = conn.execute(
        text("SELECT DISTINCT game_id, course_id FROM game_sessions")
    ).all()
    by_game: dict[int, set[int]] = {}
    for gid, cid in pairs:
        by_game.setdefault(gid, set()).add(cid)
    return [
        GameRow(
            id=g.id, title=g.title, session_courses=sorted(by_game.get(g.id, set()))
        )
        for g in games
    ]


def course_ids(conn: Connection) -> set[int]:
    return {r[0] for r in conn.execute(text("SELECT id FROM courses")).all()}


def active_sessions(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT s.id, s.room_code, s.status, s.game_id, g.title, "
            "u.display_name, u.username, u.netid, u.id AS uid, "
            "(SELECT COUNT(*) FROM session_scores sc WHERE sc.session_id = s.id) AS answers "
            "FROM game_sessions s JOIN games g ON g.id = s.game_id "
            "LEFT JOIN users u ON u.id = s.host_user_id "
            "WHERE s.status IN ('LOBBY', 'IN_PROGRESS') ORDER BY s.created_at"
        )
    ).all()
    return [
        {
            "session_id": r.id,
            "room_code": r.room_code,
            "status": r.status,
            "game_id": r.game_id,
            "game_title": r.title,
            "starter": (r.display_name or r.username or r.netid or r.uid)
            if r.uid
            else "Unknown host",
            "answers": int(r.answers or 0),
        }
        for r in rows
    ]


def propose(conn: Connection) -> list[GameRow]:
    """Apply the planning rules (first match wins, each rule terminal)."""
    games = load_games(conn)
    courses = conn.execute(text("SELECT id, name FROM courses")).all()
    demo_courses = [c.id for c in courses if c.name == DEMO_COURSE_NAME]
    demo_games = [g for g in games if g.title == DEMO_GAME_TITLE]

    for g in games:
        if g.title == DEMO_GAME_TITLE:
            # Rule 1 (terminal): demo quiz → Demo Course only when unambiguous.
            if len(demo_games) != 1:
                g.rule = f"demo: {len(demo_games)} games titled '{DEMO_GAME_TITLE}'"
            elif len(demo_courses) != 1:
                g.rule = f"demo: {len(demo_courses)} courses named '{DEMO_COURSE_NAME}'"
            elif any(c != demo_courses[0] for c in g.session_courses):
                g.rule = "demo: has sessions outside Demo Course"
            else:
                g.proposal = demo_courses[0]
                g.rule = "demo"
            continue
        if len(g.session_courses) == 1:
            g.proposal = g.session_courses[0]
            g.rule = "all sessions in one course"
        elif not g.session_courses:
            g.rule = "no sessions"
        else:
            g.rule = f"sessions in {len(g.session_courses)} courses"

    _fill_access_changes(conn, games)
    return games


def _fill_access_changes(conn: Connection, games: list[GameRow]) -> None:
    hosts: dict[int, list] = {}
    for r in conn.execute(
        text(
            "SELECT a.course_id, u.id, u.display_name, u.username, u.netid "
            "FROM user_course_access a JOIN users u ON u.id = a.user_id "
            "WHERE a.role = 'HOST'"
        )
    ).all():
        hosts.setdefault(r.course_id, []).append(r)
    grants: dict[int, list] = {}
    for r in conn.execute(
        text(
            "SELECT g.game_id, u.id, u.display_name, u.username, u.netid "
            "FROM user_game_access g JOIN users u ON u.id = g.user_id"
        )
    ).all():
        grants.setdefault(r.game_id, []).append(r)

    for g in games:
        course_hosts = hosts.get(g.proposal, []) if g.proposal else []
        host_ids = {h.id for h in course_hosts}
        g.hosts_gaining = [_user_label(h) for h in course_hosts]
        g.grants_losing = [
            _user_label(u) for u in grants.get(g.id, []) if u.id not in host_ids
        ]


def proposal_map(games: list[GameRow]) -> dict:
    return {
        str(g.id): {
            "course_id": g.proposal,
            "title": g.title,
            "session_courses": g.session_courses,
        }
        for g in games
    }


def report(games: list[GameRow], active: list[dict]) -> str:
    lines = ["Quiz → course proposals", "-" * 72]
    for g in games:
        lines.append(
            f"game {g.id} {g.title!r}: proposed course {g.proposal} ({g.rule}); "
            f"session courses {g.session_courses or '[]'}"
        )
        lines.append(
            f"    gains access (HOSTs of proposed course): {g.hosts_gaining or '-'}"
        )
        lines.append(
            f"    loses access (old game grant, not HOST): {g.grants_losing or '-'}"
        )
    unresolved = [g for g in games if g.proposal is None]
    lines.append("-" * 72)
    lines.append(f"{len(games)} quizzes, {len(unresolved)} unresolved (null)")
    lines.append("")
    lines.append("Active sessions (end or finish these before migrating)")
    lines.append("-" * 72)
    if not active:
        lines.append("none")
    for s in active:
        lines.append(
            f"{s['status']:<11} room {s['room_code']}  game {s['game_id']} "
            f"{s['game_title']!r}  started by {s['starter']}  answers {s['answers']}  "
            f"(session {s['session_id']})"
        )
        if s["answers"]:
            # The old backend's DELETE route would erase these answers (design §4 step 4).
            lines.append(
                "    has answers — if it can't finish, keep them by marking it abandoned: "
                "UPDATE game_sessions SET status='ABANDONED', completed_at=NOW() "
                f"WHERE id='{s['session_id']}' AND status IN ('LOBBY','IN_PROGRESS');"
            )
        else:
            lines.append(
                "    no answers — safe to end with DELETE /api/game/sessions/<id>"
            )
    return "\n".join(lines)


def read_map(path: Path) -> tuple[dict | None, list[str]]:
    if not path.exists():
        return None, [f"map file not found: {path}"]
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"map file is not valid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, ["map file must be a JSON object keyed by game id"]
    return data, []


def check_map(conn: Connection, data: dict) -> tuple[list[str], list[str]]:
    """Compare a reviewed map with the DB. Returns (problems, warnings); never writes."""
    problems: list[str] = []
    warnings: list[str] = []
    games = {g.id: g for g in load_games(conn)}
    courses = course_ids(conn)

    entries: dict[int, dict] = {}
    for key, entry in data.items():
        try:
            gid = int(key)
        except (TypeError, ValueError):
            problems.append(f"map key {key!r} is not a game id")
            continue
        if gid in entries:
            problems.append(f"game {gid}: duplicate map key {key!r}")
            continue
        if not isinstance(entry, dict):
            problems.append(f"game {key}: entry must be an object")
            continue
        entries[gid] = entry

    for gid, g in games.items():
        if gid not in entries:
            problems.append(f"game {gid} {g.title!r}: missing from map (new quiz?)")
    for gid, entry in entries.items():
        g = games.get(gid)
        if g is None:
            problems.append(f"game {gid}: in map but no longer exists")
            continue
        if entry.get("title") != g.title:
            problems.append(
                f"game {gid}: title changed since review "
                f"({entry.get('title')!r} → {g.title!r})"
            )
        recorded = entry.get("session_courses")
        if (
            not isinstance(recorded, list)
            or not all(isinstance(r, int) and not isinstance(r, bool) for r in recorded)
            or sorted(recorded) != g.session_courses
        ):
            problems.append(
                f"game {gid} {g.title!r}: session courses changed since review "
                f"({recorded} → {g.session_courses})"
            )
        cid = entry.get("course_id")
        if cid is None:
            problems.append(f"game {gid} {g.title!r}: course_id is null")
        elif not isinstance(cid, int) or isinstance(cid, bool) or cid not in courses:
            problems.append(f"game {gid} {g.title!r}: course {cid!r} does not exist")
        elif g.session_courses and any(c != cid for c in g.session_courses):
            warnings.append(
                f"game {gid} {g.title!r}: mapped to course {cid} but has sessions in "
                f"{g.session_courses}"
            )

    for s in active_sessions(conn):
        problems.append(
            f"session {s['session_id']} (room {s['room_code']}, game {s['game_id']}) "
            f"is {s['status']}: finish or end it first"
        )
    return problems, warnings
