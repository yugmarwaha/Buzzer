"""Quiz and question management shared by the host and admin routers (T4 design §6).

Routers do the access check (see access_service) — mutations must load the quiz with
`get_game_for_host(..., for_update=True)` first so the row lock serializes them against
room creation. The guards below live here so every route gets them.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import bleach
import structlog
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..common.exceptions import BuzzerError, ConflictError
from ..models.game import Game, Question
from ..models.image import Image
from ..models.session import GameSession, SessionScore
from ..schemas.admin import (
    NEW_TYPES,
    OPTION_IMAGES,
    PIN_IMAGE,
    PROMPT_IMAGE,
    GameCreate,
    GameUpdate,
    QuestionCreate,
    QuestionUpdate,
    has_image_field,
    normalize_image_fields,
    validate_image_refs,
)
from ..schemas.images import (
    MAX_BUNDLE_IMAGE_BYTES,
    MAX_BUNDLE_IMAGES,
    MAX_IMPORT_BYTES,
    MiB,
)
from . import access_service, image_service
from .access_service import get_question_in_game

logger = structlog.get_logger()

SUPPORTED_IMPORT_VERSION = 1
IMAGE_BUNDLE_VERSION = 2  # T8: version 1 plus embedded images (§9)
SUPPORTED_VERSIONS = (SUPPORTED_IMPORT_VERSION, IMAGE_BUNDLE_VERSION)
_PROMPT_TAGS = ["b", "i", "br", "u"]
ACTIVE_STATUSES = ("LOBBY", "IN_PROGRESS")


def clean_prompt(prompt: str) -> str:
    return bleach.clean(prompt, tags=_PROMPT_TAGS, attributes={}, strip=True)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


async def has_active_room(db: AsyncSession, game_id: int) -> bool:
    # Locking read (FOR SHARE): the request's REPEATABLE READ snapshot was taken before the
    # game-row lock was acquired, so a plain SELECT would miss a LOBBY room that
    # create_room committed while this request waited on that lock.
    result = await db.execute(
        select(GameSession.id)
        .where(GameSession.game_id == game_id, GameSession.status.in_(ACTIVE_STATUSES))
        .limit(1)
        .with_for_update(read=True)
    )
    return result.first() is not None


async def assert_no_active_room(db: AsyncSession, game_id: int) -> None:
    if await has_active_room(db, game_id):
        raise ConflictError(
            "A room using this quiz is open. Edits are locked until it ends."
        )


def assert_not_archived(game: Game) -> None:
    if game.archived_at is not None:
        raise ConflictError("This quiz is archived. Restore it to edit or run it.")


async def _assert_editable(db: AsyncSession, game: Game) -> None:
    assert_not_archived(game)
    await assert_no_active_room(db, game.id)


# ---------------------------------------------------------------------------
# Quiz summaries
# ---------------------------------------------------------------------------


async def game_summaries(db: AsyncSession, games: list[Game]) -> list[dict]:
    ids = [g.id for g in games]
    counts: dict[int, int] = {}
    active: set[int] = set()
    if ids:
        counts = dict(
            (
                await db.execute(
                    select(Question.game_id, func.count(Question.id))
                    .where(Question.game_id.in_(ids))
                    .group_by(Question.game_id)
                )
            ).all()
        )
        active = set(
            (
                await db.execute(
                    select(GameSession.game_id).where(
                        GameSession.game_id.in_(ids),
                        GameSession.status.in_(ACTIVE_STATUSES),
                    )
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": g.id,
            "course_id": g.course_id,
            "title": g.title,
            "description": g.description,
            "max_players": g.max_players,
            "question_count": counts.get(g.id, 0),
            "archived_at": g.archived_at,
            "has_active_room": g.id in active,
            "created_at": g.created_at,
        }
        for g in games
    ]


async def game_summary(db: AsyncSession, game: Game) -> dict:
    await db.refresh(game)
    return (await game_summaries(db, [game]))[0]


async def list_course_games(
    db: AsyncSession, course_id: int, *, archived: bool
) -> list[dict]:
    stmt = select(Game).where(Game.course_id == course_id)
    stmt = stmt.where(
        Game.archived_at.is_not(None) if archived else Game.archived_at.is_(None)
    )
    games = (await db.execute(stmt.order_by(Game.title))).scalars().all()
    return await game_summaries(db, list(games))


# ---------------------------------------------------------------------------
# Quiz mutations
# ---------------------------------------------------------------------------


async def create_game(db: AsyncSession, course_id: int, meta: GameCreate) -> Game:
    game = Game(
        course_id=course_id,
        title=meta.title,
        description=meta.description,
        max_players=meta.max_players,
    )
    db.add(game)
    await db.flush()
    await db.refresh(game)
    logger.info("game_created", game_id=game.id, course_id=course_id, title=meta.title)
    return game


async def update_game(db: AsyncSession, game: Game, body: GameUpdate) -> Game:
    await _assert_editable(db, game)
    if body.title is not None:
        game.title = body.title
    if body.description is not None:
        game.description = body.description
    if body.max_players is not None:
        game.max_players = body.max_players
    await db.flush()
    return game


async def archive_game(db: AsyncSession, game: Game) -> Game:
    if game.archived_at is not None:
        raise ConflictError("This quiz is already archived")
    await assert_no_active_room(db, game.id)
    game.archived_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    logger.info("game_archived", game_id=game.id)
    return game


async def restore_game(db: AsyncSession, game: Game) -> Game:
    if game.archived_at is None:
        raise ConflictError("This quiz is not archived")
    game.archived_at = None
    await db.flush()
    logger.info("game_restored", game_id=game.id)
    return game


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


async def answered_question_ids(
    db: AsyncSession, question_ids: list[int], *, lock: bool = False
) -> set[int]:
    if not question_ids:
        return set()
    stmt = (
        select(SessionScore.question_id)
        .where(SessionScore.question_id.in_(question_ids))
        .distinct()
    )
    if lock:
        stmt = stmt.with_for_update(read=True)
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def _locked_questions(db: AsyncSession, game_id: int) -> list[Question]:
    """All of a quiz's questions, read with FOR UPDATE. Mutations run after the game-row
    lock, but their REPEATABLE READ snapshot can predate it, so a plain SELECT would miss
    questions another request committed while this one waited. populate_existing makes
    already-loaded objects take the fresh row values too."""
    result = await db.execute(
        select(Question)
        .where(Question.game_id == game_id)
        .order_by(Question.order_index, Question.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


def question_item(q: Question, has_answers: bool, images: dict[str, dict]) -> dict:
    """`images` is image_service.image_info for (at least) this question's config."""
    return {
        "id": q.id,
        "game_id": q.game_id,
        "type": q.type,
        "grading_type": q.grading_type,
        "prompt": q.prompt,
        "config": q.config,
        "answer_data": q.answer_data,
        "time_limit_seconds": q.time_limit_seconds,
        "points_value": q.points_value,
        "order_index": q.order_index,
        "created_at": q.created_at,
        "has_answers": has_answers,
        "images": image_service.images_for(q.config, images),
    }


