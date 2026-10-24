# services

## Purpose
Business logic and stateful operations shared across routers and the websocket gateway: auth,
game/session lifecycle, scoring, Redis-backed live state, and report/CSV generation.

## Contents
- `__init__.py` — empty, package marker.
- `auth_service.py` — password hashing (bcrypt), RS256 JWT create/decode (access/refresh/temp
  tokens), auto-generated ephemeral signing keys when none are configured, user lookup/creation
  (by username, netid via OAuth2, or guest email).
- `bootstrap.py` — one-time admin-user creation on startup from `ADMIN_USERNAME`/`ADMIN_PASSWORD`,
  retrying until the DB schema exists (migrations applied).
- `export_service.py` — builds session-score CSVs (raw and Canvas-gradebook) from the
  question snapshot; questions an abandoned game never reached are empty cells.
- `access_service.py` — course-based access (T4): `assert_course_host`, `lock_course` (T8: the
  course row lock, exclusive or `shared=True`), `get_game_for_host` (optionally `FOR UPDATE`:
  reads the quiz's `course_id` unlocked, checks HOST, locks the **course row first** — shared with
  `shared_course_lock=True`, used by `create_room` — then the quiz row), `get_session_for_host`, parent-ownership loaders
  (`get_question_in_game`, also optionally `FOR UPDATE`; `get_roster_entry_in_course`),
  `assert_session_starter_or_admin`.
- `quiz_service.py` — quiz/question CRUD, archive/restore, JSON export/import, shared by the
  host and admin routers. Guards: active-room lock, archived lock, answered-question delete
  block, `order_index` reindex. Mutations hold the game-row lock, and every read after it is a
  locking read (`_locked_questions`, `answered_question_ids(lock=True)`, `has_active_room`):
  the request's REPEATABLE READ snapshot predates the lock wait, so a plain read is stale.
  `update_question` runs `_validate_merged_update` (stored question + incoming fields through
  `QuestionCreate`, readable string 422) only when the stored or requested type is a T7 type.
  T8: create/update/import also run the image checks — shape via `schemas/admin.py`
  (`_validate_merged_images` on update for the original four types) and the database layer via
  `image_service.check_question_images`; update/delete then `prune_removed` older versions the
  question stopped using. `question_item` carries the editor's `images` map. Export/import:
  version 1 is the pre-T8 format; a quiz with images exports as **version 2** (`images` list,
  keys `img-1`… in place of ids) after checking every question with the import rules and the
  bundle caps (so every export imports). `import_bundle` validates and decodes everything
  first, then takes the course lock itself, inserts the referenced images, and creates the quiz
  (flush only: any failure rolls the whole import back).
- `course_map.py` — T4 migration planning/check logic on a sync connection (raw SQL, works on
  the pre-004 schema); used by `scripts/plan_game_courses.py` and migration `004`.
