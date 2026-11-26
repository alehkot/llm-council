# Read-only negative critique evidence

## Scope

`run_negative_results.load_complete_results(run_id)` reads the exact,
stop-terminated normalized response from every saved active reviewer, in the
saved input order. It retains the review and Stage 1 source identities, exact
negative inputs and per-request fingerprints, adapter ID, self/peer labels,
attempt ID and immediate retry predecessor. The excluded answer owners are not
required and do not reappear. Each returned response and the label map are
detached from retained records.

A length-limited, unstarted, in-flight or uncertain reviewer makes the
whole-roster result unavailable. Historical reads work after cancellation or
current settings drift; deleted runs and incompatible causal receipts fail
closed. The projection neither reads current model settings for admission nor
orders a retry. Private raw response text stays out of the compact poll
projection. The explicit historical read is internal; it must not be wired
directly to a public poll route.

A transport stop is **not** proof that the text is a well-formed critique,
contains a score, or is safe to use in ranking. The reader does not parse,
aggregate, schedule, mark the stage complete, write run state, or change CLI/API
versions. Validated parsing with explicit self/peer scope is the next distinct
step; continuation for length-limited responses remains separate. Do not enable
a public stage or feed raw text into ranking from this evidence alone.

## Verification

- Starting [development](https://github.com/alehkot/llm-council/commit/06513d310d5591a19b3d204168f6ff57f2e458e8)
  and [main](https://github.com/alehkot/llm-council/commit/d90cb29427d17b6ffa7dec9629f89e9e4b357c00)
  had exact Woodpecker #29 and #8 passes, without per-command coverage in the
  reports.
- Seven new offline tests use durable synthetic runs to verify the full-roster
  position and provenance, excluded reviewers, detachment/mutation resistance,
  stop versus length, uncertain and in-flight attempts, explicit retry
  provenance, cancellation, deletion and corruption.
- Ruff lint/format and an authenticated FastAPI lifespan/config smoke passed; the
  public API version stayed at 0 and the separate basic-run API version at 1.
  Self-reviewed; no independent reviewer, native macOS/Keychain, Rust, live model
  or full local build in this increment.
