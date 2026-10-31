#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'printf "CI check failed at line %s\n" "$LINENO" >&2' ERR

uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run python -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run build
node --test frontend/tests/*.test.mjs

source .ci-output/build-scripts/browser.sh

for suite in frontend/tests/*.browser.mjs; do
    node "$suite"
done