- `game_service.py` — room code generation, session lifecycle (create_room takes the course
  from the quiz; `start_game` and `complete_game` are compare-and-set; `end_room` is the one way
  to end an active room and then calls `room_ended_listeners`), question snapshots
  (`session_questions`, `load_snapshot`, `mark_question_asked`), player authorization against
  the course roster, scoring (`calculate_score`), answer recording (`record_answer` raises
  `ConflictError` if the room ended mid-insert, incl. the FK `IntegrityError` when an empty
  room was removed; any other `IntegrityError` is a generic "Could not record answer"; and
  "Question is closed" when its `still_open` callback, checked after the flush, says the
  question was locked or left QUESTION meanwhile — rolled back before any Redis update; the
  Redis score/distribution updates come before the caller's commit, and `revert_answer` undoes
  them if that commit fails),
  `wait_for_answer_writes` (exclusive locking read of the session row, then commit: waits for
  answer inserts in flight, which hold a shared lock on that row until they commit), leaderboard, game-over
  summaries and course session/room listings — all read from the snapshot — plus the
  session-scoped guest merge (`merge_guest`) used by the host route (`admin.py`'s merge keeps its own pre-T4 logic).
  `merge_guest` locks guest row → target row → score rows (locking reads), in that order:
  taking score locks before the target lock deadlocks two merges into one target.
  T7 (`docs/plans/t7-new-question-types.md`): `validate_answer` (shape check for
  `pin_drop`/`closest_guess` answers, both grading modes), `closest_guess_result` (the only home
  of the tolerance/partial-credit rules; returns `(award, band)`), `new_type_dist_key`
  (`"x,y"` or `full`/`partial`/`miss`; none for closest_guess COMPLETENESS).
- `image_service.py` — T8 course images (`docs/plans/t8-images.md` §4–§8): `load_image`
  (Pillow; CPU-bound, run in a threadpool **before** any lock; 415 unsupported format, 422 too
  large/animated/broken; MPO counts as JPEG), `read_upload` (cap + 1 memory bound), reference
  helpers over question configs (`image_ids_in_config`, `replace_id_in_config`), the locking
  reference scans (`question_refs`, `snapshot_uses`), `check_question_images` (database layer of
  the reference rules: same course and current, unless the id is already in the stored
  question), `create_image` / `replace_image` / `delete_image` (the spec's ordered steps; the
  caller holds the course lock), `prune` / `prune_removed` (hard-delete a non-current version
  nothing references), per-course caps on **current** images only, and the URL maps
  (`image_urls` for payloads, `image_info` with `current` for the editor).
- `report_service.py` — generates a standalone, anonymized HTML session report (bar charts,
  word clouds, score histogram) with no PII, from the snapshot plus `SessionScore` rows;
  unreached questions are omitted. T8: prompt and option images of asked questions are
  embedded as `data:` URIs (old versions included), up to 20 MiB of image data per report
  (sizes are read first; only the bytes that fit are loaded);
  "[image removed]" / "[image omitted: report size limit]" otherwise; image-only options read
  "Option A".
- `roster_service.py` — parses Canvas gradebook CSV exports (or pre-mapped rows) into
  `course_rosters`, upserting matches and deactivating netids absent from the latest upload.
- `state_service.py` — Redis key schema and helpers for live game state: room metadata (90-min
  TTL — see Gotchas), player set/scores, current question, per-question answered sets
  (`claim_answer` is the atomic SADD claim of a player's one answer, `release_answer` undoes it),
  answer-distribution counters (`decrement_answer_dist` for `revert_answer`), a short per-player
  lock (`lock:player:{id}:{user_id}`, same SET NX scheme) around both read-modify-writes of the
  player JSON (`update_player_score`, `set_player_connected`), `claim_advance` (a 5 s SET NX key that lets only one
  `host_advance` from a given phase/question index through), and the room-state lock:
  `room_state_lock` (async context manager, `SET lock:room:{code} <token> NX PX 3000`, retry
  with backoff up to `ROOM_LOCK_WAIT_S` (4 s, read at call time), compare-and-delete Lua
  release; yields False and logs `room_state_lock_timeout` if it can't get it, and the caller
  proceeds unlocked unless it checks the yielded value) and
  `update_room_state(redis, code, mutate, require_lock=False)` (locked read → sync `mutate` →
  write; `mutate` returns False to skip the write; a missing room key is never recreated; with
  `require_lock=True` a lock timeout writes nothing and raises `RoomLockBusyError`).

## How it fits in
This is the shared logic layer beneath `backend/app`: routers (`routers/`) call these functions
for anything beyond request parsing/validation, and the websocket gateway (`websocket/gateway.py`)
calls the same functions (`game_service`, `state_service`, `auth_service`) to drive real-time game
flow — so both HTTP and websocket entry points stay consistent on scoring, room state, and auth
rules. Services touch the DB (`models/`) and Redis directly; they do not import from `websocket/`
or `routers/`.

## Gotchas
- `auth_service.py`: if `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` aren't set, it generates ephemeral
  in-memory RSA keys and logs a warning — all issued tokens become invalid on process restart.
  Convenient for dev, easy to trip over in any multi-process or restart-heavy environment.
- `create_access_token` bakes the user's `role` into the token payload, but nothing reads it —
  every authorization check (`common/dependencies.py`, `websocket/middleware.py`, routers)
  re-loads the User row and checks the DB role instead. A role change takes effect on the very
  next request; the token's `role` claim is currently decorative.
- `bootstrap.py` deliberately never crashes on a missing schema — it retries every 5s — because
  under `uvicorn --reload` a crashed startup still leaves the container looking "Up".
- `game_service.calculate_score` hardcodes each supported type under ACCURACY grading; an
  unrecognized type scores 0. Under COMPLETENESS the type check is short-circuited — an unknown
  type with COMPLETENESS grading is awarded full points, not 0. The T7 types are the exception:
  an answer failing `validate_answer` scores 0 in either mode (checked before the short-circuit).
- `closest_guess` partial credit is truncated to cents and capped one cent below full, so the
  band follows the award exactly (full ⇔ award = points, partial ⇔ 0 < award < points, miss ⇔
  0) and `is_correct` means band `full` only.
- `is_correct` follows the reveal (`points >= points_value`) for multiple choice, true/false and
  fill-in, and is never true when `points_value <= 0` (as for multi_select). multi_select counts
  each selected index once.
- `fill_in_the_blank` grading uses `rapidfuzz` Levenshtein distance against `acceptedAnswers`
  with a configurable `editDistance` tolerance — near-miss spelling can score as correct.
- `game_service.create_room`'s `MAX_ROOMS` cap counts Redis keys matching `room:*` filtered to
  an 11-char key length — i.e. it's implicitly tied to 6-character room codes. Changing the code
  length silently disables the cap. It's also a blocking `KEYS` scan on every room creation.
- `start_game`, `complete_game`, and `end_room` commit explicitly (end_room must commit before
  clearing Redis and notifying). `fresh_session_status` commits first so its read escapes the
  REPEATABLE READ snapshot — the gateway uses it before Redis writes.
- Correct-answer reveal logic exists in **three** copies: `gateway._answer_reveal`,
  `report_service._answer_reveal`, and `game_service.answer_reveal` (shared by the player/host
  game-over summaries and Past Quizzes). `report_service`'s copy is missing `multi_select`, as are
  its `_extract_answer_key`, chart selection, and type badge — a `multi_select` question in the
  HTML report gets no chart and a raw type string as its badge. Answer-distribution keys are
  derived per type in one place, `_answer_dist_keys` (fill-in text through `normalize_fill_in`,
  multi_select each option once), used by `record_answer` (Redis) and `get_host_question_summary`. The CSV
  exports do not branch on type. For the T7 types the gateway and report copies delegate to
  `game_service.answer_reveal`, and the report's one-line summary is computed from score rows
  (`_new_type_summary`), not `_extract_answer_key`.
- Live answers are scored against the live `Question` row (the gateway passes
  `db.get(Question, ...)` to `record_answer`), not the `SessionQuestion` snapshot; results, reports,
  CSVs and Past Quizzes read the snapshot. The two agree only because `quiz_service` blocks
  edits while a LOBBY/IN_PROGRESS room exists for the quiz.
- **T8 lock order** (any path taking more than one): course → quizzes (ascending id) →
  questions → images → session rows, and the course lock is always taken **first** (quiz and
  question writes via `get_game_for_host(for_update=True)`, quiz create/import and image
  upload/replace/delete via `lock_course`, room creation shared). Live-game paths (answer, start,
  advance, end) never take course, quiz or image locks. Image "is current" checks and reference
  scans are locking reads, because the request's snapshot can predate the course-lock wait. A
  deadlock or lock-wait timeout surfaces as 409 "Busy, try again" (`common/exceptions.py`).
- **Room-state writers must use the lock** (`update_room_state`, or `room_state_lock` around
  get/set): the room blob is one JSON value, and an unlocked read-modify-write can overwrite a
  newer write. Hold it only around fast Redis calls (no DB, emits or sleeps) and never nest it
  (not re-entrant: a nested acquire waits out the bounded wait, then proceeds unlocked).
  `create_room` writes a brand-new key and doesn't need it; `start_game` uses it.
- `roster_service.py` caps uploads at `MAX_ROWS = 1000`; processing stops there and an error
  string is appended, remaining rows are not processed.
- `state_service.py`'s docstring is the source of truth for the Redis key namespace, but it
  overstates TTL refresh: `refresh_room_ttl` is only called from `join_room`, `rejoin_room`,
  new-question broadcast, and `submit_answer` — not from `host_advance`'s RESULTS/GAME_OVER
  transitions or `host_lock_question`. A game sitting in RESULTS or locked for >90 min can lose
  its room key. The room-metadata key has the 90-min TTL; `session:{id}:*` keys have a 24 h backstop
  (`SESSION_KEY_TTL`, reset on write) and otherwise rely on `delete_room_state`.
- `state_service.restore_from_mysql` does not restore anything to Redis — it only reads MySQL
  and returns a dict, and has no callers anywhere in the codebase. Don't assume it's the
  Redis-loss recovery path (that's handled ad hoc in the websocket gateway's rejoin logic).
