# Design: Sample Games (T6)

Status: rev 4, implemented and merged (post-merge results in §8 and §10).
Related: T7 design `docs/plans/t7-new-question-types.md`
(defines `pin_drop` and `closest_guess`).

## 1. Problem

T6 adds **two quiz game JSON files** under `sample_games/` that import into the running
application. The set must include:

- one **classroom** game (educational, professional tone) and one **party** game (fun,
  broadly accessible);
- **each** game has at least one question of every original type: `multiple_choice`,
  `true_false`, `fill_in_the_blank`, `multi_select`;
- **each** game uses both `ACCURACY` and `COMPLETENESS`;
- at least one game uses **each** of the T7 types (`pin_drop`, `closest_guess`);
- files ship as `"version": 1`, import cleanly, and have new, distinct names (the 12 starter
  files and Yug's `classroom_computer_networks.json` / `party_group_chat_night.json` don't
  count and must not be duplicated).

## 2. Decisions

1. **Two files, both using both T7 types** (stronger than the minimum):
   `sample_games/classroom_coordinate_geometry.json` and
   `sample_games/party_living_room_showdown.json`.
2. **Classroom = coordinate geometry.** Players *solve a problem, then mark the answer on the
   grid*. Six Pin Drop questions cover six problem categories. This is the use the T7 design
   intended and differs from Yug's direct "tap point (x, y)" questions.
3. **Party = general trivia night.** Pin Drop becomes a "follow the dance moves" grid puzzle.
4. **Content only, no code.** The app compares the selected point/number with the stored
   answer; there is no equation parsing, solving or plotting. The author is responsible for
   correct answers.
5. **Prompts contain the whole problem** because the prompt is shown only on the host's
   projector; phones show only answer controls.
6. **No duplicated content** with Yug's games (e.g. Eiffel Tower, treasure hunt, mood grid,
   Pringles, DNS) **or the 12 starter games** (checked by keyword search over `sample_games/`). Every T6 game must use the same types and modes, so the *type/mode pattern*
   (e.g. a COMPLETENESS multi_select) will repeat; the *question content and wording* must not.
   COMPLETENESS questions here are about the subject (methods, examples, confidence), not generic
   "hardest topic / tools / one word" surveys.

## 3. File format and validity rules (from `backend/app/schemas/admin.py`)

Top level: `{"format": "buzzer/game", "version": 1, "game": {...}, "questions": [...]}`.
`game`: `title` 1–255 chars, `description` ≤ 5000, `max_players` 1–500 (use 100).
Every question: `type`, `grading_type`, `prompt` (1–2000 chars), `config`, `answer_data`,
`time_limit_seconds` (2–300), `points_value` (0–100000).
**Question order is the array order**: `quiz_service.import_bundle` assigns `order_index` =
array position (0, 1, 2, …), so the files contain **no `order_index` field** (none of the
existing sample files has one). The tables in §4–§5 are in array order. Import rejects the **whole file** if
any question fails. Prompt HTML other than `<b> <i> <u> <br>` is stripped; Unicode (x², −) is
fine in prompts.

Per-type shapes (ACCURACY; under COMPLETENESS `answer_data` is `{}`):

| type | `config` | `answer_data` |
|---|---|---|
| `multiple_choice` | `{"options": [≥2 strings]}` | `{"answer_points": [one ≥0 number per option]}` |
| `true_false` | `{}` | `{"answer_points": {"true": n, "false": n}}` |
| `fill_in_the_blank` | `{}` | `{"acceptedAnswers": [≥1 non-empty], "answerPoints": [one ≥0 per answer], "editDistance": int ≥0}` |
| `multi_select` | `{"options": [≥2 strings]}` | `{"answer_points": [one number per option; negatives = penalty]}` |
| `pin_drop` | `{}` | `{"target": {"x": int −5..5, "y": int −5..5}}`, `points_value` > 0 |
| `closest_guess` | `{}` or `{"unit": "1–20 chars, no outer spaces"}` | `{"correct": n, "fullTolerance": n ≥ 0, "partialTolerance": n ≥ full}`, finite, \|n\| ≤ 1e12, `points_value` > 0 |

**Correctness traps for hand-written JSON** (from `game_service.calculate_score`), which must
hold for every ACCURACY question or nobody is ever marked correct:

- `multiple_choice` / `true_false`: `points_value` **equals** the highest `answer_points`
  (correct is `points == points_value`).
