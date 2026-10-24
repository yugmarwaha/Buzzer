# player/src

## Purpose
Source for the Player app: the mobile-browser client where a player enters a room code, identifies themselves (guest, UW NetID OAuth2, or local login), and answers questions during a live game.

## Contents
- `pages/` — join flow (`JoinPage` → `NamePage`, plus `LoginPage` for the OAuth2 return) and `game/*` (`GameLayout`, `LobbyPage`, `QuestionPage`, `FeedbackPage`, `ResultsPage`, `GameOverPage`). See `pages/CLAUDE.md`.
- `components/` — `QuestionRecap.tsx` plus `ui/` primitives: `button`, `card`, `input`, `TimerBar`, `ThemeToggle`. See `components/CLAUDE.md`.
- `lib/` — `api.ts` (`get`/`post` over `/api`) and `utils.ts` (`cn`, `isTokenExpired`). See `lib/CLAUDE.md`.
- `types/` — shared socket payload types (`QuestionPayload`, `PlayerResultsPayload`, ...), in `types/game`; no child doc.
- `App.tsx`, `main.tsx`, `index.css` — router mount and styles; no child doc. `main.tsx` imports the shared `../../theme.css` before `index.css`. `App.tsx` renders `<ThemeToggle className="player-theme-toggle" />` once, then wraps every `<Routes>` in `<div className="theme-shell">`. `index.css` sets the body to `bg-page`/`text-primary` colors via the CSS variables, reserves the top strip (`.theme-shell { padding-top: var(--strip) }`, `--strip` = 3.75rem + `--banner-h`, default 0px), shrinks `.theme-shell .min-h-screen` by the strip, and positions `.player-theme-toggle` (fixed, top-right).

## Styling
All colors come from the shared token layer (`frontend/theme.css` + `frontend/tailwind.preset.ts`), not raw Tailwind palette classes. Dark is the default; light via `data-theme="light"`, set by the inline script in `index.html` (follows the OS until a choice is saved under `buzzer-theme`). Rules and checks (`npm run check:theme`, `npm run check:colors`) are in `frontend/CLAUDE.md` and `docs/plans/ui-themes.md` (section 3.8). Player-specific: body text >= 16px, tap targets >= 48px, lucide-react icons instead of decorative emoji.

## How it fits in
Dependency direction matches the host app: `pages/` → `components/ui` and `lib/`, with `lib/` at the bottom. Before a game, everything is REST (`/game/rooms/:code/ping`, `/auth/guest`, `/auth/login`, `/auth/exchange-temp`); during a game, `pages/game/GameLayout.tsx` owns the single Socket.IO connection and every nested page reads state through `useGame()`. It is the answering-side counterpart of the host app, against the same backend gateway (`backend/app/websocket/`).

## Gotchas
- **Do not add raw palette classes** (`bg-green-500`, `text-gray-400`, ...) or `text-accent`; use tokens (`text-accent-text`, `text-primary`, `bg-surface`, ...). `npm run check:colors` flags leftovers.
- **Keep the top strip when adding screens:** render new routes inside `App.tsx`'s `theme-shell` so the fixed theme toggle never covers content; use `min-h-screen` (reduced by `.theme-shell`) rather than your own fixed offsets.
- **The question screen intentionally shows no prompt:** players see only the options and timer; the prompt is on the host screen.
- **Pinch-zoom is allowed:** the viewport meta in `index.html` has no `user-scalable`/`maximum-scale` limits; keep it that way.
- **Live state has one owner:** `pages/game/GameLayout.tsx` holds the one socket (kept open for the whole game; see `pages/CLAUDE.md`) and all game state; the other `game/*` pages read it via `useGame()` (see `pages/CLAUDE.md`).
- **No auth gate on routes:** `App.tsx` doesn't guard `/game/:code`; token handling lives in the pages and `lib/utils.ts` (`isTokenExpired`).
- **`LoginPage` bypasses `lib/api`** with raw `fetch()` for the OAuth2 token exchange.

- **Accessibility:** `App.tsx` renders the routes inside `<main class="theme-shell">`. `pages/game/GameLayout.tsx` adds a screen-reader-only `h1` on every game route except game over (which shows its own `h1`). Keep icon-only buttons labelled. The audit used axe in both themes (see `docs/plans/ui-themes.md` section 11).
