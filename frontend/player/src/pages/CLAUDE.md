# pages

## Purpose
Route-level screens for the Player app: the pre-game join/auth flow, plus (under `game/`) the
screens shown during an active game session.

## Contents
- `JoinPage.tsx` — entry screen; player enters a 6-char room code, pings the backend
  (`GET /game/rooms/:code/ping`) to validate it exists, then navigates to `/name/:code`.
  Auto-submits if a `code` query param is present (QR-code deep link). Also links to `/past`.
- `LoginPage.tsx` — landing page for the OAuth2 redirect (`/player/login?from=oauth2#oauth2_data=...`).
  Parses the temp token out of the URL hash, exchanges it via `POST /api/auth/exchange-temp`,
  stores the access token, and returns the player to the room they were joining (room code read
  from `sessionStorage.joinRoomCode`), or to `/past` when `sessionStorage.postLoginPath === '/past'`
  (set by `SignInPage`; read and removed here). If hit without `?from=oauth2` it redirects straight to
  `/join` instead of attempting any exchange; if the backend hands back an `error` query param,
  that's shown directly and short-circuits before the hash/exchange logic ever runs.
- `NamePage.tsx` — identification step after the room code is validated. Three modes: `guest`
  (name + email, `POST /auth/guest`), `netid` (UW OAuth2 link, or straight to lobby if already
  authenticated, with a "Use a different account" link that removes only `token` and shows
  the guest/sign-in options again), `local` (username/password, `POST /auth/login`). Stores the access token (and,
  for guests, name/email) in `localStorage`, then navigates to `/game/:code/lobby`.
- `SignInPage.tsx` — NetID (sets `sessionStorage.postLoginPath = "/past"`) or local sign-in without a room code.
- `PastQuizzesPage.tsx` / `PastQuizDetailPage.tsx` — a signed-in non-guest's finished quizzes (`/player/past-quizzes`). The list is glass link cards (`.lift`) with an "Abandoned" `Badge` where needed and a score `Progress` bar; the detail page renders the shared `components/QuestionRecap.tsx` rows (with the T8 `image_urls` map), and unreached questions show only "Not played".
- `game/GameLayout.tsx` — layout route for the active game, rendered via `<Outlet>`. Owns the
  single `socket.io` connection (auth'd with the stored token), tracks all shared game state
  (phase, game status, player count, current question, results, game-over payload), and exposes
  it through a `GameContext` / `useGame()` hook. Listens for every server socket event
  (`sync_state`, `player_joined`, `new_question`, `question_locked`/`unlocked`, `answer_received`,
  `question_results`, `game_over`, `host_disconnected`, `game_abandoned`, `error`) and drives
  navigation to the matching child route. Also renders a host-disconnected banner and a
  connection-error screen. While the banner shows (`hostDisconnected && phase !== 'gameover'`)
  it sets `--banner-h: 2.25rem` on `<html>` (removed otherwise and on unmount) so the theme
  toggle and the reserved top strip from `index.css` move below the fixed 2.25rem (`h-9`) banner.
- `game/LobbyPage.tsx` — waiting-room screen (before start / between questions); shows room
  code (glass card, `text-code`), player count (`Badge`, number replays `animate-tick`), and status
  text from `useGame()`, with three `animate-pulse-soft` waiting dots that loop in turn while the player waits (still dots under reduced motion).
- `game/QuestionPage.tsx` — renders the active question, branching on `currentQuestion.type`
  (`multiple_choice`, `true_false`, `fill_in_the_blank`, `multi_select`, and T7 `pin_drop` —
  tappable `PinGrid`, sends `{x, y}` — and `closest_guess` — text input + ± button, Submit only
  when `parseGuess` succeeds, sends `{value}`). Tracks local
  submitted/selected state, times the answer from mount, and calls `emitAnswer()` from
  `useGame()`. The default export renders the view with `key={questionKey}` (bumped by
  `GameLayout` on every `new_question`, also a resend of the same question after a reconnect
  when the server has no answer), so local state starts fresh per push. Locks inputs once
  submitted, `questionLocked`, or the local `TimerBar` reaches 0 (`onExpire`; a later host
  unlock clears it). Shows only "Question N of M",
  the `TimerBar` (in a glass header with a question-type `Badge`) and the answer controls —
  deliberately no question prompt. Options are tactile cards from the `OPTION_*`/`CHIP_*`
  constants: idle `glass-strong` + `border-2 border-line-strong`, `rounded-2xl`, `min-h-16`, with
  an A/B/C letter chip; selected `bg-accent-hint` + `border-accent-text` and an accent chip with a
  popping `Check`; locked `glass-subtle` with a `Lock` icon (full text contrast). Entrance
  animation sits on the `<li>` (not the button) so the press scale works. Multiple choice submits
  on tap and keeps the tapped card highlighted (local `chosen`, display only). Multi-select has
  `aria-pressed` and a sticky bottom `ActionBar` Submit showing the count; Pin Drop also uses
  `ActionBar`. True/false are two big cards side by side (two columns at every width).
  Fill-in-the-blank and Closest Guess use `Input` + `Button` in a glass form; after submit they
  show `LockedInCard` (popping check plus the typed answer). The "Answers locked" pill uses
  `text-warning` on `bg-warning-tint`. T8: an option with an image wraps onto a second row where
  the image fills the card width (up to 40vh, eager), so slide-like images are readable on a
  phone and the whole card stays the tap target; the Pin Drop grid draws its background. Images
  come from `currentQuestion.imageUrls`.
- `game/FeedbackPage.tsx` — static "Answer locked in!" glass card (success ring with a popping `Check`, `Spinner`) shown between submitting an
  answer and the host revealing results.
- `game/ResultsPage.tsx` — per-question results after reveal: a `question` card (tone by result) with a large icon disc and the headline correct/incorrect (or "recorded"
  for completeness-type questions) in `text-success`/`text-danger`/`text-accent-text`, a "No answer" badge when `lastAnswerData` is null, points earned (counts up through a local `useCountUp` hook in whole numbers, ending on the exact value; instant under reduced motion; `sr-only` final value; points and total use `formatPoints`, up to 2 decimals, no trailing zeros), then a glass card with the player's answer and the reveal, Total and Rank tiles, and a "Top N%" / "In the lead" badge. Its own `describeAnswer` (separate from `QuestionRecap`'s)
  covers every question type, including `multi_select` ("A — ping, C — Wireshark", or "No selection"). "Correct" here means
  `yourPoints > 0`, except for `closest_guess` when `currentQuestion` is known: full points →
  "Correct!", 0 < points < full → "Partial credit" (`text-warning`), 0 → "Incorrect". T7
  answers also show the target or the correct value and "off by e".
