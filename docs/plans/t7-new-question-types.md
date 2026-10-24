# Design: New Question Types — Pin Drop and Closest Guess (T7)

Status: rev 8 — implemented, merged with T9 themes; mean review done (§13).
Builds on T4 (`docs/design-host-admin.md`): question authoring lives in the Host app.

## 1. Problem

Buzzer supports four question types (`multiple_choice`, `true_false`, `fill_in_the_blank`,
`multi_select`). T7 requires two new types, each a full vertical slice
(authoring, validation, live play, scoring, results, history, import/export, tests, sample
games), where:

- one type uses an HTML Canvas as the player's interaction surface;
- one type suits a classroom, the other a party;
- each has a defined scoring approach;
- the update path enforces the same structural validation as create.

## 2. Decisions

1. **`pin_drop`** (Canvas, classroom — e.g. coordinate geometry): the player taps a point on a
   fixed integer grid from −5 to 5 on both axes. ACCURACY = exact match with one target point.
2. **`closest_guess`** (party — e.g. trivia estimates): the player types a number. ACCURACY =
   absolute-error tolerances with full credit, linear partial credit, then zero.
3. **No database schema migration.** `Question.type` is free text (`String(50)`), and both types
   store their data in the existing JSON columns `config` and `answer_data`. The **API
   validation schemas do change** (`backend/app/schemas/admin.py`, §5.1). No new endpoints and
   no new socket events. Fractional scores (partial credit such as 6.25) need no storage change
   either; verified in code: `SessionScore.points_awarded` is `sa.Float`, `ScoreResult
   .points_awarded` is `float`, the Redis running score is a JSON field updated by
   read-add-write in `state_service.update_player_score` (not `INCRBY`; its `delta: int`
   annotation is not enforced), the leaderboard sums the float column, and the CSV writes the
   stored values. Existing types already produce fractional points (editor inputs use
   `step="any"`).
4. **`config` is public, `answer_data` is secret.** Players receive the full `config` in the
   `new_question` payload (`websocket/gateway.py` `_question_payload`), so no correct answer
   may be stored in `config`.
5. **Answers are validated before any points are awarded, under both grading modes.** A new
   function `game_service.validate_answer` checks the answer shape for the two new types. The
   gateway rejects invalid answers before scoring; `calculate_score` also refuses (scores 0)
   anything invalid. The four existing types are unchanged.
6. **`is_correct` means full credit only**, matching the existing convention. For
   `closest_guess` it is decided by the scoring region, never by comparing points. **What players
   see:** the live results screen labels a `closest_guess` answer by its points (full / partial
   / none, §5.7), so partial credit is never shown as "Correct!". The recap (`QuestionRecap`)
   keeps its existing, documented convention for all types: any positive points show ✓, and
   the correct answer is shown whenever points are below full.
7. **Update validation, scoped to the new types.** When the stored type or the requested type is
   `pin_drop` or `closest_guess`, an update is saved only if the stored question merged with
   the incoming fields passes the same validation as create (§5.2). Updates to questions of the
   four existing types behave exactly as today; closing their pre-existing gap is deferred to a
   joint decision with the T4 owner (§9, C1).
8. **COMPLETENESS has no correct answer.** For the new types, `answer_data` must be `{}`, and no
   path reads `answer_data` or labels an answer correct/full/partial/miss (§4).

## 3. Type definitions

### 3.1 `pin_drop`

| Field | Shape | Rules |
|---|---|---|
| `config` | `{}` | Must be exactly `{}` in T7 (T8 may add one optional image key, §8). |
| `answer_data` (ACCURACY) | `{"target": {"x": int, "y": int}}` | Required. `x`, `y` are integers in [−5, 5]; no other keys. |
| `answer_data` (COMPLETENESS) | `{}` | Must be exactly `{}`. |
| player answer | `{"x": int, "y": int}` | Exactly these keys; integers in [−5, 5]. |
| `points_value` | number | `> 0` under ACCURACY; `≥ 0` under COMPLETENESS (existing range ≤ 100000). |

"Integer" means a JSON integer: Python `int` but **not `bool`** (`isinstance(True, int)` is
true in Python, so booleans must be excluded explicitly) and **not `float`** (so `3.0` is
rejected). A browser `JSON.stringify(3)` sends `3`, so the real UI always sends ints.

**Scoring.** ACCURACY: `(x, y) == target` → `points_value`, `is_correct = true`; anything else →
`0`, `is_correct = false`. COMPLETENESS: any valid answer → `points_value`, `is_correct = true`.

**Answer distribution key** (both modes): the string `"x,y"`, e.g. `"-3,2"`.

**Answer reveal:** ACCURACY → `{"type": "pin_drop", "target": {"x": int, "y": int}}`;
COMPLETENESS → `{"type": "completeness"}`.

### 3.2 `closest_guess`

