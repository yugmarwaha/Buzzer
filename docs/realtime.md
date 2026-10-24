# How Real-Time Works in Buzzer

Lecture covered REST: the client sends a request, the server sends one response, the connection is done. A live quiz game can't work that way — when the host advances to the next question, the server must push that fact to every player *immediately*, without any player asking for it. Buzzer does this with **Socket.io** (WebSockets): each browser holds one persistent, two-way connection to the backend for the duration of a game.

This document traces **one player answering one question**, end to end, so you can see every moving part. Read it before you modify anything in the game loop — new question types, scoring changes, and host-screen features all flow through this path.

## The cast

| Piece | Where it lives | Role |
|---|---|---|
| Event name constants | `backend/app/websocket/events.py` | The complete vocabulary — every event the client and server can send. Start here. |
| Gateway | `backend/app/websocket/gateway.py` | The server-side handlers: one `@sio.on(...)` function per client→server event. |
| JWT middleware | `backend/app/websocket/middleware.py` | Authenticates the socket connection using the same JWT you get from `/api/auth/login`. |
| State service | `backend/app/services/state_service.py` | Live game state in **Redis** (current question, who has answered, scores) — fast, per-room, expires with the room. |
| Game service | `backend/app/services/game_service.py` | Scoring logic (`record_answer` and the per-type scoring branches) and **MySQL** persistence — every answer is written to the database immediately. |
| Player client | `frontend/player/src/pages/game/GameLayout.tsx` | Opens the socket, listens for server events, sends answers. |

Note the division of labor: **Redis holds the ephemeral live state; MySQL holds the permanent record.** The gateway orchestrates both on every answer.

## Trace: one answer, end to end

Assume the host has started a question, so every player has already received a `new_question` event (see payload shape in `_question_payload()` in the gateway — it deliberately includes `config` but **never** `answer_data`, so the correct answer is never in the browser).

**1. Player taps an option.** The player app emits one event over its existing socket:

```js
socket.emit("submit_answer", {
  question_id: 42,
  answer_data: { selectedIndex: 2 },   // shape depends on question type
  answer_time_ms: 4180,
});
```

`answer_data` is a type-specific dict: `{selectedIndex}` for multiple choice, `{selectedValue}` for true/false, `{text}` for fill-in-the-blank, `{selectedIndices}` for multi-select, `{x, y}` for pin drop, `{value}` for closest guess. If you add a question type, you are defining a new `answer_data` shape.

**2. The gateway validates before it scores** (`on_submit_answer` in `gateway.py`). In order, it checks: the sender is an authenticated PLAYER in a room; a question is actually open (`question_phase == "QUESTION"` in Redis) and not locked; the submitted `question_id` matches the live question; and the player hasn't already answered (idempotent — a duplicate gets an `answer_received` reply with `alreadyAnswered: true` and is *not* re-scored). The "already answered" check is an atomic Redis `SADD` claim on the answered set, so two concurrent submits from one player can't both score; the claim is released if the answer is then refused or fails. Any failure emits an `error` event **to that player only**, and nothing is recorded.

**3. Scoring and persistence.** The gateway calls `game_service.record_answer(...)`, which dispatches on `question.type` to compute correctness and points, writes the answer to **MySQL**, and updates the player's running score in **Redis**; if the MySQL commit then fails, those Redis updates are undone. After the MySQL flush it checks again that the question is still open and unlocked; if the host locked it or moved to results meanwhile, the write is rolled back and the player gets an `error`. When the host moves to results, the server sets the RESULTS phase first, waits for answer writes in flight, and only then reads the counts and scores.

**4. The server fans out three different notifications** — this is the part REST can't do:

