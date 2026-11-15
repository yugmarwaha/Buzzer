#!/bin/sh
# CI: write a .env for a throwaway compose stack. Run from the repo root.
# Refuses to overwrite an existing .env, so it can't destroy a developer's settings.
set -e
if [ -e .env ]; then
    echo ".env already exists; not overwriting it" >&2
    exit 1
fi
cp .env.template .env
# No JWT keys: the single backend process generates its own for the run.
sed -i.bak -e 's/^JWT_PRIVATE_KEY=.*/JWT_PRIVATE_KEY=/' -e 's/^JWT_PUBLIC_KEY=.*/JWT_PUBLIC_KEY=/' .env
rm -f .env.bak
# Short host-disconnect grace period, so the tests that wait for it run instead of skipping.
echo "HOST_GRACE_SECONDS=3" >> .env
