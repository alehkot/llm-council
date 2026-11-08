# Guarded first Stage 2 reviewer continuation

## Baseline and scope

Persistent working branch: `development`, starting at
[`a867359e5ee6dfc4c1cbec2fd52fae15defcad13`](https://github.com/alehkot/llm-council/commit/a867359e5ee6dfc4c1cbec2fd52fae15defcad13).
Observed upstream `main`:
[`409c67c9d1c192579231a626053299a66c61fffb`](https://github.com/alehkot/llm-council/commit/409c67c9d1c192579231a626053299a66c61fffb).
Main is already an ancestor; accumulated real development history is preserved.
Fresh branch, issue, PR and ownership checks found no conflicting active work.

The previous step pins exact first-continuation requests but cannot safely retain
their execution. This bounded step adds a separate internal callback guard using
the established Stage 1 continuation semantics and Stage 2 source provenance.
It durably records one named attempt before dispatch, requires explicit consent
for uncertain work, rejects duplicate live ownership and protects cancellation,
deletion, revisions, saved inputs and adapter identity. Read-only inspection
retains historical chunks and exposes ownership/retry state without private text.
Initial partials and earlier workflow evidence are not rewritten.

No concrete transport, worker scheduling, automatic request, concatenated ballot,
stage completion or public API1 is enabled. A stopped continuation chunk does not
yet make a length-limited reviewer eligible for the complete-ranking projection.
The next bounded dependencies are a single-attempt wire adapter, explicit worker
handoff and validated exact-chunk ranking projection; remaining negative-mode,
context-reduction and whole-run recovery acceptance still gate public execution.

Reference: the existing Stage 1 guard and
[Python cancellation/shield behavior](https://docs.python.org/3.10/library/asyncio-task.html#shielding-from-cancellation).
Neither the guard nor offline tests establish remote exactly-once execution.

## Verification and publication

The focused synthetic suite covers exact saved payload/attempt ordering, detached
replay and historical reads, concurrent reviewers and duplicate ownership,
per-reviewer consent, persistence failure/lost acknowledgment, malformed/oversized
responses, drift before dispatch and late completion, cancellation/deletion,
corrupt saved evidence, fresh-process reads and unchanged initial partials.
All 1,185 offline Python tests passed on the frozen final source in 223.985
seconds with unmocked outbound networking forbidden (baseline: 1,123 passed).
The independent final focused suite passed all 62 tests in 56.151 seconds, plus
an independent live-history/ownership and no-implicit-ballot probe. Existing 114
frontend Node tests, Biome/ESLint, repository-wide Ruff format/lint (150 Python
files), whitespace checks and real authenticated API0 startup/graceful shutdown
passed. Independent whole-change review and self-review found no introduced
moderate-or-higher finding. Test-fixture isolation defects found during review
were corrected and the final suite rerun; a clean review is not a guarantee.
Full local builds are unrun by policy. Native macOS/Keychain, Rust consultation
execution and live-provider checks are unrun; those surfaces are not changed.
This internal backend change has no rendered UI surface.

Starting exact working/default heads had zero GitHub statuses/check runs and no
Woodpecker commit comments. Empty aggregate `pending` is missing evidence, not an
observed running build or a pass/failure. Final test/review results and the exact
local/published commit/tree are recorded in the durable publication receipt and
[existing recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17),
without a self-referential commit hash. No manual CI dispatch/rerun, deployment,
PR, private-runner access or paid provider call is part of this change.
