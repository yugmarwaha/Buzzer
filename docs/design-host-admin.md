# Design: Host/Admin Restructure (T4)

Status: DRAFT rev 7. Goldfish rounds 1–3 addressed (§16).

## 1. Problem

- The Admin app does both admin work and host work. All host-type endpoints in `routers/admin.py`
  require ADMIN.
- Games have no course. Access is per-user game grants (`user_game_access`), and the course is
  picked only at room creation.

T4 needs:
- Host: HTML/CSV session downloads, roster management, quiz/question management, and quizzes
  attached to one course.
- "Only accessible to hosts and players of that course."
- Admin-first Admin app with host/player access kept but secondary.
- No bypassing access checks.

## 2. Decisions

1. **Access comes from the course.** Admin: every course. HOST on a course: every quiz, the
   roster, and every session in it, and can *see* its active rooms.
2. **Old game grants** stay in the DB. They are unused, hidden, and not converted.
3. **A quiz belongs to one course** from creation. It is never moved or shared.
4. **No deleting whole quizzes or finished sessions** (COMPLETED/ABANDONED). Tests use a test-only
   script (§9).
5. **Archive and restore** (host or admin). An archived quiz:
   - keeps its course and results
   - can't start rooms
   - is read-only
   - is listed in an "Archived" section
6. **Question delete** is allowed unless the question was answered in any session.
7. **No quiz or question edits** while a room for the quiz is LOBBY or IN_PROGRESS.
8. **Only the starter or an admin can end a room.** Other course hosts see it read-only, with the
   starter's name.
   - A forgotten room blocking other hosts is accepted.
   - With answers the room becomes ABANDONED and keeps its results; without answers it is
     removed.
9. **The Host app is course-first:** Quizzes / Rooms / Roster / Sessions.
   - Any course host can view and download any finished session: COMPLETED, plus ABANDONED ones
     with answers.
10. **Admin covers users, courses, and access**, plus a secondary Host app link.
    - The duplicate pages are removed.
    - Old admin routes stay for compatibility, calling the same services.
    - Removed actions' routes retire after the frontends stop using them.
11. **Room joining is unchanged** (guest, NetID roster, local account). Players never see
    upcoming quizzes.
12. **Guest merge:** session starter or admin only.
13. **Past Quizzes** (extra, not T4). For signed-in non-guests only:
    - their own finished sessions: played questions, their answers, right/wrong, correct
      answers, points, total
    - an "Abandoned" label where relevant
    - unreached questions show only "Not played"
14. **The migration never guesses.** A planning script proposes, the team reviews a map file,
    and the migration applies exactly that file (§4).
15. **Shared test users** have fixed usernames, are got-or-created, and are never deleted.

| Requirement | How it's met |
|---|---|
| Games attached to a course at creation | `games.course_id`, required, immutable |
| Only hosts and players of that course | Course check on every host endpoint, plus parent-ownership checks (§3). **Exception: guests (§8).** |
| Admin grants course/game access | Course access *is* game access: a quiz belongs to exactly one course, so granting HOST on the course grants every quiz in it. Per-game grants were dropped on purpose; the user page shows a read-only summary |
| No bypassing access checks | One course check at every entry point, admin routes included |

## 3. Access rules

"HOST on C" means a `user_course_access` row with `course_id = C` and `role = 'HOST'`.

| Action | Admin | HOST on C | Others |
|---|---|---|---|
| Quizzes in C: create, edit, import, export, archive, restore | yes | yes | no |
| Questions: add, edit, reorder; delete if unanswered | yes | yes | no |
| C's roster | yes | yes | no |
| C's finished sessions: list, CSV, HTML | yes | yes | no |
| Delete a quiz or a finished session | no | no | no |
| Start a room (quiz not archived) | yes | yes | no |
| See C's active rooms | yes | yes | no |
| End a room | yes | own rooms, while still HOST on C | no |
| Rejoin as a room's host | yes | starter only (unchanged) | no |
| Guest merge | yes | session starter only | no |
| Users, courses, access grants | yes | no | no |
| Past Quizzes (own) | yes | yes | signed-in non-guests |

