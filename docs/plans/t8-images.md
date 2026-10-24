# Design: Image Support in Questions (T8)

Status: rev 6 (three Goldfish reviews addressed; implemented; implementation notes in §18 rev 5–6).
Related: T4 `docs/design-host-admin.md` (course access, quiz/room locking), T7
`docs/plans/t7-new-question-types.md` (§8 Pin Drop image rule), T9 `docs/plans/ui-themes.md`
(§3.10 images and canvas, §12 design system).

## 1. Problem

T8 requires:

| Requirement | Meaning |
|---|---|
| Upload | Images can be uploaded through the admin or host interface and **stored in the database** (not on disk, not in a file store). |
| Question prompt | An image can appear in a question prompt alongside text. |
| Question options | An image can serve as a question option (e.g. multiple choice). |
| Canvas | Images can be placed on the canvas question type (T7 `pin_drop`). |
| Management | Images can be viewed, replaced and deleted through the interface. |
| R8 | "The feature holds up under realistic use (sensible handling of invalid input and repeated use)." |

Also required by other tasks: integration tests (T5), themed screens with light/dark
screenshots in `docs/ui/` (T9), and **unmodified version-1 game files keep importing** (T6).

Everything else below is a **design choice** and is marked as such where it matters.

## 2. Facts this design relies on

Confirmed in the code (2026-10-02, `main` at `c540326`):

- C-1. All quiz/question mutations **and** room creation (`game_service.create_room`) load the
  quiz with `access_service.get_game_for_host(..., for_update=True)` (13 call sites), which
  locks the `games` row. `update_question` and `delete_question` then lock the `questions` row
  (`quiz_service.py` `get_question_in_game(..., for_update=True)`).
- C-2. Quiz creation and quiz import take **no** row lock (only `assert_course_host`).
- C-3. `games.course_id` is never updated after creation.
- C-4. `courses` rows are never locked today.
- C-5. Locking reads (`FOR UPDATE` / `FOR SHARE`) see the latest committed rows under MySQL's
  REPEATABLE READ; T4 relies on this (`_locked_questions`, `has_active_room`).
- C-6. Login returns the access token in JSON; the frontends keep it in `localStorage` and send
  `Authorization: Bearer`. The only cookie is `refresh_token` with `path=/api/auth`. So a
  browser-initiated request (`<img src>`) carries **no credentials**.
- C-7. Players are often guests; `require_user` rejects GUEST.
- C-8. `start_game` copies each question's `config` into `session_questions` unchanged; results,
  Past Quizzes, CSV and HTML report read that snapshot. Live play reads the live `questions` row.
- C-9. Both quiz import routes read the whole upload with `await file.read()`, uncapped.
  Nginx (`nginx/nginx.dev.conf`, `location /api/`) sets no `client_max_body_size` (default 1 MB).
- C-10. Pillow is **not** installed. `python-multipart` is. MySQL `max_allowed_packet` is 64 MiB.
- C-11. The player app's `api` client has only `get`/`post` (JSON); the host client has `postForm`.
- C-13. Today's `QuestionCreate` option rules check only that `options` is a list of ≥ 2 entries:
  no non-empty-text and no duplicate-text rule. The host editor drops `multi_select` options whose
  text is empty (`formToPayload`), so it would drop an image-only option.
- C-14. Starlette parses a multipart body (spooling files to disk) **before** the route runs;
  the dev servers' Vite proxy sends `/api` straight to `localhost:8000` without nginx.
- C-15. `end_room` locks and deletes only session rows; it takes no question-row locks.
- C-12. Integration fixtures create a fresh `Test Course <8 hex>` per test;
  `backend/scripts/cleanup_test_quiz.py` refuses anything outside such a course.

Assumed (documented InnoDB behavior), verified by tests in §12:

- A-1. Inserting a child row takes a **shared lock on its parent row** (foreign-key check).
- A-2. The lock order in §8 leaves no cycle among the listed paths. This is analysis, not proof.

## 3. Decisions

1. **One new table `images`**, owned by a **course** (design choice: matches T4's course-based
   access). Bytes stored in MySQL `MEDIUMBLOB`. Migration `005`.
2. **Viewing by unguessable public URL** `GET /api/images/{public_key}` (design choice; see §6).
3. **Questions reference images by ID inside `config`**: `promptImageId`, `optionImageIds`,
   `imageId` (§5). No new question columns.
4. **Replace creates a new version** (new row, new ID, new URL); current quizzes are re-pointed;
   snapshots keep the old version (§7).
5. **Delete is blocked while any quiz uses the image**; otherwise soft-delete if a session still
   needs it, hard-delete if nothing does (§7).
6. **Course-row lock first** for every quiz/question/image mutation and room creation (§8).
7. **Export version 2 only for quizzes that use images**; version 1 otherwise, unchanged (§9).
8. **HTML report embeds images** as data URIs so it stays self-contained (design choice).
9. **Still images only**: decoded with Pillow; animated images rejected (§4).

## 4. Image files: limits and validation

All sizes in bytes; 1 MiB = 1,048,576. Constants live in one module,
`backend/app/schemas/images.py`, imported by upload, import and export.

