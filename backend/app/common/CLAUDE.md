# common

## Purpose
Cross-cutting backend utilities shared across routers/services: auth dependencies, error types, logging setup, rate limiting.

## Contents
- `__init__.py` — empty.
- `dependencies.py` — FastAPI dependencies: `get_current_user` extracts JWT from Authorization header or `access_token` cookie; `require_admin`, `require_user` (blocks GUEST role); `get_refresh_token` reads the `refresh_token` cookie only, no header fallback.
- `exceptions.py` — `BuzzerError` base plus `NotFoundError`/`ForbiddenError`/`UnauthorizedError`/`ConflictError`, and `register_exception_handlers` wiring these (plus validation errors and unhandled exceptions) to JSON error responses. T8: a SQLAlchemy `DBAPIError` carrying MySQL 1213 (deadlock) or 1205 (lock-wait timeout) becomes **409 `{"error": "BUSY", "message": "Busy, try again"}`** (a fallback, not the locking strategy); any other DB error still goes to the 500 handler.
- `logging.py` — `configure_logging`: structlog setup, console renderer in dev vs JSON renderer in prod; suppresses uvicorn access logs in production only (both dev mode and stress-test mode skip the suppression).
- `rate_limit.py` — slowapi `Limiter` with a key function that disables real rate limiting in development and for requests carrying a matching stress-test header.

## How it fits in
`common/` has no dependency on `schemas/` and only a light one on `models/` (`dependencies.py` imports `models.user.User` and `services.auth_service`). It's imported throughout `backend/app`'s routers and services for auth guards, consistent error responses, and app-startup logging/rate-limit config — it's infrastructure the rest of `backend/app` builds on, not domain logic.

## Gotchas
- `rate_limit.py`'s `_rate_limit_key` returns a random UUID (no real limiting) when `settings.is_development`, and also bypasses limiting for any request whose `X-Stress-Key` header matches `settings.STRESS_TEST_KEY` — must confirm `is_development` and `STRESS_TEST_KEY` are unset/false in production or rate limiting silently no-ops.
- `exceptions.py`'s validation handler routes `exc.errors()` through `jsonable_encoder` with an explicit comment that Pydantic v2 can embed non-JSON-serializable objects in a field validator's `ctx`; skipping that step would crash with a generic 500 instead of returning the real validation error.
- `require_user` allows ADMIN and USER but rejects GUEST — guest accounts (created via the guest-join flow, see `schemas/auth.py`'s `GuestJoinRequest`) cannot use endpoints behind `require_user`.
- `get_current_user` also accepts an `access_token` **cookie**, but nothing sets one: login returns the
  access token in the JSON body (the frontends keep it in `localStorage` and send `Authorization:
  Bearer`), and the only cookie is `refresh_token`, scoped to `path=/api/auth`. So a request the
  browser makes on its own (an `<img src>`, a plain link) carries no credentials.
- `get_current_user` accepts a `"temp"` token identically to a real `"access"` token — a pre-onboarding temp token carries full authorization weight through `require_admin`/`require_user`, not just enough to complete onboarding.