async def list_questions(db: AsyncSession, game: Game) -> list[dict]:
    questions = (
        (
            await db.execute(
                select(Question)
                .where(Question.game_id == game.id)
                .order_by(Question.order_index)
            )
        )
        .scalars()
        .all()
    )
    answered = await answered_question_ids(db, [q.id for q in questions])
    images = await image_service.image_info(db, [q.config for q in questions])
    return [question_item(q, q.id in answered, images) for q in questions]


async def create_question(db: AsyncSession, game: Game, body: QuestionCreate) -> dict:
    await _assert_editable(db, game)
    if body.order_index is None:
        # Locking read, so a question another request added while this one waited on
        # the game-row lock is counted (the plain snapshot would miss it).
        existing = await _locked_questions(db, game.id)
        max_idx = max((q.order_index for q in existing), default=None)
        order_index = (max_idx + 1) if max_idx is not None else 0
    else:
        order_index = body.order_index
    # T8 §5 database layer; course lock held (get_game_for_host), questions before images.
    await image_service.check_question_images(db, game.course_id, body.config, None)

    question = Question(
        game_id=game.id,
        type=body.type,
        grading_type=body.grading_type,
        prompt=clean_prompt(body.prompt),
        config=body.config,
        answer_data=body.answer_data,
        time_limit_seconds=body.time_limit_seconds,
        points_value=body.points_value,
        order_index=order_index,
    )
    db.add(question)
    await db.flush()
    await db.refresh(question)
    return question_item(
        question, False, await image_service.image_info(db, [question.config])
    )