| Constant | Value |
|---|---|
| `MAX_IMAGE_BYTES` | 5,242,880 (5 MiB) per image file |
| `MAX_IMAGE_SIDE` | 4096 px (width and height) |
| `MAX_IMAGE_PIXELS` | 16,777,216 (= 4096², Pillow's own guard, set once at module import) |
| `MAX_COURSE_IMAGES` | 200 **current** images per course |
| `MAX_COURSE_IMAGE_BYTES` | 524,288,000 (500 MiB) of bytes of **current** images per course |
| `MAX_REQUEST_BYTES` | 33,554,432 (32 MiB); backend-wide body cap (below) |
| `MAX_IMPORT_BYTES` | 31,457,280 (30 MiB) per quiz import/export file |
| `MAX_BUNDLE_IMAGES` | 20 images per import/export |
| `MAX_BUNDLE_IMAGE_BYTES` | 20,971,520 (20 MiB) decoded image bytes per import/export |
| Nginx `client_max_body_size` (`location /api/`) | `32m` = 33,554,432; a ceiling above every backend cap plus multipart overhead |

**What bounds a request, honestly:** Starlette parses the whole multipart body (spooling the
file to disk) before the route runs (C-14), so the in-route `cap + 1` read only bounds memory,
not what the server receives. The server-side bound is a small ASGI middleware in `main.py` that
returns **413** before parsing when `Content-Length` exceeds `MAX_REQUEST_BYTES`, with the app's
usual error body `{"error": "PAYLOAD_TOO_LARGE", "message": "Request is larger than 32 MiB"}` (the
`BuzzerError` shape, so the host's `toError` shows the message); it applies with
or without nginx, so the dev Vite proxy is covered too. Nginx's `client_max_body_size 32m` is the
same limit at the proxy. Requests without `Content-Length` (chunked) are bounded only by nginx in
the built app; browsers send `Content-Length` for these uploads. Inside the route, the file is
then read with `cap + 1` and rejected with **413** if larger.

**Per-course storage** (R8 "repeated use"): the caps count **current** images only (§5:
`replaced_by_id IS NULL AND deleted_at IS NULL`), which are exactly the images a host can see and
delete in the Images tab, so the 409 advice is always actionable. Retained history rows (replaced
or soft-deleted versions kept for archived quizzes and past sessions) do **not** count, so a
course that has been played can never become permanently full.

- **Upload / v2 import**: current count + new images ≤ `MAX_COURSE_IMAGES` and current bytes + new
  bytes ≤ `MAX_COURSE_IMAGE_BYTES`, else **409 "Course image storage is full; delete unused
  images"**.
- **Replace**: the old version stops being current and the new one becomes current, so the count is
  unchanged; check only current bytes − old size + new size ≤ `MAX_COURSE_IMAGE_BYTES`. A replace at
  the count cap therefore succeeds.

Counted under the course lock with a locking read, so the totals are exact. Retained history is
bounded only by how much is replaced and played (accepted, §17).

**Validation of every image** (upload, replace, version-2 import), in `image_service.load_image`:

1. Size ≤ `MAX_IMAGE_BYTES`; non-empty.
2. `Image.open` (reads the header only). `PIL.UnidentifiedImageError` (no decoder recognizes the
   bytes: SVG, text, PDF…) → **415 "Unsupported image type"**. `Image.DecompressionBombError` →
   **422 "Not a valid image"**.
3. Format from the decoder (`img.format`) must be `PNG`, `JPEG`, `WEBP` or `GIF`, checked **before**
   decoding; any other format Pillow recognizes (e.g. BMP, TIFF) → **415 "Unsupported image
   type"**. **`MPO`** (a JPEG that embeds a second image, which many phone cameras write) counts
   as **JPEG**: browsers show its primary image, so it is stored as `image/jpeg`. The stored
   `content_type` comes from this format, never from the request header or filename.
4. Check `img.size` from the header: `1 ≤ width, height ≤ MAX_IMAGE_SIDE`, else **422 "Image is
   larger than 4096 px"**, *before* any full decode. Because `MAX_IMAGE_SIDE² = MAX_IMAGE_PIXELS`,
   this keeps every decoded image within the pixel limit; no warning filter is needed (a per-call
   `warnings` filter would change process-wide state from a threadpool thread).
   `Image.MAX_IMAGE_PIXELS` is set **once at module import** as a second guard.
5. `getattr(img, "n_frames", 1) > 1` → **422 "Animated images aren't supported"** (not applied to
   MPO, whose extra frame is an embedded still image, not animation). Counting frames seeks
   through the file, so an error there (a malformed GIF) is **422 "Not a valid image"**.
6. `img.load()` (full decode): any decoder error (a truncated or corrupt file of a supported
   format) → **422 "Not a valid image"**.
7. Store the **original bytes** (no re-encoding), plus `width`, `height`, `size_bytes`.

So **415** means "not one of the supported formats" and **422** means "a supported format, but
this file is too large in pixels, animated, or broken".

**Off the event loop:** `load_image` is CPU-bound, so routes call it with
`starlette.concurrency.run_in_threadpool`, and always **before** taking the course lock (§8),
so the lock is held only for the short database work.

`filename`: the upload's name (or a v2 bundle entry's `filename`), stripped of any path,
trimmed, max 255 characters; empty, missing or not a string → `image`.

**Orientation:** stored `width`/`height` are Pillow's values, which ignore JPEG EXIF rotation;
browsers apply it. They are informational only: every client layout (notably `PinGrid`, §10)
uses the loaded element's `naturalWidth`/`naturalHeight`.

## 5. Database and question references

**Table `images`** (migration `005_images.py`):

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `course_id` | INT NOT NULL, FK `courses.id` (no `ondelete`), indexed | owner. Same as `games.course_id` and `game_sessions.course_id`: a course that has images cannot be deleted (there is no course-delete route today) |
| `public_key` | CHAR(36) NOT NULL, unique | `uuid4`, one per row/version |
| `filename` | VARCHAR(255) NOT NULL | display only |
| `content_type` | VARCHAR(20) NOT NULL | `image/png`, `image/jpeg`, `image/webp`, `image/gif` |
| `size_bytes`, `width`, `height` | INT NOT NULL | |
| `data` | MEDIUMBLOB NOT NULL | mapped with SQLAlchemy `deferred()`: list, count, URL-map, reference-scan and lock queries never load it; only the view route, export and report do |
| `uploaded_by` | VARCHAR(36) NULL, FK `users.id` ON DELETE SET NULL | |
| `created_at` | DATETIME | |
| `replaced_by_id` | INT NULL, **no foreign key**, indexed | NULL = never replaced. A plain int (like `session_questions.question_id`) so hard-deleting a newer version never blocks, and never turns an older version "current" again: once set, it stays set |
| `deleted_at` | DATETIME NULL | NULL = not deleted |

"**Current**" = `replaced_by_id IS NULL AND deleted_at IS NULL`.

**References in `Question.config`** (values are image IDs; all optional):

| Field | Allowed on | Shape |
|---|---|---|
| `promptImageId` | all six types | positive int |
| `optionImageIds` | `multiple_choice`, `multi_select` | list, **same length as `options`**, each positive int or `null` |
| `imageId` | `pin_drop` | positive int (canvas background, T7 §8 rule) |

"Positive int" means a JSON integer ≥ 1: Python `int` and **not `bool`** (`True` is an `int` in
Python) and **not `float`** (`1.0` is rejected), the same test as T7's `is_grid_int`.

**Normalized on every write** (create, update, import), before validation and storage: a
`promptImageId` or `imageId` that is `null` is removed; an `optionImageIds` whose entries are all
`null` is removed. So a stored `config` has an image field **only if it references at least one
image**, which is exactly the export's version-1/2 test (§9).

**Option text with images** (rule added to `validate_image_refs`): when a question has
`optionImageIds`, **each option must have non-empty text (after trimming) or a non-null image**;
otherwise 422 "Option B needs text or an image". Whitespace-only text counts as empty. Questions
**without** `optionImageIds` keep today's rule (C-13: no text rule), so legacy questions are
unaffected. There is no duplicate-text rule today and none is added. **Display label** of an
option with empty text, everywhere option text is printed: **"Option A"**, "Option B"… (letter by
index), produced by one helper per app (`optionDisplay(text, index)`) and the same rule in
`report_service`.
This widens T7's "`pin_drop` config must be `{}`" to "may contain only `imageId` and
`promptImageId`"; `closest_guess` config may contain only `unit` and `promptImageId`.
`config` stays public (players receive it); `answer_data` is unchanged.

**Two validation layers:**

- **Shape** — `validate_image_refs(type, config)` in `schemas/admin.py`, called from
  `QuestionCreate.validate_structure` for every type: the rules in the table, plus "field not
  allowed on this type" and the `optionImageIds` length rule.
- **Database** — `image_service.check_question_images(db, course_id, config, stored_config)` in
  `quiz_service`, after the course lock (§8), reading `images` with a **locking read**
  (`FOR SHARE`). The request's REPEATABLE READ snapshot can predate the lock wait (the access check
  ran first), so a plain read could show an image deleted or replaced meanwhile as current. Same
  for every "is current" check in replace and delete (`FOR UPDATE` on the row being changed).
  Each ID exists and belongs to the quiz's course,
  and is **current**, *unless* that ID is **already present anywhere in the stored question's image
  fields** (an **unchanged reference**; any field, any option index, so reordering or moving an
  image within a restored archived quiz, which may hold older versions, still edits normally).
  Failure → 422 naming the field.