- **Parent ownership.** A child is always loaded through its parent (404 if it doesn't belong):
  - roster entry → course
  - question → quiz
  - reorder list = exactly the quiz's question ids
  - T8 image → quiz

  This lives in the services, so the admin routes get it too.
- **Course invariant.** Quiz checks use `game.course_id`; session checks use
  `session.course_id`; both come from the DB.
  - New sessions always match their quiz's course.
  - Legacy sessions may differ if the reviewed map says so. Accepted: those sessions stay visible
    to their own course's hosts.
- **Accepted edge cases:**
  - A starter who lost HOST can't end their room; only an admin can.
  - A room with a NULL `host_user_id` is admin-only and shows "Unknown host".
  - A live host socket keeps its role if access is revoked mid-game.

## 4. Database and migration

**Schema (one Alembic migration):**
- `games.course_id` INT NOT NULL, FK `courses.id`, indexed.
- `games.archived_at` DATETIME NULL.
- `session_questions` (snapshot):
  - Columns: `session_id` (FK, cascade), `question_id` (int, no FK), `order_index`, `type`,
    `grading_type`, `prompt`, `config`, `answer_data`, `time_limit_seconds`, `points_value`,
    `asked_at` NULL.
  - Unique `(session_id, question_id)`.
  - Written by `start_game` in the same transaction. The gateway sets `asked_at` when it
    broadcasts a question.
- `user_game_access` and `session_scores.question_id` are unchanged.

**Procedure** (only for databases that already have quizzes; a fresh DB just runs
`alembic upgrade head`):

*With the backend still running:*
1. `docker compose run --rm backend python -m scripts.plan_game_courses`. It is read-only and
   runs in a one-off container.
   - If `backend/migration_maps/game_course_map.json` (git-ignored) doesn't exist, it writes it
     plus a report, `game_course_map.report.txt` next to it (also printed). A rerun after
     deleting the map rewrites both.
   - If the file already exists, it is **never overwritten**. The script runs in check mode
     (below).
2. Delete junk quizzes with the still-existing `DELETE /admin/games/{id}`.
   - That route doesn't clear Redis, so first end any active room of the quiz with
     `DELETE /game/sessions/{sid}`, which does.
   - The report lists existing LOBBY/IN_PROGRESS sessions for this reason.
3. Review and edit the map: fill every `null`. Delete the file and rerun step 1 only if you want
   a fresh proposal.
4. **Wait for every live room to finish, or end it.** A quiz in progress must never be
   interrupted by the stop. Announce a short "no new rooms" window.
   - The backend is still the *old* code here, and its `DELETE /game/sessions/{sid}` hard-deletes
     the session **and its answers**. Use it only for rooms the report shows with 0 answers.
   - For a room that can't finish and has answers (e.g. stuck after a restart), mark it
     ABANDONED instead, which is exactly what the new `end_room` does; the report prints the
     statement: `UPDATE game_sessions SET status='ABANDONED', completed_at=NOW() WHERE id='<sid>'
     AND status IN ('LOBBY','IN_PROGRESS');`. Its Redis keys are not deleted by this; they
     expire on their own TTL (the room key after 90 minutes idle, the `session:{id}:*` keys
     24 hours after their last write).
   - The old code has none of the new guards: an answer can still write Redis keys (they
     expire the same way), and a still-connected old host could advance the room from
     ABANDONED to COMPLETED (old `complete_game` is unconditional). The risk is small: these
     rooms are stuck and their hosts gone.
   - (Found while rehearsing on the dev copy: its one stuck IN_PROGRESS room held 2 answers.)
5. Rerun step 1 (check mode). It must pass with **no active sessions**; otherwise go back to
   step 3 or 4.

*With the backend stopped:*
6. `docker compose stop backend`.
7. `mysqldump` backup.
8. Rerun step 1 (check mode) once more. This catches anything created between step 5 and the
   stop. If it fails, restart the backend and go back to step 3 or 4.
9. `docker compose run --rm backend alembic upgrade head`.
10. Deploy the backend code from §12 steps 1–5 (the upgrade point), then
    `docker compose start backend`. Never start the old backend on the migrated schema.
11. On failure: fix the cause and rerun step 9, which is idempotent. Otherwise restore the backup
    and go back to step 1.

**Map file format:** each entry records what the reviewer saw, so later changes can be detected.

```
{ "<game_id>": { "course_id": <id or null>, "title": "...", "session_courses": [<ids>] } }
```

**Check mode** (whenever the map file already exists). It compares the file with the DB and
only reports; the file (and its report file) is never modified. It fails (non-zero exit, listing every problem) if
any of these hold:
- a game in the DB is missing from the map (a new quiz)
- a map entry's game no longer exists
- a game's current title or set of session courses differs from what the map recorded (the quiz
  changed after the review)
- any `course_id` is `null` or not an existing course
- any session is LOBBY or IN_PROGRESS

It passes only when the map matches the DB exactly and no room is live. A failure means another
review: edit the file by hand. A reviewed choice is only ever replaced by a person.