- `fill_in_the_blank`: `points_value` equals the highest `answerPoints`. Matching ignores case
  and repeated spaces; players type an ASCII hyphen, so negative answers list `"-7"` (and may
  also list `"−7"`).
- `multi_select`: `points_value` equals the **sum of the positive** `answer_points`
  (score = sum of picked options, floored at 0; correct is `score ≥ points_value`).

## 4. Game 1: `classroom_coordinate_geometry.json`

`game`: title **"Coordinate Geometry Check-In"**; description "A 15-minute warm-up for
algebra and geometry: solve each problem, then mark or type the answer. Most questions use the
coordinate grid from −5 to 5."; `max_players` 100.

| # | type / grading | time | pts | prompt | config / answer |
|---|---|---|---|---|---|
| 1 | multiple_choice / ACCURACY | 25 | 1 | What is the slope of the line through (1, 2) and (3, 8)? | options `["2", "3", "4", "6"]`; points `[0, 1, 0, 0]` |
| 2 | true_false / ACCURACY | 20 | 1 | True or false: y = 2x + 1 and y = 2x − 4 are parallel lines. | `{"true": 1, "false": 0}` |
| 3 | fill_in_the_blank / ACCURACY | 30 | 1 | What is the y-intercept of y = 3x − 7? Type a number. | accepted `["-7", "−7"]`, points `[1, 1]`, editDistance 0 |
| 4 | multi_select / ACCURACY | 30 | 3 | Which of these points lie on the parabola y = x²? Select all that apply. | options `["(2, 4)", "(-2, 4)", "(3, 6)", "(0, 0)", "(1, -1)"]`; points `[1, 1, -1, 1, -1]` |
| 5 | pin_drop / ACCURACY | 45 | 2 | Mark the center of the circle (x − 2)² + (y + 3)² = 16. | target (2, −3) |
| 6 | pin_drop / ACCURACY | 45 | 2 | Mark the vertex of the parabola y = x² + 2x − 3. | target (−1, −4) |
| 7 | pin_drop / ACCURACY | 45 | 2 | f(x) = 2x − 3. Mark the point on the graph of f where x = 4. | target (4, 5) |
| 8 | pin_drop / ACCURACY | 45 | 2 | Mark where the lines y = x + 1 and y = −x + 3 intersect. | target (1, 2) |
| 9 | pin_drop / ACCURACY | 45 | 2 | Mark the midpoint of the segment from A(−4, 1) to B(2, 5). | target (−1, 3) |
| 10 | pin_drop / ACCURACY | 45 | 2 | Reflect the point P(3, −2) across the y-axis and mark its image. | target (−3, −2) |
| 11 | closest_guess / ACCURACY | 45 | 2 | Estimate the area of a circle with radius 4. | unit `"square units"`; correct 50.27, full 0.5, partial 5 |
| 12 | multiple_choice / COMPLETENESS | 20 | 1 | To find where two lines cross, which method would you try first? | options `["Graphing", "Substitution", "Elimination", "Guess and check"]` |
| 13 | true_false / COMPLETENESS | 15 | 1 | True or false: I check my answer by plugging it back into the equation. | — |
| 14 | multi_select / COMPLETENESS | 20 | 1 | Which of these graphs have you drawn by hand before? Select all that apply. | options `["Lines", "Parabolas", "Circles", "Absolute value"]` |
| 15 | fill_in_the_blank / COMPLETENESS | 20 | 1 | Name one real-world object shaped like a parabola. | — |
| 16 | closest_guess / COMPLETENESS | 20 | 1 | How confident are you with coordinate geometry, from 0 to 10? | unit `"out of 10"` |

Answer checks (author-verified): slope (8 − 2)/(3 − 1) = 3; equal slopes 2 → parallel; y-intercept
−7; on y = x²: (2,4), (−2,4), (0,0) yes, (3,6) and (1,−1) no; circle center (2, −3);
y = (x + 1)² − 4 → vertex (−1, −4); f(4) = 5; x + 1 = −x + 3 → (1, 2); midpoint
((−4 + 2)/2, (1 + 5)/2) = (−1, 3); reflection across the y-axis negates x → (−3, −2);
π·4² = 50.265… → 50.27.

## 5. Game 2: `party_living_room_showdown.json`

`game`: title **"Living Room Showdown"**; description "A quick party game of surprising facts,
silly votes, and grid dance moves. No studying required."; `max_players` 100.

