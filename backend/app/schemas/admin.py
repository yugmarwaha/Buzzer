from __future__ import annotations

import math
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------


class CourseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=255)
    semester: str = Field(..., min_length=1, max_length=50)


class CourseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=255)
    semester: str | None = Field(None, min_length=1, max_length=50)


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    semester: str
    created_at: datetime


class CourseHost(BaseModel):
    user_id: str
    display_name: str


class AdminCourse(CourseResponse):
    hosts: list[CourseHost]
    active_quiz_count: int
    archived_quiz_count: int


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


class RosterEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int
    netid: str
    full_name: str
    email: str
    is_active: bool
    imported_at: datetime


class RosterUploadResult(BaseModel):
    imported: int
    updated: int
    deactivated: int
    errors: list[str]


class RosterEntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool | None = None
    netid: str | None = Field(None, min_length=1, max_length=100)
    full_name: str | None = Field(None, min_length=1, max_length=255)
    email: EmailStr | None = None


class RosterRowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    netid: str = Field(..., min_length=1, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=1, max_length=255)


class RosterImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[RosterRowIn] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------


class GameCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=5000)
    max_players: int = Field(150, ge=1, le=500)


class AdminGameCreate(GameCreate):
    """Admin compatibility route: the course comes from the body (host routes use the URL)."""

    course_id: int = Field(..., ge=1)


class GameUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    max_players: int | None = Field(None, ge=1, le=500)


class GameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int
    title: str
    description: str
    max_players: int
    archived_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

# T7 question types. Lives here (not game_service) so quiz_service can import it without a
# circular import: game_service already imports quiz_service.
NEW_TYPES = ("pin_drop", "closest_guess")

_TYPE_PATTERN = "^(multiple_choice|true_false|fill_in_the_blank|multi_select|pin_drop|closest_guess)$"

# pin_drop: fixed integer grid. closest_guess: magnitude limit for every number.
GRID_MIN, GRID_MAX = -5, 5
GUESS_LIMIT = 1e12
UNIT_MAX_LEN = 20


def is_grid_int(v: object) -> bool:
    """A JSON integer in [-5, 5]. bool is excluded (bool is an int subclass); float too."""
    return isinstance(v, int) and not isinstance(v, bool) and GRID_MIN <= v <= GRID_MAX


def is_guess_number(v: object) -> bool:
    """A finite int/float (not bool) with |v| <= 1e12. Python's json accepts NaN/Infinity."""
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and abs(v) <= GUESS_LIMIT
    )


# T8 image references in config (docs/plans/t8-images.md §5). Values are image IDs.
PROMPT_IMAGE = "promptImageId"
OPTION_IMAGES = "optionImageIds"
PIN_IMAGE = "imageId"
_OPTION_TYPES = ("multiple_choice", "multi_select")