**How each error reaches the host** (C-13's editor and T7 spec §9 P2: the host `toError` shows
a string `message` or string `detail`, but a FastAPI request-validation 422 has a *list* `detail`
and shows only "HTTP 422"):

| Error source | Response | What the host sees |
|---|---|---|
| Shape rules inside `QuestionCreate` on **create** (request validation) | 422, list `detail` | Would be "HTTP 422", so the **editor prevents them**: the picker offers only current course images, keeps `optionImageIds` aligned with options (§10), and checks the option-text rule before Save, with an inline message (like T7's `newTypeProblem`) |
| Shape rules on **update** | 422 via `_validate_merged_update`, string `detail` | The message |
| Database layer (`check_question_images`: other course, deleted, not current) | `HTTPException(422, detail="<field>: <reason>")`, string | The message |
| Image upload, replace, delete, storage cap, import, export | `HTTPException` / `ConflictError` with a string message | The message |
| Request too large (middleware) | 413 `{error, message}` | The message |

**When they run:**

- Create, import: always (both layers).
- Update, **any type**: build the merged question (stored fields overlaid with the non-`None`
  request fields, as `_validate_merged_update` already does) and run both image layers whenever
  the stored **or** merged question has an image field. For the four original types, **only the
  image checks** run, so their other pre-T8 update behavior is unchanged; T7 types keep their full
  merged validation. This covers removing images (the new `config` omits them), changing type
  (leftover `optionImageIds` on `true_false` → 422) and changing the option count without
  updating `optionImageIds` (length mismatch → 422).

**Resolving IDs to URLs.** Clients never build URLs from IDs. Every payload that carries a
question `config` also carries a map from each referenced image ID (as a string) to
`"/api/images/{public_key}"`, computed by one query `image_service.image_urls(db, configs)`.
Key name follows each payload's existing convention: **`image_urls`** in REST shapes (snake_case,
like `has_answers`), **`imageUrls`** in socket payloads (camelCase, like `answeredCount`).

| Payload | Built in | Key |
|---|---|---|
| Editor question list (`QuestionItem`) | `quiz_service.question_item`, `schemas/host.py` | `images`: `{"<id>": {"url": "...", "current": bool}}` (the editor needs to know which references are older versions) |
| Every live question payload: `new_question`, host and player `sync_state`, late-join push | `gateway._question_payload` (becomes `async`; its 4 call sites await it) | `imageUrls` |
| Question results (host and player) | `gateway._host_results_payload`, `_player_results_payload` | `imageUrls` |
| Game over (host and player) | `gateway._host_game_over_payload`, `_player_game_over_payload` (via `game_service` summaries) | `imageUrls` |
| Past Quizzes detail | `routers/player.py`, `schemas/player.py` | `image_urls` |

Snapshot-based payloads (results, game over, Past Quizzes) resolve the snapshot's IDs, old
versions included. An ID missing from the map (its row was hard-deleted) is shown by every client
as a neutral `bg-surface border-line` box with the text "Image unavailable", never a broken image.

## 6. Access

| Action | Who | Route |
|---|---|---|
| View image bytes | **anyone with the URL** | `GET /api/images/{public_key}` |
| Upload | course HOST or admin | `POST /api/host/courses/{course_id}/images` (multipart `file`) → 201 `ImageItem` |
| List current images | course HOST or admin | `GET /api/host/courses/{course_id}/images` → `ImageItem[]` with `use_count` |
| Replace | course HOST or admin of the image's course | `PUT /api/host/images/{image_id}` (multipart `file`) → 201 new `ImageItem` |
| Delete | same | `DELETE /api/host/images/{image_id}` → 204 |

Access checks reuse `access_service.assert_course_host` (admins pass). Guests and players get
401/403 on everything except viewing.

