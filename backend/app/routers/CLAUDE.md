# routers

## Purpose
FastAPI route handlers (the HTTP API surface). Parse/validate requests, enforce auth via
dependencies, and return responses — business logic mostly lives elsewhere. `main.py` mounts
every router under `/api` (so `GET /health` below is served at `/api/health`).

## Contents
- `__init__.py` — empty, package marker.
- `health.py` — `GET /health`: pings MySQL and Redis, counts active rooms/players from Redis keys, returns `healthy`/`degraded` (the player count is currently always 0 — see Gotchas).
- `auth.py` — `/auth/*`: login (password or dev netid fallback), OAuth2 callback (Traefik header-based), temp-token exchange, guest join, refresh, logout. Manages the httponly refresh-token cookie. The only rate-limited router in the app (see Gotchas).
- `game.py` — `/game/*`: my-courses / my-active-sessions, room lifecycle (create — course comes from the quiz; get/ping; `DELETE /sessions/{id}` is an alias of "end room"), guest list/merge (session starter or admin), CSV export (any host of the session's course).
- `host.py` — `/host/*` (T4): course-first quizzes (create/import/edit/archive/restore/export), questions, roster, T8 course images (`GET`/`POST /courses/{id}/images`, `PUT`/`DELETE /images/{id}`; the file is decoded in a threadpool before the course lock), active rooms + end room, finished sessions + CSV/HTML downloads. Every route checks HOST on the record's own course via `services/access_service`; children are loaded through their parent.
- `player.py` — `/player/past-quizzes*` (T4 extra): a signed-in non-guest's finished, attempted sessions, from the question snapshots (plus T8 `image_urls` for played questions).
- `images.py` — T8 public view `GET /images/{public_key}`: **no auth** (an `<img>` request carries no Bearer token), any version incl. soft-deleted ones, `Cache-Control: public, max-age=31536000, immutable`, `nosniff`, `inline`; unknown key 404. Anyone holding the URL can view the image (design choice, `docs/plans/t8-images.md` §6).
- `admin.py` — `/admin/*`: courses (with hosts + quiz counts), users (with a read-only `course_summary`), course-access grants, guests, guest merge, plus compatibility routes for games/questions/import/export/roster that call the same services as `/host/*`. All `require_admin`. Retired: game delete (405), per-game grants and `/admin/sessions*` (404).

## How it fits in
Sits directly under `backend/app` as the entry point for all REST traffic; routers call into
`services/` for anything stateful or business-rule-heavy (game_service, state_service,
auth_service, export_service, report_service, roster_service) and use `common/` for shared
auth dependencies and exceptions. Routers do not talk to `websocket/` directly — real-time
game flow happens over the socket.io gateway once a room exists.

## Gotchas
- Quiz/question/roster/session logic lives in `services/` (`quiz_service`, `roster_service`,
  `game_service`, `access_service`); `host.py` and `admin.py` only parse, check access, and
  respond. `admin.py` still queries models directly for courses/users, and `game.py` does for
  `my_courses`, `my_active_sessions`, `get_room`, and the guest list.
- Routers never import `websocket/`. Ending a room notifies live clients through
  `game_service.room_ended_listeners`, which the gateway registers at import.
- `game.py`'s guest list and `merge_guest_for_session` are 409 until the session is finished
  (COMPLETED/ABANDONED). The merge calls `game_service.merge_guest`, which is session-scoped:
  only that session's guest scores move, the netid must be active on the session's course
  roster and have an account, a target who already answered in the session is 409, and the
  guest row is deleted only when no scores are left anywhere. `admin.py`'s `merge_guest` keeps
  the pre-T4 behaviour (no session: moves all the guest's scores, or promotes the guest in place).
- There is no whole-quiz delete and no delete of finished sessions (results are kept);
  quizzes are archived instead. Integration tests use the test-only
  `backend/scripts/cleanup_test_quiz.py`. `admin.py`'s user delete still deletes that user's
  `SessionScore` rows by hand before the user.
- The import/export bundle format is versioned (version 1; **version 2** adds embedded images, T8) and has no course field; import always creates a new quiz in the course from the URL (host) or form field (admin). Both import routes read at most `MAX_IMPORT_BYTES` (30 MiB); `main.py`'s middleware refuses any request whose `Content-Length` is over 32 MiB (413 `{error, message}`), matching nginx `client_max_body_size 32m`.
- `GET /game/rooms/{room_code}/ping` is explicitly public (no auth) to let unauthenticated
  players check room validity before joining — and, along with the rest of `/game/*` and
  `/admin/*`, is not rate-limited. Only `/auth/*` has rate limits (login, exchange-temp, guest).
- `auth.py`'s netid login fallback only works when `settings.is_development` is true; the
  OAuth2 callback restricts `redirect_to` to a hardcoded allowlist (`_ALLOWED_REDIRECT_PATHS`)
  to avoid an open redirect.
- `quiz_service.clean_prompt` (`bleach.clean`) sanitizes prompts on create/update/import —
  other modules (`report_service`, the websocket gateway) rely on this and ship the prompt
  onward unescaped; don't add a new write path without sanitizing it too.
- `health.py`'s player count reads `room:{code}:players`, but the player set actually lives at
  `session:{session_id}:players` (`state_service`) — the key never matches, so `activePlayers`
  is always 0. `activeRooms` isn't hit by that bug, but it's a raw count of `room:*` keys,
  including COMPLETED/ABANDONED rooms `complete_game` deliberately leaves in Redis — a different
  (larger) number than what `game_service.create_room`'s `MAX_ROOMS` cap actually counts.
- Most mutating `admin.py` handlers (updates, deletes, access grants/revokes, guest merge) rely
  on `DbSession`'s implicit commit-on-clean-return (before the response is sent) rather than calling `db.commit()` themselves; a
  handful of create endpoints commit explicitly. No enforced convention either way.
