"""
T4 migration + planning-script tests (docs/design-host-admin.md §4, §13 "Migration").

Each test builds a throwaway MySQL database *inside the stack under test*, migrates it to
revision 003 (the pre-T4 schema), seeds legacy data, and then drives
`scripts.plan_game_courses` and `alembic` inside the backend container with DATABASE_URL
and GAME_COURSE_MAP_PATH pointed at the scratch database and a scratch map file. The real
`buzzer` database and the team's real map file are never touched.
"""

from __future__ import annotations

import json
import subprocess
import uuid

import pytest

from .conftest import _compose_cmd, _REPO_ROOT


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        _compose_cmd() + args, cwd=_REPO_ROOT, capture_output=True, text=True, input=stdin
    )


def root_sql(query: str, db: str = "") -> str:
    r = _run(
        [
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B $1 -e "$0"',
            query,
            db,
        ]
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class Scratch:
    """A scratch database + scratch map file for one test."""

    def __init__(self):
        self.name = f"migtest_{uuid.uuid4().hex[:8]}"
        self.map = f"/tmp/{self.name}.json"
        self.report = f"/tmp/{self.name}.report.txt"  # written next to a new map
        base_url = _run(["exec", "-T", "backend", "printenv", "DATABASE_URL"]).stdout.strip()
        assert base_url, "backend has no DATABASE_URL"
        self.url = base_url.rsplit("/", 1)[0] + "/" + self.name
        root_sql(f"CREATE DATABASE {self.name}")
        root_sql(f"GRANT ALL PRIVILEGES ON {self.name}.* TO 'app_user'@'%'")

    def env(self, **extra) -> list[str]:
        flags = ["-e", f"DATABASE_URL={self.url}", "-e", f"GAME_COURSE_MAP_PATH={self.map}"]
        for k, v in extra.items():
            flags += ["-e", f"{k}={v}"]
        return flags

    def backend(self, *cmd: str, **env) -> subprocess.CompletedProcess:
        return _run(["exec", "-T", *self.env(**env), "backend", *cmd])

    def alembic(self, *args: str, **env) -> subprocess.CompletedProcess:
        return self.backend("alembic", *args, **env)

    def plan(self) -> subprocess.CompletedProcess:
        return self.backend("python", "-m", "scripts.plan_game_courses")

    def sql(self, query: str) -> str:
        return root_sql(query, self.name)

    def read_map(self) -> str:
        return self.read_file(self.map)

    def read_file(self, path: str) -> str:
        r = _run(["exec", "-T", "backend", "cat", path])
        assert r.returncode == 0, r.stderr
        return r.stdout

    def file_exists(self, path: str) -> bool:
        return _run(["exec", "-T", "backend", "test", "-e", path]).returncode == 0

    def write_map(self, data: dict) -> None:
        r = _run(
            ["exec", "-T", "backend", "sh", "-c", f"cat > {self.map}"], stdin=json.dumps(data)
        )
        assert r.returncode == 0, r.stderr

    def has_column(self, table: str, column: str) -> bool:
        return (
            self.sql(
                "SELECT COUNT(*) FROM information_schema.columns WHERE "
                f"table_schema='{self.name}' AND table_name='{table}' AND column_name='{column}'"
            )
            == "1"
        )

    def drop(self):
        root_sql(f"DROP DATABASE IF EXISTS {self.name}")
        _run(["exec", "-T", "backend", "rm", "-f", self.map, self.report])


@pytest.fixture()
def scratch(docker_stack):
    s = Scratch()
    r = s.alembic("upgrade", "003")
    assert r.returncode == 0, r.stderr
    yield s
    s.drop()


def seed_legacy(s: Scratch, *, demo_session_course: str | None = None) -> dict:
    """Pre-T4 data: a demo quiz, a one-course quiz, a no-session quiz, a two-course quiz,
    an old per-game grant, and a completed session with scores."""
    s.sql(
        "INSERT INTO users (id, username, display_name, role) VALUES "
        "('u-host','hosta','Host A','USER'), ('u-grant','granty','Grant Holder','USER'), "
        "('u-player','playa','Player','USER')"
    )
    s.sql(
        "INSERT INTO courses (id, name, semester) VALUES "
        "(1,'Demo Course','Demo'), (2,'Course A','F26'), (3,'Course B','F26')"
    )
    s.sql("INSERT INTO user_course_access (user_id, course_id, role) VALUES ('u-host', 2, 'HOST')")
    s.sql(
        "INSERT INTO games (id, title, description, max_players) VALUES "
        "(10,'Buzzer Demo','',150), (11,'One Course Quiz','',150), "
        "(12,'Unplayed Quiz','',150), (13,'Two Course Quiz','',150)"
    )
    s.sql(
        "INSERT INTO questions (id, game_id, type, grading_type, prompt, config, answer_data, "
        "time_limit_seconds, points_value, order_index) VALUES "
        "(100, 11, 'true_false', 'ACCURACY', 'Legacy Q1', '{}', "
        "'{\"answer_points\": {\"true\": 1, \"false\": 0}}', 30, 1, 0), "
        "(101, 11, 'true_false', 'ACCURACY', 'Legacy Q2', '{}', "
        "'{\"answer_points\": {\"true\": 1, \"false\": 0}}', 30, 1, 1)"
    )
    s.sql("INSERT INTO user_game_access (user_id, game_id) VALUES ('u-grant', 11)")
    s.sql(
        "INSERT INTO game_sessions (id, room_code, game_id, course_id, host_user_id, status, "
        "completed_at) VALUES "
        "('s-one','AAAAAA',11,2,'u-host','COMPLETED',NOW()), "
        "('s-two-a','BBBBBB',13,2,'u-host','COMPLETED',NOW()), "
        "('s-two-b','CCCCCC',13,3,'u-host','COMPLETED',NOW())"
    )
    s.sql(
        "INSERT INTO session_scores (session_id, user_id, question_id, points_awarded, is_correct) "
        "VALUES ('s-one','u-player',100,1,1)"
    )
    if demo_session_course:
        s.sql(
            "INSERT INTO game_sessions (id, room_code, game_id, course_id, host_user_id, status) "
            f"VALUES ('s-demo','DDDDDD',10,{demo_session_course},'u-host','COMPLETED')"
        )
    return {"demo": 10, "one": 11, "unplayed": 12, "two": 13}


def complete_map(s: Scratch) -> dict:
    data = json.loads(s.read_map())
    data["12"]["course_id"] = 2
    data["13"]["course_id"] = 2
    s.write_map(data)
    return data


def test_planner_proposals_and_report(scratch):
    seed_legacy(scratch)
    r = scratch.plan()
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(scratch.read_map())
    assert data["10"]["course_id"] == 1  # demo with no sessions → Demo Course
    assert data["11"]["course_id"] == 2  # all sessions in one course
    assert data["12"]["course_id"] is None  # no sessions
    assert data["13"]["course_id"] is None  # sessions in two courses
    assert data["13"]["session_courses"] == [2, 3]
    # Report: who gains (HOSTs of the proposed course) and who loses (old grant holders).
    assert "Host A" in r.stdout and "Grant Holder" in r.stdout


def report_access(report: str, game_id: int) -> tuple[str, str]:
    """The (gains, loses) lines printed under one quiz in the planner report."""
    lines = report.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith(f"game {game_id} "))
    gains, loses = lines[i + 1], lines[i + 2]
    assert "gains access" in gains and "loses access" in loses, (gains, loses)
    return gains, loses