def is_image_id(v: object) -> bool:
    """A JSON integer >= 1; bool and float are not IDs (same test as is_grid_int)."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def normalize_image_fields(config: dict) -> dict:
    """Drop null image fields, and an optionImageIds that is all null, so a stored config
    has an image field only if it references an image (§5, export version test §9)."""
    config = dict(config)
    for field in (PROMPT_IMAGE, PIN_IMAGE):
        if field in config and config[field] is None:
            del config[field]
    opts = config.get(OPTION_IMAGES)
    if isinstance(opts, list) and all(v is None for v in opts):
        del config[OPTION_IMAGES]
    return config


def has_image_field(config: object) -> bool:
    return isinstance(config, dict) and any(
        f in config for f in (PROMPT_IMAGE, OPTION_IMAGES, PIN_IMAGE)
    )


def option_label(index: int) -> str:
    return f"Option {chr(ord('A') + index)}" if index < 26 else f"Option {index + 1}"


def validate_image_refs(q_type: str, config: dict) -> None:
    """Shape of the image fields: allowed on this type, valid IDs, optionImageIds aligned
    with options, and every option has text or an image."""
    if PROMPT_IMAGE in config and not is_image_id(config[PROMPT_IMAGE]):
        raise ValueError(f"{PROMPT_IMAGE} must be a positive integer")
    if PIN_IMAGE in config:
        if q_type != "pin_drop":
            raise ValueError(f"{PIN_IMAGE} is only allowed on pin_drop")
        if not is_image_id(config[PIN_IMAGE]):
            raise ValueError(f"{PIN_IMAGE} must be a positive integer")
    if OPTION_IMAGES not in config:
        return
    if q_type not in _OPTION_TYPES:
        raise ValueError(
            f"{OPTION_IMAGES} is only allowed on multiple_choice and multi_select"
        )
    ids = config[OPTION_IMAGES]
    opts = config.get("options")
    if not isinstance(ids, list) or not isinstance(opts, list) or len(ids) != len(opts):
        raise ValueError(f"{OPTION_IMAGES} must be a list matching options length")
    for i, (text, image_id) in enumerate(zip(opts, ids, strict=True)):
        if image_id is not None and not is_image_id(image_id):
            raise ValueError(
                f"{OPTION_IMAGES} entries must be positive integers or null"
            )
        if image_id is None and not (isinstance(text, str) and text.strip()):
            raise ValueError(f"{option_label(i)} needs text or an image")


def _validate_pin_drop(q: QuestionCreate) -> None:
    if not set(q.config) <= {PIN_IMAGE, PROMPT_IMAGE}:
        raise ValueError(
            "pin_drop config may only contain 'imageId' and 'promptImageId'"
        )
    if q.grading_type == "COMPLETENESS":
        if q.answer_data != {}:
            raise ValueError("COMPLETENESS pin_drop answer_data must be empty")
        return
    if set(q.answer_data) != {"target"}:
        raise ValueError("ACCURACY pin_drop answer_data must have exactly 'target'")
    target = q.answer_data["target"]
    if not isinstance(target, dict) or set(target) != {"x", "y"}:
        raise ValueError("pin_drop target must have exactly 'x' and 'y'")
    for axis in ("x", "y"):
        if not is_grid_int(target[axis]):
            raise ValueError(
                f"pin_drop target.{axis} must be an integer from {GRID_MIN} to {GRID_MAX}"
            )
    if q.points_value <= 0:
        raise ValueError("ACCURACY pin_drop points_value must be greater than 0")


def _validate_closest_guess(q: QuestionCreate) -> None:
    if not set(q.config) <= {"unit", PROMPT_IMAGE}:
        raise ValueError(
            "closest_guess config may only contain 'unit' and 'promptImageId'"
        )
    if "unit" in q.config:
        unit = q.config["unit"]
        if (
            not isinstance(unit, str)
            or not 1 <= len(unit) <= UNIT_MAX_LEN
            or unit != unit.strip()
        ):
            raise ValueError(
                f"closest_guess unit must be 1-{UNIT_MAX_LEN} characters "
                "with no leading or trailing spaces"
            )
    if q.grading_type == "COMPLETENESS":
        if q.answer_data != {}:
            raise ValueError("COMPLETENESS closest_guess answer_data must be empty")
        return
    keys = {"correct", "fullTolerance", "partialTolerance"}
    if set(q.answer_data) != keys:
        raise ValueError(
            "ACCURACY closest_guess answer_data must have exactly "
            "'correct', 'fullTolerance' and 'partialTolerance'"
        )
    for key in sorted(keys):
        if not is_guess_number(q.answer_data[key]):
            raise ValueError(
                f"closest_guess {key} must be a finite number no larger than 1e12"
            )
    full, partial = q.answer_data["fullTolerance"], q.answer_data["partialTolerance"]
    if full < 0:
        raise ValueError("closest_guess fullTolerance must be 0 or more")
    if partial < full:
        raise ValueError(
            "closest_guess partialTolerance must be at least fullTolerance"
        )
    if q.points_value <= 0:
        raise ValueError("ACCURACY closest_guess points_value must be greater than 0")


class QuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(..., pattern=_TYPE_PATTERN)
    grading_type: str = Field(..., pattern="^(ACCURACY|COMPLETENESS)$")
    prompt: str = Field(..., min_length=1, max_length=2000)
    config: dict = Field(default_factory=dict)
    answer_data: dict = Field(default_factory=dict)
    time_limit_seconds: int = Field(30, ge=2, le=300)
    points_value: float = Field(1.0, ge=0, le=100000)
    order_index: int | None = Field(None, ge=0)

    @field_validator("config")
    @classmethod
    def normalize_config(cls, v: dict) -> dict:
        return normalize_image_fields(v)

    @model_validator(mode="after")
    def validate_structure(self) -> QuestionCreate:
        validate_image_refs(self.type, self.config)
        if self.type == "multiple_choice":
            opts = self.config.get("options")
            if not isinstance(opts, list) or len(opts) < 2:
                raise ValueError(
                    "multiple_choice config must have 'options' list with at least 2 items"
                )
            if self.grading_type == "ACCURACY":
                pts = self.answer_data.get("answer_points")
                if not isinstance(pts, list) or len(pts) != len(opts):
                    raise ValueError(
                        "ACCURACY multiple_choice answer_data must have 'answer_points' list matching options length"
                    )
                if any(not isinstance(p, (int, float)) or p < 0 for p in pts):
                    raise ValueError(
                        "answer_points values must be non-negative numbers"
                    )
        if self.type == "true_false" and self.grading_type == "ACCURACY":
            pts = self.answer_data.get("answer_points")
            if not isinstance(pts, dict) or set(pts.keys()) != {"true", "false"}:
                raise ValueError(
                    "ACCURACY true_false answer_data must have 'answer_points' with 'true' and 'false' keys"
                )
        if self.type == "fill_in_the_blank" and self.grading_type == "ACCURACY":
            answers = self.answer_data.get("acceptedAnswers")
            if not isinstance(answers, list) or len(answers) == 0:
                raise ValueError(
                    "ACCURACY fill_in_the_blank answer_data must have 'acceptedAnswers' list with at least one item"
                )
            if any(not isinstance(a, str) or not a.strip() for a in answers):
                raise ValueError("acceptedAnswers entries must be non-empty strings")
            pts = self.answer_data.get("answerPoints")
            if not isinstance(pts, list) or len(pts) != len(answers):
                raise ValueError(
                    "ACCURACY fill_in_the_blank answer_data must have 'answerPoints' list matching acceptedAnswers length"
                )
            if any(not isinstance(p, (int, float)) or p < 0 for p in pts):
                raise ValueError("answerPoints values must be non-negative numbers")
            edit_dist = self.answer_data.get("editDistance", 0)
            if not isinstance(edit_dist, int) or edit_dist < 0:
                raise ValueError("editDistance must be a non-negative integer")
        if self.type == "multi_select":
            opts = self.config.get("options")
            if not isinstance(opts, list) or len(opts) < 2:
                raise ValueError(
                    "multi_select config must have 'options' list with at least 2 items"
                )
            if self.grading_type == "ACCURACY":
                pts = self.answer_data.get("answer_points")
                if not isinstance(pts, list) or len(pts) != len(opts):
                    raise ValueError(
                        "ACCURACY multi_select answer_data must have 'answer_points' list matching options length"
                    )
                if any(not isinstance(p, (int, float)) for p in pts):
                    raise ValueError("answer_points values must be numbers")
        if self.type == "pin_drop":
            _validate_pin_drop(self)
        if self.type == "closest_guess":
            _validate_closest_guess(self)
        return self


class QuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str | None = Field(None, pattern=_TYPE_PATTERN)
    grading_type: str | None = Field(None, pattern="^(ACCURACY|COMPLETENESS)$")
    prompt: str | None = Field(None, min_length=1, max_length=2000)
    config: dict | None = None
    answer_data: dict | None = None
    time_limit_seconds: int | None = Field(None, ge=2, le=300)
    points_value: float | None = Field(None, ge=0, le=100000)
    order_index: int | None = Field(None, ge=0)

    @field_validator("config")
    @classmethod
    def normalize_config(cls, v: dict | None) -> dict | None:
        return normalize_image_fields(v) if v is not None else None


class QuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    game_id: int
    type: str
    grading_type: str
    prompt: str
    config: dict
    answer_data: dict
    time_limit_seconds: int
    points_value: float
    order_index: int
    created_at: datetime


class QuestionReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[int] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Users (local accounts)
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=72)
    email: EmailStr | None = None

    @field_validator("username")
    @classmethod
    def lower_username(cls, v: str) -> str:
        return v.lower()


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(
        None, min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    display_name: str | None = Field(None, min_length=1, max_length=255)
    email: EmailStr | None = None
    password: str | None = Field(None, min_length=8, max_length=72)
    role: str | None = Field(None, pattern="^(USER|ADMIN)$")

    @field_validator("username")
    @classmethod
    def lower_username(cls, v: str | None) -> str | None:
        return v.lower() if v is not None else None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str | None
    netid: str | None
    display_name: str | None
    email: str | None
    role: str
    created_at: datetime
    last_login: datetime | None


# ---------------------------------------------------------------------------
# Access management
# ---------------------------------------------------------------------------


class CourseAccessGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: int
    role: str = Field(..., pattern="^(HOST|PLAYER)$")


class CourseAccessSummary(BaseModel):
    """Read-only: what a course grant implies. Quiz access comes from course access."""

    course_id: int
    name: str
    semester: str
    role: str
    active_quiz_count: int
    archived_quiz_count: int


class UserWithAccessResponse(UserResponse):
    course_access: list[dict]
    course_summary: list[CourseAccessSummary]
