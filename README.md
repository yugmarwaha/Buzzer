# Buzzer Game

A Jackbox-inspired real-time party quiz platform built for classroom use. A **Host** displays game state on a large screen while **Players** join and answer questions from their mobile browsers.

- Up to 250 players per room, 50 concurrent rooms
- Multiple question types: multiple choice, true/false, fill in the blank, multi-select, pin drop (canvas), closest guess (number)
- Two grading modes: accuracy-based and participation-based (completeness)
- Local accounts and guest players (no account required)
- Live answer distribution, per-player scoring

## Prerequisites

| Tool | Minimum version | Purpose |
|---|---|---|
| Docker Engine / Docker Desktop | 25 / 4.x | Running the full stack |
| Node.js | 20 | Frontend development servers |
| Python | 3.12 | Integration tests (optional if using Docker only) |

---

## First-time setup

### 1. Copy and configure environment

```bash
cp .env.template .env
```

Open `.env` and set:

| Variable | What to do |
|---|---|
| `MYSQL_ROOT_PASSWORD` | Choose a strong password |
| `MYSQL_PASSWORD` | Choose a strong password (used by the app) |
| `DATABASE_URL` | Update `change_me_app` to match `MYSQL_PASSWORD` |
| `ADMIN_USERNAME` | Username for the first admin account |
| `ADMIN_PASSWORD` | Password for the first admin account |
| `JWT_PRIVATE_KEY` | See step 2 |
| `JWT_PUBLIC_KEY` | See step 2 |

### 2. Create the Python virtual environment

Run the included setup script once after cloning. It creates `.venv`, installs all dependencies, and patches the activate script so your `.env` variables load automatically whenever the environment is activated:

```bash
bash scripts/setup_venv.sh
```

Then activate the environment (do this at the start of every terminal session):

```bash
source .venv/bin/activate        # macOS / Linux
```

Your shell prompt will show `(.venv)` when the environment is active, and variables like `ADMIN_USERNAME` and `ADMIN_PASSWORD` will already be set from your `.env`. All `python`, `pip`, `pytest`, and `alembic` commands below assume the environment is active.

> **Tip:** Most IDEs (VS Code, PyCharm) can detect `.venv` automatically and activate it for you in integrated terminals. In VS Code, open the Command Palette → *Python: Select Interpreter* and choose the `.venv` entry.

> **Windows:** The setup script targets bash (macOS/Linux). On Windows, create the venv manually (`python3.12 -m venv .venv`), install dependencies (`pip install -r backend/requirements.txt -r tests/integration/requirements.txt`), and load `.env` variables in your shell before running any scripts.

### 3. Generate JWT signing keys

The backend signs tokens with RS256. Run the included helper to generate a key pair and print the values ready to paste into `.env`:

```bash
# With the virtual environment active (cryptography is already installed):
python backend/scripts/generate_keys.py

# Or via Docker (no local Python required):
docker run --rm python:3.12-slim \
  sh -c "pip install cryptography -q && python" \
  < backend/scripts/generate_keys.py
```

Paste the two `JWT_*` lines into `.env`.

### 4. Install frontend dependencies and build

A root-level `package.json` manages a `concurrently` dev tool and provides convenience scripts:

```bash
npm install            # installs concurrently (root dev tool)
npm run install:all    # installs dependencies in all three frontend apps
```

> **Note:** Running `npm run build` is only needed if you want the pre-built apps to be served by Nginx at `http://localhost:8080`. If you plan to use the Vite dev servers (the typical workflow), you can skip the build step entirely.

> **Note:** If the build fails with `EACCES: permission denied` on the `dist` directory, Docker likely created it as root before the build ran. Fix with: `sudo chown -R $(id -u):$(id -g) frontend/*/dist`

### 5. Start the Docker stack

```bash
docker compose up
```

On first run, Docker builds the backend image and pulls MySQL, Redis, and Nginx. MySQL takes ~30 seconds to become healthy before the backend starts.

Use `--build` any time you change `requirements.txt` or the `Dockerfile`:

```bash
docker compose up --build
```

> **First run note:** On initial startup, MySQL takes 30–45 seconds to initialize the database user, and the backend will **wait — logging `admin_bootstrap_waiting_for_schema` every few seconds — until you run the migrations in the next step**, because its startup needs the database schema, which doesn't exist yet. Both behaviors are normal.

### 6. Run database migrations

Apply the Alembic schema migrations (this runs in its own one-off container, so it works while the backend is still waiting):

