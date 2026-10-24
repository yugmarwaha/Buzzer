"""Course images (T8): validation, storage, versioned replace, delete and retention.

Design: docs/plans/t8-images.md. Callers hold the course row lock (access_service.lock_course)
before calling any function that writes; inside, locks follow the global order
course -> questions -> images -> session rows (§8). Image bytes are never loaded except by the
public view route, export and the report (`Image.data` is deferred).
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile
from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ..common.exceptions import BuzzerError, ConflictError, NotFoundError
from ..models.game import Game, Question
from ..models.image import Image
from ..models.session import GameSession, SessionQuestion
from ..schemas.admin import OPTION_IMAGES, PIN_IMAGE, PROMPT_IMAGE
from ..schemas.images import (
    CONTENT_TYPES,
    MAX_COURSE_IMAGE_BYTES,
    MAX_COURSE_IMAGES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_SIDE,
    image_url,
)

# Pillow's own decompression-bomb guard, set once (§4). The header side check below already
# keeps every decoded image within this limit.
PILImage.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

SINGLE_IMAGE_FIELDS = (PROMPT_IMAGE, PIN_IMAGE)
OPTION_IMAGE_FIELD = OPTION_IMAGES
OPEN_ROOM_STATUSES = ("LOBBY", "IN_PROGRESS")


def _too_large(message: str) -> BuzzerError:
    return BuzzerError("PAYLOAD_TOO_LARGE", message, 413)


def _unsupported() -> BuzzerError:
    return BuzzerError("UNSUPPORTED_IMAGE", "Unsupported image type", 415)


def _invalid(message: str = "Not a valid image") -> BuzzerError:
    return BuzzerError("INVALID_IMAGE", message, 422)


# ---------------------------------------------------------------------------
# Reading and validating files (§4)
# ---------------------------------------------------------------------------


@dataclass
class LoadedImage:
    data: bytes
    content_type: str
    width: int
    height: int


async def read_upload(
    file: UploadFile, cap: int = MAX_IMAGE_BYTES, what: str = "Image"
) -> bytes:
    """Read at most cap + 1 bytes of an upload (a memory bound; the request-size middleware
    and nginx bound what the server receives)."""
    data = await file.read(cap + 1)
    if len(data) > cap:
        raise _too_large(f"{what} is larger than {cap // 1_048_576} MiB")
    return data


def load_image(data: bytes) -> LoadedImage:
    """Fully validate one image file. CPU-bound: call it with run_in_threadpool, and before
    taking the course lock. 415 = not a supported format; 422 = supported but too large in
    pixels, animated or broken (§4)."""
    if not data:
        raise _invalid("The file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise _too_large("Image is larger than 5 MiB")
    try:
        img = PILImage.open(io.BytesIO(data))
    except UnidentifiedImageError:
        raise _unsupported() from None
    except Exception:  # DecompressionBombError and any other decoder failure
        raise _invalid() from None
    with img:
        fmt = img.format
        if fmt not in CONTENT_TYPES:
            raise _unsupported()
        width, height = img.size
        if not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE):
            raise _invalid(f"Image is larger than {MAX_IMAGE_SIDE} px")
        try:
            # MPO's second frame is an embedded still image, not animation. n_frames
            # seeks through the file, so a malformed GIF can raise here.
            animated = fmt != "MPO" and getattr(img, "n_frames", 1) > 1
        except Exception:
            raise _invalid() from None
        if animated:
            raise _invalid("Animated images aren't supported")
        try:
            img.load()
        except Exception:
            raise _invalid() from None
    return LoadedImage(data, CONTENT_TYPES[fmt], width, height)


def clean_filename(name: object) -> str:
    """Display name: no path, trimmed, at most 255 characters; otherwise "image"."""
    if not isinstance(name, str):
        return "image"
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
    return base or "image"


# ---------------------------------------------------------------------------
# Image references inside question config (§5)
# ---------------------------------------------------------------------------


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def image_ids_in_config(config: object) -> set[int]:
    """Every image ID a question's config references."""
    ids: set[int] = set()
    if not isinstance(config, dict):
        return ids
    for field in SINGLE_IMAGE_FIELDS:
        if _is_id(config.get(field)):
            ids.add(config[field])
    options = config.get(OPTION_IMAGE_FIELD)
    if isinstance(options, list):
        ids.update(v for v in options if _is_id(v))
    return ids


