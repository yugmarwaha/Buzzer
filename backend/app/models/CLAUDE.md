# models

## Purpose
SQLAlchemy ORM models (declarative `Base` from `..database`) defining the database schema: users, courses/rosters, games/questions, game sessions/scores, and T8 course images.

## Contents
- `__init__.py` — re-exports all model classes; documents that import order matters (FK-dependent tables must import after the tables they reference).
- `user.py` — `User`: account row (netid, username/password_hash for local login, email, role enum ADMIN/USER/GUEST); relationships to course/game access, hosted sessions, scores.
- `course.py` — `Course`, `CourseRoster` (per-course student roster rows), `UserCourseAccess` (user↔course role: HOST/PLAYER).
- `game.py` — `Game` (required immutable `course_id`, nullable `archived_at`), `Question` (type, grading_type ACCURACY/COMPLETENESS, JSON `config`/`answer_data`, ordering, points), `UserGameAccess` (legacy per-game grant: kept in the DB, read by nothing).
- `image.py` — `Image` (T8, `docs/plans/t8-images.md` §5): a course-owned image version. `public_key` (uuid4, unique) makes the public view URL; `data` is a `MEDIUMBLOB` mapped **deferred**, so list/lock/scan queries never load the bytes (only the view route, export and the report `undefer` it). "Current" = `replaced_by_id IS NULL AND deleted_at IS NULL`. `replaced_by_id` is a plain indexed int with **no FK**, so hard-deleting a newer version never blocks or revives an older one. `course_id` FK has no `ondelete` (like `games`); `uploaded_by` is `SET NULL`.
- `session.py` — `GameSession` (a live/completed play of a game in a course, room_code, status LOBBY/IN_PROGRESS/COMPLETED/ABANDONED), `SessionScore` (per-user per-question score row), `SessionQuestion` (snapshot of each question as asked; `question_id` has no FK so it survives deletes; `asked_at` NULL = never reached).

## How it fits in
This is the persistence layer of `backend/app`; services and routers query these models via `..database`. `schemas/` mirrors selected model fields for API input/output (e.g. `CourseResponse`/`GameResponse`/`QuestionResponse` map 1:1 to model columns via `from_attributes=True`), but deliberately withholds some model fields from player-facing schemas (e.g. `Question.answer_data`/`grading_type` never reach `schemas/game.py`'s `QuestionPublic`).

## Gotchas
- `__init__.py` comment: "Import order matters — tables with FKs must come after the tables they reference" — reordering imports there can break table creation.
- `Question.config` and `Question.answer_data` are untyped JSON blobs; their required shape per question type (multiple_choice/true_false/fill_in_the_blank/multi_select) is enforced only in `schemas/admin.py`'s `QuestionCreate` validator, not by the model — the model itself accepts any dict.
- String enums (course_role, user_role, grading_type, session_status — see Contents for values) are inlined per-model rather than shared constants. Adding/renaming a value means updating the model and every place elsewhere that checks the same string.
- `GameSession.host_user_id`/`.host` are nullable — code reading `.host` must handle `None`.
- FK cascade behavior is inconsistent: FKs that reference `Course`/`Game` (e.g. `CourseRoster.course_id`, `Question.game_id`, `UserCourseAccess`/`UserGameAccess`) cascade on delete, but `GameSession`'s FKs (`game_id`, `course_id`, `host_user_id`) and `SessionScore.user_id`/`.question_id` do not — deleting a `Game`/`Course`/`User`/`Question` with associated sessions/scores raises an FK error instead of cascading.
- `CourseRoster` has a DB-enforced unique constraint on `(course_id, netid)` — a roster import/patch that would create a duplicate netid in the same course fails at the DB layer.
- Questions reference images by id **inside the JSON `config`** (`promptImageId`, `optionImageIds`, `imageId`), not by FK or link table: nothing at the DB layer stops a dangling id. `services/image_service.py` scans configs (with locking reads) to find references; clients show "Image unavailable" for an id with no row.
- `SessionScore` has no uniqueness constraint on `(session_id, user_id, question_id)` — nothing at the model level stops a double-submit from creating two score rows for the same answer; the only guard is the `alreadyAnswered` check in `websocket/gateway.py`'s answer handler.
