"""Initial schema — all tables

Revision ID: 001
Revises:
Create Date: 2026-06-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("netid", sa.String(50), unique=True, index=True, nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), unique=True, index=True, nullable=True),
        sa.Column("username", sa.String(50), unique=True, nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column(
            "role",
            sa.Enum("ADMIN", "USER", "GUEST", name="user_role"),
            nullable=False,
            server_default="USER",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_login", sa.DateTime, nullable=True),
    )

    op.create_table(
        "courses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("semester", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "course_rosters",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "course_id",
            sa.Integer,
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("netid", sa.String(50), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column(
            "imported_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("course_id", "netid", name="uq_roster_course_netid"),
    )
    op.create_index("ix_roster_course_netid", "course_rosters", ["course_id", "netid"])

    op.create_table(
        "games",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("max_players", sa.Integer, nullable=False, server_default="150"),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "questions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "game_id",
            sa.Integer,
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column(
            "grading_type",
            sa.Enum("ACCURACY", "COMPLETENESS", name="grading_type"),
            nullable=False,
        ),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("answer_data", sa.JSON, nullable=False),
        sa.Column(
            "time_limit_seconds", sa.Integer, nullable=False, server_default="30"
        ),
        sa.Column("points_value", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("order_index", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "user_course_access",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "course_id",
            sa.Integer,
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role",
            sa.Enum("HOST", "PLAYER", name="course_role"),
            nullable=False,
        ),
    )

    op.create_table(
        "user_game_access",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "game_id",
            sa.Integer,
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "game_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("room_code", sa.String(6), unique=True, index=True, nullable=False),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), nullable=False),
        sa.Column(
            "host_user_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "LOBBY",
                "IN_PROGRESS",
                "COMPLETED",
                "ABANDONED",
                name="session_status",
            ),
            nullable=False,
            server_default="LOBBY",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "session_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "question_id", sa.Integer, sa.ForeignKey("questions.id"), nullable=False
        ),
        sa.Column("points_awarded", sa.Integer, nullable=False, server_default="0"),
        sa.Column("answer_time_ms", sa.Integer, nullable=True),
        sa.Column("is_correct", sa.Boolean, nullable=False, server_default="0"),
        sa.Column(
            "timestamp",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_scores_session_user", "session_scores", ["session_id", "user_id"]
    )


def downgrade() -> None:
    op.drop_table("session_scores")
    op.drop_table("game_sessions")
    op.drop_table("user_game_access")
    op.drop_table("user_course_access")
    op.drop_table("questions")
    op.drop_table("games")
    op.drop_table("course_rosters")
    op.drop_table("courses")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.execute("DROP TYPE IF EXISTS grading_type")
    op.execute("DROP TYPE IF EXISTS course_role")
    op.execute("DROP TYPE IF EXISTS user_role")