**Planning script rules** (the first match wins, and each rule is terminal):
1. **Demo:** `Buzzer Demo` → `Demo Course` only if both are unique and every session of the game
   is in Demo Course. Otherwise `null`.
2. **All sessions in one course** → that course.
3. **Else** `null`.

The report shows:
- per quiz: the proposal and the rule behind it, its sessions' courses, the hosts who gain
  access, and the grant holders who lose it
- every existing LOBBY/IN_PROGRESS session, with its quiz, room code, and starter

**Upgrade:**
1. Before any DDL, run the same checks as check mode. It stops, listing every problem, if:
   - the file is missing, or the JSON is bad
   - any check-mode check fails, including any LOBBY/IN_PROGRESS session
   
   It warns on a map-vs-session course mismatch.
2. Run guarded DDL (checking `information_schema`), then write `course_id` where it is NULL, then
   set NOT NULL.
3. Snapshot every non-LOBBY session that has no snapshot yet, from its current questions.
   - `asked_at`: all rows for COMPLETED sessions; otherwise only questions with a score.

**Downgrade:** refuses while snapshots exist unless `ALLOW_SNAPSHOT_LOSS=1`.

**Readers** never fall back to live questions. A missing snapshot returns 409 and logs an error.

## 5. Screens

### Host app

| Route | Content |
|---|---|
| `/courses` | My courses (`/game/my-courses`) |
| `/courses/:id/quizzes` | Create, import, edit, export, Archive, Start room; a lock icon if `has_active_room`; a collapsed Archived section (view, export, Restore). No delete. |
| `/courses/:id/rooms` | Active rooms (below) |
| `/courses/:id/roster` | Moved from admin `RosterPage` |
| `/courses/:id/sessions` | Finished sessions; CSV and HTML; Merge guests where `can_merge_guests`. No delete. |
| `/quizzes/:id/edit` | Moved from admin `QuestionEditorPage` |
| `/game/:code/*` | Live game plus an **End game** button (hidden on game over) |

**Rooms tab:**
- Lists LOBBY/IN_PROGRESS sessions from MySQL. Each row shows the room code, quiz, starter
  ("Unknown host" if null), status, and answered (from MySQL: `answered_count`, the number of
  distinct players with a saved answer).
- Players come from Redis, or "—" when the room isn't live.
- A row with no Redis state is labeled **"Not running"** (host vanished, restart, or TTL
  expired).
- **Rejoin** needs `can_end` and `live`. **End room** needs only `can_end`, which is how stale
  rooms are cleared.
- Other hosts' rooms say "Started by X. Only they or an admin can end it."
- The confirmation names the starter and says whether answers will be kept as Abandoned or the
  room removed. A second end gets 409 if the room was kept as ABANDONED (or COMPLETED), or 404
  if the empty room was removed (a deleted row can't be told apart from one that never
  existed). The UI treats both as done.

**Editor:**
- It is read-only when `has_active_room` ("Edits locked until the room ends") or `archived_at`
  ("Restore to edit").
- A question's delete is disabled when `has_answers`.
- The API still returns 409 in each case.

### Admin app

- **Navigation:** Users · Courses · Guests · Open Host app ↗.
- **CoursesPage:** its HOST users, "N active, M archived", and a link to the course in the Host
  app.
- **UserDetailPage:** course grant and revoke. The game-grant panel is replaced by a read-only
  line per course.
- **Removed:** Games, Questions, Roster, and Sessions pages.
- **Sign-in:** under nginx all three apps share `localStorage["token"]`, so signing in to one
  replaces another's token. This is existing behavior, not fixed. On dev ports each app signs in
  separately.

### Player app

- `/join` gets a **Past quizzes** link.
- New `/signin` (NetID or local):
  - NetID sets `sessionStorage.postLoginPath = "/past"`.
  - `LoginPage` uses it, then clears it.
  - The OAuth allowlist is unchanged (`/player/login`).
- `/past`:
  - With no token, go to `/signin`.
  - A 403 (guest) says a NetID or local account is needed.
  - Otherwise, list finished sessions with score/max and an Abandoned label.
- `/past/:id`: played questions in full; unreached ones show only "Question N: Not played".
- The recap rendering is extracted from `GameOverPage.tsx` and shared.
- A room ending → `game_abandoned` → the existing player handler goes to `/join` (verified). The
  host handler changes from `/home` to `/courses` (§11).

## 6. API

### Helpers (`services/access_service.py`)