- To **the answering player** only: `answer_received` — `{questionId, isCorrect, pointsAwarded, totalScore}`.
- To **the host screen** only: `answer_status` — `{questionId, userId, answeredCount, totalPlayers}` (this is how the host's live "12/30 answered" counter works).
- If that was the **last** unanswered player: `answer_phase_ended` to the host, and the server cancels the question countdown timer.

Meanwhile every player who *didn't* answer receives nothing — their screens change only when the host advances and the server broadcasts `question_results` and then the next `new_question` to the whole room.

## The full event vocabulary

Client → server: `join_room`, `rejoin_room`, `submit_answer`, and host-only `host_advance`, `host_lock_question`.
Server → client: room-wide broadcasts (`new_question`, `question_results`, `game_over`, `player_joined`, `player_left`, `question_locked`/`question_unlocked`, `host_disconnected`, `host_reconnected`, `game_abandoned`), player-targeted replies (`answer_received`, `sync_state`, `error`), and host-only status (`answer_status`, `answer_phase_ended`).

**Reload/reconnect:** a `join_room` (what both web apps send on every socket `connect`) gets a `sync_state` that carries what the current screen needs, because client state does not survive a page reload. Host: `answeredCount`/`allAnswered` while a question is open, `questionResults` (same shape as the host's `question_results`) in RESULTS, `gameOver` once COMPLETED. Player: `questionPhase`, `hasAnswered`, its own `questionResults` in RESULTS, `question` (the client-safe `new_question` payload) and `yourAnswer` (`{questionId, answerData}`, that player's own submitted answer, never anyone else's) for the current question once it is answered or in RESULTS, and `gameOver` when a player who took part joins a COMPLETED room (no `authorise_player` re-check on that path: a since-deactivated roster entry still gets its own summary, never anyone else's). An unanswered open question is pushed to a joining player as `new_question` right after, with `questionLocked: true` if it is locked. `rejoin_room` (used only by tests and scripts) sends the same host `sync_state` and the player fields above, but a player on a COMPLETED room gets no `gameOver` and no open question is pushed. The server reads the room state, and on `join_room` the session status, only after the socket has entered its rooms. The final advance commits COMPLETED (MySQL, then Redis) before it emits `game_over`, so a socket joining during it either is already in its rooms for that broadcast or sees COMPLETED and gets the game-over `sync_state`. It rebuilds results (which carry the correct answer) only when the room's RESULTS phase belongs to the current question (`results_question_id` and the question index agree); otherwise it sends the state without them and the events of the advance in flight follow.

**`host_advance` payload:** the Host app sends `{from_phase: "LOBBY"|"QUESTION"|"RESULTS", question_index}` (`question_index` left out when it doesn't know it; then only the phase is checked). The server ignores an advance from a screen the room has already left, and an advance from the same screen as one already running (an atomic Redis `SET NX` claim on `advance:{session}:{phase}:{index}`, 5 s), so a double trigger can't skip a question; the ignored sender gets a fresh `sync_state` so its screen catches up. `{}` (scripts, tests) is never checked.

**Room state writes are locked:** the room blob (`room:{code}`) is one JSON value that several handlers read, change and write back (advance, lock/unlock, the question timer, host disconnect/rejoin, `start_game`). Each such update runs under a short per-room Redis lock (`state.room_state_lock` / `state.update_room_state`: `SET lock:room:{code} <token> NX PX 3000`, released with a compare-and-delete script, bounded wait with backoff, logs and proceeds unlocked if it times out — except the `host_advance` writes, which then write nothing and send the host an `error` asking it to try again). Inside it there are only Redis reads/writes, never DB queries or emits. An advance re-reads the room under the lock and writes only if the room is still on the screen it started from; a lock click that arrives after the room left the open question writes nothing.

All names are defined once in `events.py` and imported everywhere — if you add an event, add it there.

## Seeing it live

Two ways to watch this trace happen without hand-driving 30 browser tabs:

```bash
# Scripted end-to-end game that asserts on every event payload:
python scripts/smoke_test_websocket.py

# Fill a real room with simulated players (create a room in the Host app first):
python scripts/simulate_players.py --room <CODE>
```

Both scripts speak the socket protocol directly (no browser), so reading them is also the fastest way to learn the client side of the contract. The integration tests in `tests/integration/engine/socket_client.py` do the same thing under pytest.

## Rules of thumb when you extend the game loop

- **Server is the referee.** Never trust the client: validate the answer shape in the gateway (see the `multi_select` bounds and duplicate checks for the pattern), score in `game_service`, and never send `answer_data` to the browser.
- **Redis for live state, MySQL for the record.** If it must survive the room ending, it goes in MySQL. If it's "what is happening right now," it goes through `state_service`.
- **Pick the right audience for every emit:** the room, the host room (`_host_room(...)`), or one `sid`. Broadcasting a player's score to the whole room is a bug; so is telling only one player the question changed.