_MERGED_FIELDS = (
    "type",
    "grading_type",
    "prompt",
    "config",
    "answer_data",
    "time_limit_seconds",
    "points_value",
)


def _merged(question: Question, body: QuestionUpdate) -> dict:
    """The stored question with the body's non-null fields laid over it."""
    return {
        field: getattr(body, field)
        if getattr(body, field) is not None
        else getattr(question, field)
        for field in _MERGED_FIELDS
    }


def _validate_merged_update(merged: dict) -> None:
    """An update body can be partial, so QuestionUpdate alone can't check per-type structure.
    Validate the merged question exactly like a create. Only called for the T7 types
    (design §5.2), so the four original types keep their pre-T7 update behaviour."""
    try:
        QuestionCreate(**merged)
    except ValidationError as exc:
        err = exc.errors()[0]
        msg = err["msg"].removeprefix("Value error, ")
        loc = ".".join(str(part) for part in err["loc"])
        detail = f"{loc}: {msg}" if loc else msg
        raise HTTPException(
            status_code=422, detail=f"Question invalid: {detail}"
        ) from exc


def _validate_merged_images(merged: dict) -> None:
    """T8 §5: for the four original types only the image shape rules run on update."""
    try:
        validate_image_refs(merged["type"], merged["config"])
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Question invalid: config: {exc}"
        ) from exc


async def update_question(
    db: AsyncSession, game: Game, question_id: int, body: QuestionUpdate
) -> dict:
    question = await get_question_in_game(db, game, question_id, for_update=True)
    await _assert_editable(db, game)
    merged = _merged(question, body)
    stored_config = question.config
    if question.type in NEW_TYPES or body.type in NEW_TYPES:
        _validate_merged_update(merged)
    images_involved = has_image_field(stored_config) or has_image_field(
        merged["config"]
    )
    if images_involved:
        if not (question.type in NEW_TYPES or body.type in NEW_TYPES):
            _validate_merged_images(merged)
        await image_service.check_question_images(
            db, game.course_id, merged["config"], stored_config
        )
    if body.type is not None:
        question.type = body.type
    if body.grading_type is not None:
        question.grading_type = body.grading_type
    if body.prompt is not None:
        question.prompt = clean_prompt(body.prompt)
    if body.config is not None:
        question.config = body.config
    if body.answer_data is not None:
        question.answer_data = body.answer_data
    if body.time_limit_seconds is not None:
        question.time_limit_seconds = body.time_limit_seconds
    if body.points_value is not None:
        question.points_value = body.points_value
    if body.order_index is not None:
        question.order_index = body.order_index
    await db.flush()
    if images_involved:
        await image_service.prune_removed(
            db, game.course_id, stored_config, question.config
        )
    await db.refresh(question)
    answered = await answered_question_ids(db, [question.id], lock=True)
    return question_item(
        question,
        question.id in answered,
        await image_service.image_info(db, [question.config]),
    )


async def delete_question(db: AsyncSession, game: Game, question_id: int) -> None:
    question = await get_question_in_game(db, game, question_id, for_update=True)
    await _assert_editable(db, game)
    if await answered_question_ids(db, [question.id], lock=True):
        raise ConflictError("Answered in a past session, can't delete")
    stored_config = question.config
    await db.delete(question)
    await db.flush()
    await image_service.prune_removed(db, game.course_id, stored_config, None)
    # Keep order_index contiguous (0..n-1)
    remaining = await _locked_questions(db, game.id)
    for idx, q in enumerate(remaining):
        q.order_index = idx
    await db.flush()