- `assert_course_host(course_id)`: admin or HOST, else 403; 404 if the course is missing.
- `get_game_for_host(game_id, for_update=False)` and `get_session_for_host(session_id)`: check
  via the record's own course.
- `get_question_in_game` and `get_roster_entry_in_course`: parent ownership.
- `assert_session_starter_or_admin`: "Only the host who started this session or an admin can do
  this."
- `assert_host_can_use_game` is removed in step 8.

### Services (`services/quiz_service.py` + existing)

- **Operations:** quiz create/update/archive/restore/export/import; question CRUD/reorder;
  roster; session lists; room list; `end_room`.
- **Row lock:** every quiz mutation and `create_room` take the game row `FOR UPDATE` first, which
  serializes room creation against edits.
  - Every read a mutation makes after that lock is a locking read (the active-room check and
    answered-question check `FOR SHARE`; the question rows, `MAX(order_index)` source, and
    reorder id set `FOR UPDATE`). The request's REPEATABLE READ snapshot was taken at its
    auth lookup, before the lock wait, so a plain read would miss a room, question, or answer
    committed meanwhile (e.g. two concurrent creates getting the same `order_index`).
  - Locks are always taken game row first, then its children, so these can't deadlock with
    each other.
- **409 guards:**
  - active room (every mutation except restore)
  - archived (create room, edits)
  - double archive or restore
  - answered question delete
- **Question delete** reindexes the rest to `0..n-1`.

### Host routes (`routers/host.py`, `/host`)

| Route | Check → response |
|---|---|
| `GET /courses/{cid}/games?archived=` · `POST /courses/{cid}/games` · `POST /courses/{cid}/games/import` | course host → `GameSummary[]` / `GameSummary` / `{game_id}` |
| `GET/PUT /games/{gid}` · `POST /games/{gid}/archive` · `/restore` · `GET /games/{gid}/export` | game host → `GameSummary` / file |
| `GET/POST /games/{gid}/questions` · `POST .../reorder` · `PUT/DELETE .../questions/{qid}` | game host + parent check → `QuestionItem` |
| `GET/POST /courses/{cid}/roster` · `POST .../roster/import` · `PATCH .../roster/{rid}` | course host + parent check |
| `GET /courses/{cid}/rooms` | course host → `ActiveRoom[]` |
| `POST /sessions/{sid}/end` | session host + starter/admin → `{outcome: abandoned\|removed}` |
| `GET /courses/{cid}/sessions` · `GET /sessions/{sid}/export` · `/report` | course/session host → `SessionItem[]` / CSV / HTML (409 unless COMPLETED/ABANDONED) |

### Admin routes (all `require_admin`)

- **Kept:**
  - courses (`AdminCourse`)
  - users (`course_summary` instead of `game_access`)
  - guests
  - course grants
  - guest merge
  - compatibility routes via the services: `/admin/games` (POST needs `course_id`), questions,
    export, import (needs `course_id`), roster
- **Retired in step 8:**
  - `DELETE /admin/games/{id}` → 405
  - game-grant routes → 404
  - `/admin/sessions*` → 404

### Game routes

- `POST /game/rooms`: the course comes from the quiz; `course_id` is optional and must match
  (422).
