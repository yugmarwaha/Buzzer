# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Buzzer** is a Jackbox-inspired real-time party quiz platform. A **Host** displays game state on a large screen while **Players** join and answer questions from their mobile browsers.

## Development Commands

### Docker Environment
```bash
docker compose up
docker compose up --build
docker compose down
docker compose logs -f [service_name]
docker compose exec backend [command]
```

### Backend (FastAPI / Python)
```bash
# Run inside the container or with venv active
alembic upgrade head
alembic revision --autogenerate -m "description"
pytest
pytest --cov=app
ruff check .
ruff format .
```

### Frontend Applications
```bash
npm run dev           # all three apps concurrently
npm run dev:host
npm run dev:player
npm run dev:admin
npm run build
npm run check:theme   # theme contrast, token parity with the Tailwind preset, identical theme scripts, OS default + persistence
npm run check:colors  # report raw colors outside tokens (add -- --strict to fail on any)
```

### Scripts
```bash
python scripts/seed_demo.py
python scripts/simulate_players.py --room ABCDE1
python scripts/smoke_test_websocket.py
# Test-only cleanup of an integration-test quiz and/or images (Test Course only)
docker compose exec -T backend python -m scripts.cleanup_test_quiz --game-id 42 --image-id 7
# Course-ownership migration planning (read-only; see docs/design-host-admin.md §4)
docker compose run --rm backend python -m scripts.plan_game_courses
```

## Design workflow (Elephant-Goldfish)

For larger features, build context bottom-up, discuss a design, write it down, test the spec in a
fresh session, then implement. This repo ships the process as Claude Code skills and rules.

**Context hierarchy.** Nested `CLAUDE.md` files give per-directory context. Claude Code loads
them automatically as it reads into a directory. They cover:

```
backend/app/routers/       backend/app/services/     backend/app/websocket/
backend/app/models/        backend/app/schemas/      backend/app/common/
frontend/host/src/pages/       frontend/host/src/components/       frontend/host/src/lib/
frontend/player/src/pages/     frontend/player/src/components/     frontend/player/src/lib/
frontend/admin/src/pages/      frontend/admin/src/components/      frontend/admin/src/lib/
```

**Skills for each phase:**

| Phase | Skill | What it does |
|---|---|---|
| Prerequisite | `context-hierarchy` | Build or refresh the context hierarchy, leaves to root |
| Prerequisite | `verify-context` | Check existing CLAUDE.md files against their directories |
| 1 — Growing the Elephant | `design-discussion` | Scope a feature, load only the relevant context, discuss design (no code) |
| 2 — Teaching the Elephant | `write-spec` | Turn an agreed design into `docs/plans/<feature-slug>.md` |
| 3 — Goldfish Protocol | `goldfish-test` | In a genuinely fresh session: comprehension, critic, and readiness checks against the spec |
| 4 — Implementation | *(no skill — just build it)* | Write the code against the goldfish-tested spec |
| 4 — closing | `mean-review` | Adversarial review of the finished implementation |

Invoke any of these explicitly with `/<name>`, or let Claude pick them up automatically when the
conversation matches. `.claude/rules/` enforces: `no-code-during-design`, `sycophant-challenge`,
`context-sync`, `small-meaningful-commits`,
`ste-writing-style`, and `no-commit-without-approval` — see each file for what it does.

Design docs live in `docs/plans/` and `docs/design-host-admin.md`. Realtime protocol notes are in
`docs/realtime.md`.