- `game/GameOverPage.tsx` — final screen: a glass rank hero (trophy/medal disc for places 1-3 in warning/accent/success tints, a flag otherwise), final rank and score, the `RecapStrip` bar strip, then a full question-by-question
  recap (`QuestionRow`, from `components/QuestionRecap.tsx`, which also holds the
  `describePlayerAnswer`/`describeCorrectAnswer` per-question-type helpers). "Play Again" clears the token and returns to `/join`.

## How it fits in
These are the routed screens mounted by the Player app's router. The top-level files form the
join flow (`Join` → `Name` → `game/*`); `game/GameLayout.tsx` mounts under `/game/:code` and owns
the socket connection that every nested game page reads via `useGame()`. UI-atom usage is
inconsistent: `JoinPage`/`NamePage`/`SignInPage` use `Button`/`Input`/`Card` (glass cards), `NamePage` also uses `Badge`, `LoginPage` uses `Card`/`Spinner`, `PastQuizzesPage` uses `Button`/`Card`/`Badge`/`Progress`, `PastQuizDetailPage` uses `Card`/`Badge`/`Spinner`, `GameOverPage` uses `Button`/`Card`/`Spinner`, `ResultsPage` uses `Card`/`Badge`/`Progress`/`Spinner`, and `GameLayout`/`LobbyPage`/`FeedbackPage`/`QuestionPage` use `Button`/`Input`/`Badge`/`Spinner`
plus raw elements with token classes (answer cards, glass panels). Every page renders inside `App.tsx`'s `theme-shell` strip (fixed theme toggle on top), so pages use `min-h-screen` and never position content under the top-right corner. `LoginPage` is the one page that also bypasses `lib/api`, calling
raw `fetch()` directly.