def replace_id_in_config(config: dict, old_id: int, new_id: int) -> dict:
    """A copy of config with every reference to old_id pointing at new_id."""
    updated = dict(config)
    for field in SINGLE_IMAGE_FIELDS:
        if _is_id(updated.get(field)) and updated[field] == old_id:
            updated[field] = new_id
    options = updated.get(OPTION_IMAGE_FIELD)
    if isinstance(options, list):
        updated[OPTION_IMAGE_FIELD] = [
            new_id if _is_id(v) and v == old_id else v for v in options
        ]
    return updated


@dataclass
class QuestionRef:
    question_id: int
    game_id: int
    game_title: str
    archived: bool


async def question_refs(
    db: AsyncSession, course_id: int, image_id: int
) -> list[QuestionRef]:
    """Questions of the course referencing image_id. A locking read (FOR SHARE OF questions):
    the request's snapshot can predate the course-lock wait (§8)."""
    rows = (
        await db.execute(
            select(Question.id, Question.config, Game.id, Game.title, Game.archived_at)
            .join(Game, Game.id == Question.game_id)
            .where(Game.course_id == course_id)
            .with_for_update(read=True, of=Question)
        )
    ).all()
    return [
        QuestionRef(qid, gid, title, archived_at is not None)
        for qid, config, gid, title, archived_at in rows
        if image_id in image_ids_in_config(config)
    ]


async def snapshot_uses(db: AsyncSession, course_id: int, image_id: int) -> bool:
    """Whether any session snapshot of the course references image_id (locking read)."""
    configs = (
        (
            await db.execute(
                select(SessionQuestion.config)
                .join(GameSession, GameSession.id == SessionQuestion.session_id)
                .where(GameSession.course_id == course_id)
                .with_for_update(read=True, of=SessionQuestion)
            )
        )
        .scalars()
        .all()
    )
    return any(image_id in image_ids_in_config(c) for c in configs)


# ---------------------------------------------------------------------------
# Rows, caps and payload helpers
# ---------------------------------------------------------------------------


def is_current(image: Image) -> bool:
    return image.replaced_by_id is None and image.deleted_at is None


async def lock_current_image(db: AsyncSession, course_id: int, image_id: int) -> Image:
    """Replace/delete: the row, locked, and current. It existed at the access check, so a
    row gone by now was pruned or deleted by a request that won the course lock: 409."""
    try:
        image = await lock_image(db, image_id)
    except NotFoundError:
        image = None
    if image is None or image.course_id != course_id or not is_current(image):
        raise ConflictError("This image was replaced or deleted; reload")
    return image


async def lock_image(db: AsyncSession, image_id: int) -> Image:
    image = (
        await db.execute(
            select(Image)
            .where(Image.id == image_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if image is None:
        raise NotFoundError(f"Image {image_id} not found")
    return image


async def course_of_image(db: AsyncSession, image_id: int) -> int:
    """The image's course, read unlocked (it never changes) for the access check."""
    course_id = (
        await db.execute(select(Image.course_id).where(Image.id == image_id))
    ).scalar_one_or_none()
    if course_id is None:
        raise NotFoundError(f"Image {image_id} not found")
    return course_id


async def _check_storage(
    db: AsyncSession,
    course_id: int,
    *,
    add_count: int,
    add_bytes: int,
    remove_bytes: int = 0,
) -> None:
    """Per-course caps on **current** images only (§4); retained history does not count."""
    count, total = (
        await db.execute(
            select(func.count(Image.id), func.coalesce(func.sum(Image.size_bytes), 0))
            .where(
                Image.course_id == course_id,
                Image.replaced_by_id.is_(None),
                Image.deleted_at.is_(None),
            )
            .with_for_update(read=True)
        )
    ).one()
    if (
        count + add_count > MAX_COURSE_IMAGES
        or total - remove_bytes + add_bytes > MAX_COURSE_IMAGE_BYTES
    ):
        raise ConflictError("Course image storage is full; delete unused images")


def image_item(image: Image, use_count: int, archived_use_count: int) -> dict:
    return {
        "id": image.id,
        "course_id": image.course_id,
        "filename": image.filename,
        "content_type": image.content_type,
        "size_bytes": image.size_bytes,
        "width": image.width,
        "height": image.height,
        "url": image_url(image.public_key),
        "created_at": image.created_at,
        "use_count": use_count,
        "archived_use_count": archived_use_count,
    }


def ids_in_configs(configs: Iterable[object]) -> set[int]:
    ids: set[int] = set()
    for config in configs:
        ids |= image_ids_in_config(config)
    return ids


async def image_urls(db: AsyncSession, configs: Iterable[object]) -> dict[str, str]:
    """{"<id>": "/api/images/<key>"} for every image the configs reference that still has a
    row (old versions included). A missing ID means "Image unavailable" to clients (§5)."""
    ids = ids_in_configs(configs)
    if not ids:
        return {}
    rows = (
        await db.execute(select(Image.id, Image.public_key).where(Image.id.in_(ids)))
    ).all()
    return {str(i): image_url(key) for i, key in rows}


async def image_info(db: AsyncSession, configs: Iterable[object]) -> dict[str, dict]:
    """The editor's map: {"<id>": {"url": ..., "current": bool}} (QuestionItem.images)."""
    ids = ids_in_configs(configs)
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                Image.id, Image.public_key, Image.replaced_by_id, Image.deleted_at
            ).where(Image.id.in_(ids))
        )
    ).all()
    return {
        str(i): {
            "url": image_url(key),
            "current": replaced_by is None and deleted_at is None,
        }
        for i, key, replaced_by, deleted_at in rows
    }


