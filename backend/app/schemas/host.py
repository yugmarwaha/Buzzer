"""Response shapes for the course-first host API (T4 design §6 Shapes)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class GameSummary(BaseModel):
    id: int
    course_id: int
    title: str
    description: str
    max_players: int
    question_count: int
    archived_at: datetime | None
    has_active_room: bool
    created_at: datetime


class QuestionItem(BaseModel):
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
    has_answers: bool
    # T8: {"<image id>": {"url": ..., "current": bool}} for this question's references;
    # current=False marks an older version kept by an archived/restored quiz.
    images: dict[str, dict] = {}


class ActiveRoom(BaseModel):
    session_id: str
    room_code: str
    status: str
    game_id: int
    game_title: str
    host_user_id: str | None
    host_display_name: str | None
    created_at: datetime
    live: bool
    player_count: int | None
    has_answers: bool
    # Distinct players with at least one saved answer (MySQL session_scores).
    answered_count: int
    can_end: bool


class SessionItem(BaseModel):
    session_id: str
    room_code: str
    status: str
    game_id: int
    game_title: str
    host_user_id: str | None
    host_display_name: str | None
    created_at: datetime
    completed_at: datetime | None
    player_count: int
    can_merge_guests: bool


class EndRoomResult(BaseModel):
    outcome: str  # "abandoned" | "removed"


class ImportResult(BaseModel):
    game_id: int