| Field | Shape | Rules |
|---|---|---|
| `config` | `{}` or `{"unit": str}` | Only key allowed is `unit`: a string of 1–20 characters (Python `len`, i.e. code points) with no leading or trailing whitespace (`unit == unit.strip()`). A question without a unit omits the key. Allowed in both modes. |
| `answer_data` (ACCURACY) | `{"correct": num, "fullTolerance": num, "partialTolerance": num}` | All required, no other keys. |
| `answer_data` (COMPLETENESS) | `{}` | Must be exactly `{}`. |
| player answer | `{"value": num}` | Exactly this key. |
| `points_value` | number | `> 0` under ACCURACY; `≥ 0` under COMPLETENESS. |

"num" means Python `int` or `float`, not `bool`, finite (`math.isfinite`) and with absolute
value ≤ `1e12`. (Python's `json` module accepts `NaN`/`Infinity`, so a hand-made client could
send them; they must be rejected.) Also `0 ≤ fullTolerance ≤ partialTolerance`. `correct` may be
zero or negative.

**Scoring (ACCURACY).** Let `e = |value − correct|`, `F = fullTolerance`,
`P = partialTolerance`, `EPS = 1e-9`. Check in this order; the first match wins:

1. **Full:** `e ≤ F + EPS` → award `points_value`, band `"full"`.
2. **Zero:** `e ≥ P − EPS` → award `0`, band `"miss"`.
3. **Partial region:** otherwise
   - `raw = points_value × (P − e) / (P − F)`;
   - `award = min(floor(raw × 100) / 100, points_value − 0.01)` (truncate to cents, then cap one
     cent below full);
   - if `award ≤ 0` → award `0`, band `"miss"`; else band `"partial"`.

`is_correct = (band == "full")`. The band is a function of the award, so **the three outcomes
are exactly: full ⇔ award = `points_value`; partial ⇔ 0 < award < `points_value`; miss ⇔
award = 0.** Every display (host counts, player headline, recap, report) therefore agrees,
whether it reads the band or compares points.

Consequences, all intended:

- The full-tolerance boundary scores full credit; the partial-tolerance boundary scores zero.
- **`F == P`** needs no special case: rule 1 or rule 2 always matches. Rule 3 is reachable only
  when `F + EPS < e < P − EPS`, which implies `P − F > 2·EPS > 0`, so there is no division by
  zero.
- **Partial credit that truncates to 0 is a miss.** E.g. points 10, F 0, P 10, e 9.9995 →
  raw 0.0005 → award 0 → band `"miss"`, not `"partial"`.
- **Partial credit can never reach full points.** In exact arithmetic `raw < points_value` in
  rule 3, but in floating point `(P − e)/(P − F)` can round to exactly 1.0 (e.g. F 0, P 1e12,
  e 1e-8). The cap at `points_value − 0.01` makes that case a partial worth one cent less than
  full. If `points_value ≤ 0.01`, the cap makes every rule-3 answer a miss (consistent, just
  coarse).
- Truncation may lose up to 0.01 point through float representation; accepted.
- Zero and negative `correct` work unchanged because error is absolute
  (e.g. correct −40, F 2, P 10, guess −45 → e 5 → raw = `points_value × 5/8`).
- Near `1e12`, floats resolve only to about `1e-4`, so `EPS` has no effect there. The comparisons
  are still deterministic; this is a documented precision limit.

**Scoring (COMPLETENESS):** any valid answer → `points_value`, `is_correct = true`, no band.

**Answer distribution key:** ACCURACY → the band (`"full"`, `"partial"`, `"miss"`) from the one
shared helper `closest_guess_result` (§5.3). COMPLETENESS → **no distribution entry is
recorded**.

**Answer reveal:** ACCURACY → `{"type": "closest_guess", "correct": num, "fullTolerance": num,
"partialTolerance": num, "unit": str | null}` (`unit` from `config`); COMPLETENESS →
`{"type": "completeness"}`.

## 4. COMPLETENESS behaviour on every path (new types)

| Path | `pin_drop` | `closest_guess` |
|---|---|---|
| Authoring (editor) | Target inputs hidden; sends `answer_data: {}`; Points input shown. | Correct/tolerance inputs hidden; sends `answer_data: {}`; Unit and Points inputs shown. |
| Server validation | `answer_data` must be `{}`. | Same. |
| Answer validation | `validate_answer` as in ACCURACY. | Same. |
| Scoring | `points_value`, correct. | Same. |
| Reveal (all three copies) | `{"type": "completeness"}` — already returned first, before any type check, by `gateway._answer_reveal`, `game_service.answer_reveal` and `report_service._answer_reveal`. | Same. |
| Player results screen | Existing "Answer recorded!" plus own point `(x, y)`. | "Answer recorded!" plus own value (with unit). No "off by". |
| Host results screen | Grid **without** target; top 5 answered points with counts; "N of M answered". | "N of M answered" only. |
| Host game-over card | Top 5 points as text; answered count. | Answered count only. |
| Recap (player game over, Past Quizzes) | `QuestionRecap` already hides the correct answer for `completeness`; shows own point. | Shows own value. |
| HTML report | Type badge, "Completeness" badge, existing "N / M answered" line; no summary. | Same. |