def test_planner_report_puts_each_user_on_the_right_side(scratch):
    seed_legacy(scratch)
    # Host A also holds an old grant on game 11, but as a HOST of course 2 keeps access.
    scratch.sql(
        "INSERT INTO user_game_access (user_id, game_id) VALUES ('u-host', 11), ('u-grant', 13)"
    )
    r = scratch.plan()
    assert r.returncode == 0, r.stdout + r.stderr

    gains, loses = report_access(r.stdout, 11)  # proposed course 2 (Host A is its HOST)
    assert "Host A" in gains and "Grant Holder" not in gains
    assert "Grant Holder" in loses and "Host A" not in loses
    gains, loses = report_access(r.stdout, 13)  # unresolved: nobody gains, grants are lost
    assert gains.endswith(": -") and "Grant Holder" in loses
    gains, loses = report_access(r.stdout, 10)  # demo → Demo Course, which has no HOSTs
    assert gains.endswith(": -") and loses.endswith(": -")


def test_planner_writes_report_file_and_never_overwrites(scratch):
    seed_legacy(scratch)
    assert not scratch.file_exists(scratch.report)
    r = scratch.plan()
    assert r.returncode == 0, r.stdout + r.stderr
    report = scratch.read_file(scratch.report)
    assert report.strip() and report.strip() in r.stdout  # same report as printed
    assert scratch.report in r.stdout

    complete_map(scratch)
    map_before = scratch.read_map()
    # Check mode (map exists): a change in the DB is reported but neither file is touched.
    scratch.sql("UPDATE games SET title='Renamed' WHERE id=11")
    r = scratch.plan()
    assert r.returncode == 1 and "title changed" in r.stdout
    assert "'Renamed'" in r.stdout and "'Renamed'" not in scratch.read_file(scratch.report)
    assert scratch.read_map() == map_before
    assert scratch.read_file(scratch.report) == report

    # A missing report is not recreated in check mode either.
    assert _run(["exec", "-T", "backend", "rm", scratch.report]).returncode == 0
    assert scratch.plan().returncode == 1
    assert not scratch.file_exists(scratch.report)
    assert scratch.read_map() == map_before