**What the public URL means:** anyone holding `/api/images/{public_key}` can view that image;
there is no course authorization on viewing. A UUID makes guessing impractical, nothing more.
A deleted-but-retained version (§7) stays viewable by its URL because history needs it. Hosts
should not upload sensitive material. Response headers: `Content-Type` from the stored type,
`Cache-Control: public, max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`,
`Content-Disposition: inline`. Unknown key → 404.

`ImageItem`: `id, course_id, filename, content_type, size_bytes, width, height, url,
created_at, use_count, archived_use_count`. `use_count` = questions in **non-archived** quizzes
referencing it; `archived_use_count` = questions in archived quizzes. Delete is allowed only when
**both** are 0, matching §7, and the Images tab shows both.

The Images tab is the Host route **`/courses/:courseId/images`**.

There is no separate admin-app image screen: the admin app links to the Host app for host
work (T4), and admins pass every course check there.

## 7. Replace, delete and retention

**Replace** `PUT /api/host/images/{id}`, in this exact order (each lock follows §8):

1. Access check; read the upload and validate it with `load_image` in a threadpool (§4). **No
   lock yet.**
2. Course row **X**.
3. Locking read (`FOR SHARE`) of the course's questions (join `games`); keep those whose image
   fields contain the ID, split into non-archived and archived quizzes.
4. Image row **`FOR UPDATE`**: not current → **409 "This image was replaced or deleted;
   reload"**. Storage cap (§4, replace rule: bytes only, count unchanged) → 409.
5. Locking read (`FOR SHARE`) of `game_sessions` for open rooms (LOBBY/IN_PROGRESS) of the
   non-archived quizzes found in step 3 → any → **409** naming the quizzes (live play reads live
   rows, C-8).
6. Insert the new row (new `id`, new `public_key`, same `course_id`, **the new upload's
   `filename`**); set the old row's `replaced_by_id`.
7. `FOR UPDATE` **only the matched non-archived question rows** (by primary key) and rewrite the
   old ID to the new ID in their `config`. **Archived** quizzes (read-only in T4) and **snapshots**
   are not touched.
8. **Prune** the old row (below; its snapshot scan is a `FOR SHARE` read of `session_questions`).

**Prune** (`image_service.prune(db, image_id)`): if the row is **not current** and no question
and no snapshot references it, hard-delete it. Called in the same transaction after: replace
(the old version), and any question update or delete that removes a reference to a non-current
image (e.g. switching a restored quiz to the current version, or deleting its question). So the
retention rule below is actually enforced, not just stated.

**Caching:** every version has its own URL whose bytes never change, so immutable caching is
safe; after a replace, questions point to the new URL and browsers fetch it naturally.

**Delete** `DELETE /api/host/images/{id}`, in this order:

1. Access check; course row **X**.
2. Locking read (`FOR SHARE`) of the course's questions; any reference, **archived quizzes
   included** → **409** listing the quizzes. For an archived quiz the message says how to fix
   it: "Restore the quiz, remove the image, then archive it again."
3. Image row **`FOR UPDATE`**: not current → 409 (as above).
4. Locking read (`FOR SHARE`) of the course's `session_questions`: any reference → **soft
   delete** (`deleted_at`); none → **hard delete** the row.

Open rooms need no extra check for delete: a quiz in an open room references its images, so
step 2 already blocks.

**Retention rule:** a row is physically removed only when no question and no snapshot
references it, and every path that can make a non-current row unreferenced calls **prune**.
Replaced versions stay while referenced (archived quizzes, snapshots) and are invisible in the
Images tab. Rows referenced only by snapshots are kept indefinitely (they are history).

**Finding references** (design choice: no link table): scan the three `config` fields of the
course's questions (join `games`) and snapshots (join `game_sessions`), using locking reads
(§8). Course-scoped, so a few hundred rows at class scale.

**Restoring an archived quiz** changes nothing: its references to older versions stay valid
(unchanged references, §5). The editor knows which references are older from `QuestionItem.images`
(`current: false`) and shows them with an "Older version" note; the host **picks a replacement
by hand** from the course's current images (a new reference, so it must be current). There is no
one-click "use current version": the version chain is not exposed, and its end may be deleted.
Snapshots are never rewritten.

## 8. Locking

**Global order** (any path taking more than one follows it):
**course → quizzes (ascending id) → questions → images → session rows.**
A path that needs the course lock takes it **first**; a path that doesn't, never takes it later.

| Path | Locks (in order) | Change |
|---|---|---|
| Quiz/question mutations: quiz update, archive, restore, question create/update/delete/reorder (host + admin routes) | course **X** → quiz X → (question X) | `get_game_for_host(for_update=True)` reads the quiz's `course_id` unlocked (C-3), locks the course row, then the quiz row |
| Room creation (`create_room`) | course **S** → quiz X → insert session | `get_game_for_host(..., course_lock="shared")`; shared so rooms in one course don't block each other; prevents the A-1 cycle (session insert takes S on its course) |
| Quiz creation, quiz import (host + admin) | course **X** → inserts | new `access_service.lock_course` after the access check |
| Image upload | course X → insert | new |
| Image replace | course X → questions S (scan) → image row X → sessions S (open rooms) → matched question rows X → snapshots S (prune) | new; §7 steps 1–8 |
| Image delete | course X → questions S → image row X → snapshots S | new; §7 delete steps 1–4 |
| Answer, start, advance, end game | session/question rows only | unchanged; never take course or quiz locks |

**Why the known cycles are gone:**

- **Every path that touches `images` holds course X first** (question writes, upload, replace,
  delete, import). So image paths run one at a time per course, and their inner order (questions,
  images, sessions) cannot form a cycle with each other. Replace's step 7 takes X on question rows
  *after* the image row; this is safe because every other holder of those rows' locks is either
  behind the course lock or a live-game path, and those rows belong to quizzes with no open room
  (step 5), so no answer or end-room path holds them.
- **Live-game paths** (answer, start, advance, end) never take course, quiz or image locks
  (C-15). Their only overlap with image paths: an answer's foreign-key S lock on its question is
  compatible with the S scans; replace's X locks only rows of quizzes without open rooms.
  **Replace no longer locks all questions of the course exclusively**, so answers in a running game
  of another quiz in the same course never wait for a replace.