async def reorder_questions(db: AsyncSession, game: Game, order: list[int]) -> None:
    await _assert_editable(db, game)
    questions = {q.id: q for q in await _locked_questions(db, game.id)}
    if len(order) != len(set(order)) or set(order) != set(questions.keys()):
        raise ConflictError(
            "order list must contain exactly the IDs of all questions in this game"
        )
    for idx, qid in enumerate(order):
        questions[qid].order_index = idx
    await db.flush()


# ---------------------------------------------------------------------------
# JSON export / import. Version 1 is the pre-T8 format; version 2 adds an `images` list
# and puts image keys in place of IDs (docs/plans/t8-images.md §9).
# ---------------------------------------------------------------------------

_QUESTION_FIELDS = (
    "type",
    "grading_type",
    "prompt",
    "config",
    "answer_data",
    "time_limit_seconds",
    "points_value",
)
_IMAGE_FIELDS = (PROMPT_IMAGE, OPTION_IMAGES, PIN_IMAGE)


def _too_large(message: str) -> BuzzerError:
    return BuzzerError("PAYLOAD_TOO_LARGE", message, 413)


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    msg = err["msg"].removeprefix("Value error, ")
    loc = ".".join(str(part) for part in err["loc"])
    return f"{loc}: {msg}" if loc else msg


def _refs_in_order(config: dict) -> list[tuple[str, int]]:
    """(field, image id) in first-use order: prompt, options, pin canvas."""
    refs: list[tuple[str, int]] = []
    if PROMPT_IMAGE in config:
        refs.append((PROMPT_IMAGE, config[PROMPT_IMAGE]))
    for i, v in enumerate(config.get(OPTION_IMAGES) or []):
        if v is not None:
            refs.append((f"{OPTION_IMAGES}[{i}]", v))
    if PIN_IMAGE in config:
        refs.append((PIN_IMAGE, config[PIN_IMAGE]))
    return refs


def _swap_image_values(config: dict, mapping: dict) -> dict:
    """A copy of config with every image value v replaced by mapping[v]."""
    config = dict(config)
    for field in (PROMPT_IMAGE, PIN_IMAGE):
        if field in config:
            config[field] = mapping[config[field]]
    if OPTION_IMAGES in config:
        config[OPTION_IMAGES] = [
            None if v is None else mapping[v] for v in config[OPTION_IMAGES]
        ]
    return config


