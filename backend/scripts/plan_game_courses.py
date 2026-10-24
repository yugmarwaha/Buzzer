"""Plan (or re-check) the quiz → course map for the T4 migration. Read-only on the DB.

Usage (from the repo root):
    docker compose run --rm backend python -m scripts.plan_game_courses

- No map file yet: writes backend/migration_maps/game_course_map.json with a proposal for
  every quiz (null where the course can't be determined), writes the review report next to
  it (game_course_map.report.txt), and prints the report.
- Map file exists: check mode. Neither file is modified; the script exits non-zero,
  listing every problem, if the map no longer matches the database or a room is live.

See docs/design-host-admin.md §4.
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.services import course_map


async def main() -> int:
    engine = create_async_engine(settings.DATABASE_URL)
    path = course_map.map_path()
    try:
        async with engine.connect() as conn:
            games = await conn.run_sync(course_map.propose)
            active = await conn.run_sync(course_map.active_sessions)
            report = course_map.report(games, active)
            print(report)
            print()

            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                # The report belongs to this proposal, so it is (re)written only with a new map.
                report_path = course_map.report_path(path)
                report_path.write_text(report + "\n")
                path.write_text(
                    json.dumps(course_map.proposal_map(games), indent=2) + "\n"
                )
                print(f"Wrote proposal to {path} and the report to {report_path}.")
                print("Review the map and fill every null.")
                return 0

            data, problems = course_map.read_map(path)
            warnings: list[str] = []
            if data is not None:
                problems, warnings = await conn.run_sync(course_map.check_map, data)
            print(f"Check mode: {path} exists and was not modified.")
            for w in warnings:
                print(f"WARNING: {w}")
            if problems:
                print("Map needs another review:")
                for p in problems:
                    print(f"  - {p}")
                return 1
            print("OK: the map matches the database and no room is live.")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