def images_for(config: object, info: dict[str, dict]) -> dict[str, dict]:
    """The part of an image map one question's config references."""
    return {str(i): info[str(i)] for i in image_ids_in_config(config) if str(i) in info}


def _labelled_refs(config: dict) -> list[tuple[str, int]]:
    refs = [(f, config[f]) for f in SINGLE_IMAGE_FIELDS if _is_id(config.get(f))]
    options = config.get(OPTION_IMAGE_FIELD)
    if isinstance(options, list):
        refs += [
            (f"{OPTION_IMAGE_FIELD}[{i}]", v)
            for i, v in enumerate(options)
            if _is_id(v)
        ]
    return refs


async def check_question_images(
    db: AsyncSession, course_id: int, config: dict, stored_config: dict | None
) -> None:
    """Database layer of the reference rules (§5). Each new reference must be an image of
    this course that is current; an ID already anywhere in the stored question is an
    unchanged reference and passes. Caller holds the course lock; the read is FOR SHARE
    because the request's snapshot can predate that lock wait. 422 names the field."""
    unchanged = image_ids_in_config(stored_config)
    refs = [(f, i) for f, i in _labelled_refs(config) if i not in unchanged]
    if not refs:
        return
    rows = {
        row.id: row
        for row in (
            await db.execute(
                select(
                    Image.id, Image.course_id, Image.replaced_by_id, Image.deleted_at
                )
                .where(Image.id.in_({i for _f, i in refs}))
                .with_for_update(read=True)
            )
        ).all()
    }
    for field, image_id in refs:
        row = rows.get(image_id)
        if row is None or row.course_id != course_id:
            reason = "image not found in this course"
        elif row.deleted_at is not None:
            reason = "image deleted"
        elif row.replaced_by_id is not None:
            reason = "image is not current (it was replaced); pick the current version"
        else:
            continue
        raise HTTPException(status_code=422, detail=f"{field}: {reason}")


async def get_public(db: AsyncSession, public_key: str) -> Image:
    """The row and its bytes for the public view route; any version, even soft-deleted."""
    image = (
        await db.execute(
            select(Image)
            .options(undefer(Image.data))
            .where(Image.public_key == public_key)
        )
    ).scalar_one_or_none()
    if image is None:
        raise NotFoundError("Image not found")
    return image


# ---------------------------------------------------------------------------
# List, upload, replace, delete, prune (§6, §7). Callers hold the course lock for writes.
# ---------------------------------------------------------------------------


async def list_images(db: AsyncSession, course_id: int) -> list[dict]:
    images = (
        (
            await db.execute(
                select(Image)
                .where(
                    Image.course_id == course_id,
                    Image.replaced_by_id.is_(None),
                    Image.deleted_at.is_(None),
                )
                .order_by(Image.created_at.desc(), Image.id.desc())
            )
        )
        .scalars()
        .all()
    )
    rows = (
        await db.execute(
            select(Question.config, Game.archived_at)
            .join(Game, Game.id == Question.game_id)
            .where(Game.course_id == course_id)
        )
    ).all()
    counts: dict[int, list[int]] = {}
    for config, archived_at in rows:
        for image_id in image_ids_in_config(config):
            pair = counts.setdefault(image_id, [0, 0])
            pair[1 if archived_at is not None else 0] += 1
    return [image_item(img, *counts.get(img.id, [0, 0])) for img in images]