- `/game/my-games`: course-based until step 8, then 404.
- `/game/sessions/{sid}/export`: `get_session_for_host`; 409 unless the session is finished.
- Guests and merge: starter or admin; 409 unless the session is finished. The host merge is scoped to the session: only that session's guest scores move; the NetID must be active on the
  course roster and have an account; a NetID that already answered in the session is 409; the
  guest row is deleted only when it has no scores left. The admin Guests-page merge keeps its
  pre-T4 behaviour (no session; all the guest's scores; promotes the guest if no account exists).
- `DELETE /game/sessions/{sid}`: an alias of the end route.

### Player routes (`routers/player.py`, `require_user`)

- `GET /player/past-quizzes`: COMPLETED/ABANDONED sessions with the user's score rows, newest
  first.
- `GET /player/past-quizzes/{sid}`: 404 unless finished and attempted.
  - Unreached rows are exactly `{order_index, played: false}`.
  - `answer_data` is never returned raw.

### Shapes

```
GameSummary  { id, course_id, title, description, max_players, question_count,
               archived_at|null, has_active_room, created_at }
QuestionItem = QuestionResponse + { has_answers }
ActiveRoom   { session_id, room_code, status, game_id, game_title, host_user_id|null,
               host_display_name|null, created_at, live, player_count|null, has_answers,
               answered_count, can_end }
SessionItem  { session_id, room_code, status, game_id, game_title, host_user_id|null,
               host_display_name|null, created_at, completed_at, player_count, can_merge_guests }
AdminCourse  = CourseResponse + { hosts[], active_quiz_count, archived_quiz_count }
CourseAccessSummary { course_id, name, semester, role, active_quiz_count, archived_quiz_count }
PastQuizItem { session_id, game_title, course_name, status, completed_at, score, max_score }
PastQuizDetail { ...PastQuizItem, questions: [ {order_index, played: true, prompt, type,
                 gradingType, config, playerAnswer|null, pointsAwarded, maxPoints, answerReveal}
                 | {order_index, played: false} ] }
```

### Ending a room

**`end_room`**, the one path for both buttons, the admin, and the host-disconnect timer, in this
exact order:
1. `SELECT … FROM game_sessions WHERE id=? FOR UPDATE`. If the session isn't LOBBY or
   IN_PROGRESS, return 409.
   - The routes run `get_session_for_host` first, so ending a room that was already
     **removed** returns 404 there; an already-ended (COMPLETED/ABANDONED) room returns 409.
2. **Fresh answer check, after the lock:**
   `SELECT 1 FROM session_scores WHERE session_id=? LIMIT 1 FOR SHARE`.
   - A locking read sees the latest committed rows, not the REPEATABLE READ snapshot taken
     earlier in the request. A plain `SELECT` here could miss an answer committed just before the
     lock.
3. **With answers:** a compare-and-set to ABANDONED, with `completed_at = now`.
4. **Without answers:** delete the row.
   - An answer insert racing this waits on the parent-row lock (the FK check), then fails
     because the session is gone. The player gets an error rather than a silently lost answer.
5. **Commit** (`end_room` commits itself rather than relying on `get_db`'s end-of-request
   commit).
6. Only after a successful commit: delete the Redis state, then call the room-ended listeners.
   - If the commit fails, Redis and the connected clients are untouched, and the room stays
     live.

**Notification without breaking layering:**
- `game_service` keeps a `room_ended_listeners` list.
- The gateway registers one listener at import. In this order, it:
  1. cancels this process's question timer and host-abandon task for the session, **skipping
     `asyncio.current_task()`**. When the disconnect timer is what called `end_room`, the
     listener runs inside that timer's task and must not cancel itself.
  2. emits `game_abandoned` to the players' room (`room_code`) and the host's room
     (`{code}:host`)
  3. calls `close_room` on both
- `user:{id}` rooms are per-user, not per-game, so they are left alone.
- Dependencies stay `routers → services ← websocket`.
- In production, `AsyncRedisManager` delivers the emit and `close_room` across workers.

**Nothing can recreate an ended room:**
- **Answers:** `on_submit_answer` rejects anything not IN_PROGRESS (verified). Its check reads
  the request's snapshot, so `record_answer` re-checks after the insert with a locking read
  (`FOR SHARE` on the session row) and rolls back with 409 before any Redis write if the room
  ended while the insert waited on the lock. If the end *removed* the room (no answers), the
  insert fails its FK check instead; that `IntegrityError` is rolled back and raised as the
  same 409.
- **`start_game`:** LOBBY → IN_PROGRESS becomes a compare-and-set
  (`UPDATE … SET status='IN_PROGRESS' WHERE id=? AND status='LOBBY'`), with the snapshot written
  in the same transaction.
  - If 0 rows change (the room was ended or removed meanwhile), it raises 409.
  - The gateway then emits an error to the host and writes no Redis state.
  - After its commit, it re-reads the status in a fresh transaction right before its Redis
    write (as `host_advance` does) and raises 409 if the room was ended meanwhile.
- **`host_advance`:** immediately before each Redis write (`set_room_state`, current-question and
  answer keys), re-read the session status from MySQL **in a fresh transaction** (commit, then
  read with `populate_existing`). Inside the same REPEATABLE READ transaction a plain re-read
  would return the stale snapshot and miss `end_room`'s commit. If it isn't IN_PROGRESS, stop
  without writing or emitting.
  - The window left between that re-check and the write is milliseconds. A key written in it
    expires on its normal TTL (90 minutes idle for the room key, 24 hours for
    `session:{id}:*`). Accepted.
- **`complete_game`:** becomes a compare-and-set (`… WHERE status='IN_PROGRESS'`), so it can't
  overwrite ABANDONED.
- **Leftover timers on other workers:** the listener only cancels tasks in its own process.
  - A leftover `_question_timer_task` still emits `answer_phase_ended` to `{code}:host` first
    (verified). After `close_room` that room has no sockets, so the emit reaches no one.
  - It then finds no room state and skips the auto-lock and `QUESTION_LOCKED` broadcast.

## 7. Downloads, import/export, demo

- **CSV and HTML** read `session_questions`. Unasked questions are empty in the CSV and omitted
  from the HTML.
- **Quiz JSON** is unchanged (`version: 1`, no course field). Import targets the course from the
  URL or the form field.
- **`seed_demo.py`** creates Demo Course, then the quiz with `course_id`.

## 8. Risks

1. **Guests** can join any room by code, which breaks R4's literal wording. It's documented as an
   exception; guests see nothing saved.
2. **"Game access" wording:** mitigated by the mapping table and the admin summary.
3. **The Host link** must stay visible for "full access".
4. **A wrong-course quiz** is fixed by export → import → archive.
5. **Legacy snapshots** use today's questions.
6. **A forgotten room** blocks other hosts until the starter or an admin ends it. There is no
   auto-expiry.
7. **The shared token key** across apps (existing).

## 9. Test cleanup

- `tests/integration/conftest.py` names courses `Test Course <8 hex>` (verified, lines 190/197).
- The teardown runs `docker compose exec -T backend python -m scripts.cleanup_test_quiz
  --game-id <id>`.
  - It deletes the quiz's Redis state, scores, snapshots, sessions, questions, grants, and the
    quiz.
  - It keeps the course and users.
  - It refuses unless in dev mode and the course name matches `^Test Course [0-9a-f]{8}$`.
- Later change: `docker_stack` also runs `cleanup_test_quiz --sweep` at the start and end of
  each session. It removes the suite's courses (semester `Test`, named `Test Course <8 hex>` or
  `Real-looking course <6 hex>`) with all their rows, then the suite's users (`t4_*` accounts,
  `t4?<6 hex>` NetIDs, `testplayer<n>.<6 hex>@example.com` guests). A user still linked to other
  data is kept. Rules are unit-tested in `tests/unit/test_cleanup_sweep_rules.py`.
