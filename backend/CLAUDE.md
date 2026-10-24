# backend

## Purpose
The Buzzer server: a FastAPI REST API plus a python-socketio gateway, backed by MySQL (durable data) and Redis (live game-room state). All application code lives in `app/`; see `app/CLAUDE.md` for the layered breakdown.

## Contents
- `app/` — the whole application: `routers/`, `websocket/`, `services/`, `models/`, `schemas/`, `common/`, plus `main.py`, `config.py`, `database.py`, `redis_client.py`, `migrations/`. Context: `app/CLAUDE.md` and one `CLAUDE.md` per subdirectory.
- `Dockerfile`, `requirements.txt`, `alembic.ini`, `scripts/` — build, dependency, migration-config and helper-script files at this level. No context doc covers them.

## How it fits in
Serves the three frontends (`frontend/host`, `frontend/player`, `frontend/admin`). REST endpoints are mounted under `/api`, which is the base path all three frontends' `lib/api.ts` use. Live play runs over socket.io: the Host and Player apps each open one connection from their `pages/game/GameLayout.tsx`. HTTP and socket entry points share `app/services/`, so scoring, room state, and auth rules stay the same on both paths.

## Gotchas
Only the facts that matter before touching anything under `backend/`; the rest is in `app/CLAUDE.md`.
- **Two client contracts sit on top of this code:** the REST shapes in `app/schemas/` and the socket event names and payloads in `app/websocket/`. Host and player each define their own socket payload types (`src/types/game.ts`), and host builds question payloads itself (`QuestionEditorPage`), so backend changes to either contract need matching edits in the frontend apps.
- **Cross-cutting rules** (adding a question type, environment flags that change production behavior) are in `app/CLAUDE.md`.