```bash
docker compose run --rm backend alembic upgrade head
```

This creates all tables. Within a few seconds the waiting backend picks up the new schema, finishes starting, and bootstraps the admin account from `ADMIN_USERNAME`/`ADMIN_PASSWORD` — no manual step needed.

> **Upgrading a database that already has quizzes (migration `004`)?** Every quiz now
> belongs to one course, and the migration never guesses which. Follow the reviewed-map
> procedure in [docs/design-host-admin.md §4](docs/design-host-admin.md#4-database-and-migration):
> run `docker compose run --rm backend python -m scripts.plan_game_courses`, review and complete
> `backend/migration_maps/game_course_map.json` (git-ignored; the planner also writes its
> report to `game_course_map.report.txt` there), finish or end every live room,
> **stop the backend**, back up, re-check, then `alembic upgrade head` and start the new backend.
> Fresh databases need none of this.

---

## Accessing the application

| Interface | URL | Notes |
|---|---|---|
| Player app | http://localhost:8080/player/ | Mobile-optimised |
| Host app | http://localhost:8080/host/ | Large-screen display |
| Admin app | http://localhost:8080/admin/ | Users, courses, and course access (host work links to the Host app) |
| API (direct) | http://localhost:8000 | Bypasses Nginx |
| Interactive API docs | http://localhost:8000/api/docs | Swagger UI (dev only) |
| Health check | http://localhost:8000/api/health | Service status |

Log in to the Host or Admin app with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` you set in `.env`.

---

## Development workflow

### Starting a session

Run these commands at the beginning of every development session.

**1. Activate the Python virtual environment** in any terminal where you will run Python tools (tests, migrations, scripts):

```bash
source .venv/bin/activate
```

If you ran `scripts/setup_venv.sh` during first-time setup, this also loads all your `.env` variables automatically (e.g. `ADMIN_PASSWORD`). This is not needed for the Docker stack itself — the backend runs inside its own container. It is needed whenever you run `pytest`, `alembic`, or any Python script directly in your terminal.

**2. Start the Docker stack** (backend, MySQL, Redis, Nginx):

```bash
docker compose up
```

The backend mounts `./backend` and runs with `--reload`, so any Python change takes effect immediately — no restart needed. Logs stream to the terminal.

**3. Start all frontend dev servers** in a single terminal from the repo root:

```bash
npm run dev
```

This starts all three Vite dev servers concurrently with color-coded, labeled output. **Check the terminal output for the exact URLs** — Vite picks the next available port if the default is already in use, so the numbers may differ from the typical defaults shown below:

```
[host]   VITE ready  →  http://localhost:5173
[player] VITE ready  →  http://localhost:5174
[admin]  VITE ready  →  http://localhost:5175
```

Press `Ctrl+C` once to stop all three.

To start a single app instead:

```bash
npm run dev:host    # typically http://localhost:5173
npm run dev:player  # typically http://localhost:5174
npm run dev:admin   # typically http://localhost:5175
```

In each case, check the terminal output for the actual URL Vite is listening on.

The Vite dev servers watch your source files and hot-reload the browser instantly on every save — no manual rebuild step. They proxy `/api` traffic to the Docker backend, so the full stack works at these ports during development.

> **Backend only?** If you are only changing Python code, you can skip the frontend dev servers and use the pre-built apps at `http://localhost:8080/host/`, `http://localhost:8080/player/`, and `http://localhost:8080/admin/` instead.

### During a session

- **Backend changes** — saved automatically; uvicorn reloads the process within a second.
- **Frontend changes** — saved automatically; the browser hot-reloads via the Vite dev server.
- **Database model changes** — generate and apply a migration:

```bash
docker compose exec backend alembic revision --autogenerate -m "describe change"
docker compose exec backend alembic upgrade head
```

### Ending a session

When you are done, stop all running processes:

- Press `Ctrl+C` in each terminal running a Vite dev server.
- Stop the Docker stack:

```bash
docker compose down
```

If you made frontend changes during the session and want `localhost:8080` to reflect them (e.g. for integration testing), run a final build before stopping:

```bash
npm run build
```

### Other migration commands

```bash
# Apply all pending migrations
docker compose exec backend alembic upgrade head

# Revert the last migration
docker compose exec backend alembic downgrade -1
```

---

## Testing

### Integration test suite

Tests run against the live Docker stack (start it first). Dependencies are already installed if you followed the virtual environment setup above. Activate the environment, then run:

```bash
python -m pytest tests/integration/ -q
```

Include timer-expiry edge cases (sets `time_limit_seconds=2` so tests finish quickly):

```bash
python -m pytest tests/integration/ -q --fast
```

Quizzes can't be deleted through the API, so fixtures remove their test quizzes with the
test-only `scripts.cleanup_test_quiz` inside the backend container (it only touches quizzes in
`Test Course <8 hex>` courses, and only in development mode). The host/admin tests also create shared
users `t4_host_a`, `t4_host_b`, `t4_host_c`, `t4_player`, `t4_player2` (random password each run;
local stacks only). Courses and users have no delete route, so the suite runs
`scripts.cleanup_test_quiz --sweep` at the start and end of each run. It removes every course and
user the tests made (semester `Test` and the fixture naming only), so after a run the database
holds only your own data. To run against a separate compose project, point the fixtures at it:

```bash
BUZZER_TEST_COMPOSE="docker compose -p myproject -f my-compose.yml" \
  python -m pytest tests/integration/ -q --base-url http://localhost:18000
```

The host-disconnect tests wait out the real grace period, so they **skip** unless the backend
under test runs with `HOST_GRACE_SECONDS<=10` (the default is 300). Set a short one in the
test stack's backend environment, e.g. `HOST_GRACE_SECONDS=3` under `backend.environment` in
its compose file (or in `.env` for a throwaway stack), then restart the backend.

### WebSocket smoke test

A quick end-to-end script that creates seed data, plays through a full game, and verifies every event payload (dependencies are included in `backend/requirements.txt`):

```bash
python scripts/smoke_test_websocket.py
```

Override credentials or the target URL with environment variables:

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD=changeme123 BASE_URL=http://localhost:8000 \
  python scripts/smoke_test_websocket.py
```

### Simulate players

`scripts/simulate_players.py` populates a live room with realistic fake players so you can test the full game loop without real students. It creates temporary local accounts via the Admin API, connects them over WebSocket, and has them answer automatically. All dependencies are already installed by `scripts/setup_venv.sh`.

**Workflow:**

1. Start the Docker stack and open the Host app.
2. Create a room and note the room code shown in the lobby.
3. In a separate terminal (with the virtual environment active), run:

```bash
python scripts/simulate_players.py --room ABCDE1
```

4. Press **Start** / **Next** on the host screen to advance through questions. Players answer automatically. Late players join after their randomised delay and print a message when they arrive.
5. When the game ends, temporary accounts are deleted automatically.

**Player profiles** and default mix:

| Profile | Default % | Behaviour |
|---------|-----------|-----------|
| `fast` | 25 % | Answers in 0.5–3 s; rarely skips |
| `normal` | 35 % | Answers in 3–8 s; occasionally skips |
| `slow` | 20 % | Answers in 8–18 s; more likely to skip |
| `absent` | 10 % | Never submits an answer |
| `late` | 10 % | Waits 25–70 s before joining, then answers at a normal pace |

**Common options:**

```bash
# 40 players with a custom profile mix (remainder becomes "normal")
python scripts/simulate_players.py --room ABCDE1 --players 40 \
    --fast-pct 20 --slow-pct 20 --absent-pct 10 --late-pct 10

# Pass the exported game JSON so fast/normal players lean toward correct answers
python scripts/simulate_players.py --room ABCDE1 --players 30 \
    --game-json sample_games/my_quiz.json

# Custom fill-in-the-blank word pool (repeat a word to weight it higher)
python scripts/simulate_players.py --room ABCDE1 --players 25 \
    --fitb-words "spring,spring,summer,summer,summer,autumn,winter"

# Leave temporary accounts in the DB after the run (useful for inspecting scores)
python scripts/simulate_players.py --room ABCDE1 --keep
```

Admin credentials default to the `ADMIN_USERNAME` / `ADMIN_PASSWORD` environment variables loaded automatically by the virtual environment. Override them with `--admin-user` and `--admin-pass`, or point at a different backend with `--base-url http://localhost:8000`.

---

## Common operations

### Load the demo quiz

After running migrations, seed the database with a demo game that covers the basic question types (multiple choice, true/false, fill in the blank) and both grading modes:

```bash
python scripts/seed_demo.py
```

This creates a **Demo Course** and a **Buzzer Demo** game with 9 questions (1 point each):

| # | Type | Grading | Question |
|---|------|---------|---------|
| 1 | Multiple choice | Accuracy | Which planet has the most moons? |
| 2 | True/False | Accuracy | A group of flamingos is called a 'flamboyance'. |
| 3 | Multiple choice | Completeness | What is your go-to comfort food? |
| 4 | Multiple choice | Accuracy | What is the largest land animal on Earth? |
| 5 | True/False | Accuracy | The Great Wall of China is visible from space. |
| 6 | True/False | Completeness | Do you prefer coffee over tea? |
| 7 | Fill in the blank | Accuracy (exact) | Chemical symbol for Gold? |
| 8 | Fill in the blank | Accuracy (fuzzy) | Who wrote 'Romeo and Juliet'? |
| 9 | Fill in the blank | Completeness | What country would you visit? |

### Host interface

The Host app (`http://localhost:8080/host/`) is course-first. Hosts see only the courses they
have **HOST** access to (admins see every course); HOST on a course gives access to every quiz,
the roster, and every session in it.

| Tab | What you can do |
|---|---|
| **Quizzes** | Create quizzes (they belong to that course permanently), import JSON, edit and reorder questions, export JSON, archive / restore, start a room. Quizzes can't be deleted — archive instead; archived quizzes keep their results but can't start rooms. |
| **Rooms** | See every open room in the course, including other hosts' rooms and stale ones ("Not running"). Only the host who started a room, or an admin, can end it. Ending a room with answers saves it as Abandoned. |
| **Roster** | Upload a Canvas gradebook or basic CSV; activate or deactivate entries. |
| **Sessions** | Finished sessions (completed, plus abandoned ones with answers) from any host of the course: download the scores CSV (Canvas or raw) and the HTML summary; merge guests (session starter or admin only). |

While a room using a quiz is open, that quiz and its questions are read-only. A question that
has been answered in any session can't be deleted. Downloads always show the questions as they
were asked, even after later edits.

### Admin interface

The Admin app (`http://localhost:8080/admin/`) is admin-first:

| Section | What you can do |
|---|---|
| **Users** | Create local accounts; grant or revoke course access (HOST/PLAYER). The user page shows, read-only, which quizzes that access implies — there are no per-quiz grants. |
| **Courses** | Create and edit courses; see each course's hosts and quiz counts; jump to it in the Host app. |
| **Guests** | View guest accounts; merge a guest's scores into a real netid account; delete stale guests. |

Quiz, roster, and session work happens in the Host app (the **Open Host app** link). Admins can
do everything a host can there. Behind nginx both apps share one origin, so you usually stay
signed in; on the separate Vite dev ports you sign in to each app separately (set
`VITE_HOST_APP_URL` in the admin app to point the link at the host dev server).

Log in with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` from your `.env`.

### Player past quizzes

Signed-in players (NetID or local account — not guests) can open **Past quizzes** from the join
page to review every finished quiz they took: their answers, right/wrong, the correct answers,
points, and total. Games that were ended early are labelled *Abandoned*; questions they never
reached show only "Not played".

### Quiz import and export

Games (metadata + all questions including answer data) can be exported and re-imported as JSON files.

**Export** — in the Host app, open a quiz and click **Export JSON**. Or via the API:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/host/games/{game_id}/export" \
  -o my_quiz.json
```

**Import** — in the Host app, click **Import JSON** on a course's Quizzes tab. This always creates a *new* quiz in that course — it never overwrites an existing one. Or via the API:

```bash
curl -X POST "http://localhost:8000/api/host/courses/{course_id}/games/import" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@my_quiz.json"
```

The older admin route still works for scripts, but now needs the course:
`curl -X POST http://localhost:8000/api/admin/games/import -H "Authorization: Bearer $TOKEN" -F "file=@my_quiz.json" -F "course_id=1"`.
The file format has no course field, so unmodified version-1 files keep importing.

The file format is:

```json
{
  "format": "buzzer/game",
  "version": 1,
  "game": { "title": "...", "description": "...", "max_players": 150 },
  "questions": [
    {
      "type": "multiple_choice",
      "grading_type": "ACCURACY",
      "prompt": "...",
      "time_limit_seconds": 30,
      "points_value": 1000,
      "config": { "options": ["A", "B", "C", "D"] },
      "answer_data": { "answer_points": [1000, 0, 0, 0] }
    }
  ]
}
```

Question order is determined by array position; `order_index` is assigned automatically on import.

**Images (version 2).** A quiz whose questions use images exports as `"version": 2`. It adds an
`images` list, and each image field in a question's `config` holds that list's `key` instead of
a database id. Importing creates new image rows in the target course. A quiz without images
still exports as version 1, unchanged, and version-1 files keep importing. A version-1 file must
not contain image fields (a raw id from another server can't be trusted).

```json
{
  "format": "buzzer/game",
  "version": 2,
  "game": { "title": "...", "description": "...", "max_players": 150 },
  "questions": [
    {
      "type": "multiple_choice",
      "grading_type": "ACCURACY",
      "prompt": "Which graph is y = x²?",
      "time_limit_seconds": 30,
      "points_value": 1,
      "config": {
        "options": ["", "", "Neither"],
        "promptImageId": "img-1",
        "optionImageIds": ["img-2", "img-3", null]
      },
      "answer_data": { "answer_points": [1, 0, 0] }
    }
  ],
  "images": [
    { "key": "img-1", "filename": "axes.png", "content_type": "image/png", "data": "<base64>" }
  ]
}
```

Image fields: `promptImageId` (any type), `optionImageIds` (multiple choice and multi-select,
same length as `options`, `null` for an option without an image; an option needs text or an
image), `imageId` (Pin Drop background). Limits: PNG, JPEG, WebP or still GIF, at most 5 MiB
and 4096 px a side per image; at most 20 images and 20 MiB of image data per file; at most
30 MiB per file. Each entry's `data` is decoded and checked like an upload; its
`content_type` is ignored. Design: `docs/plans/t8-images.md`.

### Export session scores

After a game session completes, any host of that course (or an admin) can download a CSV of all player scores:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/game/sessions/{session_id}/export" \
  -o scores.csv
```

Columns: `Player, Q1, Q2, …, Qn, Total`.

### Import a course roster

Roster uploads expect either a minimal CSV roster or a **Canvas gradebook export CSV**. When importing, the user can match columns to necessary fields for the players.

See `sample_rosters/*sample_roster_from_gradebook*.csv` for a working examples. Upload in the Host app (course → Roster → Upload CSV), or via the API (`/api/host/courses/{course_id}/roster` works the same way for hosts):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -X POST "http://localhost:8000/api/admin/courses/{course_id}/roster" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sample_rosters/sample_roster_from_gradebook.csv"
```

### View structured logs

```bash
docker compose logs -f backend       # stream backend logs
```

### Reset the database (destructive)

```bash
docker compose down -v               # removes the MySQL volume
docker compose up --build
docker compose run --rm backend alembic upgrade head   # the waiting backend finishes startup on its own
```

---

## Project structure

```
buzzer/
├── backend/
│   ├── app/
│   │   ├── routers/        HTTP endpoints  (auth, game, admin, health)
│   │   ├── services/       Business logic  (auth, game, state, roster, export)
│   │   ├── websocket/      Socket.io gateway and JWT middleware
│   │   ├── models/         SQLAlchemy ORM models
│   │   ├── schemas/        Pydantic request/response schemas
│   │   └── migrations/     Alembic schema migrations
│   ├── scripts/
│   │   └── generate_keys.py   RS256 key-pair generator
│   └── requirements.txt
├── frontend/
│   ├── host/               React app — host/display screen  (typically port 5173 in dev)
│   ├── player/             React app — player mobile browser (typically port 5174 in dev)
│   └── admin/              React app — admin management UI   (typically port 5175 in dev)
├── nginx/
│   └── nginx.dev.conf      Reverse-proxy config
├── mysql-init/             SQL scripts run by MySQL on first startup
├── scripts/
│   ├── smoke_test_websocket.py   End-to-end WebSocket smoke test
│   ├── simulate_players.py       Multi-profile async player simulator
│   └── seed_demo.py              Seeds a demo course and game
├── tests/
│   └── integration/        Pytest integration test suite
├── sample_games/           Example game JSON files for import
├── sample_rosters/         Example Canvas gradebook CSV files for roster import
├── docker-compose.yml
├── .env.template           Copy to .env and fill in before first run
└── .claude/                Claude Code settings, rules, and skills
```

---

## Architecture overview

```
Browser (HTTP)
      │
      ▼
   Nginx :8080
   ├── /api/*      → FastAPI backend :8000
   ├── /socket.io/ → FastAPI backend :8000  (WebSocket upgrade)
   ├── /host/      → Host React app   (static)
   ├── /player/    → Player React app (static)
   └── /admin/     → Admin React app  (static)
                          │
                     FastAPI :8000
                     ├── SQLAlchemy → MySQL :3306
                     └── redis-py   → Redis :6379
```

Real-time communication uses **Socket.io** (python-socketio on the server, socket.io-client in the browser). Game state is stored in Redis for low-latency reads; every player answer is immediately persisted to MySQL.