def test_demo_quiz_with_sessions_only_in_demo_course(scratch):
    seed_legacy(scratch, demo_session_course="1")
    r = scratch.plan()
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(scratch.read_map())["10"]["course_id"] == 1
    assert "proposed course 1 (demo)" in r.stdout


@pytest.mark.parametrize("duplicate", ["title", "course"])
def test_demo_rule_needs_unique_title_and_course(scratch, duplicate):
    """Rule 1 applies only when `Buzzer Demo` and `Demo Course` are both unique; otherwise
    the demo quiz stays null. The rule is terminal: game 10's sessions are all in one
    course, which rule 2 would otherwise accept."""
    seed_legacy(scratch, demo_session_course="1")
    if duplicate == "title":
        scratch.sql(
            "INSERT INTO games (id, title, description, max_players) "
            "VALUES (14,'Buzzer Demo','',150)"
        )
        expected = "2 games titled 'Buzzer Demo'"
    else:
        scratch.sql("INSERT INTO courses (id, name, semester) VALUES (4,'Demo Course','Old')")
        expected = "2 courses named 'Demo Course'"
    r = scratch.plan()
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(scratch.read_map())
    assert data["10"]["course_id"] is None
    assert data["10"]["session_courses"] == [1]
    if duplicate == "title":
        assert data["14"]["course_id"] is None
    assert expected in r.stdout


def test_demo_rule_is_terminal(scratch):
    seed_legacy(scratch, demo_session_course="2")
    assert scratch.plan().returncode == 0
    data = json.loads(scratch.read_map())
    # All its sessions are in *one* course, but not Demo Course: stays unresolved.
    assert data["10"]["course_id"] is None


