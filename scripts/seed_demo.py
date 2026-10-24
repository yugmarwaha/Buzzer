#!/usr/bin/env python3
"""
Seed a demo quiz into a running Buzzer backend.

Creates:
  - A "Demo Course" used to host the quiz
  - A "Buzzer Demo" game with 9 fun questions covering the
    basic question types (multiple_choice, true_false, fill_in_the_blank)
    and both grading modes (ACCURACY and COMPLETENESS)

Each question is worth 1 point:
  - ACCURACY questions reward only correct answers
  - COMPLETENESS questions reward any answer (participation credit)
  - Multiple choice / true-false: 30-second timer
  - Fill in the blank: 60-second timer

Idempotent: if a "Buzzer Demo" quiz already exists, it creates nothing and says so.
An existing "Demo Course" (without the quiz) is reused rather than duplicated.

Usage:
    python scripts/seed_demo.py

Admin credentials come from ADMIN_USERNAME / ADMIN_PASSWORD in the repo's .env, then the
environment, then the defaults (admin / changeme123) — the same order as
scripts/smoke_test_websocket.py and the integration tests.

Optional environment variables (defaults shown):
    BASE_URL        http://localhost:8000
"""

import os
import pathlib
import sys

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


def _dotenv() -> dict[str, str]:
    """KEY=VALUE pairs from the repo-root .env (quotes stripped), as smoke_test_websocket."""
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars


_ENV = _dotenv()
ADMIN_USERNAME = _ENV.get("ADMIN_USERNAME") or os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = _ENV.get("ADMIN_PASSWORD") or os.getenv(
    "ADMIN_PASSWORD", "changeme123"
)

G = "\033[92m✓\033[0m"
AR = "\033[94m→\033[0m"


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
#
# Order is intentional: start with familiar types (MC, T/F) then introduce
# fill-in-the-blank, interleaving ACCURACY and COMPLETENESS throughout so
# players experience both grading modes early.

QUESTIONS = [
    # ── 1. Multiple Choice / ACCURACY ──────────────────────────────────────
    {
        "type": "multiple_choice",
        "grading_type": "ACCURACY",
        "prompt": "Which planet in our solar system has the most moons?",
        "config": {"options": ["Mars", "Jupiter", "Saturn", "Neptune"]},
        # Saturn (index 2) is correct — it holds the record at 146 moons.
        "answer_data": {"answer_points": [0, 0, 1, 0]},
        "points_value": 1,
        "time_limit_seconds": 30,
    },
    # ── 2. True / False / ACCURACY ─────────────────────────────────────────
    {
        "type": "true_false",
        "grading_type": "ACCURACY",
        "prompt": "True or False: A group of flamingos is called a 'flamboyance'.",
        "config": {},
        # True is correct.
        "answer_data": {"answer_points": {"true": 1, "false": 0}},
        "points_value": 1,
        "time_limit_seconds": 30,
    },
    # ── 3. Multiple Choice / COMPLETENESS ──────────────────────────────────
    {
        "type": "multiple_choice",
        "grading_type": "COMPLETENESS",
        "prompt": "What is your go-to comfort food? (Any answer earns a point!)",
        "config": {"options": ["Pizza", "Ice cream", "Tacos", "Mac and cheese"]},
        # All choices earn full points — this is a participation question.
        "answer_data": {},
        "points_value": 1,
        "time_limit_seconds": 30,
    },
    # ── 4. Multiple Choice / ACCURACY ──────────────────────────────────────
    {
        "type": "multiple_choice",
        "grading_type": "ACCURACY",
        "prompt": "What is the largest land animal on Earth?",
        "config": {
            "options": [
                "Hippopotamus",
                "African Elephant",
                "White Rhinoceros",
                "Giraffe",
            ]
        },
        # African Elephant (index 1) is correct.
        "answer_data": {"answer_points": [0, 1, 0, 0]},
        "points_value": 1,
        "time_limit_seconds": 30,
    },
    # ── 5. True / False / ACCURACY ─────────────────────────────────────────
    {
        "type": "true_false",
        "grading_type": "ACCURACY",
        "prompt": (
            "True or False: The Great Wall of China is visible "
            "from space with the naked eye."
        ),
        "config": {},
        # False is correct — a common misconception.
        "answer_data": {"answer_points": {"true": 0, "false": 1}},
        "points_value": 1,
        "time_limit_seconds": 30,
    },
    # ── 6. True / False / COMPLETENESS ─────────────────────────────────────
    {
        "type": "true_false",
        "grading_type": "COMPLETENESS",
        "prompt": "Do you prefer coffee over tea? (Earn a point just for answering!)",
        "config": {},
        # Both answers earn full points — this is a participation question.
        "answer_data": {},
        "points_value": 1,
        "time_limit_seconds": 30,
    },
    # ── 7. Fill in the Blank / ACCURACY — exact match ──────────────────────
    {
        "type": "fill_in_the_blank",
        "grading_type": "ACCURACY",
        "prompt": "What is the chemical symbol for Gold? (Exact spelling required!)",
        "config": {"maxLength": 5},
        # "Au" — case-insensitive, so "au", "Au", and "AU" all match.
        "answer_data": {
            "acceptedAnswers": ["Au"],
            "answerPoints": [1],
            "editDistance": 0,
        },
        "points_value": 1,
        "time_limit_seconds": 60,
    },
    # ── 8. Fill in the Blank / ACCURACY — fuzzy match ──────────────────────
    {
        "type": "fill_in_the_blank",
        "grading_type": "ACCURACY",
        "prompt": (
            "Who wrote the play 'Romeo and Juliet'? "
            "(Close spellings accepted — up to 2 typos OK!)"
        ),
        "config": {"maxLength": 40},
        # editDistance: 2 allows minor misspellings like "Shakespear".
        "answer_data": {
            "acceptedAnswers": ["Shakespeare", "William Shakespeare"],
            "answerPoints": [1, 1],
            "editDistance": 2,
        },
        "points_value": 1,
        "time_limit_seconds": 60,
    },
    # ── 9. Fill in the Blank / COMPLETENESS ────────────────────────────────
    {
        "type": "fill_in_the_blank",
        "grading_type": "COMPLETENESS",
        "prompt": (
            "If you could visit any country in the world, where would you go? "
            "(Any answer earns a point!)"
        ),
        "config": {},
        # Any non-empty text earns full points.
        "answer_data": {},
        "points_value": 1,
        "time_limit_seconds": 60,
    },
]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def login(client: httpx.Client) -> str:
    resp = client.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print(f"  {G} Logged in as {ADMIN_USERNAME!r}")
    return token


