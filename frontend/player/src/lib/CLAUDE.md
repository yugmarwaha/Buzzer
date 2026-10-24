# lib

## Purpose
Small shared utilities for the Player app: the HTTP client and generic helpers used by pages
and UI components.

## Contents
- `api.ts` — thin `fetch` wrapper exposing `api.get`/`api.post`. Prefixes paths with `/api`,
  attaches a `Bearer` token from `localStorage` when present, JSON-encodes request bodies, and
  on a non-OK response throws an `ApiError` (with `.status`) using the backend's `detail` field
  (falling back to `HTTP {status}`); `PastQuizzesPage` relies on the status to handle 401/403. Handles empty response bodies.
- `utils.ts` — T7 helpers: `formatNum()` (at most two decimals, no −0) and `parseGuess()`
  (Closest Guess input → number or null: optional `-`, one `.`/`,` separator, no exponent or
  thousands separators, finite, |v| ≤ 1e12 — the server's bounds). T8: `optionLetter`, `optionDisplay` ("Option A" for an image-only option) and `optionAnswerLabel` ("A — text" for answer lines). `cn()` (clsx + a tailwind-merge extended with the shared preset's custom names, kept in step with `frontend/tailwind.preset.ts`) for merging Tailwind class names; `isTokenExpired()`
  decodes a JWT's payload (base64url) client-side and checks its `exp` claim, treating a missing
  or malformed token as expired.

## How it fits in
`lib/` is a leaf dependency of `frontend/player/src`: `pages/` calls `api` for backend requests
(`JoinPage`, `NamePage`) and uses `isTokenExpired` for client-side auth checks (`NamePage`,
`pages/game/GameLayout`); `cn` is used by `button`, `card`, and `input` (not `TimerBar` or `ThemeToggle`) — see
`components/CLAUDE.md`. Nothing in this directory imports from `pages/` or `components/`.

## Gotchas
- Not a styling layer: colors/tokens live in `frontend/theme.css` and `frontend/tailwind.preset.ts`, not here. `cn()` only merges class names (tailwind-merge), so it will not catch a raw palette class.
- `api.ts` swallows JSON-parse failures on error responses (`.catch(() => ({}))`), silently
  falling back to a generic `HTTP {status}` message instead of surfacing the parse error.
- `isTokenExpired` does no signature verification — it's a client-side convenience to avoid
  needless requests / gate navigation, not a security check; real auth must be enforced
  server-side.
- No token-refresh logic here; an expired token is simply treated as "not authenticated" by
  callers (see `pages/game/GameLayout.tsx`, `pages/NamePage.tsx`).