## Gotchas
- **Styling:** token classes only (see `../CLAUDE.md` "Styling", `frontend/CLAUDE.md`, `docs/plans/ui-themes.md`); no raw palette classes, no `text-accent` (use `text-accent-text`). `npm run check:colors` from the repo root catches leftovers. Disabled controls (including `QuestionPage`'s submit buttons) use a muted label on `bg-raised`, not opacity.
- **The question screen has no prompt on purpose** — do not "fix" this; the prompt is shown on the host display.
- Auth/session state is spread across `localStorage` (`token`, `playerName`, `playerEmail`) and
  `sessionStorage` (`joinRoomCode`, `postLoginPath`), touched independently by `LoginPage`, `NamePage`,
  `GameLayout`, and `GameOverPage` (which clears `token` on "Play Again") — `NamePage` also
  writes `sessionStorage.joinRoomCode` for the netid-OAuth redirect path, not just `LoginPage`.
  No single helper owns any of it besides `isTokenExpired` in `lib/utils.ts`. `JoinPage` does
  not touch storage at all.
- `GameLayout` keeps ONE socket for the whole game: the socket effect depends only on `[code]`.
  `navigate` is held in `navigateRef` (react-router returns a new `navigate` on every pathname
  change; depending on it used to tear down and reopen the socket on each in-game navigation and
  dropped lock/unlock events). Always call `navigateRef.current(...)` inside the effect. `phaseRef`
  is not a general mirror of `phase` — it's a one-way latch, initialized to `'lobby'` and set
  to `'gameover'` by the `game_over` handler or a `sync_state` carrying `gameOver`, to let
  long-lived closures know the game has ended.
- `GameLayout`'s `connect` handler re-emits `join_room` on every socket `connect` event, not just
  the first — guarded only by `phaseRef.current !== 'gameover'`. A mid-game network drop with
  socket.io auto-reconnect silently re-joins the room.
- Reconnect uses `join_room`, never `rejoin_room`. Mid-question, the backend's late-join path
  re-sends `new_question` only if the question is open, unlocked, and this player hasn't answered
  (with `timeLimitSeconds` set to the remaining time, floor 5s).
- A server `error` event before this socket's join succeeded (`joinedRef`, reset on each
  `connect`, set by `sync_state`: auth, "This game is not accepting players"...) sets `error`,
  and `GameLayout` renders a full-screen error with only "Back to Join" in place of
  `<Outlet/>` (also used for `connect_error`; cleared by the next `connect`). After the join,
  errors (e.g. "Question is locked", "No active question" for a late `submit_answer`) set
  `notice` instead: a `role="alert"` toast at the bottom that hides after 4 s; the game keeps running.
- `QuestionPage` falls back to "Unsupported question type." for any type without a branch.
- `GameLayout`'s `answer_received` handler ignores the event when `data.alreadyAnswered` is
  true (a duplicate-submit echo), skipping the feedback-screen navigation.
- `GameLayout`'s `sync_state` handler sets `questionLocked` from `data.questionLocked` (mirrors the
  server flag both ways, so a stale local lock can't survive a resync, e.g. options staying locked
  after the host advances), then branches: any `gameOver` → game over from whatever screen is showing (latches `phaseRef` and
  disconnects; a join that raced the final advance gets this); LOBBY → lobby; IN_PROGRESS →
  restores `currentQuestion` and `lastAnswerData` from `question`/`yourAnswer` (the player's own
  answer, so ResultsPage's "You answered" line survives a reload, also when results arrive live
  after a reload on feedback; cleared when `yourAnswer` is for another question), then results
  (from `questionResults`) or feedback (`hasAnswered`), otherwise it waits for the `new_question`
  push (which carries `questionLocked` for a locked question). `ABANDONED` is a no-op. A
  `sync_state` arriving after game over (built before the game ended) is ignored.
- `question_locked`/`question_unlocked` are ignored when their `questionId` isn't the current
  question; `host_reconnected` clears the host-disconnected banner, and so does a `new_question`
  unless it is flagged `questionLocked` (a reconnect push of a question locked while the host is away).
- `JoinPage`'s auto-submit effect and `LoginPage`'s OAuth-exchange effect both carry
  `eslint-disable-next-line react-hooks/exhaustive-deps` — deliberately mount-once, not a missed
  dependency.
- `useGame()` throws if called outside `GameLayout`'s subtree — all `game/*` pages depend on
  being rendered through that layout's `<Outlet>`.
- Shared payload/type shapes (`QuestionPayload`, `PlayerResultsPayload`, etc.) live in
  `../../types/game`, outside this directory — not documented here.
- Once a game starts, navigation between `game/*` pages is driven entirely by server socket
  events dispatched in `GameLayout`, not by user-initiated client-side routing.