DEMO_COURSE = "Demo Course"
DEMO_QUIZ = "Buzzer Demo"


def existing_quiz(client: httpx.Client) -> str | None:
    """Describe an existing demo quiz, or None."""
    resp = client.get(f"{BASE_URL}/api/admin/games")
    resp.raise_for_status()
    for game in resp.json():
        if game["title"] == DEMO_QUIZ:
            return (
                f"quiz '{DEMO_QUIZ}' (id={game['id']}, course_id={game['course_id']})"
            )
    return None


def existing_course(client: httpx.Client) -> int | None:
    """Id of an existing demo course, or None."""
    resp = client.get(f"{BASE_URL}/api/admin/courses")
    resp.raise_for_status()
    for course in resp.json():
        if course["name"] == DEMO_COURSE:
            return course["id"]
    return None


def create_course(client: httpx.Client) -> int:
    resp = client.post(
        f"{BASE_URL}/api/admin/courses",
        json={"name": DEMO_COURSE, "semester": "Demo"},
    )
    resp.raise_for_status()
    course = resp.json()
    print(f"  {G} Created course  '{course['name']}'  (id={course['id']})")
    return course["id"]


def create_game(client: httpx.Client, course_id: int) -> int:
    # Every quiz belongs to exactly one course, fixed at creation.
    resp = client.post(
        f"{BASE_URL}/api/admin/games",
        json={
            "course_id": course_id,
            "title": DEMO_QUIZ,
            "description": (
                "A demonstration game covering every question type and grading mode. "
                "ACCURACY questions reward correct answers only; "
                "COMPLETENESS questions reward participation."
            ),
        },
    )
    resp.raise_for_status()
    game = resp.json()
    print(f"  {G} Created game    '{game['title']}'  (id={game['id']})")
    return game["id"]


def create_questions(client: httpx.Client, game_id: int) -> None:
    type_labels = {
        "multiple_choice": "MC  ",
        "true_false": "T/F ",
        "fill_in_the_blank": "FITB",
    }
    for i, q in enumerate(QUESTIONS, start=1):
        resp = client.post(
            f"{BASE_URL}/api/admin/games/{game_id}/questions",
            json=q,
        )
        resp.raise_for_status()
        tag = type_labels[q["type"]]
        mode = q["grading_type"]
        prompt_preview = q["prompt"][:65] + ("…" if len(q["prompt"]) > 65 else "")
        print(f"  {G} Q{i:02d} [{tag} / {mode:<11}]  {prompt_preview}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"\n{AR} Buzzer — demo seed  ({BASE_URL})\n")

    with httpx.Client(timeout=15) as client:
        token = login(client)
        client.headers["Authorization"] = f"Bearer {token}"

        found = existing_quiz(client)
        if found:
            print(f"\n{AR} Demo already seeded: {found} exists. Nothing created.\n")
            return

        course_id = existing_course(client)
        if course_id is None:
            course_id = create_course(client)
        else:
            print(f"  {G} Reusing course  '{DEMO_COURSE}'  (id={course_id})")
        game_id = create_game(client, course_id)

        print(f"\n{AR} Creating {len(QUESTIONS)} questions …\n")
        create_questions(client, game_id)

    print(
        f"\n{AR} Done!  To host the demo:\n"
        f"\n"
        f"    1. Open the Host app  →  http://localhost:8080/host/\n"
        f"       (or http://localhost:5173 if the Vite dev server is running)\n"
        f"    2. Log in with your admin credentials\n"
        f"    3. Open 'Demo Course' and start a room for 'Buzzer Demo'\n"
        f"    4. Share the room code with players at http://localhost:8080/player/\n"
        f"\n"
        f"    course_id={course_id}   game_id={game_id}\n"
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as exc:
        print(
            f"\n✗  HTTP {exc.response.status_code}: {exc.response.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    except httpx.ConnectError:
        print(
            f"\n✗  Could not connect to {BASE_URL}.\n"
            f"   Is the Docker stack running?  (docker compose up)",
            file=sys.stderr,
        )
        sys.exit(1)
