# host/src

## Purpose
Source for the Host app: course-first host work (quizzes, active rooms, roster, finished sessions) plus the big-screen live game (lobby → questions → results → game over).

## Contents
- `pages/` — routed screens: `LoginPage`, `CoursesPage`, `course/*` (`CourseLayout` with Quizzes / Rooms / Roster / Sessions / Images tabs), `QuestionEditorPage`, and `game/*` (`GameLayout`, `LobbyPage`, `QuestionPage`, `ResultsPage`, `GameOverPage`). See `pages/CLAUDE.md`.
- `components/` — `ui/` primitives (`button`, `card` with glass and question variants, `input`, `badge`, `progress`, `spinner`, `motion.ts`, `TimerBar`, `ThemeToggle`), `charts/` (inline-SVG charts for the results and game-over screens; see `components/charts/CLAUDE.md`), the T7 `PinGrid`/`NewTypeResults`, and the T8 `QuestionImage`/`ImagePicker`. See `components/CLAUDE.md`.
- `lib/` — `api.ts` (`get`/`post`/`put`/`patch`/`delete`, `postForm`/`putForm`, `download`, and `ApiError` carrying the HTTP status) and `utils.ts` (`cn`, `formatNum`, `optionLetter`/`optionDisplay`). See `lib/CLAUDE.md`.
- `types/` — shared socket payload types (e.g. `QuestionPayload`, `AnswerReveal`, T8 `QuestionConfig` and `ImageUrls`) and REST shapes (`host.ts`, incl. T8 `ImageItem`/`QuestionImages`); no child doc.
- `App.tsx`, `main.tsx`, `index.css` — router mount, theme corner, and styles; no child doc. `App.tsx` mounts `ThemeCorner` (the `ThemeToggle`) above the routes; `main.tsx` imports the shared `../../theme.css` before `index.css`; `index.css` only sets the Tailwind layers and a token-based `body` background/color.

## Styling
Colors come from the shared token layer (`frontend/theme.css` + `frontend/tailwind.preset.ts`), not raw Tailwind palette classes; use classes like `bg-page`/`bg-surface`/`bg-raised`, `text-primary`/`text-secondary`/`text-muted`, `border-line`, `bg-accent`, `text-accent-text`, `success`/`warning`/`danger`, `timer-*`, `shadow-card`. Dark is the default theme, light via `data-theme="light"`. Full rules and the design decisions are in `frontend/CLAUDE.md` and `docs/plans/ui-themes.md` (sections 3.8 and 12: type scale, glass, motion); run `npm run check:theme` and `npm run check:colors` from `frontend/` before committing styling changes. The host's `index.html` has an inline script that sets `data-theme` before React mounts (follows the OS until a choice is saved under `buzzer-theme`, exposes `window.buzzerTheme.set`).

## How it fits in
Dependency direction: `pages/` → `components/ui`, `components/charts` and `lib/`; `components/` → `lib/utils` (except `TimerBar`); `lib/` depends on nothing here. The host talks to the backend two ways: REST (mostly via `lib/api`; `LoginPage` calls `/api/auth/exchange-temp` with raw `fetch`) and a Socket.IO connection owned solely by `pages/game/GameLayout.tsx`, which exposes all live state to child pages through `useGame()`. The host is the only client that drives the game: it advances phases, locks questions, and shows results. Its counterparts are `backend/app/routers/host.py` (the main REST counterpart), `backend/app/routers/game.py`, and `backend/app/websocket/`.

## Gotchas
- **No raw palette classes:** don't add `bg-red-500`-style classes, `text-accent` (use `text-accent-text`), text opacity modifiers, or color classes assembled from string fragments; `check:colors` flags them. The only color exemptions are `theme-exempt`-marked lines, e.g. the white QR-code wrappers in `LobbyPage` and `GameLayout`.
- **`ThemeToggle` placement:** `App.tsx`'s `ThemeCorner` fixes it top-right on every route except `/game/*`, where it moves top-left because `GameLayout`'s End game button is top-right. Don't add other fixed top-corner controls without checking this.
- **Live state has one owner:** `pages/game/GameLayout.tsx` holds the socket and all game state; the other `game/*` pages read it via `useGame()` (see `pages/CLAUDE.md`).
- **Routing:** `App.tsx` guards `/courses/*`, `/quizzes/:gameId/edit`, and `/game/:code` with a token-exists check in `localStorage` only; `/home` redirects to `/courses`. `/` and unknown paths go to `/courses` with a live token the Host can use (unexpired access token, ADMIN or USER role), else `/login`; `LoginPage` redirects to `/courses` the same way (except during the OAuth return). Route guards still check only that a token exists. `types/host.ts` holds the `/host/*` REST shapes.
- **T8 images come only from URL maps.** A question's `config` holds image ids; every screen looks the URL up in the payload's `imageUrls` (or the editor's `images`) through `components/QuestionImage`, never builds one, and shows "Image unavailable" for a missing id. Image-only options read "Option A" (`optionDisplay`).
- **`ResultsPage` and `GameOverPage` build their answer rows separately** (both then render `charts/AnswerBars`) and differ; change both when changing answer display.

- **Accessibility:** `App.tsx` wraps the toggle and routes in `<main>`. `pages/game/GameLayout.tsx` adds a screen-reader-only `h1` on the question and results routes (lobby and game over show their own `h1`). Icon-only buttons need an `aria-label`, and the QR codes carry a `title`. The audit used axe in both themes (see `docs/plans/ui-themes.md` section 11).
