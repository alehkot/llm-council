# Repo Care: protect internal handoffs before legacy recovery

## Baseline and scope

Repository: [alehkot/llm-council](https://github.com/alehkot/llm-council).
Persistent working branch: `development`, starting at
[8367b6dbb36be5af9968d55b6b231153cd53a2d4](https://github.com/alehkot/llm-council/commit/8367b6dbb36be5af9968d55b6b231153cd53a2d4).
Observed upstream `main`:
[409c67c9d1c192579231a626053299a66c61fffb](https://github.com/alehkot/llm-council/commit/409c67c9d1c192579231a626053299a66c61fffb), already an ancestor.
The latest branch contains the accumulated CLI work and has no open PR or
conflicting active owner. No upstream integration or branch reset is needed.
The publication receipt and [existing recovery issue 17](https://github.com/alehkot/issues-tracker/issues/17)
record exact final local/published SHAs and tree verification without a
self-referential commit hash in this file.

## Problem, rationale and completed work

A freshly initialized manager has empty in-memory queues even when a legacy
queued job or incomplete assistant placeholder for the same conversation is
still saved on disk. The internal refinement, initial Stage 1 and first Stage 1
continuation handoffs checked only local ownership. Each of those three handoffs
could dispatch a provider request before legacy recovery, allowing conflicting
work to acquire the same conversation later. Six synthetic reproductions
confirmed one mocked provider request for each handoff/evidence combination.
This is an internal manager-contract defect, not a claimed public production
incident: public CLI run execution is still gated.

Stage 2 already checked the exact legacy recovery predicates. A shared
`_has_legacy_owner` now applies that existing check to all four handoffs while
holding the manager lock. A conflict rejects admission before queuing/provider
work and preserves the CLI receipt, legacy job and assistant placeholder.
Terminal and unrelated legacy work remains nonblocking. Active duplicate
handles retain their existing identity and ownership behavior. No workflow,
provider request payload, cancellation, retry, public API or API0 gate is changed.

This concrete reproduced coordination gap was selected over further CLI feature
expansion and parser-format changes without comparably strong evidence. Ranking
projection remains aligned with the existing algorithms. Research context:
[LangGraph replay and side-effect boundaries](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/functional-api.mdx)
and [Python executor lifecycle](https://docs.python.org/3.10/library/concurrent.futures.html).
Those references motivate preserving ownership across recovery; repository tests,
not general documentation, establish the limited behavior changed here.

## Verification

Nine new regression tests cover queued journals, orphan placeholders and
terminal/unrelated work for refinement and both Stage 1 handoffs. They assert
no provider request or receipt mutation after rejected admission. Journals and
conversation data are isolated in temporary directories. The four worker suites
contain 77 passing tests, including existing Stage 2 coverage.

All 1,105 offline Python 3.10.22 tests passed in 154.877 seconds with unmocked
outbound networking forbidden. Ruff lint/format passed for all 143 Python files;
114 existing frontend Node tests, ESLint and Biome passed. Real loopback Uvicorn
startup, authenticated API0 configuration and graceful shutdown passed with empty
temporary data and synthetic keys. Whitespace checks passed.

Self-review and independent whole-change code/test/document review found no
introduced moderate-or-higher findings. The independent reviewer also ran all
77 worker tests and 27 additional probes covering recoverable legacy statuses
and completed/failed assistant-linked journals. A clean review is not a guarantee
of correctness. Exact remote parent/tree and normal CI must be verified in the
publication receipt and existing recovery issue. No local full build was run.

## CI and remaining work

Exact starting development and upstream main each expose zero GitHub statuses,
check runs and commit comments, including no Woodpecker report. The checked-in
Woodpecker workflow expects pushes to main/development, but empty aggregate
`pending` is missing evidence, not an observed running build or pass/failure.
Observe the normal result for the exact published SHA and actual job coverage;
no manual dispatch, private runner access or security/settings change is allowed.

This remains single-process internal scheduling, not durable queue admission,
multiprocess coordination or a guarantee of exactly-once external execution.
No frontend/Rust sources change; rendered browser, native macOS/Keychain and
live-provider verification are unrun. Continue the separate CLI plan's remaining
continuation/negative-mode and workflow/context/recovery boundaries before public
execution acceptance. No new product decision or outstanding source fix is
introduced by this narrow repair.
