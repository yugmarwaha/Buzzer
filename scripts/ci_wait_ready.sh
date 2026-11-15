#!/bin/sh
# CI: create the schema, then wait until the backend is healthy and the bootstrap admin
# can log in (the backend creates that account on its own once the schema exists).
# Usage: sh scripts/ci_wait_ready.sh <base-url>     (run from the repo root)
set -e
BASE_URL="${1:-http://localhost:8000}"
docker compose exec -T backend alembic upgrade head > /dev/null

ADMIN_USERNAME=$(grep '^ADMIN_USERNAME=' .env | cut -d= -f2-)
ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-)
i=0
until curl -fsS -o /dev/null -X POST "$BASE_URL/api/auth/login" \
        -H 'Content-Type: application/json' \
        -d "{\"username\": \"$ADMIN_USERNAME\", \"password\": \"$ADMIN_PASSWORD\"}" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "Backend at $BASE_URL is not ready" >&2
        docker compose logs backend --tail 40 >&2
        exit 1
    fi
    sleep 2
done
echo "Backend ready at $BASE_URL"