async def export_bundle(db: AsyncSession, game: Game) -> tuple[str, bytes]:
    questions = (
        (
            await db.execute(
                select(Question)
                .where(Question.game_id == game.id)
                .order_by(Question.order_index)
            )
        )
        .scalars()
        .all()
    )
    # 1. Validate with the import rules, so every export can be imported. Replaced and
    # deleted versions are fine as long as the row (and its bytes) still exists.
    rows: dict[int, Image] = {}
    all_ids = image_service.ids_in_configs(q.config for q in questions)
    if all_ids:
        found = await db.execute(
            select(Image).where(
                Image.id.in_(all_ids), Image.course_id == game.course_id
            )
        )
        rows = {img.id: img for img in found.scalars().all()}
    problems: list[str] = []
    for n, q in enumerate(questions, start=1):
        try:
            QuestionCreate(**{f: getattr(q, f) for f in _QUESTION_FIELDS})
        except ValidationError as exc:
            problems.append(f"Question {n} ({q.type}): {_first_error(exc)}")
            continue
        missing = [f for f, i in _refs_in_order(q.config) if i not in rows]
        if missing:
            problems.append(f"Question {n} ({q.type}): {missing[0]}: image is missing")
    if problems:
        raise HTTPException(
            status_code=422, detail="Can't export: " + "; ".join(problems)
        )

    # 2-3. Version 1 without images; version 2 with keys in first-use order.
    keys: dict[int, str] = {}
    for q in questions:
        for _field, image_id in _refs_in_order(q.config):
            keys.setdefault(image_id, f"img-{len(keys) + 1}")
    if len(keys) > MAX_BUNDLE_IMAGES:
        raise _too_large(
            f"This quiz uses {len(keys)} images; an export holds at most "
            f"{MAX_BUNDLE_IMAGES}"
        )
    if sum(rows[i].size_bytes for i in keys) > MAX_BUNDLE_IMAGE_BYTES:
        raise _too_large(
            f"This quiz's images are larger than {MAX_BUNDLE_IMAGE_BYTES // MiB} MiB "
            "together; an export can't hold them"
        )
    bundle: dict = {
        "format": "buzzer/game",
        "version": IMAGE_BUNDLE_VERSION if keys else SUPPORTED_IMPORT_VERSION,
        "game": {
            "title": game.title,
            "description": game.description,
            "max_players": game.max_players,
        },
        "questions": [
            {
                "type": q.type,
                "grading_type": q.grading_type,
                "prompt": q.prompt,
                "config": _swap_image_values(q.config, keys) if keys else q.config,
                "answer_data": q.answer_data,
                "time_limit_seconds": q.time_limit_seconds,
                "points_value": q.points_value,
            }
            for q in questions
        ],
    }
    if keys:
        data = dict(
            (
                await db.execute(
                    select(Image.id, Image.data).where(Image.id.in_(list(keys)))
                )
            ).all()
        )
        bundle["images"] = [
            {
                "key": key,
                "filename": rows[image_id].filename,
                "content_type": rows[image_id].content_type,
                "data": base64.b64encode(data[image_id]).decode("ascii"),
            }
            for image_id, key in keys.items()
        ]
    # 4. Check the whole file against the import cap, so every export can be imported.
    content = json.dumps(bundle, indent=2, ensure_ascii=False).encode()
    if len(content) > MAX_IMPORT_BYTES:
        raise _too_large(f"Export is larger than {MAX_IMPORT_BYTES // MiB} MiB")
    # ASCII only: the name goes into a Content-Disposition header, which must be latin-1.
    safe_title = "".join(
        c if (c.isascii() and c.isalnum()) or c in " _-" else "_" for c in game.title
    )
    return f"{safe_title}.json", content


def _bundle_images(bundle: dict) -> dict[str, tuple[object, bytes]]:
    """Version 2 `images`: {key: (filename, raw bytes)}. Shape and caps only; decoding
    happens later. The declared content_type is ignored: the decoder decides (§9)."""
    entries = bundle.get("images")
    if not isinstance(entries, list):
        raise HTTPException(status_code=422, detail="'images' must be a list")
    if len(entries) > MAX_BUNDLE_IMAGES:
        raise _too_large(
            f"File has {len(entries)} images; the limit is {MAX_BUNDLE_IMAGES}"
        )
    images: dict[str, tuple[object, bytes]] = {}
    for n, entry in enumerate(entries, start=1):
        key = entry.get("key") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=422, detail=f"Image {n}: 'key' must be a non-empty string"
            )
        if key in images:
            raise HTTPException(status_code=422, detail=f"{key}: duplicate key")
        data = entry.get("data")
        try:
            if not isinstance(data, str):
                raise ValueError
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"{key}: 'data' is not valid base64"
            ) from None
        images[key] = (entry.get("filename"), raw)
    if sum(len(raw) for _name, raw in images.values()) > MAX_BUNDLE_IMAGE_BYTES:
        raise _too_large(
            f"File images are larger than {MAX_BUNDLE_IMAGE_BYTES // MiB} MiB together"
        )
    return images


def _question_image_keys(n: int, config: dict, images: dict) -> set[str]:
    """Version 2: every image field holds keys of `images` (§5 shapes, strings for ints).
    Returns the keys the question uses."""
    used: set[str] = set()

    def check(field: str, value: object) -> None:
        if not isinstance(value, str) or not value:
            raise HTTPException(
                status_code=422, detail=f"Question {n}: {field} must be an image key"
            )
        if value not in images:
            raise HTTPException(
                status_code=422,
                detail=f"Question {n}: {field}: unknown image key {value!r}",
            )
        used.add(value)

    for field in (PROMPT_IMAGE, PIN_IMAGE):
        if field in config:
            check(field, config[field])
    if OPTION_IMAGES in config:
        options = config[OPTION_IMAGES]
        if not isinstance(options, list):
            raise HTTPException(
                status_code=422, detail=f"Question {n}: {OPTION_IMAGES} must be a list"
            )
        for i, value in enumerate(options):
            if value is not None:
                check(f"{OPTION_IMAGES}[{i}]", value)
    return used


