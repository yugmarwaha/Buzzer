"""Add answer_data column to session_scores

Revision ID: 002
Revises: 001
Create Date: 2026-06-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "session_scores",
        sa.Column("answer_data", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_scores", "answer_data")