async def create_image(
    db: AsyncSession,
    course_id: int,
    user_id: str | None,
    loaded: LoadedImage,
    filename: object,
) -> Image:
    await _check_storage(db, course_id, add_count=1, add_bytes=len(loaded.data))
    image = Image(
        course_id=course_id,
        public_key=str(uuid.uuid4()),
        filename=clean_filename(filename),
        content_type=loaded.content_type,
        size_bytes=len(loaded.data),
        width=loaded.width,
        height=loaded.height,
        data=loaded.data,
        uploaded_by=user_id,
    )
    db.add(image)
    await db.flush()
    await db.refresh(image, ["created_at"])
    return image


async def replace_image(
    db: AsyncSession,
    course_id: int,
    image_id: int,
    user_id: str | None,
    loaded: LoadedImage,
    filename: object,
) -> Image:
    """§7 replace steps 3-8 (the caller did 1-2: validate the file, lock the course)."""
    refs = await question_refs(db, course_id, image_id)  # 3
    old = await lock_current_image(db, course_id, image_id)  # 4
    await _check_storage(
        db,
        course_id,
        add_count=0,
        add_bytes=len(loaded.data),
        remove_bytes=old.size_bytes,
    )
    live_game_ids = sorted({r.game_id for r in refs if not r.archived})
    if live_game_ids:  # 5
        open_titles = (
            (
                await db.execute(
                    select(Game.title)
                    .join(GameSession, GameSession.game_id == Game.id)
                    .where(
                        GameSession.game_id.in_(live_game_ids),
                        GameSession.status.in_(OPEN_ROOM_STATUSES),
                    )
                    .with_for_update(read=True, of=GameSession)
                )
            )
            .scalars()
            .all()
        )
        if open_titles:
            names = ", ".join(sorted(set(open_titles)))
            raise ConflictError(
                f"A room is open for: {names}. End it before replacing this image."
            )
    new = Image(  # 6
        course_id=course_id,
        public_key=str(uuid.uuid4()),
        filename=clean_filename(filename),
        content_type=loaded.content_type,
        size_bytes=len(loaded.data),
        width=loaded.width,
        height=loaded.height,
        data=loaded.data,
        uploaded_by=user_id,
    )
    db.add(new)
    await db.flush()
    old.replaced_by_id = new.id
    live_question_ids = sorted(r.question_id for r in refs if not r.archived)
    if live_question_ids:  # 7: X only on the rows rewritten
        questions = (
            (
                await db.execute(
                    select(Question)
                    .where(Question.id.in_(live_question_ids))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        for question in questions:
            question.config = replace_id_in_config(question.config, image_id, new.id)
    await db.flush()
    await prune(db, course_id, image_id)  # 8
    await db.refresh(new, ["created_at"])
    return new


async def delete_image(db: AsyncSession, course_id: int, image_id: int) -> None:
    """§7 delete steps 2-4 (the caller did 1: lock the course)."""
    refs = await question_refs(db, course_id, image_id)
    if refs:
        titles = ", ".join(sorted({r.game_title for r in refs}))
        message = f"This image is used by: {titles}."
        if any(r.archived for r in refs):
            message += (
                " For an archived quiz: restore the quiz, remove the image, then archive it"
                " again."
            )
        raise ConflictError(message)
    image = await lock_current_image(db, course_id, image_id)
    if await snapshot_uses(db, course_id, image_id):
        image.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.flush()
    else:
        await db.execute(delete(Image).where(Image.id == image_id))


async def prune(db: AsyncSession, course_id: int, image_id: int) -> bool:
    """Hard-delete a non-current image that nothing references any more (retention rule,
    §7). Returns whether it was deleted. Caller holds the course lock and has flushed the
    change that dropped the reference."""
    try:
        image = await lock_image(db, image_id)
    except NotFoundError:
        return False
    if is_current(image):
        return False
    if await question_refs(db, course_id, image_id):
        return False
    if await snapshot_uses(db, course_id, image_id):
        return False
    await db.execute(delete(Image).where(Image.id == image_id))
    return True


async def prune_removed(
    db: AsyncSession, course_id: int, old_config: object, new_config: object
) -> None:
    """Prune every image the old config referenced and the new one does not (question
    update or delete). prune itself skips current images."""
    for image_id in sorted(
        image_ids_in_config(old_config) - image_ids_in_config(new_config)
    ):
        await prune(db, course_id, image_id)
