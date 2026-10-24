# websocket

## Purpose
Real-time game communication layer (python-socketio) connecting Host and Player clients:
room join/rejoin, question phase transitions, answer submission, and disconnect/timeout handling.

## Contents
- `__init__.py` — empty, package marker.
- `events.py` — socket.io event-name constants, grouped by direction and audience
  (client→server, server→room broadcast, server→specific client, server→host-only, and a
  separate server→all-clients-in-room group for lock/unlock).
- `middleware.py` — `authenticate_socket`: validates the JWT passed in the socket.io `auth`
  dict at connect time (access or temp token only) and loads the corresponding user.
- `gateway.py` — the socket.io server (`sio`, `socket_app`) and all event handlers: `connect`/
  `disconnect`, `join_room`, `rejoin_room`, `host_advance` (drives the LOBBY→QUESTION→RESULTS→…
  state machine), `submit_answer`, `host_lock_question`. Owns per-process in-memory state
  (`_sid_ctx`, `_abandon_tasks`, `_timer_tasks`) and background tasks for question timers and
  host-disconnect grace periods.

## How it fits in
Sits alongside `routers/` under `backend/app` as the other half of the API surface — routers
handle request/response HTTP, this handles persistent bidirectional game state. `gateway.py`
calls into `services/` (`game_service`, `state_service`, `auth_service`) for all business logic
and shared state, and reads `models/` (`Question`, `GameSession`) directly for DB lookups; it
does not call back into `routers/`. `socket_app` defined here is unused dead code — `main.py`
imports `sio` directly and builds its own separate ASGI wrapper instead, because mounting via
Starlette's `Mount` strips the prefix and breaks socket.io's path matching.

## Gotchas
- **T8 image URLs.** Every payload that carries a question `config` also carries `imageUrls`
  (`{"<id>": "/api/images/<key>"}` from `image_service.image_urls`): `_question_payload` (now
  `async`, takes `db`; used by `new_question`, host/player `sync_state` and the late-join push),
  both results builders **and the inline live `question_results` broadcast in
  `_on_host_advance_impl`** (it builds its own payload — keep the two in step), and both
  game-over payloads (from the snapshot configs, so old versions resolve). Clients never build
  URLs from ids; an id missing from the map shows "Image unavailable".
- Per-process in-memory dicts (`_sid_ctx`, `_abandon_tasks`, `_timer_tasks`) are **not** shared
  across instances. The module comment notes that in production, socket.io's Redis manager
  routes events correctly across instances, but task bookkeeping (timers, abandon countdowns)
  stays local to whichever process owns it — a scale-out deployment must keep that in mind.
- `_client_manager` deliberately uses no Redis pubsub manager (`None`) in development and
  `AsyncRedisManager` only in production, because the Redis-backed manager's pubsub listener
  was found to drop connections under idle load in dev.
- `_question_timer_task`'s cleanup only pops `_timer_tasks[session_id]` if it's still the
  current task (`is asyncio.current_task()`) — a plain `.pop()` would risk evicting a newer
  replacement timer task started before this one's `CancelledError` is delivered.
- On timer expiry, the task doesn't just emit `ANSWER_PHASE_ENDED` — after a 0.5s grace period
  for in-flight answers, it also locks the question and broadcasts `QUESTION_LOCKED` itself,
  with no host action needed.