| # | type / grading | time | pts | prompt | config / answer |
|---|---|---|---|---|---|
| 1 | multiple_choice / ACCURACY | 20 | 1 | Which planet takes longer to spin once on its axis than to orbit the Sun once? | options `["Mars", "Venus", "Jupiter", "Earth"]`; points `[0, 1, 0, 0]` |
| 2 | true_false / ACCURACY | 15 | 1 | True or false: a group of owls is called a parliament. | `{"true": 1, "false": 0}` |
| 3 | fill_in_the_blank / ACCURACY | 20 | 1 | How many keys does a standard piano have? Type a number. | accepted `["88"]`, points `[1]`, editDistance 0 |
| 4 | multi_select / ACCURACY | 25 | 3 | Which of these countries have no coastline (are landlocked)? Select all that apply. | options `["Switzerland", "Portugal", "Bolivia", "Chile", "Nepal"]`; points `[1, -1, 1, -1, 1]` |
| 5 | pin_drop / ACCURACY | 30 | 2 | Dance moves! Start at (0, 0). Step 3 left, 2 up, then 1 right. Where do you end up? | target (−2, 2) |
| 6 | pin_drop / ACCURACY | 30 | 2 | Dance moves! Start at (1, −1). Double both coordinates, then step 2 down. Where are you? | target (2, −4) |
| 7 | closest_guess / ACCURACY | 30 | 2 | How many bones are in an adult human body? | no unit; correct 206, full 2, partial 30 |
| 8 | closest_guess / ACCURACY | 30 | 2 | How tall is the Statue of Liberty, in meters, from the ground to the tip of the torch? | unit `"m"`; correct 93, full 5, partial 30 |
| 9 | multiple_choice / COMPLETENESS | 15 | 1 | Best board game for a party? | options `["Codenames", "Monopoly", "Uno", "Pictionary"]` |
| 10 | true_false / COMPLETENESS | 15 | 1 | True or false: I have fallen asleep during a movie at the cinema. | — |
| 11 | multi_select / COMPLETENESS | 20 | 1 | What would you bring to a picnic? Select all that apply. | options `["Sandwiches", "Fruit", "Board games", "Speaker", "Blanket"]` |
| 12 | fill_in_the_blank / COMPLETENESS | 20 | 1 | What is your go-to karaoke song? | — |
| 13 | pin_drop / ACCURACY | 30 | 2 | Frog hops! A frog starts at (−5, −5). Each hop moves it 2 right and 3 up. Where is it after 3 hops? | target (1, 4) |
| 14 | closest_guess / COMPLETENESS | 20 | 1 | How many cups of coffee or tea have you had today? | unit `"cups"` |

Answer checks: Venus rotates once on its axis in ~243 Earth days and orbits in ~225 (the only
planet with rotation longer than orbit; Mercury is excluded from the options because its
*solar* day, ~176 days, is longer than its 88-day year, which would make a "day" wording
ambiguous); "parliament" is the
collective noun for owls; standard pianos have 88 keys; Switzerland, Bolivia and Nepal are
landlocked, Portugal and Chile have coastlines; (0,0) → (−3,0) → (−3,2) → (−2,2);
(1,−1) → (2,−2) → (2,−4); frog: x = −5 + 3·2 = 1, y = −5 + 3·3 = 4 → (1, 4); adults have 206 bones; the Statue of Liberty is ~93 m (305 ft) from
ground to torch (the statue alone is ~46 m, so the prompt says "from the ground").

## 6. Coverage check

| Requirement | Classroom | Party |
|---|---|---|
| multiple_choice / true_false / fill_in_the_blank / multi_select | Q1 / Q2 / Q3 / Q4 (+ Q12–Q15) | Q1 / Q2 / Q3 / Q4 (+ Q9–Q12) |
| ACCURACY and COMPLETENESS | Q1–Q11 / Q12–Q16 | Q1–Q8, Q13 / Q9–Q12, Q14 |
| pin_drop and closest_guess | Q5–Q10, Q11, Q16 | Q5, Q6, Q13 / Q7, Q8, Q14 |
| version 1, new names | yes | yes |

## 7. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Direct "tap point (x, y)" Pin Drop questions | Tests reading coordinates only; Yug's games already do this. Problem-solving uses the type as designed. |
| New types in only one game (the minimum) | Both games using both types shows each type in both contexts and is safer for grading. |
| Disputed or vague facts (Great Wall length, "average" statistics) | Closest Guess needs one defensible number; tolerances cannot fix a wrong answer. |
| 14 questions (matching Yug) | The six Pin Drop categories make the classroom game 16; about 15 minutes is still fine for class. |
| Adding images | Images are T8; T6 files must import as version 1 now. T8 later added a version 2 format for images (`bc64303`); these files stay version 1 on purpose, because T8 requires unmodified version-1 files to keep importing. |

