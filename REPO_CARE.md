# Completed synthesis snapshot reconciliation

## Baseline and publication

Repository: [alehkot/llm-council](https://github.com/alehkot/llm-council).
Persistent working branch: `development`, starting at
[220291a8ec6252cc101190377202516cb8ca2b59](https://github.com/alehkot/llm-council/commit/220291a8ec6252cc101190377202516cb8ca2b59).
Observed upstream `main`:
[fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d),
unchanged and preserved as an ancestor. Existing CLI/refinement work is retained.
Local implementation: `19f81cdb21ef179b5f423d21d4b498900d8caad6`.
Published implementation:
[fd571d4e90a2f246c6260c0ed10e74a6a528205c](https://github.com/alehkot/llm-council/commit/fd571d4e90a2f246c6260c0ed10e74a6a528205c),
exact tree `c16f5619d150ddc1863675bb4c335c532f9c6e90` matches the tested local tree.
Both remote heads were rechecked before the non-force expected-head update.
The published commit was imported and hash-verified in the real-history checkout;
prior local sibling commits remain preserved. A bundle and full patch retain
recovery. This documentation-only descendant's exact final SHA and CI receipt
are recorded in [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem, rationale and repair

The previous checkpoint's deferred issue is now repaired: after completing a
consultation, resynthesis/version selection updates persisted conversation data,
but status snapshots used the worker's original cached synthesis. Returning to
the conversation could replace the new synthesis with that stale snapshot.
Reproduce by completing a synthetic consultation, calling resynthesize,
resynthesize-all or set-current-version, then comparing consultation status with
the persisted assistant message. The old cursor also failed to change.

Completed status and long-poll snapshots now read the persisted synthesis and
metadata for the exact absolute assistant index, matching consultation ID and
persisted completed status. Active and failed runs remain worker-owned. Returned
completed snapshots are detached from cached state. Missing, unrelated or
nonterminal persisted messages do not replace the cached completion. This shared
read path covers all three existing mutation routes without separate cache
invalidation hooks or changes to provider calls, storage format or CLI contracts.

The repository reproduction establishes the issue. React's
[asynchronous synchronization guidance](https://react.dev/learn/synchronizing-with-effects)
provides context for avoiding stale asynchronous data, not proof of this repair.
This fix does not cancel already-in-flight responses or make separate storage
writes transactional; it ensures newly generated terminal snapshots reflect the
matching persisted message. No broader race elimination is claimed.

## Tests and review

- Four of the initial eight new tests failed on unchanged production code. The
  final suite has ten new tests covering single/all resynthesis, version selection,
  changed/stable cursors, actual long-poll route wake, older-message isolation,
  a new active run during resynthesis, identity/index/role/status guards and aliases.
- 480 offline Python 3.10.22 tests pass, independently repeated by the reviewer.
  The runner forbids unmocked outbound network calls. An initial older environment
  lacked ACP and could not import the full suite; the existing synchronized
  project environment resolved that tooling mismatch without installation.
- 114 Node tests, repository-wide Ruff formatting/checks, frontend Biome/ESLint
  and Git whitespace checks pass.
- 14 existing rendered synthesis-navigation cases pass against current-source
  Vite, at desktop/mobile widths, using Chromium headless-shell with its sandbox
  enabled. These use synthetic API responses: they verify UI navigation/error
  behavior, not the backend fix. Backend endpoint/storage tests establish that.
- Real loopback backend startup, authenticated config request and shutdown
  lifecycle completed using empty temporary data and synthetic unused keys.
  The harness's exit-code assertion failed because the process exited with
  SIGTERM status (-15) after logging application shutdown complete; no exit-0 claim.
- Self-review and independent whole-change review found no introduced
  moderate-or-higher findings. The reviewer also checked manager-to-storage lock
  ordering and actual long-poll behavior. A clean review is not a guarantee.

No local full build, live provider/model-quality evaluation, macOS Keychain or
end-to-end CLI consultation verification ran. No paid services were used.

## CI and next steps

At 23:55 UTC, exact starting development, upstream main and published
implementation have zero GitHub statuses, check runs and Woodpecker commit
comments. Aggregate `pending` with zero contexts is **missing evidence**, not a
running pipeline, failure or pass. The checked-in workflow expects development
push coverage, but no actual build/job/platform result is established.
No manual CI rerun/dispatch, paused-check activation or private runner access
was attempted. Existing issue #17 tracks this reporting blocker without duplication.

Observe normal exact-head Woodpecker reports and diagnose any real relevant
failure first. If reports remain absent, the owner can inspect normal integration,
webhook, worker and reporting health through authorized access. Resume the CLI
plan from its latest development contents only after fresh ownership/head checks;
this bounded fix does not complete public durable consultation integration.
No outstanding introduced moderate-or-higher finding or unpublished code remains.

---

# Skill-library outage recovery checkpoint

## Baseline and publication

- Repository: [alehkot/llm-council](https://github.com/alehkot/llm-council).
- Persistent working branch: `development`, starting at
  [3055b2558d1beb2a572e7bd6ca048ebf1c075858](https://github.com/alehkot/llm-council/commit/3055b2558d1beb2a572e7bd6ca048ebf1c075858).
- Upstream `main`: [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d),
  still an ancestor. Existing accumulated CLI and Repo Care work is preserved.
- Local implementation commit: `5082b926855ca43ed0ffe08219ac9a40b6eda0ce`.
- Published implementation: [abf57217508cb558a58801960e3c4abc2299e55e](https://github.com/alehkot/llm-council/commit/abf57217508cb558a58801960e3c4abc2299e55e).
  Exact tree `5e34488706d1503916fc44800ff4e5a85da7271d` matches the tested local tree.
  Both remote heads were rechecked before the non-force expected-head update.
  The exact published commit was imported and hash-verified in the real-history
  checkout. A local bundle and full binary patch preserve implementation recovery.
  This checkpoint is a documentation-only descendant; its final SHA/CI receipt is
  recorded in [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem, evidence and bounded repair

A transient `/api/skills` error previously replaced the library with an empty list
and marked it loaded. Existing normalization effects then persisted an empty
saved-default selection. A subsequent successful request could not restore it.
The production callback and effects reproduced this data loss with synthetic
network, 503, authentication, abort and malformed-response cases.

Failed initial loads now preserve defaults and keep drafts unsent until a valid
catalogue arrives, unless the configuration explicitly disables skills. Failed
refreshes retain the last verified catalogue. Successful empty/disabled catalogues
still prune unavailable IDs. Feature-disabled requests omit skills without
clearing saved defaults. Unknown configuration is not treated as disabled. HTTP
status is retained on skill-library errors so a legitimate disabled endpoint 404
stays quiet without being mistaken for an authoritative empty catalogue.

React's [effect guidance](https://react.dev/learn/synchronizing-with-effects) and
[TanStack Query's previous-data guidance](https://tanstack.com/query/latest/docs/framework/react/guides/placeholder-query-data)
provide context for preserving valid data across asynchronous fetching. No new
cache dependency or broad rewrite was introduced. The actual application
reproduction, not those general references, establishes this defect.

## Verification and review

- Baseline and final: 456 offline Python 3.10 tests pass. The runner rejects
  outbound network calls; these tests do not measure live provider quality.
- Final: 114 Node tests pass (22 new). The initial 12-case regression suite failed
  10 cases on the starting code, then passed with the repair. It exercises actual
  production callbacks/effects, saved defaults, malformed/error responses,
  readiness, unsent drafts, disabled configuration and outgoing skill selection.
- 12 rendered cases pass on the current-source Vite development server using
  Chromium headless-shell with its sandbox enabled, at 1280px and 390px widths.
  Cases cover initial 503, pending loading, failed refresh, valid empty/disabled
  catalogues, and Clear/Save API-key flow when the server disables skills.
  Actual controls, local persistence, draft retention/clearing and captured
  request bodies are asserted. Desktop/mobile screenshots were inspected.
  All requests are synthetic; no live API key, provider or paid calls were used.
- Repository-wide Ruff check/format, frontend Biome format/ESLint, Git whitespace,
  authenticated loopback backend startup/API0 and graceful shutdown pass.
- Independent whole-change review initially found a moderate disabled-feature
  regression: cached skills could still be sent to a server that disabled them.
  It was fixed, regression-tested and rendered at both widths. Final re-review
  reports no remaining introduced moderate-or-higher findings; the reviewer also
  independently passed 33 focused Node tests. A clean review is not a guarantee.
- No full local build ran. Rust/macOS Keychain, live-provider integration and
  model-quality evaluation are unrun; this frontend repair does not verify them.

## CI evidence and next priorities

At 17:58 UTC, exact starting development, upstream main and published
implementation have zero GitHub statuses, check runs and Woodpecker commit
comments. The aggregate status API's `pending` with zero contexts is **missing
build evidence**, not evidence of a queued/running pipeline or a pass. Existing
Woodpecker push configuration expects development coverage, but actual build/job
coverage and outcome remain unverified. No manual CI rerun/dispatch, paused-check
activation or private-runner access was attempted. Issue #17 retains the reporting
blocker; the owner can check normal webhook/worker/reporting health before merge.

A separate pre-existing likely-use issue was reproduced and deferred to the next
bounded run: after a synthetic completed consultation, resynthesis writes a second
version/new synthesis to persisted conversation data, while consultation status
still returns the original single version/mode and an unchanged cursor. Selecting
the conversation can apply that stale completed snapshot over the fresh result.
Investigate terminal snapshot reconciliation in the consultation manager versus
resynthesis/set-current handlers. Test both mutation paths, cursor freshness,
assistant-message identity and preservation of active consultations. No repair
for this separate issue is included here; do not infer it is resolved.

---

# OpenRouter terminal-outcome checkpoint

## Baseline and publication

- Repository: [alehkot/llm-council](https://github.com/alehkot/llm-council).
- Persistent working branch: `development`, starting at
  [b737aa9f77e8ab7bbb8cefcb00457f48ed0160c3](https://github.com/alehkot/llm-council/commit/b737aa9f77e8ab7bbb8cefcb00457f48ed0160c3).
- Upstream `main`:
  [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
  It remains an ancestor; all main changes and accumulated CLI work are preserved.
- Local implementation: `6fce9293de48cc95300670f7c88e702cb500ad08`.
- Published implementation:
  [24ef33b2cedf568ceae12194247248025c602e6f](https://github.com/alehkot/llm-council/commit/24ef33b2cedf568ceae12194247248025c602e6f),
  tree `195d641c06b40eb69b3f58680f929f71e0aeeecc`, matches the reviewed local tree.
  The exact published commit was imported and verified locally, preserving real
  history. Publication rechecked both remote heads and used a non-force,
  expected-head update. This checkpoint is a documentation-only descendant;
  its exact final publication/CI receipt is retained in [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem, rationale and change

The live OpenRouter client accepted HTTP 200 responses with nonempty partial
text even when the provider explicitly reported `content_filter`, `error`, or
`tool_calls`, or attached a top-level/choice error. Such text could become a
member answer, peer evidence, revision or final synthesis. Independently reproduced
through the real `query_model` path with synthetic HTTP responses.

[OpenRouter's API reference](https://openrouter.ai/docs/api_reference/overview)
defines the normalized terminal reasons and choice error field. Its
[HTTP-success error example](https://openrouter.ai/blog/tutorials/model-routing-for-support-bots-cheap-first-faq-handling/)
explains that generation errors can arrive with HTTP 200 and partial content.
These protocol sources and reproductions support this bounded repair. Broader
pipeline-provenance/CLI integration was considered but deferred rather than
expanding scope or claiming that partially implemented milestones are complete.

The client now rejects explicit terminal failure/filter/tool outcomes, embedded
errors and unresolved tool calls. It does not automatically retry them, continue
the partial prose or expose provider error text in diagnostics. Existing
HTTP-status retries (including 429/5xx), malformed/blank-response retry budgets,
normal completions, missing/provider-specific finish reasons and token-limit
continuations remain unchanged. No frontend, durable-adapter, API capability,
CLI command, model selection or ACP behavior changed.

## Verification and review

- Baseline: 445 offline Python 3.10.22 tests passed.
- Final source: 456 offline Python tests passed; outbound network calls are
  forbidden by the runner. Eleven new tests cover terminal reasons with blank
  and partial text, embedded errors, unresolved tools, single-request behavior,
  sanitized traces, stage1 failure, preserved deliberation originals, rejected
  synthesis, interrupted continuation, and successful/missing/unknown outcomes.
- The initial seven regression tests produced 12 failing subcases on the baseline;
  they pass with the repair. Additional integration/compatibility tests also pass.
- 92 Node tests, repository-wide Ruff format/check, frontend format/lint and Git
  whitespace checks passed. Backend startup, authenticated loopback config/API0
  and graceful shutdown passed using synthetic credentials and empty data.
- Independent full-change review: no introduced moderate-or-higher findings.
  The reviewer ran 33 focused OpenRouter tests and 24 additional actual HTTPX
  MockTransport cases, including trace redaction and no-retry checks. A wording
  ambiguity about HTTP-status retries was corrected. Self-review agreed; a clean
  review is not a guarantee of bug-free code.
- No full local builds, live/paid provider calls, model-quality evaluations,
  macOS Keychain, end-to-end CLI workflow or new rendered-browser verification ran.
  No frontend presentation changed. These offline tests establish handling of
  specified response shapes, not production-provider reliability or answer quality.

## CI and remaining work

At 15:59 UTC, exact starting development, main and published implementation have
zero GitHub statuses, check runs and Woodpecker commit comments. The workflow
expects normal pushes to main/development, but missing reports are not evidence
of a running build, failure or pass. No full-build or platform coverage is claimed.
No manual CI dispatch/rerun, private runner access, security/settings changes or
PR lifecycle action occurred.

Observe normal CI for the exact latest published head; diagnose relevant failures
before unrelated work. If reports remain absent, the owner can inspect normal
Woodpecker activation/webhook/worker/reporting health. Existing recovery issue #17
tracks this integration-evidence blocker without a duplicate issue. There are no
outstanding moderate-or-higher introduced review findings or unpublished code
changes for this repair. Continue the separately documented CLI plan dependencies
only after fresh branch, ownership and CI checks.

---

# Authentication replacement checkpoint — 2026-11-01

## Baseline and completed change

Persistent branch: `development`, starting at
[4a8ad5b7daa6ed617b0433559a8fdc7f37ffe987](https://github.com/alehkot/llm-council/commit/4a8ad5b7daa6ed617b0433559a8fdc7f37ffe987).
Observed upstream `main`:
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
The upstream API client has the same credential-response race. Its separate
ACP/model-routing changes remain preserved; integration was not required.

Implementation:
[f6335dc539b876465db0925a8ce1b3d2b01685ac](https://github.com/alehkot/llm-council/commit/f6335dc539b876465db0925a8ce1b3d2b01685ac),
tree `acf968dc889e5b4e1bb207576485a24033c5612d`.
This checkpoint's containing commit is documentation-only. The publication
receipt/recovery issue records the exact final SHA and its CI evidence separately.

A delayed old 401/403 response could clear a newly entered API key; an old 429
could latch a replacement session as rate-limited. A successful old probe could
also send a queued mutation with the replacement key. Reproduce by holding a
probe/request, clearing and replacing credentials, then releasing the old result.

Probes and requests now retain their credential generation and key. Ownership is
checked after awaited responses and before sending queued requests. Explicit
Clear and Save invalidate old generations, including re-entering the same key.
Obsolete operations reject with `AbortError` without a false auth notification;
the UI ignores that obsolete outcome. Current-key rejection/cooldown behavior
and single-flight probing remain intact. Mutations are never automatically
replayed. Existing CLI admission/durability work is unchanged.

## Verification and limitations

- Baseline: eight existing auth tests passed; all ten added regressions failed.
- Final: 91 Node tests passed, including all 18 auth cases.
- 261 backend tests passed with outbound networking forbidden under Python 3.10.
- Frontend Biome format and ESLint passed; repository Ruff format/check passed
  using the existing Python 3.13 tooling environment. Git whitespace passed.
- 20 rendered sandboxed Chromium desktop/mobile cases passed, exercising actual
  Clear/Save controls twice, stale probe/request 200/401/403/429, same-key reset,
  current-invalid-key control, and absence of unexpected mutation requests.
  Four representative screenshots were inspected. External fonts were stubbed.
- Six existing browser API-module transport recovery cases also passed.
- Independent implementation, tests and documentation review found no introduced
  moderate-or-higher findings. Published implementation head/tree matched the
  reviewed local tree; 18 focused tests passed again after publication.

No local full build, live provider, real credential, macOS Keychain or other
browser-engine verification ran. Tests do not cancel already accepted server
work or prevent delayed body parsing that started before the credential reset.
A clean review is not a guarantee of bug-free code.

## CI and next steps

The exact starting development, upstream and published implementation commits had
no statuses, check runs or Woodpecker commit comments. Empty aggregate `pending`
is missing evidence, not a running build or pass. The checked-in workflow expects
normal pushes; observe the final checkpoint SHA separately. No manual CI rerun,
private-runner access, security/settings change, release or deployment occurred.

If normal reports remain absent, inspect integration/reporting health through
authorized owner access. Do not infer a build failure from missing evidence.
Continue the separate [CLI plan](cli/PLAN.md) from its latest durability
checkpoint; this frontend fix neither completes nor changes those milestones.

Research: [React guidance on ignoring obsolete asynchronous results](https://react.dev/reference/react/useEffect#fetching-data-with-effects).
Repository reproductions establish the defect; the reference supports the
ownership approach, not an assertion that all asynchronous races are solved.
