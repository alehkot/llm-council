# Repo Care checkpoint: preserve JSON string content

## Baselines and publication

Repository: [alehkot/llm-council](https://github.com/alehkot/llm-council).
Persistent working branch: `development`, starting at
[06390fb9003e934da58990e51c179ab29699b573](https://github.com/alehkot/llm-council/commit/06390fb9003e934da58990e51c179ab29699b573).
Upstream `main` was freshly observed at
[fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), already an ancestor. No upstream integration was needed. No open PR or issue in the source repository duplicated this change.

Implementation: [859b7cab71a341a0a758a3e111227ec87422ebd2](https://github.com/alehkot/llm-council/commit/859b7cab71a341a0a758a3e111227ec87422ebd2).
Its verified tree `5df099bb2d6ac6f060bd1c4323d68f25c8f1bc38` exactly matches tested local implementation `a9049f18baecc5d84593889185a7291f1b0e6170`. Publication used a non-force expected-head update after rechecking both remote heads. Accumulated development work and unrelated files are preserved. This documentation-only descendant records the result; its final SHA and exact CI observation belong in the publication receipt and [existing recovery issue 17](https://github.com/alehkot/issues-tracker/issues/17), avoiding a self-referential hash.

## Problem, choice and scope

The shared `extract_json_object` fallback counted braces inside JSON string values. A valid model result such as `{"refined_prompt":"Explain the closing } character."}` was rejected, so prompt refinement silently fell back to the original question. Reference-selection reasons and negative-review summaries containing code braces suffered the same loss.

This reproduced normal coding-question failure was selected over broader unfinished CLI feature expansion and speculative malformed-storage hardening. The repair adds string/escape state to the existing linear brace scanner. It retains fenced-first parsing and last-valid complete outer-object selection, ignores unrelated closing braces in prose, and does not extract nested objects from an invalid outer container. No provider contract, public CLI gate, workflow state or UI changed.

Research: [RFC 8259 string grammar](https://www.rfc-editor.org/rfc/rfc8259#section-7) distinguishes quoted string content and escapes from structural characters. [Python JSON decoder documentation](https://docs.python.org/3.10/library/json.html#json.JSONDecoder.raw_decode) documents parsing with trailing text. Repeated decode attempts at each opening brace were not adopted because the bounded scanner preserves existing outer-object selection without repeated parsing. The concrete bug and fix are established by offline reproductions, not by general documentation.

## Verification and review

- Baseline: 509 networking-guarded offline Python 3.10 tests passed.
- Final source: 521 offline tests passed, including 12 new tests. New coverage includes 125 generated quote/backslash/brace combinations, nested containers, multiple candidates, fence priority, embedded fences, unrelated closing braces, and mocked real refinement/reference-selection/negative-review callers. The original function failed 14 subcases in the initial 11-test regression suite.
- Repository-wide Ruff formatting and lint passed for 92 Python files; whitespace checks passed.
- 114 unchanged frontend Node tests, frontend formatting and ESLint passed. No frontend implementation changed, so rendered UI verification is inapplicable to this parser-only change. Rust source/tests unchanged and not rerun.
- Real loopback Uvicorn startup, authenticated config response (CLI API 0) and graceful shutdown passed using synthetic credentials and empty temporary storage.
- Self-review and independent whole-change review found no introduced moderate-or-higher findings. Independent review reran all 12 targeted tests and checked 20,000 deterministic valid extraction cases across Unicode, braces, escaped quotes/backslashes, nested data and prose/fenced envelopes. Published implementation tree matches the reviewed source. Review is not a correctness guarantee.

## Limitations and next steps

Malformed unterminated strings/objects are not repaired. An unterminated quote in an earlier malformed candidate may hide a later corrected object; this is explicitly documented and tested. The fix preserves valid JSON, not arbitrary malformed model-output recovery or model answer quality.

Exact starting development, upstream main and published implementation heads each exposed zero GitHub statuses, check runs and commit comments, including no Woodpecker report. An empty aggregate `pending` is **missing CI evidence**, not proof that a build is running or passed/failed. Full builds remain unrun under the no-local-full-build policy. No live model/provider, paid evaluation or native-platform verification was performed.

Next: observe normal Woodpecker evidence for the exact final development head and actual job coverage. If still absent, the owner can inspect the integration's activation/reporting health using their own authorized access; no failure cause is inferred. Do not manually dispatch CI or access private runners. Resume the separately checkpointed CLI plan only after fresh branch/coordination checks; this repair does not complete that plan or enable public run commands. No moderate-or-higher introduced review finding or unpublished source fix remains.
