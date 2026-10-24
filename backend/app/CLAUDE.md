# app

## Purpose
The Buzzer backend (FastAPI + python-socketio): a REST API for auth/admin/host resources, plus a
socket.io gateway that runs live game rooms between one Host and many Players. MySQL is the
durable store; Redis holds live room state.

## Contents
- `routers/` — REST surface: `/health`, `/auth/*`, `/game/*` (rooms), `/host/*` (course-first host work), `/player/*` (Past Quizzes), `/admin/*` (users, courses, access + compatibility routes).
- `websocket/` — socket.io gateway: join/rejoin, question state machine, answers, timers, disconnect handling.
- `services/` — shared business logic (auth/JWT, game lifecycle + scoring, Redis state, CSV/HTML export, roster import). Called by both routers and the gateway.
- `models/` — SQLAlchemy ORM schema (users, courses/rosters, games/questions, sessions/scores).
- `schemas/` — Pydantic request/response models; `QuestionPublic` is the client-safe question view.
- `common/` — auth dependencies, error types, logging, rate limiting.
- Files at this level (no child doc):
  - `main.py` — app entry: creates the FastAPI app, mounts the six routers under `/api`, and runs Redis init and admin bootstrap in the lifespan. Wraps the app with `sio` from `websocket/gateway.py` in its own `socketio.ASGIApp`.
  - `config.py` — `settings` (e.g. `is_development`, `is_stress_test_mode`, `STRESS_TEST_KEY`, `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`, `ADMIN_USERNAME`/`ADMIN_PASSWORD`).
  - `database.py` — async engine, `Base`, `get_db` (commits implicitly on clean return, rolls back on error), and `DbSession` — the alias every route uses (`Depends(get_db, scope="function")`), so the commit happens before the response is sent. Don't use bare `Depends(get_db)`: a mixed scope gives one request two DB sessions.
  - `redis_client.py` — lazy shared Redis client (`get_redis`/`close_redis`). Imported by `main.py`, `routers/{auth,game,health,host}.py`, and `websocket/gateway.py`; `services/` receive the client as an argument rather than importing it.
  - `migrations/` — Alembic (`env.py`, `versions/`).

## How it fits in
Layering, top to bottom: `routers/` and `websocket/` (entry points, never calling each other) →
`services/` → `models/` + Redis. `schemas/` shapes what routers accept and return; `common/` is
infrastructure imported by everything. Services import neither `routers/` nor `websocket/`, which
is what keeps HTTP and socket paths consistent on scoring, room state, and auth. Routers are the
leakiest layer: `admin.py` and parts of `game.py` query models directly instead of going through
`services/`.

## Gotchas
Cross-directory facts; per-directory details live in the child docs.
- **Adding a question type spans directories:** `schemas/admin.py` (the only shape check for `Question.config`/`answer_data`), `services/game_service.calculate_score`, the answer-reveal logic duplicated across `websocket/gateway.py`, `services/report_service.py` and `services/game_service.py`, and the frontends. See `services/CLAUDE.md`.
- **Answer data must not reach clients:** `schemas/game.py`'s `QuestionPublic` omits `answer_data`/`grading_type`, and the gateway emits only derived `answerReveal` facts; new schemas or socket payloads need the same care.
- **Config flags that change production behavior:** `is_development` relaxes HTTP and websocket rate limiting, enables netid-only login, and turns on the API docs; `STRESS_TEST_KEY` also bypasses both rate limits. Unset JWT keys mean ephemeral signing keys, so tokens die on restart.
- **Authorization is re-derived from the DB, not the token:** the token's `role` claim is unused, and `"temp"` tokens are accepted like `"access"` tokens in both `common/dependencies.py` and `websocket/middleware.py`. The websocket path trusts the HOST/PLAYER role cached at join time.
- **Redis room lifetime:** one 90-minute TTL on the room key, refreshed only on some paths; the `session:{id}:*` keys carry a 24-hour backstop TTL reset on every write. See `services/` and `websocket/` docs.
- **Prompt sanitization happens once, at write time** (`bleach` in `services/quiz_service.py`); downstream code embeds prompts unescaped.
- **Access is course-based (T4):** every host/admin quiz, roster, and session route goes through `services/access_service.py` (HOST on the record's own course; children loaded through their parent). Quizzes can't be deleted (archive instead), and past results read the `session_questions` snapshot, never the live questions.
- **FK cascades are inconsistent** (`models/`): `GameSession`'s FKs to games/courses/users and `SessionScore`'s to users/questions don't cascade, so `routers/admin.py` deletes dependents by hand.