def _parse_question(n: int, q: object) -> QuestionCreate:
    try:
        return QuestionCreate(**q)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Question {n} invalid: {exc}"
        ) from exc


async def import_bundle(
    db: AsyncSession, course_id: int, raw: bytes, user_id: str | None = None
) -> Game:
    """Always creates a *new* quiz in `course_id`. Everything that needs no database is
    checked before the course lock; the writes then run in the request transaction (flush
    only), so any failure rolls the whole import back (§9)."""
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != "buzzer/game":
        raise HTTPException(status_code=422, detail="Unrecognised file format")
    version = bundle.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported version {version!r}; server supports versions 1 and 2",
        )

    try:
        game_meta = GameCreate(**bundle.get("game", {}))
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid game metadata: {exc}"
        ) from exc

    questions = bundle.get("questions", [])
    if not isinstance(questions, list):
        raise HTTPException(status_code=422, detail="'questions' must be a list")

    images: dict[str, tuple[object, bytes]] = {}
    used: set[str] = set()
    drafts: list[dict] = []
    if version == SUPPORTED_IMPORT_VERSION:
        for i, q in enumerate(questions):
            if has_image_field(_parse_question(i + 1, q).config):
                # A raw database ID from another server must not be trusted (§9).
                raise HTTPException(
                    status_code=422,
                    detail=f"Question {i + 1}: image fields need a version 2 file",
                )
            drafts.append(q)
    else:
        images = _bundle_images(bundle)
        for i, q in enumerate(questions):
            config = q.get("config", {}) if isinstance(q, dict) else None
            if not isinstance(config, dict):
                raise HTTPException(
                    status_code=422, detail=f"Question {i + 1} invalid: bad shape"
                )
            config = normalize_image_fields(config)
            used |= _question_image_keys(i + 1, config, images)
            # The rest of the question, with the image fields set aside.
            plain = {k: v for k, v in config.items() if k not in _IMAGE_FIELDS}
            _parse_question(i + 1, {**q, "config": plain})
            drafts.append({**q, "config": config})

    # Decode every entry (slow; no lock held). Inside an import every image failure is 422.
    loaded: dict[str, image_service.LoadedImage] = {}
    for key, (_name, data) in images.items():
        try:
            loaded[key] = await run_in_threadpool(image_service.load_image, data)
        except BuzzerError as exc:
            raise HTTPException(
                status_code=422, detail=f"{key}: {exc.message}"
            ) from None

    # T8 §8: course lock first. One row per referenced key; unreferenced keys are ignored.
    await access_service.lock_course(db, course_id)
    key_ids: dict[str, int] = {}
    for key in images:
        if key in used:
            image = await image_service.create_image(
                db, course_id, user_id, loaded[key], images[key][0]
            )
            key_ids[key] = image.id

    validated: list[QuestionCreate] = []
    for i, q in enumerate(drafts):
        if key_ids:
            q = {**q, "config": _swap_image_values(q["config"], key_ids)}
        question = _parse_question(i + 1, q)
        await image_service.check_question_images(db, course_id, question.config, None)
        validated.append(question)

    game = await create_game(db, course_id, game_meta)
    for idx, q in enumerate(validated):
        db.add(
            Question(
                game_id=game.id,
                type=q.type,
                grading_type=q.grading_type,
                prompt=clean_prompt(q.prompt),
                config=q.config,
                answer_data=q.answer_data,
                time_limit_seconds=q.time_limit_seconds,
                points_value=q.points_value,
                order_index=idx,
            )
        )
    await db.flush()
    logger.info(
        "game_imported",
        game_id=game.id,
        course_id=course_id,
        question_count=len(validated),
        image_count=len(key_ids),
    )
    return game
