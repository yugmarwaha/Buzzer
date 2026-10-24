"""T4: course-owned quizzes, archive flag, and per-session question snapshots

Revision ID: 004
Revises: 003
Create Date: 2026-09-29

See docs/design-host-admin.md §4. On a database that already has quizzes this must be run
with the backend stopped, after reviewing backend/migration_maps/game_course_map.json
(produced by `python -m scripts.plan_game_courses`).

MySQL commits every DDL statement immediately, so each step checks what already exists
and skips it. A run that failed partway can be finished by simply running it again.

Coupling: this imports app.services.course_map (shared with scripts.plan_game_courses), so
renaming or moving that module breaks the migration. Offline `alembic upgrade --sql` mode
is not supported: the migration inspects the live database.
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services import course_map

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table: str) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(conn).get_columns(table)}


def _validate(conn) -> dict[int, int]:
    """Run the check-mode checks before any DDL. Returns {game_id: course_id} to apply."""
    cols = _columns(conn, "games")
    game_count = conn.execute(sa.text("SELECT COUNT(*) FROM games")).scalar_one()

    needs_map = game_count > 0
    if "course_id" in cols:
        missing = conn.execute(
            sa.text("SELECT COUNT(*) FROM games WHERE course_id IS NULL")
        ).scalar_one()
        needs_map = missing > 0

    if not needs_map:
        # Fresh DB, or every quiz already has a course (rerun after a partial failure).
        problems = [
            f"session {s['session_id']} (room {s['room_code']}) is {s['status']}: "
            "finish or end it first"
            for s in course_map.active_sessions(conn)
        ]
        if problems:
            raise RuntimeError(_format(problems))
        return {}

    path = course_map.map_path()
    data, problems = course_map.read_map(path)
    warnings: list[str] = []
    if data is not None:
        problems, warnings = course_map.check_map(conn, data)
    for w in warnings:
        print(f"WARNING: {w}")
    if problems:
        raise RuntimeError(_format(problems, path))
    return {int(k): v["course_id"] for k, v in data.items()}


def _format(problems: list[str], path=None) -> str:
    head = "T4 migration stopped before changing the schema."
    if path is not None:
        head += (
            f" Map file: {path}. Run `python -m scripts.plan_game_courses` for the "
            "report, fix the map by hand, and rerun."
        )
    return head + "\n  - " + "\n  - ".join(problems)


def upgrade() -> None:
    conn = op.get_bind()
    assignments = _validate(conn)

    cols = _columns(conn, "games")
    if "course_id" not in cols:
        op.add_column("games", sa.Column("course_id", sa.Integer(), nullable=True))
    if "archived_at" not in cols:
        op.add_column("games", sa.Column("archived_at", sa.DateTime(), nullable=True))

    if not sa.inspect(conn).has_table("session_questions"):
        op.create_table(
            "session_questions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "session_id",
                sa.String(36),
                sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("question_id", sa.Integer(), nullable=False),
            sa.Column("order_index", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(50), nullable=False),
            sa.Column("grading_type", sa.String(20), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("answer_data", sa.JSON(), nullable=False),
            sa.Column("time_limit_seconds", sa.Integer(), nullable=False),
            sa.Column("points_value", sa.Float(), nullable=False),
            sa.Column("asked_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "session_id", "question_id", name="uq_session_question"
            ),
        )

    for game_id, course_id in assignments.items():
        conn.execute(
            sa.text(
                "UPDATE games SET course_id = :c WHERE id = :g AND course_id IS NULL"
            ),
            {"c": course_id, "g": game_id},
        )

    if _columns(conn, "games")["course_id"]["nullable"]:
        op.alter_column(
            "games", "course_id", existing_type=sa.Integer(), nullable=False
        )
    insp = sa.inspect(conn)
    if "ix_games_course_id" not in {i["name"] for i in insp.get_indexes("games")}:
        op.create_index("ix_games_course_id", "games", ["course_id"])
    if not any(
        fk["referred_table"] == "courses" for fk in insp.get_foreign_keys("games")
    ):
        op.create_foreign_key(
            "fk_games_course_id", "games", "courses", ["course_id"], ["id"]
        )

    # Snapshot every started session that has none yet, from its current questions
    # (best-effort: the originals are gone). COMPLETED → every question was asked;
    # otherwise only questions somebody answered count as asked.
    conn.execute(
        sa.text(
            """
            INSERT INTO session_questions
                (session_id, question_id, order_index, type, grading_type, prompt,
                 config, answer_data, time_limit_seconds, points_value, asked_at)
            SELECT s.id, q.id, q.order_index, q.type, q.grading_type, q.prompt,
                   q.config, q.answer_data, q.time_limit_seconds, q.points_value,
                   CASE
                     WHEN s.status = 'COMPLETED' THEN COALESCE(s.completed_at, s.created_at)
                     WHEN EXISTS (SELECT 1 FROM session_scores sc
                                  WHERE sc.session_id = s.id AND sc.question_id = q.id)
                       THEN COALESCE(s.completed_at, s.created_at)
                     ELSE NULL
                   END
            FROM game_sessions s
            JOIN questions q ON q.game_id = s.game_id
            WHERE s.status IN ('IN_PROGRESS', 'COMPLETED', 'ABANDONED')
              AND NOT EXISTS (SELECT 1 FROM session_questions sq
                              WHERE sq.session_id = s.id)
            """
        )
    )

    # A started session whose quiz has no questions left gets no snapshot rows, so its
    # results show as empty. Nothing to copy from; name them so an operator knows.
    empty = conn.execute(
        sa.text(
            """
            SELECT s.id, s.room_code, s.status FROM game_sessions s
            WHERE s.status IN ('IN_PROGRESS', 'COMPLETED', 'ABANDONED')
              AND NOT EXISTS (SELECT 1 FROM session_questions sq
                              WHERE sq.session_id = s.id)
            """
        )
    ).all()
    for session_id, room_code, status in empty:
        print(
            f"WARNING: session {session_id} (room {room_code}, {status}) has no "
            "questions to snapshot; its results will show no questions"
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if insp.has_table("session_questions"):
        rows = conn.execute(
            sa.text("SELECT COUNT(*) FROM session_questions")
        ).scalar_one()
        if rows and os.environ.get("ALLOW_SNAPSHOT_LOSS") != "1":
            raise RuntimeError(
                f"Refusing to downgrade: session_questions holds {rows} snapshot rows. "
                "Re-upgrading would re-snapshot from today's questions and rewrite "
                "history. Set ALLOW_SNAPSHOT_LOSS=1 to downgrade anyway."
            )
        op.drop_table("session_questions")

    for fk in insp.get_foreign_keys("games"):
        if fk["referred_table"] == "courses":
            op.drop_constraint(fk["name"], "games", type_="foreignkey")
    if "ix_games_course_id" in {
        i["name"] for i in sa.inspect(conn).get_indexes("games")
    }:
        op.drop_index("ix_games_course_id", table_name="games")
    cols = _columns(conn, "games")
    if "archived_at" in cols:
        op.drop_column("games", "archived_at")
    if "course_id" in cols:
        op.drop_column("games", "course_id")
