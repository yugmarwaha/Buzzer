#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup_venv.sh — create and configure the Python virtual environment
#
# Supported: macOS and Linux (bash required).
# Windows:   Use WSL or Git Bash, or follow the manual steps in README.md.
#
# Run once after cloning the repo (from the project root):
#   bash scripts/setup_venv.sh
#
# After this, simply activating the venv also loads all .env variables:
#   source .venv/bin/activate
# ---------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN=""
for candidate in python3.12 python3.13 python3.14 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
        major="${version%%.*}"
        minor="${version##*.}"
        if [ "$major" -eq 3 ] && [ "$minor" -ge 12 ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: no Python 3.12+ interpreter found on PATH (tried python3.12, python3.13, python3.14, python3, python)." >&2
    echo "Install Python 3.12 or newer, then re-run this script." >&2
    exit 1
fi

echo "→ Creating virtual environment (.venv) with $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1)) …"
"$PYTHON_BIN" -m venv .venv

echo "→ Installing dependencies …"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r backend/requirements.txt
.venv/bin/pip install --quiet -r tests/integration/requirements.txt

# ---------------------------------------------------------------------------
# Patch the activate script so that sourcing it also loads .env.
# Uses $VIRTUAL_ENV (set by activate itself) to locate the project root,
# so it works regardless of where the developer runs 'source .venv/bin/activate'.
# ---------------------------------------------------------------------------
echo "→ Patching activate script to auto-load .env …"
cat >> .venv/bin/activate << 'PATCH'

# Load project .env variables whenever the venv is activated.
if [ -f "$VIRTUAL_ENV/../.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$VIRTUAL_ENV/../.env"
    set +a
fi
PATCH

echo ""
echo "✓ Done!  Activate the environment with:"
echo ""
echo "    source .venv/bin/activate"
echo ""
echo "  Your .env variables (ADMIN_USERNAME, ADMIN_PASSWORD, etc.) will load"
echo "  automatically every time you activate the venv."
echo ""
