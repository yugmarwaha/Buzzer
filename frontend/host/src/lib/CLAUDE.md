# lib

## Purpose
Small shared utilities for the Host app: the HTTP client and a Tailwind class-merging helper.

## Contents
- api.ts — `api` object (`get`/`post`/`put`/`patch`/`delete`, `postForm`/`putForm` for multipart (T8 image upload/replace), `download` for file responses) wrapping `fetch` against a `/api` base path; attaches `Authorization: Bearer <token>` from `localStorage` when present, JSON-encodes bodies, and throws an `ApiError` (with `.status`) carrying the backend's `detail` message (or `HTTP <status>`) on a non-OK response. `errorMessage()` turns a caught error into display text. `hasLiveToken()` is true when a stored token is an `access` token with role ADMIN or USER (not GUEST) and a numeric `exp` not passed (10 s skew; fails closed; no signature check); `App.tsx` and `LoginPage` use it to skip the login form.
- utils.ts — `cn()`: combines `clsx` (conditional class names) and `tailwind-merge` (dedupes/overrides conflicting Tailwind classes) into one helper. Its tailwind-merge is `extendTailwindMerge`, taught the shared preset's custom names (font sizes, shadows, `rounded-pill`, durations, easings) so e.g. `text-primary` and `text-heading` are not merged away; keep that list in step with `frontend/tailwind.preset.ts`. `formatNum()`: T7 number display (at most two decimals, trailing zeros removed, no −0). T8: `optionLetter(i)` (A, B, … then numbers) and `optionDisplay(text, i)` (the option text, or "Option A" for an image-only option — the one label rule for every screen).

## How it fits in
These are the lowest-level shared modules under frontend/host/src. Not every page calls `api.*` — see `pages/CLAUDE.md`'s "How it fits in" for which ones do. `cn()` is used only by `components/ui/button`/`card`/`input`, not `TimerBar` or `ThemeToggle` — see `components/CLAUDE.md`.

## Gotchas
- `api.ts` reads the auth token from `localStorage.getItem('token')` on every call — there's no in-memory token cache or refresh logic here; a stale/missing token simply results in an unauthenticated request (backend then returns an error the caller surfaces).
- Non-JSON or empty response bodies are handled (`apiFetch` returns `{}` cast to `T` if the response text is empty), but a genuinely malformed JSON body on an OK response would throw uncaught.
- Error parsing on failure (`res.json().catch(() => ({}))`) assumes a JSON error body; a non-JSON error response falls back to the generic `HTTP <status>` message.
- `toError` uses `message`, else a string `detail`, else the first `msg` of a list-shaped `detail` (the backend's 422 handler in `common/exceptions.py` sends `{error, detail: [...]}` with no `message`; a leading "Value error, " is stripped), else `HTTP <status>`.