- **Shared users** (`t4_host_a`, …): the password is reset to a random per-run value, and the
  fixture refuses a non-localhost `base_url`.

## 10. Coordination with T7/T8

- **Editor:** it moves to Host in step 6. New question types and images go into the Host copy
  only.
- **Validation:** it stays in `schemas/admin.py`, called by `quiz_service`.
- **Merge point:** step 3.
- **Migrations:** a linear chain; theirs doesn't depend on the map file.
- **Images:**
  - use the same helpers, parent check, and locks
  - can't be hard-deleted while any snapshot uses them
- **New types** need:
  - a reveal-logic branch (4 copies, `services/CLAUDE.md`)
  - HTML report rendering
  - shared recap rendering, with a text fallback
- **Import:** if the version is bumped, accept both versions.

## 11. Files

| File | Change |
|---|---|
| `migrations/versions/<new>.py`, `scripts/plan_game_courses.py`, `.gitignore` | §4 |
| `models/game.py`, `session.py`, `__init__.py`, `migrations/env.py` | new columns and model |
| `services/access_service.py`, `quiz_service.py` (new), `roster_service.py` | §6 |
| `services/game_service.py` | snapshot, `create_room`, `end_room`, listeners, compare-and-set in `start_game` and `complete_game` |
| `services/export_service.py`, `report_service.py` | read snapshots |
| `websocket/gateway.py` | `asked_at`; register listener; disconnect timer → `end_room` (only when no other host socket for the room is still connected); `host_advance` status re-check before Redis writes |
| `routers/host.py`, `player.py` (new), `admin.py`, `game.py`, `main.py` | §6 |
| `schemas/admin.py`, `game.py`, `host.py`, `player.py` | shapes |
| `backend/scripts/cleanup_test_quiz.py` | §9 |
| host frontend | course pages, Rooms tab, moved editor/roster/sessions, End game, `api.ts` verbs; `GameLayout.tsx` `game_abandoned` → `/courses` |
| admin frontend | navigation, CoursesPage, UserDetailPage; delete 4 pages |
| player frontend | JoinPage, LoginPage, SignIn, Past pages, shared recap |
| `seed_demo.py`, `conftest.py`, README, TODO, context `CLAUDE.md`s | updates |

## 12. Steps

Every step keeps all three apps working. The migration is **not applied on its own**, because
the new NOT NULL `course_id` would break the old quiz-create route and old code wouldn't write
snapshots.

1. Write the migration, planning script, and models. Rehearse §4 **on a copy** of dev data only,
   including a failed rerun.
