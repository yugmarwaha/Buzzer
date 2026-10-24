# pages

## Purpose
Route-level screens for the admin app (users, courses, course access, guests, login). Each file is a top-level page rendered by the app's router.

## Contents
- CoursesPage.tsx — list courses with their HOST users and quiz counts, create/edit course (name + semester), "Manage in Host app" link.
- GuestsPage.tsx — list guest users, merge a guest into a netid account (guest ID + netid; moves all the guest's scores, or promotes the guest in place if no account has that netid yet; a `confirm()` names the guest and the netid first), delete a guest.
- LoginPage.tsx — username/password login card (centered, `max-w-sm`); the theme toggle is mounted by `App.tsx`, not here; stores the returned JWT in `localStorage` and navigates to `/users`.
- UserDetailPage.tsx — single user's profile: edit username/display name/email/password/role, grant/revoke course access (HOST/PLAYER), and a read-only "Quiz access" summary (no per-quiz grants).
- The quiz editor, roster, games and sessions pages moved to the Host app in T4 (`frontend/host/src/pages/`).
- UsersPage.tsx — list users, create a local (username/password) account, click through to UserDetailPage.

## How it fits in
All pages except `LoginPage` are rendered inside `AdminLayout` (glass sidebar with nav, Open Host app, theme toggle and logout, defined in `frontend/admin/src/App.tsx`, not in this directory); each page imports `api` from `../lib/api` for all network calls and the `button`/`card`/`input` primitives (plus `badge`/`spinner`/`motion` where used) from `../components/ui` (not every page uses `CardHeader`/`CardContent`). Pages own all page-specific state and logic themselves — there's no shared hook abstraction. Route registration and the auth gate (`RequireAdmin`, which only checks that a token exists in `localStorage` — no role check despite the name) both live in `App.tsx`.

## Gotchas
- Pages use theme tokens only: color tokens (`text-muted`, `bg-raised`, `text-danger`, etc.), type tokens (`text-title`/`text-heading`/`text-subheading`/`text-body`/`text-caption`), `Badge` for roles and counts, and `Card variant="glass"`/`"glass-subtle"` panels (empty states use `glass-subtle`). While a list loads, Users, Courses and Guests show `.skeleton` rows (`aria-busy`); UserDetailPage shows a `Spinner` with "Loading…". `text-xs` appears only on IDs; small labels are `text-caption` or `text-sm`.
- UserDetailPage hand-rolls `<select>`/`<textarea>`/checkbox markup instead of using a shared component — `components/ui` has no Select/Textarea/Checkbox.
- User edit/password flows: when `user.username === null` (a netid/OAuth account), the username and password inputs are hidden, not shown read-only. The separate "OAuth2 — not editable" label is gated on `user.netid`, not on `user.username`, and appears in the non-editing view.
- Destructive actions use the browser `confirm()` dialog (GuestsPage merge and delete, UserDetailPage's delete) — except UserDetailPage's course access **revoke**, which has no confirmation step at all.
- Admin-role users cannot be deleted from the UI — UserDetailPage hides the delete button for them.
