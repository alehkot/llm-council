# Build checks and manual browser coverage

## Normal builds

`bash .ci/check.sh` retains all Python and Node unit tests, formatting/lint,
production frontend build, and Rust formatting/Clippy/tests. Browser checks use
`bash .ci/check-browser.sh smoke`:

- Six small browser API-transport recovery checks.
- Four rendered auth checks: stale 401 cannot clear a replacement key, while a
  current-key 401 must reject it, at desktop and mobile sizes. The original
  assertions about stored keys, dialogs, auth events, errors and no queued POST
  under replacement credentials remain intact.
- Two rendered successful Enter-submission checks, desktop and mobile.

This is an intentional reduction of routine E2E scope, **not** proof that every
full-suite scenario passes. No application behavior or unit coverage is removed.
Navigation in the two rendered smoke harnesses has a separate 30-second budget
for cold Vite transforms on the CPU-limited Pi. Other action/assertion timeouts
are unchanged except the observed Save API Key click (15 seconds). Chromium
sandboxing remains enabled. Each process has a 240-second outer deadline with
10-second forced cleanup; existing shorter internal suite deadlines remain.
Failures propagate; there are no automatic retries or ignored failures.

## Full E2E: user-run before preparing a release

Run this explicitly when **you** want the complete matrix. It is not scheduled,
not automatically triggered for releases, and not part of ordinary builds.
All twelve existing `*.browser.mjs` harnesses and their original scenarios are
retained. `full` explicitly overrides a smoke environment setting.

From the repository root, install frontend dependencies and a pinned browser
runner (Node 22+, npm, GNU `timeout`, Chromium system dependencies and a
sandbox-capable non-root account are required):

```bash
npm --prefix frontend ci
npm install --prefix .ci-output/browser-tools --no-save --no-package-lock --ignore-scripts playwright@1.62.1
export PLAYWRIGHT_MODULE="$PWD/.ci-output/browser-tools/node_modules/playwright/index.mjs"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.ci-output/browsers"
node .ci-output/browser-tools/node_modules/playwright/cli.js install chromium
export CHROMIUM_EXECUTABLE
CHROMIUM_EXECUTABLE=$(node -e 'console.log(require("./.ci-output/browser-tools/node_modules/playwright").chromium.executablePath())')

# Explicit user-run complete E2E matrix:
bash .ci/check-browser.sh full
```

With those variables already configured, the last command alone is sufficient.
Use `bash .ci/check-browser.sh full --list` to inspect every selected harness
without launching a browser, or `bash .ci/check-browser.sh smoke` for the normal
build selection. These suites use synthetic API responses, not real keys or
model providers. A green smoke run does not certify the manual full matrix,
physical mobile devices, native macOS behavior or live providers.

## Timing

The initial Pi full-build attempts spent 590.820s and 566.517s in the unchanged
1,185-test Python suite alone. The original 20-case rendered auth suite took
146s in an isolated cold-cache worker-like run. Reducing E2E work does not make
Python cheap; do not infer total build duration from browser duration. The
browser runner prints each completed harness duration; Woodpecker retains the
full stage log and exact-SHA result.
