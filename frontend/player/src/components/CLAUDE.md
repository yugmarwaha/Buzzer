# components

## Purpose
Reusable presentation components for Player pages: `QuestionRecap.tsx` (the shared question
recap, used by `GameOverPage` and `PastQuizDetailPage`) and the `ui/` primitives. All are styled
with shared theme tokens (see `../CLAUDE.md` "Styling"), never raw palette classes.

## Contents
- `QuestionRecap.tsx` — `QuestionRow`, `NotPlayedRow`, `RecapStrip`, `describePlayerAnswer`, `describeCorrectAnswer`, and the `RecapItem` type. `QuestionRow` is a `Card variant="question" edge="left"` whose edge tone shows the result (success full points, warning partial, danger zero, accent completeness; plain `glass` card for no answer), with a `Q{n}` `Badge`, the prompt, `+N` / max points, and an optional `style` prop (for `staggerStyle`). Status is also conveyed by a lucide icon plus color (`Check`/`text-success` for any positive points, `X`/`text-danger`, `Minus`/`text-muted` for no answer, filled `Circle`/`text-accent-text` for completeness questions) and an `sr-only` "Correct:"/"Incorrect:" prefix, so color is never the only signal. `NotPlayedRow` is a dashed-border placeholder for questions an abandoned game never reached. `RecapStrip` is a per-question bar strip (HTML bars on `bg-track`, height = points / max, same status colors, `aria-label` and `title` per bar). Covers all six types, incl. T7 `pin_drop` `(x, y)` and `closest_guess` value + unit. T8: `QuestionRow` takes the payload's `urls` map and shows the question image and the images of the chosen and correct options; option text uses `optionAnswerLabel` ("A — text", or "Option A" for an image-only option).
- `QuestionImage.tsx` — T8 framed image or "Image unavailable" box, and `urlFor`. **Copy of the host app's `components/QuestionImage.tsx`** apart from the header comment.
- `PinGrid.tsx` — Pin Drop canvas (tappable when `onSelect` is given; `maxSize` 360 on phones; optional T8 `imageUrl` background under the grid).
  **Copy of the host app's `components/PinGrid.tsx`** — keep both identical apart from the
  header comment; the conversion is specified in `docs/plans/t7-new-question-types.md` §6.
  Colors come from the theme tokens at draw time (`tokenColors` reads the token channels
  once per draw from the root element's computed style) and it redraws when `data-theme` changes.
- `ui/TimerBar.tsx` — countdown dial (56px SVG ring, seconds inside) for question time limits. Tracks remaining time itself via
  refs (`endTimeRef`, `frozenRef`, `pausedRef`) and a 100ms interval; supports a `paused` prop
  (used while a question is locked) and an optional `onExpire` callback, called once at 0. The arc uses `stroke-timer-1` above 75% remaining, `stroke-timer-2` above 40%, then `stroke-timer-4`, on a `stroke-timer-track` ring; the number pulses in the last 5 seconds (reduced motion: one update per second).
  See Gotchas for its remount contract.
- `ui/button.tsx` — `Button` primitive with `variant` (`default`/`outline`/`ghost`/`destructive`)
  and `size` (`sm`/`md`/`lg`) props, styled with token classes merged via `cn()`. `rounded-lg`, `font-semibold`, `active:scale-95`, and a focus-visible ring recipe (`ring-2 ring-ring ring-offset-2 ring-offset-page`). `default` is `bg-accent text-accent-fg hover:bg-accent-hover`; `destructive` is `bg-danger text-danger-fg hover:bg-danger-hover`. Disabled state is a muted label on a raised background (`disabled:bg-raised disabled:text-muted`; outline/ghost use `disabled:text-muted`), not opacity.
- `ui/card.tsx` — `Card`, `CardHeader`, `CardContent`: plain styled `div` wrappers. `Card` is `rounded-xl border border-line bg-surface/60 shadow-card` (the one elevation level; no shadow in dark).
- `ui/input.tsx` — `Input` primitive: `rounded-lg border border-line-strong bg-surface`, `text-base`, `placeholder-muted`, focus-visible ring.
- `ui/ThemeToggle.tsx` — light/dark switch (`variant` `icon` or `row`). Calls `window.buzzerTheme.set()` from the inline script in `index.html` (which owns the `buzzer-theme` localStorage key and the OS-preference listener; falls back to setting `data-theme` directly) and watches `data-theme` with a `MutationObserver` to keep its icon/label right. The `icon` variant is a 48px (`h-12 w-12`) square. `App.tsx` renders it once with `className="player-theme-toggle"` (fixed top-right, styled in `index.css`), so every route has it; `GameLayout` sets `--banner-h` while the host-disconnected banner shows so it moves below the banner. Same component is copied into each app.

## How it fits in
This is the shared UI-atom layer for `frontend/player/src`, but usage is inconsistent — see
`pages/CLAUDE.md`'s "How it fits in" for which pages use these vs. raw elements.
`JoinPage`/`NamePage`/`SignInPage` use `Button`/`Input`/`Card`, `GameOverPage` uses `Button`/`Card`/`Spinner`, `ResultsPage` uses `Card`/`Badge`/`Progress`/`Spinner`, `PastQuizzesPage` uses `Button`; `QuestionPage` uses `TimerBar` (its option cards and submit buttons are raw elements with token classes, see `pages/CLAUDE.md`). `App.tsx` mounts `ThemeToggle`.
`button`/`card`/`input` depend on `cn()` from `../lib/utils.ts` for className merging; `TimerBar`
does not (it has no `className` prop and builds classes with plain template literals), and `ThemeToggle` builds its own class strings. None of them import from `../pages`.

## Gotchas
- `button`/`card`/`input` import `cn` via a relative `../../lib/utils` path — moving either
  directory breaks the import. `TimerBar` and `ThemeToggle` have no such import.
- `TimerBar`'s effect has an intentionally empty dependency array (see in-file comment) — if a
  question changes without the parent remounting it via `key`, the timer will not reset.
- No barrel/index file — each `ui/` component is imported by its own file path, not from a
  single entry point.
- Do not add raw palette classes, `text-accent` (use `text-accent-text`), text opacity modifiers, or color classes assembled from string fragments (`bg-${x}-500`); translucent fills only as `bg-page/NN` or `bg-surface/NN`. `TimerBar`'s full class names are spelled out so the Tailwind scan sees them.
- `ThemeToggle` has no effect on the initial theme (the inline script sets `data-theme` before React); it only reads/changes it afterwards.
