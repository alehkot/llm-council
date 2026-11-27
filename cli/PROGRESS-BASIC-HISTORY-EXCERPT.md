# Basic CLI history: recognize a consultation without reopening it

Before this change, history returned an ID, date and status but no label to help
tell similar runs apart. The planned history excerpt is now part of the
authenticated basic history response. It is derived on read from the saved
original question. It folds whitespace and is at most 80 Unicode code points,
including any ellipsis. It is not stored separately and does not enter inspect,
exports, or cancellation cursors. The CLI renders it on the same line and
applies its existing terminal escaping. It still accepts an older backend
without the new metadata field. No model call, API version or legacy web route
changes.

## Verification and limits

Start: [development 09cf6bc](https://github.com/alehkot/llm-council/commit/09cf6bc082ef54f775831dcbb3eca84dc9b97297)
and [main d90cb29](https://github.com/alehkot/llm-council/commit/d90cb29427d17b6ffa7dec9629f89e9e4b357c00);
exact Woodpecker #32 and #8 passed. No open issues or PRs at inspection.
A regression failed on the missing excerpt in the original code. The authenticated
ASGI check covers Unicode truncation, unauthenticated rejection, waiting-run
visibility and unchanged inspect state and saved prompt; Ruff lint and formatting
passed for backend, tests and scripts. The Rust test covers one-line terminal
escaping and older backends. No local Rust toolchain, full build, rendered
browser, native macOS Keychain/terminal, live provider, or independent review.
Check normal Woodpecker on the exact resulting commit for CI and Rust evidence.

The first [published candidate’s Woodpecker #33](https://github.com/alehkot/llm-council/commit/1ded9b9ad08479e91df158d9562863f5e6b6c1da#commitcomment-212081463)
reached the Rust step after the backend and frontend checks, then stopped on
Rust formatting for the new assertion. This follow-up applies the exact
displayed rustfmt correction without changing its conditions or runtime code.
All 1,363 backend tests passed locally with networking guarded after allowing
the suite its full runtime.