2. Access and quiz services (parent checks, locks, guards). The admin routes switch to them.
   Update `seed_demo` and `conftest` for `course_id`. The old admin `GamesPage` create and import
   forms get a course dropdown so they keep working until step 7 deletes the page.
3. Host routes, `end_room` and the listener, and the game-route changes, all additive. **T7/T8
   merge point.**
4. Snapshots, `asked_at`, and the downloads reading them.
5. Cleanup script and fixtures; the existing suite passes.

**Upgrade point (after steps 1–5):**
- The migration is applied to a real database only here, with the §4 procedure (backend
  stopped), deployed together with the backend code from steps 1–5 and the step 2 `GamesPage`
  dropdown.
- Before this point, those steps run only against rehearsal copies.
- After it, steps 6–11 need no schema change.

6. Host frontend.
7. Admin frontend.
8. Retire the old routes and helpers.
9. T4 integration tests.
10. Past Quizzes.
11. Docs.

## 13. Tests

- **Access:**
  - Host A works in course 1 and gets 403 in course 2.
  - Players and guests get 403 on `/host/*`.
  - Admin passes everywhere.
  - A host gets 403 on admin-only routes.
  - An old grant alone gives 403.
- **Parent ownership:** a cross-course question edit or delete, or a roster patch, returns 404
  and changes nothing. A reorder with a foreign id returns 409. The same holds on the admin
  routes.
- **Retired routes:** `DELETE /admin/games/{id}` returns 405; the others return 404.
- **Rooms:**
  - The course comes from the quiz.
  - A mismatched `course_id` returns 422.
  - Another course's host gets 403.
  - `POST /admin/games` without a course returns 422.
- **Lock:**
  - With a LOBBY room open, edits return 409; after it ends they succeed.
  - Edits queued on the game-row lock while a LOBBY room commits (the test holds the lock
    itself, inserts the room, then releases it) all return 409 and change nothing.
  - N question creates queued on the lock get unique, contiguous `order_index` values.
- **Archive:**
  - It leaves the default list.
  - Starting a room and editing return 409.
  - Export works.
  - Restore allows a room again.
  - It returns 409 during a LOBBY room, and on a double action.
- **Question delete:** an unanswered question returns 204 and the rest are reindexed. An answered
  one returns 409.
