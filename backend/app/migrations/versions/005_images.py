"""T8: images table (course-owned, versioned image bytes)

Revision ID: 005
Revises: 004
Create Date: 2026-10-02

See docs/plans/t8-images.md §5. Additive only: a new table, nothing else changes, so it can run
with the backend up. MySQL commits DDL immediately, so the upgrade skips a table or index that
already exists and a run that failed partway can simply be run again.
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEXES = [
    ("ix_images_course_id", ["course_id"], False),
    ("ix_images_replaced_by_id", ["replaced_by_id"], False),
    ("uq_images_public_key", ["public_key"], True),
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("images"):
        existing = {ix["name"] for ix in inspector.get_indexes("images")}
    else:
        existing = set()
        _create_table()
    for name, columns, unique in _INDEXES:
        if name not in existing:
            op.create_index(name, "images", columns, unique=unique)


def _create_table() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False
        ),
        sa.Column("public_key", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "data",
            sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        # No foreign key on purpose (design §5): deleting a newer version must not block.
        sa.Column("replaced_by_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("images"):
        return
    rows = conn.execute(sa.text("SELECT COUNT(*) FROM images")).scalar_one()
    if rows and os.environ.get("ALLOW_IMAGE_LOSS") != "1":
        raise RuntimeError(
            f"Refusing to downgrade: images holds {rows} rows, and questions and session "
            "snapshots may reference them. Set ALLOW_IMAGE_LOSS=1 to drop them anyway."
        )
    op.drop_table("images")
