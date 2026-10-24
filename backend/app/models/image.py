from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base

# MEDIUMBLOB holds up to 16 MiB; uploads are capped at 5 MiB (docs/plans/t8-images.md §4).
_BLOB = sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")


class Image(Base):
    """One version of one course image (T8). Replace makes a new row; see design §5 and §7.

    "Current" = replaced_by_id IS NULL AND deleted_at IS NULL. Older versions are kept while
    an archived quiz or a session snapshot still references them.
    """

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # No ondelete, like games.course_id: a course that has images cannot be deleted.
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    # Unguessable key in the public view URL /api/images/{public_key}; one per version.
    public_key: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    # Deferred: lists, counts, URL maps and reference scans never load the bytes.
    data: Mapped[bytes] = mapped_column(_BLOB, nullable=False, deferred=True)
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Plain int, no foreign key: deleting a newer version must never block or revive this one.
    replaced_by_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