- **Ending rooms:**
  - Host B gets `can_end=false` and 403.
  - Ending a LOBBY room removes it and notifies players.
  - Ending an IN_PROGRESS room with answers:
    - sets ABANDONED and `completed_at`, and keeps the scores
    - notifies players
    - rejects later answers
  - The admin can end any room.
  - A stale room shows `live=false` and can still be ended.
  - The Rooms tab's `answered_count` is 0 before any answer and counts distinct players after.
  - A `host_advance` racing the end still leaves ABANDONED and recreates no Redis room key.
  - A `start_game` racing the end of a LOBBY room returns an error and writes no Redis state.
  - An answer committed just before the end is kept, and the session becomes ABANDONED, not
    removed.
  - A double end returns 409 for an abandoned room, and 404 for a removed one.
  - The disconnect timer removes an empty room and abandons one with answers, and connected
    players still receive `game_abandoned` (the listener didn't cancel its own task).
  - Closing one of two host sockets (two tabs, or a reconnect whose old socket drops later)
    is not a disconnect: no lock, no `host_disconnected`, room still IN_PROGRESS after the
    grace. (These timer tests skip unless the backend runs with `HOST_GRACE_SECONDS<=10`.)
  - After `game_abandoned`, the host lands on `/courses`.
- **Downloads:**
  - Host B gets host A's CSV and HTML, and the totals match: the HTML report's figures and
    both Canvas formats (single-column and per-question) agree with the raw CSV totals.
  - The other course gets 403.
  - Unasked questions are handled.
- **Guest merge:** host B gets 403; the starter and the admin succeed.
- **Roster:** an upload works, and a deactivated NetID can't join.
- **Import:** an unmodified sample file imports; the wrong course gets 403.
- **Snapshot:** edit after a session, and the downloads and Past Quizzes still show the original.
- **Past Quizzes:**
  - A player sees only their own.
  - Another player's session, or an IN_PROGRESS one, returns 404.
  - Unreached rows are exactly `{order_index, played:false}`.
  - A guest gets 403.
- **Migration:**
  - The demo quiz goes to Demo Course only when its sessions are all in Demo Course.
  - A one-course quiz is proposed for that course.
  - A quiz with no sessions is proposed as `null`.
  - Duplicate `Buzzer Demo` titles or duplicate Demo Courses leave the demo quiz `null`.
  - The report shows access gained and lost, each user on the correct side.
  - A new proposal also writes the report file; check mode never modifies the map or report.
  - A bad map stops the migration before DDL, listing every problem.
  - With an existing reviewed map, the planner leaves the file byte-for-byte unchanged.
  - Check mode fails on each of these: a quiz added after the review, a mapped quiz deleted, a
    changed title, and a new session in another course.
  - Check mode and the migration both refuse while any LOBBY/IN_PROGRESS session exists.
  - The rerun after a partial failure succeeds (after the first DDL, the NOT NULL step, the
    index step, or the FK step), and never duplicates snapshot rows.
  - Every non-LOBBY session gets snapshots.
  - The downgrade guard works.
  - An empty DB needs no map.
- **Cleanup:** the quiz and its Redis state are gone, while the course and users remain. The
  script refuses a non-test course, and the fixture refuses a non-localhost URL.

## 14. Alternatives rejected

| Alternative | Why not |
|---|---|
| Per-quiz "delegated host" grants | Reaches a quiz without course membership |
| Convert old grants to HOST | Silently widens access |
| Auto-assign inside the migration / "Unassigned" course | Unreviewed, irreversible |
| Hard delete | Erases results; archive covers it |
| Any host ends any room | The team chose starter or admin |
| Router imports `websocket/` | Breaks layering |
| Socket-only `host_end_game` (rev 5) | Rooms-tab ends didn't notify players |
| Redis pub/sub notification | More machinery; dev has no socket Redis manager |
| Stale-room auto-expiry | New background job for an edge case |
| Live-question fallback | Shows the wrong history |
| Lock played quizzes instead of snapshotting | Typos could never be fixed |
| Move quizzes between courses | Mixes session histories |
| HTTP test-cleanup route | Could erase real data |
| Per-app token keys | Outside T4 scope |
| Shared frontend package | No setup exists; moving the pages is simpler |

## 15. Open

None. The room-ended listener (§6) replaced the rev 5 socket event.

## 16. Goldfish log

**Round 1:**
1. There was no fix path for unresolved quizzes → reviewed map.
2. Past-quiz detail leaked live answers → it now requires a finished and attempted session.
3. A stale room locked quizzes → the Rooms tab plus an end rule.
4. An end raced the game loop → compare-and-set.
5. `DELETE` returned 405, not 404 → fixed.
6. CoursesPage data was missing → `AdminCourse`.
7. The demo rule mismatched courses → tightened.
8. Response shapes were missing → added.
9. Old sessions had no snapshot → backfill.
10. Room creation raced edits → row lock; question delete now reindexes.
11. Routes retired too early → moved to step 8.
12. The fixture claim was unverified → verified.
13. `completed_at` on abandon was unconfirmed → confirmed.

**Round 2:**
- B1: child ids weren't tied to their parent → parent-ownership rule.
- B2: the demo rule fell through → rules are terminal.
- B3: the migration was unreviewed and not rerunnable → planning script, access report,
  validated map, guarded DDL, backup, downgrade guard.
- B4: stale rooms were under-specified → MySQL-driven list, `live` flag, admin-only edge cases,
  the timer uses `end_room`.
- B5: notification had gaps → one end path plus the listener.
- B6: the backend ran during the migration → stop it first.
- Also fixed:
  - unplayed questions leaked → only "Not played"
  - test-user credentials → random per run, localhost only
  - token collision → documented
  - `postLoginPath` → cleared after use
  - the starter-check message → generic
  - a double end → 409 treated as done

**Round 3:**
- B1: the procedure called an HTTP route after stopping the backend → junk is deleted and stuck
  rooms are ended while it runs, then a final re-plan is done after the stop (§4). The report
  lists active sessions.
- B2: the listener could cancel the disconnect timer's own task → it skips
  `asyncio.current_task()`.
- B3: `end_room` used a stale answer check and an unordered commit → a locking fresh read after
  the session lock; commit, then Redis, then listeners.
- A racing `host_advance` or `start_game` could recreate an ended room → a status re-check
  before Redis writes; `start_game` compare-and-set.
- Also fixed:
  - `close_room` scope → players' and host rooms
  - the timer claim → corrected
  - the host redirect → `/courses`
  - "HOST on C" → defined
- Follow-up:
  - The planner rerun could overwrite the reviewed map → it never overwrites; check mode
    detects new, deleted, or changed quizzes.
  - The migration could interrupt live quizzes → all rooms must finish or be ended first,
    enforced by check mode and the migration.