- On unlock, `started_at` is rewritten to `now - (time_limit - remaining)` ("virtual start
  time") so a later lock recomputes the correct remaining time — a load-bearing invariant for
  anyone touching lock/unlock or question timing (explained in a comment at the host-unlock
  call site; the reconnect-triggered auto-unlock site computes the same thing without a comment).
- An ADMIN-role user can act as HOST on any session, independent of that session's actual
  `host_user_id` — but only when it asks to: `join_room` and `rejoin_room` both take a `role`
  and apply the same per-role check (HOST: starter or admin; PLAYER: `authorise_player`).
  A `rejoin_room` without `role` is HOST only for the session's starter, else PLAYER, so an
  admin who joined as a player stays one on reconnect. A player new to the room via
  `rejoin_room` gets `PLAYER_JOINED` sent to the host, as with `join_room`. `host_advance` itself does no
  DB/role check; it just trusts the HOST/PLAYER role already decided at join time and cached
  in the in-memory `_sid_ctx`.
- A host disconnecting during LOBBY is a complete no-op — the disconnect handler's host branch
  and the abandon task both only act on `IN_PROGRESS` sessions. A lobby whose host vanishes
  stays LOBBY in MySQL until its starter or an admin ends it from the Host app's Rooms tab
  (it shows there as "Not running" once Redis's 90-min TTL clears it).
- Ending a room (T4): `_on_room_ended` is registered with `game_service` at import and runs after
  `end_room` commits — it cancels this process's timers (skipping `asyncio.current_task()`,
  since the disconnect timer calls `end_room` itself), emits `game_abandoned` to the room, and
  closes the player and host rooms. `host_advance` re-checks the session status in a fresh
  transaction before its Redis writes, and sets `session_questions.asked_at` as it broadcasts.
- A player joining after a question's timer has already expired can still get a live question
  with a hard 5-second floor (`max(5, time_limit - elapsed)`) and score on it — this only happens
  when the question is unlocked with zero time remaining and no replacement timer running (the
  normal path auto-locks 0.5s after expiry), but it is reachable.
- `submit_answer` validates answer shape before scoring for `multi_select` (index bounds, no
  repeated index) and the T7 types (`game_service.validate_answer` → `error` event, no score
  row). The other three
  original types go unvalidated into scoring: a well-typed but out-of-range answer scores 0 with
  a normal `ANSWER_RECEIVED`; a wrongly-typed one (e.g. a string `selectedIndex`, non-string
  `text`, or non-dict `answer_data`) raises inside `calculate_score`, and since
  `on_submit_answer` only catches `ConflictError`, the handler dies with no reply to the player
  and nothing recorded (the answer claim is released).
- **One answer per player, none after close:** `on_submit_answer` keeps `has_answered` as a
  fast path, then claims the answer with `state.claim_answer` (atomic SADD; a concurrent second
  submit gets `alreadyAnswered: true`). `record_answer` gets a `still_open` callback
  (`_answer_window_open`: still QUESTION, unlocked, same question) checked after the DB flush;
  a late answer is rolled back with a "Question is closed" error. The handler commits before
  any emit and releases the claim on every failure; if the commit itself fails it also calls
  `game_service.revert_answer` (record_answer already counted the score and distribution in
  Redis). `answer_received` still carries `isCorrect`/`pointsAwarded`/`totalScore` while the question is open (integration tests and
  scripts assert them), so a player sees its own correctness before RESULTS.
- **QUESTION → RESULTS reads the answers after the phase change:** the advance writes RESULTS
  first, then calls `game_service.wait_for_answer_writes` (waits for answer inserts in flight),
  and only then reads the distribution, leaderboard and per-question points.
- `_answer_reveal`'s correct-answer-derivation logic is reimplemented in
  `report_service._answer_reveal` too (not fully in sync — see `services/CLAUDE.md`), and
  `game_service.answer_reveal` is a third copy. For the T7 types the gateway delegates to
  `game_service.answer_reveal` instead of adding another copy; a new type must still be added
  wherever the copies branch.
- Live play reads the live `Question` row (`_question_payload`, `_answer_reveal`, scoring), not
  the `session_questions` snapshot; only `asked_at` is written to the snapshot here.
- Neither frontend emits `rejoin_room`: both the host and player `GameLayout` re-emit
  `join_room` on every socket `connect`. Only the integration tests exercise `rejoin_room`.
- **SYNC_STATE rebuilds the current screen** (page reload/reconnect): `_host_sync_payload` and
  `_player_sync_extras` add answered count, results, and game-over data using the same
  `_host_results_payload` / `_player_results_payload` / `_host_game_over_payload` /
  `_player_game_over_payload` builders as the live broadcasts — change a payload there, not inline.
  A PLAYER `join_room` on a COMPLETED room returns the game-over summary to players who took part
  (no rooms joined, and no `authorise_player` re-check: a deactivated roster entry still gets its
  own summary, nobody else's); anyone else still gets the "not accepting players" error.
  `rejoin_room` does not have that COMPLETED path or the open-question push.
- **Results in a sync can carry the correct answer, so they are gated:** every join path reads
  state only after `enter_room`, via `_read_live_state` (current question first, then room
  state: an advance writes them in the opposite order), and `_revealable_question_id` allows
  results only when the room is in RESULTS for that question (`results_question_id`, set on
  entering RESULTS and cleared on the next question, plus the index check in
  `_question_in_sync`). Readers don't take the room lock, so these checks still guard against
  a reader seeing the two keys mid-advance.
- **Every room-state read-modify-write holds the per-room lock** (`state.room_state_lock` /
  `state.update_room_state`, see `services/CLAUDE.md`): the timer auto-lock, the disconnect
  auto-lock, `_auto_unlock_for_host` (which also records `host_sid`; it takes the lock itself, so
  callers must not hold it), the lock/unlock toggle, and each `host_advance` write. Inside the
  lock: only Redis calls and synchronous bookkeeping (`_cancel_timer`, registering the new timer
  task), never DB queries or emits. Each writer re-reads the blob under the lock and re-checks
  its precondition (still QUESTION, same index via `_question_in_sync`/`_phase_unchanged`), so a
  lock click after RESULTS writes nothing and an advance whose room moved on (two concurrent
  `{}` advances) writes nothing and resyncs the host (`_advance_lost_race`). The lock is not
  re-entrant: never call a locking helper while holding it. The advance still writes room_state
  before current_q (both inside the lock). If the lock wait times out, the QUESTION → RESULTS
  and next-question writes of `host_advance` write nothing and send the host an error ("The room
  is busy", `_emit_room_busy`). Limit: the other writers (timer and disconnect auto-lock,
  lock toggle, auto-unlock, `mark_completed`, `start_game`) still fall back to an unlocked write
  after the wait, and a holder whose 3 s TTL ran out mid-write is not fenced.
- **Game over can't be missed by a joiner:** the final advance commits COMPLETED in MySQL
  (`complete_game`), mirrors it into Redis, and only then emits `game_over`. `join_room`
  (PLAYER) and `_host_sync_payload` read the status with `fresh_session_status` after the socket
  entered its rooms; a player who sees COMPLETED there leaves the rooms and gets the game-over
  sync if they took part (`_emit_player_game_over_sync`; a player added only by this join is
  removed again and gets the "not accepting players" error).
- PLAYER sync also carries `question` (client-safe payload) and `yourAnswer` (the caller's own
  `answer_data`, via `_own_answer`, filtered by user in SQL and again in Python) for the
  current question when answered or revealable, so the results screen's "You answered" line
  survives a reload.
- A LOBBY advance on a session already IN_PROGRESS in MySQL (an earlier start committed, then
  its advance hit "room busy" or an error before question 0 was written) does not call
  `start_game` again: it takes the question ids from the snapshot and continues to question 0.
- Ranks (`yourRank`, `yourFinalRank`) come from `_rank_of`: standard competition ranking over
  the MySQL leaderboard (equal totals share a rank, 1, 1, 3; no score rows counts as 0 points).
- `host_advance` with `from_phase` (and optionally `question_index`) is ignored if the room has
  already left that screen (`_stale_advance`) or another advance from the same screen holds the
  `state.claim_advance` SET NX key (5 s); the ignored host gets a fresh SYNC_STATE. `{}` skips
  both. Reconnecting hosts emit `host_reconnected` to the room when they cancel a pending abandon
  task, clearing the players' banner. `_auto_unlock_for_host` keeps the question locked when no
  time was left.
- `_on_host_advance_impl` deliberately selects raw columns instead of ORM objects (see the
  comment in that function) to avoid triggering the `GameSession.scores` backref, which raises
  `MissingGreenlet` in async SQLAlchemy when the collection isn't eager-loaded.
- WebSocket connection rate limiting (`_WS_RATE_LIMIT`/`_WS_RATE_WINDOW`) is skipped entirely in
  development (all dev traffic shares a Docker-internal IP) and has a `STRESS_TEST_KEY` bypass
  for load testing — both are no-ops if Redis is unavailable (fails open, not closed).
- `answer_data` is documented as "NEVER forwarded directly" to clients — only derived
  `answerReveal` facts are emitted — an invariant worth preserving when adding question types.
- The `disconnect` handler auto-locks the active question when the host drops during an
  IN_PROGRESS session and starts a 5-minute (`_HOST_GRACE_SECONDS`) abandon countdown;
  reconnecting host or `rejoin_room` cancels the abandon task and auto-unlocks via
  `_auto_unlock_for_host`. It does none of this while another HOST socket for the same session
  is still in `_sid_ctx` (`_host_socket_connected`: second tab, admin, or a reconnect whose old
  sid times out later), and re-checks after its awaits, unlocking if a host joined meanwhile.
  `_sid_ctx` is per-process, so with several workers a host socket on another worker isn't seen.
