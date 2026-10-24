# admin/src

## Purpose
Source for the Admin app: an admin-first console for users, courses, course access, and guests. Quiz, roster, and session work lives in the Host app (linked from the sidebar via `lib/hostApp.ts`). No live-game or socket.io involvement; it is a pure REST client.

## Contents
- `pages/` — one screen per resource: `UsersPage`, `UserDetailPage`, `CoursesPage`, `GuestsPage`, `LoginPage`. See `pages/CLAUDE.md`.
- `components/` — `ui/` primitives only: `button`, `card` (with glass variants), `input`, `badge`, `progress`, `spinner`, `motion.ts` (`staggerStyle`), `ThemeToggle`. See `components/CLAUDE.md`.
- `lib/` — `api.ts` (full verb set plus `postForm` and `download`) and `utils.ts` (`cn`). See `lib/CLAUDE.md`.
- `App.tsx` — route registration, `AdminLayout` (a floating `glass-strong` sidebar on desktop, a top bar on small screens; nav, then a footer with Open Host app, the `ThemeToggle` row and Logout, aligned through a wrapper with `[&>button]:` child classes because `ThemeToggle.tsx` must stay identical in all three apps), the `RequireAdmin` gate, and the `/login` route, which mounts a fixed top-right `ThemeToggle` beside `LoginPage`; per `pages/CLAUDE.md`. No child doc.
- `main.tsx`, `index.css` — entry point (imports `../../theme.css` before `./index.css`) and Tailwind layers; `index.css` sets the body background and text from the `page`/`primary` tokens.

## How it fits in
Simplest of the three apps: `pages/` → `components/ui` and `lib/api`, with `lib/utils.ts` (`cn`) under the UI primitives. All calls hit `/admin/*` on `backend/app/routers/admin.py` except `/auth/login`. Unlike host/player, there's no `types/` directory and no shared state hook; each page owns its own state.

## Gotchas
- **Styling is token-only:** use `bg-page`/`bg-surface`/`bg-raised`, `text-primary`/`text-secondary`/`text-muted`, `border-line`, `bg-accent`, status colors, the type tokens (`text-heading`, `text-body`, `text-caption`, ...), and `.glass*` panels, never Tailwind palette classes or hex. Both themes come from `frontend/theme.css`; see `frontend/CLAUDE.md`. `index.html` carries the shared inline theme script.
- **`RequireAdmin` uses `hasLiveAdminToken()`** (`lib/api.ts`): an unexpired access token with the ADMIN role (10 s clock skew, fail closed). An expired or non-admin token is cleared and the user returns to `/login`. A 401 from any API call clears the token and returns to `/login` once (except the login call). The real gate is still the backend's `require_admin`.
- **Host work is not here:** the roster wizard and question editor (with their client-side CSV parsing and `config`/`answer_data` building) moved to `frontend/host/src/pages/`.

- **Accessibility:** the login route is wrapped in `<main>` (the signed-in layout already has `<aside>` and `<main>`). Icon-only buttons need an `aria-label` (course Edit has one). The audit used axe in both themes (see `docs/plans/ui-themes.md` section 11).
