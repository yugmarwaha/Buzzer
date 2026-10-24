# lib

## Purpose
Shared, non-UI helper code for the admin app: the API client and a small className utility.

## Contents
- api.ts — `api` object wrapping `fetch` against a hardcoded `/api` base path. Provides `get`/`post`/`put`/`patch`/`delete` (JSON body, `Authorization: Bearer <token>` from `localStorage.getItem('token')`), `postForm` (multipart file upload), and `download` (fetches a file response, reads the filename from `Content-Disposition`, and triggers a browser download via an object URL). Non-OK responses throw an `Error` carrying the backend's `message` (app errors) or `detail` (FastAPI errors), else `HTTP <status>`.
- hostApp.ts — `HOST_APP_URL` (defaults to `/host`, overridable with `VITE_HOST_APP_URL`) and `hostAppLink(path)`, used by `App.tsx` and `CoursesPage` to link into the Host app.
- utils.ts — `cn()`: merges `clsx` + `tailwind-merge` for conditional/deduplicated Tailwind class strings.

## How it fits in
`lib/api.ts`'s `api` object is imported by every page in `frontend/admin/src/pages` for all backend calls. `lib/utils.ts`'s `cn` is imported by every component in `frontend/admin/src/components/ui` to merge class names. This directory has no dependency on `pages/` or `components/` — it's the base layer both depend on.

## Gotchas
- No theme code lives here: theme switching is the inline script in `index.html` plus `components/ui/ThemeToggle.tsx`; the `buzzer-theme` localStorage key is separate from the `token` key `api.ts` reads.
- Auth token handling is minimal: JWT is read straight from `localStorage` on every request, with no refresh, expiry check, or 401-triggered redirect to login — an expired token surfaces only as a thrown `Error` from whatever page made the call.
- Error parsing (`res.json().catch(() => ({}))`) silently swallows non-JSON error bodies and falls back to a generic `HTTP <status>` message — callers relying on `err.message` for detail may get a vague message on non-JSON failures.
- `BASE` (`/api`) is hardcoded, not read from an env var — assumes the admin app is always served behind a proxy/gateway that rewrites `/api`.
- `apiFetch` sets `Content-Type: application/json` on every request, including GET/DELETE; `postForm` and `download` deliberately omit it so the browser can set the multipart boundary — don't copy the `apiFetch` header block onto a new verb without checking which one you need.
- An empty response body resolves to `{}` cast to the expected type rather than erroring (in both `apiFetch` and `postForm`) — a 204 response typed as `Promise<Foo>` silently hands the caller an empty object.
