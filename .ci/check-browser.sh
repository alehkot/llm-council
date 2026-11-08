#!/usr/bin/env bash
# Normal builds use smoke; the user can explicitly run full before a release.
set -euo pipefail
cd "$(dirname "$0")/.."
mode=${1:-smoke}
case "$mode" in
    smoke)
        suites=(frontend/tests/auth-probe-recovery.browser.mjs
                frontend/tests/auth-replacement.browser.mjs
                frontend/tests/composition-submit.browser.mjs)
        ;;
    full) suites=(frontend/tests/*.browser.mjs) ;;
    *) printf 'Usage: bash .ci/check-browser.sh [smoke|full] [--list]\n' >&2; exit 64 ;;
esac
if [[ ${2:-} == --list ]]; then
    printf '%s\n' "${suites[@]}"
    exit 0
fi
if (( $# > 1 )); then
    printf 'Only --list is supported as the second argument\n' >&2
    exit 64
fi
: "${PLAYWRIGHT_MODULE:?See .ci/README.md for browser setup}"
: "${CHROMIUM_EXECUTABLE:?See .ci/README.md for browser setup}"
export BROWSER_TEST_MODE="$mode"
for suite in "${suites[@]}"; do
    start=$SECONDS
    printf 'Browser %s: %s\n' "$mode" "$suite"
    timeout --kill-after=10s 240s node "$suite"
    printf 'Browser completed in %ss: %s\n' "$((SECONDS - start))" "$suite"
done