## 8. Implementation and verification

1. Write both files exactly as §4–§5, with the §3 shapes (ACCURACY questions use full
   `answer_data`; COMPLETENESS questions use `{}`; Closest Guess COMPLETENESS keeps `config.unit`).
2. **Mechanical checks** (no database writes). `docker-compose.yml` mounts only `./backend` into
   the backend container, so `sample_games/` is mounted read-only into a one-off container:

   ```bash
   MSYS_NO_PATHCONV=1 docker compose run --rm -T \n     -v "$PWD/sample_games:/sample_games:ro" -e PYTHONPATH=/app \n     backend python - < <scratch>/check_t6_games.py
   ```

   (`MSYS_NO_PATHCONV=1` stops Git Bash on Windows rewriting `/sample_games`.) The script is a
   one-off verification aid kept in the session scratchpad and **not committed** (the real
   import in step 3 is the deliverable check). For each of the two files it: checks
   `format`/`version`; validates `game` with `GameCreate` and every question with
   `QuestionCreate`; checks no question has `order_index`; asserts the §3 traps (MC/TF/FITB
   `points_value` = highest answer points, multi_select `points_value` = sum of positives);
   asserts the §6 coverage (all four original types, both modes, both T7 types); and
   compares each Pin Drop target and Closest Guess `correct` with §4–§5.
3. **Real import** into the running app through the Host app (course → Quizzes → Import
   JSON), as T6 requires.
4. **Play one round** of each as host and player, in light and dark themes, checking that each
   Pin Drop target and Closest Guess answer scores as expected.
5. Commit the two files (one commit), then push the branch and open a merge request.

**Results (rev 4):**

- Step 2 passed for both files before the commit (`1c869f3`).
- Step 3: both files were imported through the Host app into the `Smoke Test Course` on
  2026-10-02 and again on 2026-10-03. Every import creates a new quiz
  (`quiz_service.import_bundle` never updates an existing one), so each re-import leaves one
  more copy of the quiz in the course. Delete old copies before a demo if one copy is wanted.
- Step 5: merged to `main` in `4a6e690`.
- After T8 (`bc64303`) changed the import code, the step 2 container check ran again on
  2026-10-05 against `main`. It uses `quiz_service._parse_question` (the same per-question
  parser that the import uses) and `GameCreate`. Both files pass as version 1: classroom 16
  questions, party 14 questions, each with all six types and both grading modes, and no image
  fields.

## 9. Out of scope

Changes to the app, README or import format; T8 images; translations.

## 10. Revision history

- **rev 1:** design discussion result.
- **rev 2 (Goldfish review, fresh session):** party Q1 reworded to "spin once on its axis" and
  Mercury removed (its solar day is longer than its year, so "day" was ambiguous); party Q13
  changed from a mood grid (too close to Yug's) to a pizza grid; classroom Q12–Q16 rewritten as
  subject-specific COMPLETENESS questions and Decision 6 clarified (patterns repeat, content
  must not); classroom length unified at about 15 minutes; party Q8 prompt says "in meters";
  §8 gives the exact one-off container command and says the check script is not committed;
  §3 states that question order is array order and files have no `order_index`.
- **rev 3 (review of the written file):** party Q13 was a COMPLETENESS "build your dream
  pizza" grid. One tap gives one point, which cannot "build" a pizza, and "extra cheese vs extra
  veggies" is not one scale. Replaced with an ACCURACY grid puzzle (frog hops → (1, 4)). The
  party game keeps both grading modes (Q9–Q12, Q14 are COMPLETENESS); neither of these games now
  has a COMPLETENESS Pin Drop, which T6 does not require (Yug's games include two). A full
  re-review then found party Q4 (botanical fruits: tomato, carrot, cucumber, avocado) nearly
  duplicated a starter question in `food_myths_and_facts.json`; replaced with landlocked
  countries (keyword search: no overlap). Every scored question was also replayed through
  `calculate_score` with its intended answer (full points, correct) and a wrong answer.
- **rev 4 (after merge and T8):** status set to merged; §8 now records the import results, the
  duplicate copies that re-imports create, and a re-check of both files against the post-T8
  import code (still valid as version 1). §7 notes that T8 added version 2 and why these files
  stay version 1. The game files did not change.
