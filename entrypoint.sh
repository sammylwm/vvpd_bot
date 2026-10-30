#!/usr/bin/env bash

set -e

echo "Apply alembic migrations"
uv run alembic -c /app/alembic.ini upgrade head
echo "Successfully applied migrations."

exec "$@"