def test_existing_map_is_never_overwritten_and_check_mode_detects_changes(scratch):
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    before = scratch.read_map()

    r = scratch.plan()
    assert r.returncode == 0, r.stdout
    assert scratch.read_map() == before

    # Each change after the review makes check mode fail — and the file stays unchanged.
    cases = [
        ("INSERT INTO games (id, title, description, max_players) VALUES (14,'New','',150)",
         "missing from map", "DELETE FROM games WHERE id=14"),
        ("UPDATE games SET title='Renamed' WHERE id=11", "title changed",
         "UPDATE games SET title='One Course Quiz' WHERE id=11"),
        ("INSERT INTO game_sessions (id, room_code, game_id, course_id, status) "
         "VALUES ('s-new','EEEEEE',11,3,'COMPLETED')", "session courses changed",
         "DELETE FROM game_sessions WHERE id='s-new'"),
        ("INSERT INTO game_sessions (id, room_code, game_id, course_id, status) "
         "VALUES ('s-live','FFFFFF',11,2,'LOBBY')", "LOBBY",
         "DELETE FROM game_sessions WHERE id='s-live'"),
    ]
    for change, expected, undo in cases:
        scratch.sql(change)
        r = scratch.plan()
        assert r.returncode == 1 and expected in r.stdout, (expected, r.stdout)
        assert scratch.read_map() == before
        scratch.sql(undo)

    scratch.sql("DELETE FROM games WHERE id=12")
    r = scratch.plan()
    assert r.returncode == 1 and "no longer exists" in r.stdout


def test_bad_map_stops_before_any_schema_change(scratch):
    seed_legacy(scratch)
    r = scratch.alembic("upgrade", "head")  # no map file at all
    assert r.returncode != 0 and "map file not found" in (r.stdout + r.stderr)
    assert not scratch.has_column("games", "course_id")

    assert scratch.plan().returncode == 0  # proposal still has nulls
    r = scratch.alembic("upgrade", "head")
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "game 12" in out and "game 13" in out  # every problem listed at once
    assert not scratch.has_column("games", "course_id")

    data = complete_map(scratch)
    data["11"]["course_id"] = 999  # unknown course
    scratch.write_map(data)
    r = scratch.alembic("upgrade", "head")
    assert r.returncode != 0 and "does not exist" in (r.stdout + r.stderr)
    assert not scratch.has_column("games", "course_id")

    scratch.write_map({"11": "garbage"})
    r = scratch.alembic("upgrade", "head")
    assert r.returncode != 0
    assert not scratch.has_column("games", "course_id")