## 5. Detailed implementation

Paths are relative to the repo root. "Branch" means one added case alongside the existing
per-type cases in that function. Existing behaviour for the four old types must not change,
except §5.2.

### 5.1 Validation schemas — `backend/app/schemas/admin.py`

- Define `NEW_TYPES = ("pin_drop", "closest_guess")` here. It lives in the schema module, not
  `game_service`, because `game_service` already imports `quiz_service`; importing
  `game_service` from `quiz_service` (§5.2) would be circular. Both services import it from here.
- Add `pin_drop` and `closest_guess` to the `type` regex in both `QuestionCreate` and
  `QuestionUpdate`.
- In `QuestionCreate.validate_structure`, add one branch per new type enforcing every rule in
  §3.1/§3.2 for `config`, `answer_data` (per grading mode) and `points_value`. Raise
  `ValueError` with a specific message (e.g. `"pin_drop target.x must be an integer from -5 to
  5"`).
- Import (`quiz_service.import_bundle`) already validates each question through
  `QuestionCreate`, so import/export need no other change; files stay `"version": 1`.

### 5.2 Update validation — `backend/app/services/quiz_service.py` `update_question`

This function was written in T4; give its owner a heads-up before changing it. The change
follows the T4 design's rule that validation "stays in `schemas/admin.py`, called by
`quiz_service`" (`docs/design-host-admin.md`).

Today `update_question` copies every non-`None` field of `QuestionUpdate` onto the stored
question with no structural check, and `QuestionUpdate` has no structure validator (an update
body can be partial, so the schema alone cannot know which type's rules apply).

1. Load and lock the question exactly as today (`get_question_in_game(..., for_update=True)`,
   then `_assert_editable`).
2. **Scope:** if neither the stored `question.type` nor the incoming `body.type` (when not
   `None`) is in `NEW_TYPES` (defined in `schemas/admin.py`, §5.1), skip steps 3–4 and continue exactly as
   today. Checking the stored type too means turning a `pin_drop` into, say, a
   `multiple_choice` without options is also rejected.
3. Build `merged` from the stored question's `type`, `grading_type`, `prompt`, `config`,
   `answer_data`, `time_limit_seconds`, `points_value`, then overwrite each with the incoming
   `QuestionUpdate` field **if that field is not `None`**. Omitted and `null` both mean "keep the
   stored value" (today's behaviour; no `Question` column is nullable, so `null` never means
   "clear"). `order_index` is not part of the merge; this is safe because
   `QuestionCreate.order_index` is optional (`int | None = Field(None, ge=0)`), and
   `QuestionUpdate.order_index` keeps its existing handling.
