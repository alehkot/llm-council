#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CI=true NO_COLOR=1
mkdir -p .ci-output
trap 'printf "CI check failed at line %s\n" "$LINENO" >&2' ERR
python3 -B .ci/test_report.py

uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run python -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run build
node --test frontend/tests/*.test.mjs

# Existing browser harnesses require Playwright outside the application dependencies.
npm install --prefix .ci-output/browser-tools --no-save --no-package-lock --ignore-scripts playwright@1.62.1
export NODE_PATH="$PWD/.ci-output/browser-tools/node_modules"
export PLAYWRIGHT_MODULE="$NODE_PATH/playwright/index.mjs"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.ci-output/browsers"
node "$NODE_PATH/playwright/cli.js" install chromium
export CHROMIUM_EXECUTABLE
CHROMIUM_EXECUTABLE=$(node -e 'console.log(require("playwright").chromium.executablePath())')

for suite in frontend/tests/*.browser.mjs; do
    node "$suite"
done
