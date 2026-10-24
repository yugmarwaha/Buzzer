"""Change points_value and points_awarded columns from INTEGER to FLOAT

Revision ID: 003
Revises: 002
Create Date: 2026-06-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "questions",
        "points_value",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        existing_server_default=sa.text("1000"),
    )
    op.alter_column(
        "session_scores",
        "points_awarded",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.alter_column(
        "questions",
        "points_value",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "session_scores",
        "points_awarded",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