- Update-question vs delete-image, and room creation vs replace, both start at the course (X vs X,
  S vs X), so the second waits before taking anything else.

**Replace sees every affected quiz:** quiz creation/import, question writes and room creation
all take the course lock first, so none can commit while replace holds course X; anything
committed before replace got the lock is seen by its locking reads (C-5); a request blocked behind replace re-checks its references after it gets the
lock, so a *new* reference to the replaced version is rejected (not current).

**Fallback:** a MySQL deadlock (error 1213) **or lock-wait timeout (1205)** is mapped by an
exception handler in `common/exceptions.py` to **409 "Busy, try again"**. It is a fallback, not
the strategy.

**Cost (accepted):** quiz/question edits within one course run one at a time; image
operations briefly block room creation in that course. Other courses are unaffected.

## 9. Export and import

**Export** `GET /api/host/games/{id}/export` (and the admin route):

1. Validate content with the import rules: every question through full `QuestionCreate` (all
   types) and every image reference exists in the course with bytes retained (replaced/deleted
   versions allowed). Failures → **422** listing each failing question: `"Question 3
   (multiple_choice): <first error>"`. Nothing is modified or dropped. (Behavior change: a quiz
   left invalid by the pre-T8 unchecked update path can no longer be exported until it is
   re-saved in the editor, which always sends a complete question.)
2. No image references → **version 1**, byte-for-byte the current format.
3. Otherwise **version 2**: a top-level `images` list with **one entry per distinct image ID**
   `{"key": "img-1", "filename", "content_type", "data": "<base64>"}` (keys `img-1`, `img-2`…
   in first-use order), and each question's image fields hold keys instead of IDs.
4. Serialize the complete file (UTF-8, `indent=2`, `ensure_ascii=False`, as today) and check
   it with the import caps: bytes ≤ `MAX_IMPORT_BYTES`, images ≤ `MAX_BUNDLE_IMAGES`, decoded
   image bytes ≤ `MAX_BUNDLE_IMAGE_BYTES`; else **413** naming the limit. So every successful
   export can be imported.

**Import** (host and admin routes), one request transaction (`flush()` only, never `commit()`;
the request-scoped session commits once at the end, so any exception rolls everything back):

1. Read ≤ `MAX_IMPORT_BYTES` + 1 → 413. Parse JSON; `format == "buzzer/game"`.
2. **Version 1**: exactly today's behavior, plus: after the null normalization (§5), any image
   field left in a question → **422** (a raw DB ID from elsewhere must not be trusted).
3. **Version 2**:
   a. Bundle layer, before any write: `images` is a list (≤ `MAX_BUNDLE_IMAGES`); keys are unique
      non-empty strings (duplicate → 422); each `data` is valid base64 and passes §4; decoded
      total ≤ `MAX_BUNDLE_IMAGE_BYTES`. The entry's declared `content_type` is **ignored**; the
      decoder decides the type (as for uploads). Inside an import, **every** failure of an image
      entry is a **422** whose message names the key and the reason (`"img-2: animated images
      aren't supported"`); 413 is used only for the whole-file and bundle-total caps. Each question's image fields are **keys** with the §5
      shapes (strings instead of ints) and must exist in `images` (unknown key → 422 naming the
      question); the rest of the question passes `QuestionCreate` with image fields set aside.
   b. Lock the course (X). Insert one image row per **referenced** key (repeated references
      share one row; unreferenced entries are ignored). Build `key → new id`.
   c. Rewrite keys to IDs and run the full `QuestionCreate` and the database layer (§5) on every
      question; then create the quiz and questions exactly as today.
4. Version > 2 → 422 "Unsupported version".

## 10. Where images appear (frontend)

Option text everywhere uses the `optionDisplay` label rule (§5): host `ResultsPage`/`GameOverPage`
bars, player `ResultsPage.describeAnswer`, `QuestionRecap.describePlayerAnswer`/
`describeCorrectAnswer`, the editor list and the HTML report.

All image elements: `<img>` with `alt` (filename, or "Option A image"), `loading="lazy"` except
in the live question, `object-fit: contain`, framed with `bg-surface border-line rounded-xl`
(T9 §3.10: images are never recolored). Token classes and §12 primitives only.