def test_upgrade_applies_map_and_snapshots_then_downgrade_guard(scratch):
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING" in r.stdout  # game 13 mapped to 2 but has a session in course 3

    rows = scratch.sql("SELECT id, course_id FROM games ORDER BY id")
    assert rows.splitlines() == ["10\t1", "11\t2", "12\t2", "13\t2"]
    assert scratch.sql(
        "SELECT is_nullable FROM information_schema.columns WHERE "
        f"table_schema='{scratch.name}' AND table_name='games' AND column_name='course_id'"
    ) == "NO"
    # Every non-LOBBY session got snapshot rows; a COMPLETED one counts all as asked.
    assert scratch.sql(
        "SELECT COUNT(*), SUM(asked_at IS NOT NULL) FROM session_questions WHERE session_id='s-one'"
    ) == "2\t2"
    assert scratch.sql("SELECT COUNT(*) FROM user_game_access") == "1"  # untouched

    r = scratch.alembic("downgrade", "003")
    assert r.returncode != 0 and "ALLOW_SNAPSHOT_LOSS" in (r.stdout + r.stderr)
    assert scratch.has_column("games", "course_id")
    r = scratch.alembic("downgrade", "003", ALLOW_SNAPSHOT_LOSS="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not scratch.has_column("games", "course_id")


def test_rerun_after_partial_failure(scratch):
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    # Simulate a run that died right after its first DDL statement.
    scratch.sql("ALTER TABLE games ADD COLUMN course_id INT NULL")
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Duplicate column" not in r.stderr
    assert scratch.sql("SELECT COUNT(*) FROM games WHERE course_id IS NULL") == "0"


def _fk_to_courses(s: Scratch) -> list[str]:
    return s.sql(
        "SELECT constraint_name FROM information_schema.referential_constraints WHERE "
        f"constraint_schema='{s.name}' AND table_name='games' "
        "AND referenced_table_name='courses'"
    ).split()


def _has_index(s: Scratch) -> bool:
    return s.sql(
        "SELECT COUNT(*) FROM information_schema.statistics WHERE "
        f"table_schema='{s.name}' AND table_name='games' AND index_name='ix_games_course_id'"
    ) != "0"


def _snapshot_counts(s: Scratch) -> str:
    return s.sql(
        "SELECT session_id, COUNT(*), SUM(asked_at IS NOT NULL) FROM session_questions "
        "GROUP BY session_id ORDER BY session_id"
    )


@pytest.mark.parametrize("failed_after", ["not_null", "index", "fk"])
def test_rerun_after_failure_in_later_steps(scratch, failed_after):
    """A run that died after the NOT NULL, index, or FK step (MySQL DDL commits at once, so
    the version is still 003) is finished by simply rerunning the upgrade."""
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    snapshots = _snapshot_counts(scratch)
    assert snapshots

    # Roll the schema back to exactly what the failed run would have left behind.
    scratch.sql("DELETE FROM session_questions")  # the snapshot step is always last
    if failed_after in ("not_null", "index"):
        for fk in _fk_to_courses(scratch):
            scratch.sql(f"ALTER TABLE games DROP FOREIGN KEY {fk}")
    if failed_after == "not_null":
        scratch.sql("DROP INDEX ix_games_course_id ON games")
    assert scratch.alembic("stamp", "003").returncode == 0

    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert scratch.sql(
        "SELECT is_nullable FROM information_schema.columns WHERE "
        f"table_schema='{scratch.name}' AND table_name='games' AND column_name='course_id'"
    ) == "NO"
    assert _has_index(scratch)
    assert len(_fk_to_courses(scratch)) == 1
    assert _snapshot_counts(scratch) == snapshots
    assert scratch.sql("SELECT course_id FROM games ORDER BY id").split() == ["1", "2", "2", "2"]


def test_snapshot_backfill_is_idempotent_on_rerun(scratch):
    """Rerunning the upgrade never duplicates session_questions rows; a session missing its
    snapshot gets it back, the others are left alone."""
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    assert scratch.alembic("upgrade", "head").returncode == 0
    snapshots = _snapshot_counts(scratch)
    ids_before = scratch.sql("SELECT id FROM session_questions WHERE session_id='s-one' ORDER BY id")

    assert scratch.alembic("stamp", "003").returncode == 0
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot_counts(scratch) == snapshots
    assert scratch.sql(
        "SELECT id FROM session_questions WHERE session_id='s-one' ORDER BY id"
    ) == ids_before

    scratch.sql("DELETE FROM session_questions WHERE session_id='s-one'")
    assert scratch.alembic("stamp", "003").returncode == 0
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot_counts(scratch) == snapshots
    assert scratch.sql(
        "SELECT COUNT(*) FROM (SELECT session_id, question_id FROM session_questions "
        "GROUP BY session_id, question_id HAVING COUNT(*) > 1) d"
    ) == "0"


def test_empty_database_needs_no_map(scratch):
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert scratch.has_column("games", "course_id")


@pytest.mark.parametrize("status", ["LOBBY", "IN_PROGRESS"])
def test_check_mode_and_migration_refuse_while_a_room_is_live(scratch, status):
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    scratch.sql(
        "INSERT INTO game_sessions (id, room_code, game_id, course_id, host_user_id, status) "
        f"VALUES ('s-live','LLLLLL',11,2,'u-host','{status}')"
    )
    before = scratch.read_map()

    r = scratch.plan()
    assert r.returncode == 1 and status in r.stdout and "s-live" in r.stdout, r.stdout
    assert scratch.read_map() == before

    r = scratch.alembic("upgrade", "head")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "s-live" in out and status in out, out
    assert not scratch.has_column("games", "course_id")
    assert "session_questions" not in scratch.sql("SHOW TABLES")

    # Once the room is finished (here: marked ABANDONED as §4 step 4 prescribes), both pass.
    scratch.sql(
        "UPDATE game_sessions SET status='ABANDONED', completed_at=NOW() WHERE id='s-live'"
    )
    assert scratch.plan().returncode == 0
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr


def test_rerun_refuses_while_a_room_is_live(scratch):
    """The rerun path (every quiz already has a course, so no map is needed) still refuses
    while a LOBBY/IN_PROGRESS session exists."""
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    scratch.sql("ALTER TABLE games ADD COLUMN course_id INT NULL")
    scratch.sql("UPDATE games SET course_id=2")
    scratch.sql(
        "INSERT INTO game_sessions (id, room_code, game_id, course_id, status) "
        "VALUES ('s-live','LLLLLL',11,2,'IN_PROGRESS')"
    )
    r = scratch.alembic("upgrade", "head")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "s-live" in out and "IN_PROGRESS" in out, out
    assert "session_questions" not in scratch.sql("SHOW TABLES")


def test_abandoned_session_snapshot_marks_only_answered_questions(scratch):
    """§4 upgrade step 3: every non-LOBBY session gets snapshot rows. COMPLETED → all asked;
    otherwise (ABANDONED — an IN_PROGRESS one can't exist here, the migration refuses it)
    only questions with a score get asked_at."""
    seed_legacy(scratch)
    scratch.sql(
        "INSERT INTO game_sessions (id, room_code, game_id, course_id, host_user_id, status, "
        "completed_at) VALUES ('s-ab','GGGGGG',11,2,'u-host','ABANDONED',NOW()), "
        "('s-ab-empty','HHHHHH',11,2,'u-host','ABANDONED',NOW())"
    )
    scratch.sql(
        "INSERT INTO session_scores (session_id, user_id, question_id, points_awarded, "
        "is_correct) VALUES ('s-ab','u-player',100,1,1)"
    )
    assert scratch.plan().returncode == 0
    complete_map(scratch)
    r = scratch.alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr

    rows = scratch.sql(
        "SELECT question_id, order_index, prompt, asked_at IS NOT NULL FROM session_questions "
        "WHERE session_id='s-ab' ORDER BY order_index"
    )
    assert rows.splitlines() == ["100\t0\tLegacy Q1\t1", "101\t1\tLegacy Q2\t0"]
    assert scratch.sql(
        "SELECT COUNT(*), SUM(asked_at IS NOT NULL) FROM session_questions "
        "WHERE session_id='s-ab-empty'"
    ) == "2\t0"
    # COMPLETED sessions: every row asked (s-one has questions; s-two-* quiz has none).
    assert scratch.sql(
        "SELECT COUNT(*), SUM(asked_at IS NOT NULL) FROM session_questions WHERE session_id='s-one'"
    ) == "2\t2"


def _check_problems(s: Scratch, expected: str) -> str:
    """Check mode and the migration's own validation must both refuse the map, reporting
    `expected` (check mode as a listed problem, not a crash). Returns check mode's output."""
    r = s.plan()
    assert r.returncode == 1 and expected in r.stdout, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    up = s.alembic("upgrade", "head")
    assert up.returncode != 0 and expected in up.stdout + up.stderr, up.stdout + up.stderr
    assert not s.has_column("games", "course_id")
    return r.stdout


def test_check_mode_rejects_mixed_type_session_courses(scratch):
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    data = complete_map(scratch)
    data["13"]["session_courses"] = [2, "3"]  # mixed types: must be a problem, not a crash
    scratch.write_map(data)
    before = scratch.read_map()
    out = _check_problems(scratch, "session courses changed")
    assert "game 13" in out
    assert scratch.read_map() == before

    data["13"]["session_courses"] = ["2", "3"]
    scratch.write_map(data)
    assert "game 13" in _check_problems(scratch, "session courses changed")


def test_check_mode_rejects_duplicate_normalised_keys(scratch):
    seed_legacy(scratch)
    assert scratch.plan().returncode == 0
    data = complete_map(scratch)
    data["011"] = dict(data["11"], course_id=3)  # same game as "11" once parsed as an int
    scratch.write_map(data)
    before = scratch.read_map()
    out = _check_problems(scratch, "duplicate map key")
    assert "'011'" in out
    assert scratch.read_map() == before
