#!/usr/bin/env bash
# Native pre-deploy test gate for the LXC. Mirrors .github/workflows/app-tests.yml,
# run with the uv/Python toolchain installed directly on the LXC host so it doesn't
# require a Docker image rebuild first. Run this after rsyncing new code and before
# `docker compose up -d --build`.
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --locked --group dev

echo "== ruff (report only, not a hard gate) =="
uv run ruff check src || echo "ruff found issues (non-fatal, see above)"

echo "== makemigrations --check =="
(cd src && uv run manage.py makemigrations --check --dry-run)

echo "== test suite =="
uv run coverage run src/manage.py test app users integrations lists events --parallel
uv run coverage combine
uv run coverage report
