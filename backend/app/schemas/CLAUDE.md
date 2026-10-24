# schemas

## Purpose
Pydantic request/response models for the API layer: validates incoming payloads and shapes outgoing JSON.

## Contents
- `__init__.py` — empty.
- `admin.py` — admin/host CRUD schemas: courses, roster upload/patch, games, questions (with per-question-type structural validation), local user accounts, course access grants (`CourseAccessGrant`/`CourseAccessSummary`).
- `auth.py` — `LoginRequest` (username+password or netid), `TokenResponse`, `TempTokenResponse`, `RefreshResponse`, `GuestJoinRequest`.
- `game.py` — player/host gameplay schemas: `QuestionPublic` (client-safe question view), room create (`course_id` optional, must match the quiz)/info, `GuestMergeRequest` (host merge body), active session list, host's course list, internal `ScoreResult`.
- `host.py` — `/host/*` shapes: `GameSummary`, `QuestionItem` (+`has_answers`, and T8 `images`: `{id: {url, current}}` for the question's references, `current: false` = an older version), `ActiveRoom` (+`has_answers`, `answered_count`), `SessionItem`, `EndRoomResult`.
- `player.py` — Past Quizzes shapes; an unplayed question is exactly `{order_index, played: false}`. `PastQuizDetail.image_urls` (T8) maps the *played* questions' image ids to URLs.
- `images.py` — T8 limits in one place (`MAX_IMAGE_BYTES` 5 MiB, `MAX_IMAGE_SIDE` 4096, `MAX_IMPORT_BYTES` 30 MiB, bundle and per-course caps, `MAX_REQUEST_BYTES` 32 MiB), `CONTENT_TYPES` (decoder format → stored type; `MPO` phone photos count as JPEG), `image_url(public_key)`, and `ImageItem`.

## How it fits in
Sits between routers and `models/`/`services/`: routers validate request bodies against these and serialize responses through them. Several `*Response` schemas mirror `models/` fields directly via `from_attributes=True` (e.g. `CourseResponse`↔`Course`, `GameResponse`↔`Game`, `QuestionResponse`↔`Question`), so a model field change needs a matching schema update. Player-facing question payloads are *not* built from schemas: `websocket/gateway.py`'s `_question_payload` builds them by hand (no `answer_data`, but it does send `gradingType` and the full `config`).

## Gotchas
- `QuestionCreate.validate_structure` (admin.py) hand-validates `config`/`answer_data` shape per question type (the original four, plus T7's `pin_drop`/`closest_guess` via `_validate_pin_drop`/`_validate_closest_guess`). `QuestionUpdate` has no structure validator (a partial body can't say which rules apply); for the T7 types `quiz_service.update_question` merges the update onto the stored question and re-runs `QuestionCreate`, but the original four types are still unchecked on update (design `docs/plans/t7-new-question-types.md` §5.2, gap C1).
- T7 shared definitions live here so both services can import them without a cycle (`game_service` imports `quiz_service`): `NEW_TYPES`, `is_grid_int` (int in −5..5, `bool`/`float` rejected), `is_guess_number` (finite, |v| ≤ 1e12, not `bool`). `game_service.validate_answer` reuses them for player answers.
- T8 image references (`docs/plans/t8-images.md` §5) are checked here for **shape** only: `QuestionCreate` normalizes `config` first (`normalize_image_fields` drops a null `promptImageId`/`imageId` and an all-null `optionImageIds`), then `validate_image_refs` checks allowed fields per type, positive-int ids (`bool`/`float` rejected, `is_image_id`), `optionImageIds` aligned with `options`, and "each option has text or an image" once option images exist (`option_label` gives "Option A"). Pin Drop config may hold only `imageId`/`promptImageId`; Closest Guess only `unit`/`promptImageId`. Whether an id exists, is in the course and is current is the database layer (`image_service.check_question_images`).
- `LoginRequest.netid` is commented "dev-only fallback" — netid-only login bypasses username+password.
- `UserWithAccessResponse.course_access` is an untyped `list[dict]` (not a typed sub-schema); `course_summary` is typed (`CourseAccessSummary`). There is no `game_access` any more.
- `RosterRowIn.netid`/`RosterEntryPatch.netid` allow up to 100 chars, but `models.CourseRoster.netid` is `String(50)` — a netid between 51-100 chars passes schema validation and fails at the DB layer.
- `MyCourseItem.role` is commented "HOST (always, for this endpoint)" — that invariant is enforced by the caller/router, not by the schema.
- `QuestionPublic` (game.py) is defined but unused anywhere; its header comment ("answer_data and grading_type never exposed") does not describe what players actually receive — see `_question_payload` in `websocket/gateway.py`. Anything players must not see belongs in `answer_data`, not `config`.
- `UserCreate`/`UserUpdate` silently lowercase any submitted username — usernames are effectively case-insensitive/stored-lowercase.
- `RosterRowIn.email` (bulk import) is a plain unvalidated `str`, while `RosterEntryPatch.email` (single-row edit) is `EmailStr` — bulk import accepts a malformed email that a single patch would reject.