4. Validate with `QuestionCreate(**merged)`. On `pydantic.ValidationError`, take the first
   error `err = exc.errors()[0]`, build `msg = err["msg"]` with a leading `"Value error, "`
   removed (Pydantic adds it to messages from `ValueError` raised in validators), prefix the
   field path when `err["loc"]` is non-empty (`"type: String should match pattern …"`), and raise
   `HTTPException(status_code=422, detail=f"Question invalid: {msg}")`. Change nothing. The
   detail is a plain string, so the host editor's `lib/api.ts` shows it. `HTTPException(422,
   ...)` is the error style `quiz_service.import_bundle` already uses for validation failures.
   (A Pydantic error raised inside a service is not handled by the request-validation handler
   and would otherwise become a 500.)
5. If valid, apply the incoming fields as today (`clean_prompt` on the prompt, etc.).

Consequence of merging: a request that changes only `grading_type` on a new-type question is
rejected when the stored `answer_data` doesn't fit the new mode (ACCURACY → COMPLETENESS with a
stored target fails the `{}` rule; COMPLETENESS → ACCURACY fails "target required"). This is
intended: the request must send the matching `answer_data`. The editor always sends a full
payload, so it is unaffected.

Effects: an update that involves a new type is saved only if the merged question is valid;
otherwise 422 with a readable message and nothing changes. **Nothing repairs invalid rows
automatically:** an edit to such a question fails until the request makes the merged question
valid. Updates to the four existing types (when neither stored nor requested type is new),
create, and import behave exactly as today.

### 5.3 Answer validation and scoring — `backend/app/services/game_service.py`

- Import `NEW_TYPES` from `schemas/admin.py` (§5.1).
- `validate_answer(question, answer_data) -> str | None`: returns an error message or `None`.
  Non-dict `answer_data` is an error. Applies §3.1/§3.2 player-answer rules. Takes any object
  with `.type` (live `Question` or `SessionQuestion`).
- `closest_guess_result(value, answer_data, points_value) -> (award, band)`: rules 1–3 of
  §3.2, including the cap and the "truncates to 0 → miss" rule. It takes `points_value`
  because the band depends on the award. Any object with `answer_data` and `points_value`
  works (live `Question` or `SessionQuestion` snapshot). It is the **only** place the rules
  live: scoring, `record_answer`, `get_host_question_summary` and the HTML report all call it,
  so all agree.
- `calculate_score`: before the existing COMPLETENESS short-circuit, if `question.type in
  NEW_TYPES` and `validate_answer(...)` returns an error → `ScoreResult(0, False)`. Then add the
  ACCURACY branches from §3.1/§3.2. (COMPLETENESS for new types then falls through to the
  existing full-points short-circuit.)
- `record_answer` distribution: `pin_drop` → key `"x,y"`; `closest_guess` under ACCURACY → band
  from `closest_guess_result`; under COMPLETENESS → no increment.
- `answer_reveal`: add the two ACCURACY reveal shapes (§3).
- `get_host_question_summary`: same distribution rules as `record_answer`, computed from
  `SessionScore.answer_data` and the snapshot question's `grading_type`/`answer_data`.

### 5.4 Gateway — `backend/app/websocket/gateway.py`

- `on_submit_answer`: after the question is loaded (where the existing `multi_select` check
  is), if `question.type in NEW_TYPES` and `game_service.validate_answer` returns a message →
  `_emit_error(sid, message)` and return. No score row is written.
- `_answer_reveal`: for the new types, delegate to `game_service.answer_reveal` (identical
  shapes; one definition instead of another copy — as implemented).
- `_question_payload`: no change (`config` already carries `unit`).

### 5.5 HTML report — `backend/app/services/report_service.py`

- `_answer_reveal`: for the new types, delegate to `game_service.answer_reveal` (as
  implemented).
- Type badge map: add "Pin Drop" and "Closest Guess".
- No chart for the new types (the existing `else: chart = ""` applies). `_extract_answer_key`
  is **not** extended: it returns `None` for unknown types, so new-type answers add nothing to
  the report's `dist`. Instead, under ACCURACY, compute a one-line summary directly from the
  question's `SessionScore` rows and its snapshot, shown below the prompt:
  - `pin_drop` → "Target (x, y) · K correct", where K = number of rows with `is_correct`;
  - `closest_guess` → "Correct C unit · full a · partial b · miss c", counting each row's band
    from `closest_guess_result(row.answer_data["value"], q.answer_data, q.points_value)`.

  Under COMPLETENESS no summary (existing "N / M answered" line only).
- CSV exports (`export_service.py`): no change; they write points per question only.

### 5.6 Host app — `frontend/host/src/`

- `types/game.ts`: add the two types to the `type` unions (`QuestionPayload`,
  `HostQuestionSummaryItem`); add `unit?: string` to `config`; add the two reveal variants to
  `AnswerReveal`.
- New `components/PinGrid.tsx`: draws the grid (§6) with optional `selected` point, optional
  `target` point, optional `onSelect` (interactive only if given).
- `pages/QuestionEditorPage.tsx`:
  - dropdown options "Pin Drop (grid)" and "Closest Guess (number)";
  - `FormState` fields `pinX`, `pinY`, `cgCorrect`, `cgFull`, `cgPartial`, `cgUnit`;
  - `questionToForm` and `formToPayload` branches; **`formToPayload` must not fall into its
    final fill-in-the-blank `else` for the new types**;
  - for both new types, `points_value = form.pointsValue` in both modes, and the Points input is
    shown in both modes;
  - under COMPLETENESS send `answer_data: {}`;
  - target `x` and `y` are chosen from two `<select>` lists of the integers −5..5, so they
    cannot be out of range or fractional;
  - **new-question defaults:** `pin_drop` target (0, 0); `closest_guess` correct, full and
    partial tolerance fields empty (Save disabled until filled) and unit empty; Points 1 for
    both;
  - **unit:** trimmed before sending; a blank unit omits the `unit` key so `config` is `{}`;
    length is counted in code points (`[...unit].length`) to match the server's Python `len`;
  - **client-side checks for the new types** (because create errors show only "HTTP 422", §9
    P2): Save is disabled and an inline message names the problem when the prompt is empty
    (found in manual testing) or any §3.2 rule fails —
    `correct`, `fullTolerance`, `partialTolerance` must parse to finite numbers with absolute
    value ≤ 1e12, `0 ≤ F ≤ P`, unit at most 20 characters, and `points_value > 0` under
    ACCURACY. The server remains authoritative;
  - a `PinGrid` preview of the target; question-list summaries `Target (x, y)` and
    `C unit (±F full, ±P partial)`; `typeLabel` entries.
- `pages/game/QuestionPage.tsx`: `typeLabel` entries; for `pin_drop` render a non-interactive
  `PinGrid` (no target) below the prompt; for `closest_guess` show "Answer in {unit}" if set.
  While the question is locked the page already hides the prompt; hide the grid and the unit
  line with it.
- `pages/game/ResultsPage.tsx`: `pin_drop` → `PinGrid` with the target (ACCURACY only), "K of N
  correct" (K = count at the target key) under ACCURACY or "N of M answered", plus the top 5
  `"x,y"` keys by count (ties broken by x ascending, then y ascending, numerically);
  `closest_guess` → ACCURACY: correct value + unit and the three band counts; COMPLETENESS:
  answered count only. Do not route the new types through `buildBars`.
- `pages/game/GameOverPage.tsx` (`QuestionCard`): the same summaries as text.
- As implemented, both summaries live in one shared `components/NewTypeResults.tsx`
  (`showGrid` on for the results screen, off for game-over cards), with `isNewType` and
  `topPins`.
- All numbers shown for the new types use the §5.8 format.

### 5.7 Player app — `frontend/player/src/`

- `types/game.ts`: extend the `type` unions, `config` (`unit?`), `playerAnswer`
  (`x?`, `y?`, `value?`) and `PlayerAnswerReveal` (two variants).
- New `components/PinGrid.tsx`: same drawing and conversion as the host copy (§6).
- `pages/game/QuestionPage.tsx`:
  - `pin_drop`: interactive `PinGrid`; each tap moves the marker; label `(x, y)`; Submit
    enabled once a point is selected; sends `{x, y}`.
  - `closest_guess`: text input with `inputMode="decimal"` plus a **±** button (the iOS decimal
    keypad has no minus key); shows the unit; sends `{value}`. Parsing rule (`parseGuess`):
    1. trim the text; it must match `^-?(\d+([.,]\d*)?|[.,]\d+)$` — optional leading minus,
       digits, at most one decimal separator (`.` or `,`, for locales whose keypad types a
       comma), no exponent (`1e13`), no thousands separators, not empty, not a bare `-`;
    2. replace `,` with `.` and convert with `Number()`;
    3. the result must be finite with absolute value ≤ 1e12 — the same bounds as
       `validate_answer`.
    Submit is enabled only when `parseGuess` succeeds; otherwise a short hint explains why
    (e.g. "Too large"). The **±** button toggles a leading `-` on the current text; on an empty
    field it inserts `-` (which alone does not enable Submit).
  - Both disabled after submit or while `questionLocked`, like the existing types.
- `pages/game/ResultsPage.tsx`:
  - `describeAnswer`: `(x, y)` and `value unit`. Under ACCURACY also show `Target (x, y)` or
    `Correct C unit — off by e`, where `e = |value − correct|` (§5.8 format).
  - **Headline for `closest_guess` under ACCURACY** (only when `currentQuestion` is known; a
    player who joined while the question was locked never got `new_question`, so the page
    falls back to today's headline — found in the mean review): `yourPoints ≥ pointsValue` → "Correct!";
    `0 < yourPoints < pointsValue` → "Partial credit" with `+yourPoints pts`; `0` → "Incorrect"
    (`pointsValue` comes from `currentQuestion`; it is already in the `new_question` payload).
    Exact comparison is safe because §3.2 guarantees full ⇔ award = `points_value`, partial ⇔
    0 < award < `points_value`, miss ⇔ award = 0. Other types keep today's headline.
- `components/QuestionRecap.tsx`: `describePlayerAnswer` and `describeCorrectAnswer` branches
  (used by the game-over screen and Past Quizzes). Its ✓ rule is unchanged (any positive points
  show ✓, and the correct answer is shown when points are below full), so a partial
  `closest_guess` shows ✓ plus the correct value — the same convention `multi_select` partial
  credit already follows.

### 5.8 Number formatting (new types, all displays)

One rule everywhere a `closest_guess` value, tolerance, error or partial score is shown: at most
two decimals, trailing zeros removed, no thousands separators, and `−0` shown as `0`.
- TypeScript (each app's `lib/utils.ts`): `formatNum(v) = Number(v.toFixed(2)).toString()`
  (values up to 1e12 never switch to exponent notation).
- Python (`report_service`): `f"{v:.2f}".rstrip("0").rstrip(".")`, with `"-0"` → `"0"`.

## 6. Pin Drop canvas: pixel ↔ grid conversion

Both `PinGrid` copies must implement exactly this.

- The canvas is a square of `S` CSS pixels, `S = min(container width, maxSize)`, with a margin
  `P = 16` CSS px so edge points are tappable. `maxSize` is a `PinGrid` prop: **360** on the
  player phone, **520** on the host projector screens (question, results), **240** for the
  editor preview. Nothing below depends on `S`.
- **Resize/rotation:** a `ResizeObserver` on the container recomputes `S` and redraws. The
  selected point and target are stored in grid coordinates, so they survive a resize; taps read
  `getBoundingClientRect()` at tap time, so the conversion stays correct.
- Spacing between grid lines: `g = (S − 2P) / 10` (−5..5 is 10 intervals).
- **Tap → grid:** with `rect = canvas.getBoundingClientRect()`,
  `px = clientX − rect.left`, `py = clientY − rect.top` (CSS px, independent of
  `devicePixelRatio`); `gx = (px − P)/g − 5`; `gy = 5 − (py − P)/g` (screen y grows down, grid y
  grows up); `x = clamp(Math.round(gx), −5, 5)`, same for `y`. Taps in the margin snap to the
  nearest edge point.
- **Grid → pixel (drawing):** `(P + (x + 5)·g, P + (5 − y)·g)`.
- **Sharpness:** backing store `S·dpr × S·dpr` (`dpr = window.devicePixelRatio`), CSS size
  `S × S`, then `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)` so all drawing is in CSS px.
- **Input:** `pointerdown` (mouse and touch alike) with CSS `touch-action: none` so a tap does not
  scroll the page.
- Draw axes through 0 more prominently than other grid lines; label −5, 0, 5 on each axis.
- **Theme (T9, `docs/plans/ui-themes.md` §3.10):** all colors are theme tokens read at draw
  time (canvas cannot use CSS variables, so `tokenColor` resolves `rgb(var(--token))` through
  the element's computed style); a `MutationObserver` on `data-theme` redraws on a theme
  switch. Background and frame are drawn on the canvas, never as a CSS border.

## 7. Alternatives considered

| Alternative | Why rejected |
|---|---|
| "Order It" (drag items into order) as the party type | Drag-and-drop on phones is much more UI work, and results are harder to display. |
| Free-position Pin Drop with distance-based partial credit | More scoring and authoring complexity; exact match on a snapped integer grid is fair and easy to explain. |
| Percentage tolerances for Closest Guess | Undefined when the correct answer is 0; awkward for negatives. |
| Rounding partial credit to nearest | Can produce full points while `is_correct` is false. |
| Image-based Pin Drop now | Depends on T8 image storage; the grid works without images, and T8 can add an image later (§8). |
| Heatmap / pin scatter on the host screen | Not needed; distribution keys already give counts per point. |
| One shared grid component for both apps | The host and player apps share no package; a ~60-line duplicate with a single written spec (§6) is simpler. |
| Validating answers only inside `calculate_score` | The player would get a normal "answered" reply for a rejected answer; the gateway check gives an error instead and writes no row. Both are kept. |

## 8. Future T8 compatibility (not implemented in T7)

> **T8 note (2026-10-03):** T8 widened the §4 config rules (`docs/plans/t8-images.md` §5):
> `pin_drop` config may contain only `imageId` (grid background) and `promptImageId`;
> `closest_guess` config may contain only `unit` and `promptImageId`. Answer and scoring rules are
> unchanged; a background never changes scoring. The rendering rule below is what `PinGrid`
> implements, using the loaded image's natural size.

T8 may add an optional `config.imageId` to `pin_drop` (the only `config` key change). Rendering
rule: **preserve the image's aspect ratio and center it inside the grid square.** With grid
square side `L = 10g`, an image `w × h` is scaled by `s = min(L/w, L/h)`, drawn at
`w·s × h·s`, offset `((L − w·s)/2, (L − h·s)/2)` from the grid square's top-left
(i.e. from pixel `(P, P)`). Image first, grid lines on top.

- Coordinates never depend on the image; §6 is unchanged, and questions without `imageId`
  behave exactly as in T7.
- Grid points outside the displayed image (in letterbox bands) remain valid targets and
  answers. The image is decoration under the grid; the editor preview shows which points lie on
  it; the server does not check this.
- Limitations of integer snapping with images: 121 points ≈ one tenth of the image width per
  step, so small features cannot be targeted precisely; a target is exactly one point (regions
  are out of scope).

## 9. Known limitations (deferred, not fixed in T7)

These exist in the current code, were found during the T7 context review, and are recorded for
a later joint review. They have not been tested against the new types, so they may appear in
normal play.

| ID | Limitation | Possible effect on the new types |
|---|---|---|
| C1 | Updates to the four existing types skip structural validation (`QuestionUpdate` has no structure validator; `update_question` saves any non-`None` field). T7 closes this only for the new types (§5.2). | None for the new types. A direct API call can still save an invalid existing-type question; the host editor always sends complete, well-formed payloads. |
| P1 | Any server `error` event replaces the player's game screen with a "Back to Join" screen until the socket reconnects (`frontend/player/src/pages/game/GameLayout.tsx`). | A player submitting just as the question auto-locks gets "Question is locked" and this screen, for any type. |
| U1 | `submit_answer` checks "already answered" in Redis before the DB insert and marks it after; no DB uniqueness constraint; python-socketio runs a client's handlers concurrently by default. Concurrent submissions might both be scored. Not reproduced. | Applies equally; the UI disables Submit after one tap. |
| U3 | If a connection drop shows the P1 error screen, `QuestionPage` remounts and loses local state. Not verified. | A selected but unsubmitted pin or typed guess may be lost. |
| C2 | For the four existing types, a wrongly-typed answer raises inside `calculate_score` and the handler dies with no reply. | None — new types are validated first (§5.4). |
| P2 | Create-time 422 errors show only "HTTP 422" in the host editor (list-shaped `detail`). | Mitigated, not fixed: the editor checks the new types' rules client-side before saving (§5.6); a request that still fails on create shows no reason. Update shows one (§5.2). |
| U4 | Observed during manual testing (2026-10-02, room SC2NDD): two `host_advance` events processed together started the same question twice, 6 ms apart (`question_started index=1` logged twice). Likely the same check-then-act gap as U1, in `_on_host_advance_impl`. Not investigated. | Any type; possibly a double-click on "Next Question" or a click racing auto-advance. |
| A1 | Observed during manual testing (2026-10-02): access tokens expire after 2 hours (`auth_service.py`, `exp = now + 2h`), but the Admin app's route guard (`frontend/admin/src/App.tsx`) only checks that a token exists, and its API client does not redirect on 401. An expired session shows "Invalid or expired token" on every page instead of returning to `/login`; the user must log out or clear site data. Pre-existing, not caused by T7/T9. | None directly; affects anyone testing T7 in the Admin app after 2 hours. |
| F1 | Python float sums can show binary noise (e.g. `0.30000000000000004`) in CSV totals, which write raw sums. Existing behaviour for any fractional points. | `closest_guess` partial credit makes fractional scores more common. Accepted; on-screen displays use §5.8. |

## 10. Tests (T5) — `tests/integration/test_t7_question_types.py`

Against the live Docker stack, reusing `tests/integration/conftest.py` helpers.

1. **Authoring:** create, list, update, export and re-import each new type in both grading
   modes. Rejected on **create and update** with 422: type switch without matching data;
   `pin_drop` target out of range, float or boolean coordinate; extra `config` keys;
   `closest_guess` with `P < F`, negative `F`, non-finite or boolean numbers, unit > 20 chars,
   empty unit, unit with leading/trailing whitespace;
   non-empty `answer_data` under COMPLETENESS; `points_value` 0 under ACCURACY. An update error
   response carries a string `detail`.
2. **Update validation scope:** a partial update of a new-type question (e.g. only
   `answer_data` with an out-of-range target) → 422, question unchanged; a valid partial update
   (prompt only) still succeeds; `PUT {"type": "multiple_choice"}` on a `pin_drop` question with
   no options → 422; sending only `grading_type: "COMPLETENESS"` on an ACCURACY `pin_drop` → 422
   (stored target fails the `{}` rule), and succeeds when `answer_data: {}` is sent with it; an
   error `detail` has no `"Value error, "` prefix; an update to an existing multiple-choice
   question behaves as before T7 (regression check).
3. **Pin Drop live game:** exact hit → full points, correct; miss → 0; COMPLETENESS with no
   target → `points_value`; out of range, float or boolean coordinate → error event and **no
   `SessionScore` row**.
4. **Closest Guess live game:** `e = F` → full and correct; `F < e < P` → truncated partial, not
   correct; `e = P` → 0; `F = P`; correct 0 and negative; non-numeric, boolean and over-limit
   values rejected; COMPLETENESS → `points_value` for any valid value. A partial award (e.g.
   6.25) is stored exactly, reported in `answer_received.pointsAwarded`, and included in the
   leaderboard total and the CSV. **Truncates to 0:** points 10, F 0, P 10, e 9.9995 → 0
   points, not correct, band `"miss"` in the host summary. **Cap:** points 10, correct 0, F 0,
   P 1e12, value 1e-8 → 9.99 points, band `"partial"`, not correct.
5. **Results paths:** reveal payloads per mode; host game-over distribution keys (`"x,y"`;
   bands under ACCURACY; none for `closest_guess` COMPLETENESS); Past Quizzes detail; CSV points;
   HTML report returns 200 and contains the type badge.
6. **Existing behaviour, recorded:** a second sequential submit → `alreadyAnswered`; a submit
   after lock → error event (documents P1 as-is).

## 11. Out of scope

T8 images (§8 is guidance only); multi-point or region targets; "Order It"; fixing C1 for the
existing types, P1, U1, U3, C2, P2 or other pre-existing issues; T9 theming of the new UI (new
components use the existing classes so T9 can retheme them).

**Sample games are required, but not in this branch.** Sample games
(T6) are in each new type's scope, and at least one sample game must use each new
type. They are delivered as §12 step 7 on a separate T6 branch and merge request. **T7 is not considered finished until those
files are merged.**

**Pin Drop content for the classroom game (math).** Players solve a problem, then mark the
answer on the grid. These are different question *contents* within the one `pin_drop` type,
not new types: the author writes the prompt and enters the correct coordinates; the app only
compares the selected point with the stored target. **No equation parsing, automatic solving
or graph plotting.** Each question has exactly one correct target with integer coordinates in
[−5, 5]; the author checks this (import also enforces the range). Categories, with checked
examples:

| Category | Example prompt | Target |
|---|---|---|
| Circle center | Mark the center of (x − 2)² + (y + 3)² = 16 | (2, −3) |
| Parabola vertex | Mark the vertex of y = x² + 2x − 3 | (−1, −4) |
| Point on a function | f(x) = 2x − 3. Mark the point on f where x = 4 | (4, 5) |
| Line intersection | Mark where y = x + 1 and y = −x + 3 intersect | (1, 2) |
| Midpoint | Mark the midpoint of A(−4, 1) and B(2, 5) | (−1, 3) |
| Reflect / translate | Reflect P(3, −2) across the y-axis · Move Q(−2, 4) 5 right and 3 down | (−3, −2) · (3, 1) |

## 12. Implementation order

Each step is one focused commit, after the spec is committed and Goldfish-reviewed:

1. Scoped update validation (§5.2), after a heads-up to the T4 owner.
2. Backend: schemas, `game_service`, gateway, report (§5.1, §5.3–§5.5).
3. Host app (§5.6, §6).
4. Player app (§5.7, §6).
5. Integration tests (§10).
6. Context docs (`CLAUDE.md`) for every changed directory.
7. After T7 merges, on a separate T6 branch: two `sample_games/` files (one classroom, one
   party, `"version": 1`) that together use both new types, and each cover all six types and
   both grading modes; each imports cleanly through the Host app (§11).

Frontend behaviour that integration tests cannot reach (§5.6 client checks, §5.7 `parseGuess`
and headline, §6 canvas) is checked by hand against the running app before the merge request.

## 13. Revision history

- **rev 1–3:** design discussion (type choice, simplified Pin Drop, absolute tolerances,
  COMPLETENESS on every path, truncation rule).
- **rev 4:** update validation scoped to the new types; `NEW_TYPES` moved to `schemas/admin.py`
  to avoid a circular import.
- **rev 5 (Goldfish review, fresh session):**
  1. Fractional-score storage verified in code; "no migration" holds (§2.3).
  2. Player results headline for partial credit defined; recap convention stated (§2.6, §5.7).
  3. `closest_guess` input parsing defined: no empty or bare `-`, comma decimals, no exponents,
     client applies the server's bounds (§5.7).
  4. Editor client-side checks added for the new types to mitigate P2 (§5.6).
  5. 422 message format defined (prefix stripped); `HTTPException` matches `import_bundle`;
     `order_index` optional confirmed; grading-only change behaviour stated and tested (§5.2,
     §10).
  6. Per-screen grid size and resize handling (§6); top-5 tie-break (§5.6); number formatting
     (§5.8); float-sum noise recorded (§9 F1).
- **rev 6 (second Goldfish review, fresh session):**
  1. A partial award that truncates to 0 is now a miss, so host bands and player screens agree;
     the band is derived from the award (§3.2).
  2. Partial credit capped at `points_value − 0.01`, because float division can round
     `(P − e)/(P − F)` to exactly 1.0 (§3.2). The helper became
     `closest_guess_result(value, answer_data, points_value) -> (award, band)` (§5.3).
  3. Sample-game scope made consistent: required, delivered on a separate T6 branch as step 7
     (§11, §12).
  4. Unit rules (trimmed, omitted when blank, code-point length) and new-question defaults
     (§3.2, §5.6).
  5. Report summaries computed from score rows, not `_extract_answer_key` (§5.5).
  6. Host grid hidden while the question is locked, like the prompt (§5.6).
- **rev 7 (implementation and mean review):** the gateway and report reuse
  `game_service.answer_reveal` for the new types (§5.4, §5.5); host summaries share
  `components/NewTypeResults.tsx` (§5.6); the editor also blocks an empty prompt, found in
  manual testing (§5.6); the player headline falls back when `currentQuestion` is unknown,
  found in the mean review (§5.7).
- **rev 8 (merged with T9 themes):** all T7 UI uses T9 token classes (`check:colors --strict`
  passes with an empty allowlist); `PinGrid` reads theme tokens at draw time and redraws on a
  theme switch (§6).