| Screen | File | Images |
|---|---|---|
| **Images tab** (new) | `frontend/host/src/pages/course/ImagesPage.tsx`, tab in `CourseLayout.tsx`, route in `App.tsx` | grid of current images (thumbnail, filename, size, `use_count`); upload; replace; delete with the 409 message |
| Question editor | `frontend/host/src/pages/QuestionEditorPage.tsx` + new `components/ImagePicker.tsx` | picker for prompt image (all types), per-option image (MC/multi-select), Pin Drop background; "upload new" inline; "Older version" note from `QuestionItem.images[id].current`. **`formToPayload` keeps an option whose text is empty when it has an image** (today it drops empty multi-select options, C-13). The form keeps one image slot **per option**, so adding, removing or reordering an option adds, removes or moves its image with it; the payload always sends `optionImageIds` aligned with `options` (or omits it when all are empty) |
| Host question (projector) | `pages/game/QuestionPage.tsx` | prompt image; Pin Drop background in `PinGrid` |
| Host results / game over | `pages/game/ResultsPage.tsx`, `GameOverPage.tsx`, `components/NewTypeResults.tsx` | prompt image; option images next to option labels; Pin Drop background |
| Player question (phone) | `frontend/player/src/pages/game/QuestionPage.tsx` | option images on option cards; Pin Drop background (phones still show no prompt, existing convention) |
| Player results, recap, game over, Past Quizzes | `pages/game/ResultsPage.tsx`, `pages/game/GameOverPage.tsx`, `pages/PastQuizDetailPage.tsx`, `components/QuestionRecap.tsx` | prompt and option images |
| Pin Drop canvas (both copies) | `components/PinGrid.tsx` | new optional `imageUrl` prop: load the image, draw it first, preserving aspect ratio, centered in the grid square (T7 §8: scale `s = min(L/w, L/h)`, offset `((L−w·s)/2, (L−h·s)/2)` from `(P, P)`, with **`w`, `h` = the loaded image's `naturalWidth`/`naturalHeight`**, which include EXIF rotation, not the stored values), then grid lines on top; redraw on load and theme change; if the image fails to load, draw the plain grid |

API client: host `lib/api.ts` gains a multipart `putForm` (replace); upload uses `postForm`.

## 11. HTML report and test cleanup

**Report** (`report_service.build_session_report`): embed prompt and option images of asked
questions as `data:` URIs from the snapshot's IDs (old versions included); images whose row no
longer exists show the text "[image removed]". Pin Drop backgrounds are not drawn (the report
has no canvas). Each use embeds its own data URI (HTML has no way to share one without
scripts); to bound the file, embedding stops once **20,971,520 bytes** (20 MiB) of image data
have been embedded, and later images show "[image omitted: report size limit]". Sizes are read
first, so only the bytes of the images that fit the budget are loaded from the database.

**Cleanup:** `backend/scripts/cleanup_test_quiz.py` gains `--image-id ID` (repeatable). It
removes exactly the listed images, refuses any whose course is not `Test Course <8 hex>`, and
runs after the quiz cleanup (so they are no longer referenced). Test helpers record every image
ID they upload, import **or create by replace** (the replace response's new ID) and pass them all
to teardown. Because `replaced_by_id` has no foreign key (§5), the listed rows can be deleted in
any order. No test deletes "all images of a course".

## 12. Tests (T5): `tests/integration/test_t8_images.py`

1. **Upload**: PNG, JPEG, WebP, still GIF accepted with correct width/height and content type;
   rejected: > 5 MiB (413); SVG, a text file and a BMP (415); truncated PNG (422); animated GIF
   (422); 4097-px side (422).
2. **Access**: other course's host 403 on upload/list/replace/delete; guest token 401/403; view URL
   works with no auth; unknown key 404; response headers (§6).
3. **References** on create, update and v2 import: prompt image on all six types; option images
   on MC and multi-select; Pin Drop background; 422 for another course's image, a deleted image, a
   field on the wrong type, `optionImageIds` length mismatch (incl. changing option count), type
   change leaving `optionImageIds`; removing an image OK; unchanged reference to an older version
   OK after restore; regression: legacy updates without image fields behave as before.
4. **Replace**: new id and URL; current quizzes re-pointed; archived quiz keeps the old id; old
   URL still serves old bytes; a finished session's Past Quizzes and HTML report still show the
   original; 409 while an open room uses it; second replace of the old id → 409.
5. **Delete**: 409 while any quiz (incl. archived) uses it; `use_count`/`archived_use_count`
   match; after removing the reference: soft delete when a snapshot uses it (history still
   renders), hard delete otherwise (404). **Version chain**: replace A→B, then hard-delete B →
   A is still not current (cannot be newly referenced) and still serves its URL if a snapshot
   uses it. **Prune**: replace an unused image → the old row is gone; switch a restored quiz from
   an older version to current → the older row is pruned when nothing else uses it.
5b. **Normalization**: `promptImageId: null`, `imageId: null` and an all-`null` `optionImageIds`
   are not stored; such a quiz exports as version 1 and re-imports.
5c. **Decoding limits**: an image of 5000 × 100 px (inside the byte cap) → 422 without a full
   decode (header check).
5d. **Option text**: with `optionImageIds`, an option with empty or whitespace text and no image →
   422 naming it; with an image → OK and shown as "Option B" in results, recap and report; legacy
   question without `optionImageIds` and empty option text → unchanged behavior.
5e. **Bounds**: request with `Content-Length` over 32 MiB → 413 with `{error, message}` before
   parsing (also against `localhost:8000` directly); course storage full (`MAX_COURSE_IMAGES` or
   bytes, current images only) → 409; **a course whose history rows alone exceed the caps can
   still upload** (history does not count); **a replace at the count cap succeeds**.
5g. **Strict IDs**: `promptImageId: true`, `1.0`, `0`, `"5"` → 422.
5f. **Editor data**: `QuestionItem.images` marks an older-version reference `current: false`.
6. **Export/import**: no images → version 1; images → version 2 with one entry per distinct image
   used twice; round trip into another course creates new images; 422 for bad base64, unknown key,
   duplicate key, image field in a v1 file; 413 over caps; **nothing created on failure** (no quiz,
   no image rows); invalid legacy question → export 422 naming it, fixed via editor → export OK;
   all four `sample_games/` T6 files and the starter files still import.
7. **Live game**: `new_question`, a mid-question reconnect's `sync_state`, question results and
   game over all carry the image fields and `imageUrls`; Past Quizzes and the editor list carry
   `image_urls`; Pin Drop with a background scores exactly as without.
8. **Concurrency.** The T4 `GameRowLock` helper holds a row from its own MySQL connection; both
   requests are started, the test waits until both are blocked (`lock_waiters() == 2`), then
   releases. InnoDB does not promise which waiter goes first, so each test accepts **exactly the
   two outcomes listed** and fails on anything else (a 5xx, a 409 "Busy, try again", a timeout, or
   a mixed state). Every request must finish within 10 s.
   - **(a) Update adding image I vs delete of I** (hold the course row). Outcome 1: `PUT` → 200,
     `DELETE` → 409 naming the quiz; I is current; the question references I. Outcome 2: `DELETE`
     → 204, `PUT` → 422 "promptImageId: image deleted"; I is gone (hard delete: no snapshot uses
     it); the question has no image field.
   - **(b) Room creation vs replace of I** (hold the course row; the quiz references I; no room
     open before). Outcome 1: `POST /game/rooms` → 201, `PUT /images/I` → 409 naming the quiz; the
     quiz still references I; I is current. Outcome 2: `PUT` → 201 with new id J, `POST rooms` →
     201; the quiz references J; I is pruned (nothing else references it), so its old URL returns
     404; after `host_advance` starts the game, the snapshot references J.
   - **(c) Replace of I vs adding a new question referencing I** to an existing quiz Q2 (hold the
     course row). Outcome 1: replace → 201 (J), then `POST question` → 422 "promptImageId: image
     is not current". Outcome 2: `POST question` → 201, then replace → 201 (J) and that new question
     now references J. In both: no non-archived question references I afterwards.
   - **(d) Answering while a delete of I is refused** (game IN_PROGRESS on a quiz whose current
     question references I; no lock helper). Player submits while `DELETE /images/I` runs. Result:
     `DELETE` → 409; the player gets a normal `answer_received`; exactly one score row; I is current.
   - **(e) Two replaces of I** (hold the course row). Exactly one → 201 (new id J); the other →
     409 "This image was replaced or deleted; reload"; exactly one new image row exists.

Plus manual: the Images tab, editor picker, projector, phone and Pin Drop background in light
and dark, with screenshots in `docs/ui/`.

## 13. Files

| Area | Files |
|---|---|
| Dependencies/infra | `backend/requirements.txt` (+`Pillow`, rebuild image), `nginx/nginx.dev.conf` (`client_max_body_size 32m` in `location /api/`) |
| Backend new | `models/image.py` (+ `models/__init__.py` import order), `migrations/versions/005_images.py`, `schemas/images.py` (constants, `ImageItem`), `services/image_service.py` (load/validate, URL map, reference scan, replace, delete), `routers/images.py` (public view; included in `main.py`) |
| Backend changed | `main.py` (Content-Length middleware, images router), `schemas/host.py` (`QuestionItem.images`), `schemas/player.py` (`image_urls`), `routers/player.py` (Past Quizzes), `schemas/admin.py` (`validate_image_refs`, null normalization, widened T7 config rules), `services/quiz_service.py` (DB image checks on create/update, export/import v2, caps), `services/access_service.py` (`lock_course`, course-first in `get_game_for_host`), `services/game_service.py` (`create_room` shared course lock, `imageUrls` in summaries), `websocket/gateway.py` (`imageUrls` in `new_question`), `services/report_service.py` (embedding), `routers/host.py` and `routers/admin.py` (image routes, course lock on quiz create/import, import cap), `common/exceptions.py` (deadlock → 409), `scripts/cleanup_test_quiz.py` (`--image-id`) |
| Frontend host | `lib/api.ts`, `types/*`, `components/ImagePicker.tsx`, `components/QuestionImage.tsx`, `components/PinGrid.tsx`, `pages/course/ImagesPage.tsx`, `pages/course/CourseLayout.tsx`, `App.tsx`, `pages/QuestionEditorPage.tsx`, `pages/game/QuestionPage.tsx`, `ResultsPage.tsx`, `GameOverPage.tsx`, `components/NewTypeResults.tsx` |
| Frontend player | `types/game.ts`, `types/past.ts`, `components/PinGrid.tsx`, `components/QuestionImage.tsx`, `pages/game/QuestionPage.tsx`, `ResultsPage.tsx`, `GameOverPage.tsx`, `pages/PastQuizDetailPage.tsx`, `components/QuestionRecap.tsx` |
| Tests | `tests/integration/test_t8_images.py`, `tests/integration/conftest.py` (image cleanup helper) |
| Docs | this spec; `CLAUDE.md` of every changed directory; README format section (version 2); T7 spec note (§5 widening) |

## 14. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Images on disk / object store | Forbidden by T8. |
| Fetch images with the `Authorization` header (blob URLs) | Works for guests, but adds loading code to ~12 display spots in two apps and to the report. |
| Short-lived signed URLs | Strongest, but key management and expiry handling are out of proportion for quiz images. |
| Per-quiz image ownership | Duplicates images across a course's quizzes; T4 access is per course. |
| `options` as objects (`{text, imageId}`) | Breaks every existing reader of `options: string[]` (screens, export, tests, sample games). |
| Replace in place (same row) | Changes past results' images; cache invalidation becomes hard. |
| Link table `question_images` | Faster reference lookup, but another table to keep in sync on every config write; scans are small at class scale. |
| Re-encode uploads | Loses quality and changes bytes; decode-to-validate is enough. |
| Allow animated GIF | Canvas shows one frame while `<img>` animates; inconsistent. |
| Always export version 2 | Would change every plain quiz's file and the T6 sample-game format for no benefit. |
| Fine-grained locks instead of a course lock | Harder to reason about; cycles found during design (§8) came from mixing lock entry points. |

## 15. Out of scope

Image editing/cropping, image search, per-image alt text authoring, an admin-app image screen,
images in fill-in-the-blank or true/false options, region (multi-point) targets for Pin Drop,
moving images between courses.

## 16. Implementation order

Each step one focused commit, after this spec is committed and Goldfish-reviewed:

1. Pillow + nginx limit + migration `005` + `Image` model.
2. Course-first locking in `access_service` / `create_room` / quiz create+import (+ deadlock handler);
   T4 tests still pass.
3. `image_service` + image routes (upload, list, view, replace, delete).
4. Question references: shape + database validation on create/update; `imageUrls` in payloads.
5. Export/import version 2 + caps + export content validation.
6. HTML report embedding; cleanup script `--image-id`.
7. Host app: Images tab, editor picker, game screens, `PinGrid` background.
8. Player app: option images, Pin Drop background, results/recap.
9. Integration tests (§12).
10. Docs, screenshots, mean review.

## 17. Remaining tradeoffs

- **Public URLs**: simple and `<img>`-friendly; anyone with a URL can view.
- **Versioned replace**: correct history; the database keeps old versions while referenced.
- **Course lock**: simple and analyzable; edits within one course are serialized.
- **No link table**: reference scans instead of an index.
- **Still images only**; **export caps** (20 images, 20 MiB) limit very image-heavy quizzes.
- **Per-course storage cap** (200 current images / 500 MiB): bounds what hosts manage; retained
  history (replaced and soft-deleted versions still needed by archived quizzes and past sessions)
  is **not** capped and grows with replaces and played sessions. Accepted: it is the price of
  correct history, and it only grows through deliberate host actions.
- **Chunked uploads without `Content-Length`** are bounded only by nginx in the built app.
- **No one-click "use current version"**: the host re-picks the image for restored quizzes.
- **Export now validates content**: safer, newly strict for legacy-invalid quizzes.
- **Changes to T4 paths** (`get_game_for_host`, `create_room`, quiz create/import) — Yug should know.
- **New dependency** (Pillow) requires a backend image rebuild for every developer.

## 18. Revision history

- **rev 1**: written from design-discussion revisions 1–4 (public UUID URLs; versioned replace;
  retention; course-first lock order; v2 import two-layer validation; exact byte limits; export
  content validation).
- **rev 2 (Goldfish review, fresh session, 17 findings):** `replaced_by_id` has no FK, so
  deleting a newer version neither blocks nor revives an older one (G1); a **prune** step enforces
  the retention rule after replace and reference removals (G2); image fields are normalized
  (nulls removed) so the v1/v2 export test and v1 import agree (G3); the pixel-size check runs on
  the header before decoding and Pillow's bomb warning is an error (G4); decoding runs in a
  threadpool before the course lock (G5); `imageUrls` is built in `_question_payload` and named
  for every payload, covering reconnects (G6, G12); an unchanged reference means the ID appears
  anywhere in the stored question (G7); the delete lock row follows the global order (G8);
  `use_count` and `archived_use_count` match the delete rule (G9); missing files listed (G10);
  test cleanup records replace results (G11); declared bundle `content_type` ignored and
  import image errors are 422 naming the key (G13); replace keeps the new upload's filename (G14);
  the editor keeps option images aligned (G15); report embedding capped at 20 MiB (G16);
  `images.course_id` has no `ondelete`, matching `games` (G17; checked: there is no course-delete
  route and `games`/`game_sessions` course FKs have none).
- **rev 3 (second Goldfish review, fresh session, 14 findings):** image checks use locking reads
  (G1); the editor gets `images[id].current` and the host re-picks older versions by hand, no
  version chain exposed (G2); exact option-text rule with images and one "Option A" display label
  everywhere, and the editor stops dropping image-only options (G3); replace scans questions with
  `FOR SHARE` and locks only the rows it rewrites, so live answers elsewhere in the course never
  wait (G4); replace and delete written as exact ordered steps matching §8 (G5); the warning filter
  is dropped (the header side check already enforces the pixel limit) and `MAX_IMAGE_PIXELS` is set
  at import (G6); a Content-Length middleware bounds requests with or without nginx, and the
  in-route read is described as a memory bound only (G7); `images.data` is deferred (G8); missing
  images render an "Image unavailable" box (G9); bundle `filename` rule (G10); per-course storage
  cap (G11); lock-wait timeout 1205 also maps to 409 (G12); Images route named and archived-quiz
  delete message gives the fix (G13); `PinGrid` uses the loaded image's natural size (EXIF) (G14).
- **rev 4 (third Goldfish review, fresh session, 7 findings):** unsupported formats (SVG, text,
  BMP) are 415 via `UnidentifiedImageError` and a format check before decoding, while 422 is
  reserved for a supported format that is too large, animated or broken (G1); the storage caps
  count **current** images only, so history can never fill a course and the 409 advice is always
  actionable (G2); a table states how each error reaches the host, and the editor prevents
  create-time shape errors that would show only "HTTP 422" (G3); concurrency tests list the exact
  two acceptable outcomes and fail on anything else (G4); a replace changes bytes but not the
  current count (G5); image IDs reject `bool` and `float` (G6); the middleware's 413 uses the
  `{error, message}` body (G7).
- **rev 5 (implementation, step 3):** Pillow reports many phone-camera JPEGs as `MPO` with 2
  frames; without this note they would be rejected as unsupported or animated. MPO is treated as
  JPEG and exempt from the frame check (§4).
- **rev 6 (implementation and tests, steps 3–9):** what the code does where it differs from or
  adds to revs 1–5.
  - **Messages for an id with no row.** A hard delete or a prune removes the row, so a later
    reference reads **"image not found in this course"**, not "image deleted" or "not current".
    So in §12.8 (a) outcome 2 and (c) outcome 1 the 422 reads "…: image not found in this
    course" (the tests accept any `promptImageId: image…` detail and check the row is gone).
    "image deleted" is only for a soft-deleted row; "image is not current (it was replaced);
    pick the current version" only for a replaced row that history still keeps.
  - **Replace/delete of a row that is gone by the time of the course lock** returns **409**
    "This image was replaced or deleted; reload" (the access check found it, so another request
    removed it). The first draft returned 404; found by test §12.8 (e).
  - **Import lock.** `import_bundle` validates and decodes everything first and then takes the
    course lock itself; the import routes no longer lock before reading the file (§8 table:
    "course X → inserts" still holds, the lock is just taken later).
  - **Live results.** The live `question_results` broadcast builds its own payload in
    `_on_host_advance_impl`; it now carries `imageUrls` too (found by test §12.7; only the
    reload path had it).
  - **Screens (manual testing).** Host question: with a question image the prompt uses
    `text-title` and the image may use up to 42vh, so the screen still fits a projector. Player
    question: an option image fills the card width (up to 40vh) on a second row of the card.
    Player results: the player app shows no prompt, so the results screen shows the images of
    the options the player chose (not the prompt image); recap, game over and Past Quizzes show
    both.
  - **Editor.** Every type now needs a prompt before Save ("Enter the question prompt"); before,
    an empty prompt gave only "HTTP 422" for the four original types. This was a pre-T8 gap,
    fixed here by the owner's decision because the editor was already changing.
  - **Names.** `prune(db, course_id, image_id)`; `lock_current_image` does the step-4 check.
  - **Mean review, accepted tradeoffs (not fixed):** delete/replace read the course's snapshots
    with `FOR SHARE`, which can deadlock with a game start inserting its snapshot in the same
    course; the §8 fallback then returns 409 "Busy, try again" (not tested). `alt` text is generic
    ("Question image", "Option A image"), not the filename (§10), because payloads carry no
    filenames. The view route reads the whole image into memory per request (immutable caching
    keeps repeats rare).
  - **Tests not written** (§12): the 500 MiB course byte cap (the count cap is tested), the
    export-side 413 caps (the import-side ones are), and "Option B" in the report text (the
    label helper was only checked by a manual run). 43 integration tests in `test_t8_images.py`.
