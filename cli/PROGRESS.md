# CLI checkpoint: explicit continuation wire adapter (2026-11-06)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This slice extends local `e6be8af9c4773b7d2990d104dc25302bf11edd33`
and preserves all nine existing unpublished commits. The earlier remote
commit-creation denial remains unresolved; no retry or alternative publication
route was attempted. The recovery receipt records final local commit/tree IDs.

New `run_stage1_continuation_provider.execute_openrouter` explicitly sends one
saved first-continuation request through its separate durable guard. `turn=1`
is required. The versioned identity `openrouter-stage1-continuation-v1` cannot
adopt an existing continuation bound to another callback. Exact endpoint, timeout,
messages, partial/reasoning and settings are retained; no input binding, mutable
prompt recomposition, next-turn inference or automatic progression occurs.

The initial adapter's credential-bound callback was extracted for shared use.
Its parser and request helpers are unchanged, and its existing identity remains
`openrouter-stage1-v1`. Both adapters use the same finite duplicate-free JSON,
strict one-choice stop/length envelope, identity encoding, 2 MiB wire/result caps,
whole-request deadline and no retries/redirects/environment proxies. Credentials
remain ephemeral. Separate guards preserve operation identity and retry consent;
initial partials, reasoning, usage and ancestry are never replaced or aggregated.

Failures leave the continuation uncertain. Its own current token is required
for retry; replay returns the same retained chunk without another POST. The
existing guard owns cancellation cleanup and deletion checks. Remote execution
or billing cessation is not guaranteed, and an uncooperative injected test
transport can delay cleanup. No worker, route, startup hook, automatic loop or
public CLI consultation command selects this adapter. API version remains 0.

## Verification and review

- Final 857 offline Python 3.10 tests passed in 70.087 seconds, including 16 new
  continuation wire cases. Coverage includes exact saved payloads, pre-dispatch
  receipts, separate identities, unchanged initial work, independent stop/length
  chunks, detached replay/credential rotation, isolated consent, invalid turns
  and credentials, strict errors, no transport retry/redirect/proxy, wire caps,
  total deadline, caller/durable cancellation and deletion during body reads.
- A real mocked initial-adapter `length` response followed by explicit input
  binding and the new continuation adapter makes exactly two requests, retains
  exact partial/reasoning text and leaves the initial receipt unchanged. All calls
  use synthetic credentials and an outbound-network-blocking offline harness.
- The existing 22 initial-wire tests passed after extraction. An AST comparison
  confirmed the parser and HTTP request helpers are unchanged. Repository-wide
  Ruff format/lint passed for 121 Python files. All 114 Node tests, frontend
  formatting/ESLint and authenticated loopback Uvicorn API0 startup/graceful
  shutdown passed. No full builds, browser, Rust/native macOS/Keychain or live
  providers were run; those surfaces remain unchanged.
- Independent review found no moderate-or-higher issue. It passed the initial
  22 wire tests and 198 Stage 1 tests, then reviewed the final integration addition
  and reran all 16 frozen continuation-wire tests. Ruff/whitespace passed. Review
  covered extraction equivalence, exact requests, separate identity/token family,
  guard delegation and absence of automatic progression. It did not independently
  verify the full 857-test run or recovery archive.

## Recovery, CI and next steps

A bounded 256 KiB shared recovery slot was physically preallocated and fsynced
before implementation despite zero advertised free space. Work/tests use temporary
storage; no existing work or caches were deleted. The slot is intended for a
cumulative genuine-history bundle, current-slice patch, exact identities and test
records. Consult the recovery receipt for the final archive and restore-check
outcome. Preserve the verified base bundle and all earlier recovery artifacts.

Exact development/default checks at 14:59 UTC still report no statuses, check-runs
or commit comments; Actions and open PR/issue collections are empty. The empty
aggregate `pending` is missing normal Woodpecker evidence, not a running or passed
build. No remote objects, refs or PRs were changed, and no CI dispatch, private
runner/user computer, deployment, settings change or paid provider was used.

Next define validated chunk aggregation and terminal Stage 1 output without
rewriting retained inputs/results; any further-turn or context-reduction strategy
needs its own explicit causal and retry boundaries. Remaining stages, human
boundaries, durable worker ownership and whole-consultation acceptance remain
required before public consultation commands. Publication still needs materially
new direct authorization and fresh exact ref checks, without force/reset or
manufactured history.

---

# CLI checkpoint: guarded first continuation (2026-11-06)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This slice extends reviewed local `bc2be091e6440006102d56b59e75122e92afdf30`
and preserves all eight existing unpublished commits. Publication remains blocked
by the earlier remote commit-creation denial; no retry or alternative publication
route was attempted. The separate recovery receipt records final local identities.

New `run_stage1_continuation_execution.execute` guards an explicitly named first
continuation with a trusted injected callback. `turn=1` is required; no next turn
is inferred. Attempts are persisted separately before dispatch and tied to the
exact saved member request. Initial partials, usage, reasoning and retry ancestry
are not replaced. Complete/length chunks remain separate, without aggregation.

An uncertain continuation requires its own current attempt token. Initial and
sibling tokens cannot authorize retry, and saved continuation identities cannot
reuse initial attempt identities. Duplicate invocation or repeated consent does
not make another call. Per-request identity permits adding prepared sibling inputs
before/during another member's callback without invalidating that member's work.

The guard rechecks source, adapter identity and full owned operation at dispatch
and completion. New mutations also check current configuration/revision, while
historical reads and valid completion retain saved causal inputs. Shielded caller
cancellation signals once and retains ownership through repeated cleanup;
late responses cannot commit after cancellation or deletion. The shared registry
allows durable run cancellation to cover initial and continuation tasks together.

Inspection exposes separate compact member/turn states without response bodies.
Live continuation ownership and uncertainty participate in status precedence.
A completed chunk does not complete the consultation or aggregate the initial
partial. No wire adapter, worker, route, automatic loop, second turn, exports or
CLI consultation capability selects this helper. API version stays 0. Arbitrary
callbacks are trusted to make one bounded request; ownership cannot enforce their
internal network behavior or prove remote execution/billing stopped.

## Verification and review

- Final 841 offline Python 3.10.22 tests passed, including 62 new continuation
  guard tests. The offline runner forbids unmocked outbound connections. New cases
  cover exact payload/provenance, explicit turn validation, untouched initial
  results, independent complete/partial chunks, concurrent/out-of-order members,
  cross-event-loop ownership, sibling input preparation before/during dispatch,
  strict retry/token-family isolation, replay/stale consent, callback errors,
  pre-dispatch and completion-write failure/lost acknowledgment, malformed records,
  saved-source/adapter/ancestry drift, cancellation/deletion, suppressed/repeated
  caller cancellation, private polling/errors and fresh-process historical reads.
- An early full-suite run loaded the test file while it was being adapted and
  found three stale initial-stage running-phase assertions. They were corrected
  to the explicit continuation phase; the frozen final suite passes. Production
  behavior was not relaxed to satisfy those assertions.
- Independent review found no moderate-or-higher issue. It passed 178 Stage 1
  tests, then reviewed the final five appended cases and reran all 62 continuation
  execution tests. Ruff formatting/lint and whitespace passed. Review covered
  per-request identity, sibling growth, token isolation, durable replay,
  persistence/lost-ack boundaries, cancellation cleanup and status/history
  behavior. It did not independently verify the recovery archive.
- Repository-wide Ruff format/lint passed for 119 Python files. All 114 Node
  tests, frontend formatting/ESLint and actual authenticated loopback Uvicorn API0
  startup/graceful shutdown passed. Tests used synthetic callbacks/credentials.
  Full builds, browser, Rust/native macOS/Keychain and live providers were not run.

## Recovery, CI and next steps

Before implementation, a bounded 256 KiB recovery file was successfully
preallocated and fsynced despite zero advertised shared free space. Work/tests
remain in temporary storage; no existing work or caches were deleted. The slot
is intended for an archive containing a cumulative genuine-history bundle from
verified base `8f374449`, a current-slice patch, exact SHA/tree receipt and test
records. Consult its receipt for the eventual archive/restore-verification
outcome. Keep the existing base bundle and recovery artifacts intact.

Exact development/default status, check-run and commit-comment endpoints remain
empty; no Actions runs or open PRs/issues exist. Empty aggregate `pending` means
normal Woodpecker evidence is missing, not that a build ran or passed. No remote
object/ref/PR changes, CI dispatch, private runner/user-computer access, settings
changes, deployment or paid calls occurred.

Next: supply an explicit one-request continuation wire adapter, then define
validated chunk aggregation and any further turns/context reduction separately.
Remaining stages, human boundaries, durable worker ownership and whole-consultation
acceptance are still required before enabling public consultation commands.
Publication needs materially new direct authorization, fresh exact ref checks
and preserved genuine ancestry without reset/force.

---

# CLI checkpoint: exact first-continuation inputs (2026-11-06)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This slice extends reviewed local `dd5125bb4525623c8929f88f440d1f3dd207ed9a`
and preserves all seven existing unpublished commits. The prior remote commit
creation denial remains unresolved; no publication retry or alternative route
was attempted. The recovery receipt records the final local commit/tree.

New internal `run_stage1_continuation_inputs.bind` pins one member's exact first
continuation request separately from its retained initial partial. It requires a
saved completed `length` response, a policy allowing continuation, matching
revision/current configuration and an uncancelled, undeleted run. It retains the
initial input digest, full operation/consent-ancestry digest, attempt ID, response
digest and initial adapter identity. The original initial result is not replaced.

The payload copies saved initial settings/messages, then appends exact partial
text and nonempty reasoning details plus the existing continuation instruction.
Usage stays out of the prompt. All serialized messages must already fit the
saved input budget, including UTF-8 and reasoning metadata; equality fits.
Overflow fails without a write, truncation, output-budget reduction or external
call. No provider, catalog or live skill-library lookup is introduced.

Duplicate binding is idempotent; distinct members are retained in original
member order. Saved inputs are canonically validated and derived again from
retained sources. Historical detached reads remain available after cancellation
or live configuration drift; deletion removes the checkpoint. Compact inspection
adds only identity/digests/counts and rejects continuation state on initializing
runs. This is input preparation only: no continuation attempt, retry token,
worker ownership, further turn, result aggregation, export or public API is added.
The initial execution helper still returns its original partial without replay.

## Verification and review

- The existing 749-test offline Python suite and final 779-test suite passed on
  Python 3.10.22 with unmocked outbound connections forbidden. Thirty new tests
  cover exact legacy continuation-message parity, preserved initial inputs and
  results, full operation/response/adapter identity, initial retry ancestry,
  allowance/revision/configuration gates, exact-fit and overflow boundaries,
  nonempty reasoning metadata, canonical numeric types, malformed/tampered
  sources/records, duplicate and sibling races, persistence failure and lost
  acknowledgment, cancellation/deletion serialization and fresh-process reads.
- Independent read-only review found no moderate-or-higher issue. It independently
  passed all 121 Stage 1 tests (30 new inputs, 50 initial guard, 22 adapter and
  19 initial inputs), reran the final 30 tests after the last assertions, and
  passed Ruff/whitespace checks. Source validation, private compact inspection,
  exact message parity, preserved partials and persistence/lost-ack boundaries
  were reviewed.
- Self-review strengthened provenance with a full initial-operation digest so
  valid-shape retry-ancestry changes cannot be silently accepted. Canonical JSON
  comparison also rejects same-value numeric type changes rather than relying on
  Python equality. Regression tests cover both; no execution behavior was expanded.
- Repository-wide Ruff formatting/lint passed for 117 Python files. All 114 Node
  tests, frontend formatting/ESLint and authenticated loopback Uvicorn API0
  startup/graceful shutdown passed. No full build, browser, Rust/native macOS/
  Keychain or live-provider verification was run; those surfaces are unchanged.

## Recovery, CI and next steps

The shared filesystem still advertises zero available bytes, but a bounded
256 KiB recovery file was successfully preallocated and fsynced before this slice
started. Implementation/tests stayed in temporary storage; no existing files or
caches were deleted. The reserved slot is intended to hold an archive with a genuine-history
incremental bundle, patch, exact SHA/tree receipt and verification evidence.
Consult that receipt for the eventual restore-verification outcome using the
existing base and prior incremental bundles. Keep the bundles together.

Fresh development/default status, check-run and commit-comment endpoints are
empty; no Actions runs or open PRs/issues exist. Empty aggregate `pending` is
missing normal Woodpecker evidence, not a build result. No remote object/ref/PR
change, CI dispatch, runner/user-computer access, deployment or paid call occurred.

Next: separately guard a specifically identified continuation turn, retaining all
prior chunks and isolating uncertain retry consent from initial/sibling attempts.
Then address context reduction, further turns, remaining stages/human boundaries,
worker scheduling and whole-consultation acceptance. Keep API0 and consultation
commands gated. Publication still requires materially new direct authorization,
fresh exact ref checks and preserved genuine ancestry without reset/force.

---

# CLI checkpoint: bounded initial Stage 1 wire adapter (2026-11-05)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This slice extends reviewed local `8f3744499fcfe9e41351e5f96cff47bf0a8f7644`
and preserves all six unpublished commits. Publication remains blocked by the
prior denied remote commit creation; no retry or alternative publication route
was attempted. The recovery receipt records the final local commit/tree.

New internal `run_stage1_provider.execute_openrouter` explicitly selects one
versioned adapter over the existing member attempt guard. It sends the exact
saved endpoint, JSON payload and 120-second timeout with an ephemeral credential.
There are no default transport retries, redirects, environment proxies, catalog
lookups, context reduction or continuation calls. Each HTTP attempt is recorded
before dispatch; uncertain work still requires its exact member retry token.
Already saved results are returned without another request.

The transport requests identity encoding, rejects compressed responses before
reading them, and caps retained body bytes at 2 MiB before each append. An overall
asynchronous deadline covers request and body read alongside HTTPX timeouts.
UTF-8 JSON rejects duplicate keys, nonfinite literals and overflowed floats,
including ignored metadata. Exactly one assistant text choice with a `stop` or
`length` finish is required; error, refusal and tool/function-call responses fail
closed. Reasoning details and usage retain their existing finite-JSON and
normalized-size checks. Partial responses stay partial without automatic replay.

Research reused the explicit refinement adapter and the new Stage 1 guard;
transport/parsing policy is separate from the legacy retrying provider path.
No worker, startup hook, public route or CLI consultation command selects this
adapter. API0 stays unchanged. Later stages, continuation/context-reduction
boundaries, independent worker ownership and receipt-to-export integration remain
incomplete. Arbitrary injected test transports remain trusted and could violate
single-request or cancellation behavior; no remote exactly-once/billing guarantee
is made. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline 727 and final 749 offline Python 3.10.22 tests passed, with unmocked
  outbound connections forbidden. Twenty-two new tests cover exact saved wire
  inputs and durable pre-dispatch receipt, default retry settings, credential
  rotation, saved-result replay, uncertain-work consent, HTTP errors/redirects,
  malformed/error/refusal/tool envelopes, strict JSON, body limits,
  compression rejection before reading, total timeout, caller/durable
  cancellation, deletion, ownership through stream cleanup, persistence failure
  and lost completion acknowledgments. No live provider connection was made.
- Self/independent review caught a finite-JSON nuance: overflowed numbers in
  ignored envelope fields must fail too. A finite float parser closes that gap.
  Two early tests had the wrong HTTPX mock target and an overly permissive empty
  function-call expectation; they were corrected without relaxing the adapter.
- Independent final review found no remaining moderate-or-higher issue. It passed
  91 focused tests (22 adapter, 50 attempt guard, 19 saved-input), eight additional
  wire/timeout/cancellation scenarios, 35 malformed parser cases plus a valid
  control, and whitespace. Final production/test hashes were confirmed. Review
  is not a correctness guarantee.
- Repository-wide Ruff format/lint passed for 115 Python files. All 114 Node tests,
  frontend formatting/ESLint, and actual authenticated loopback Uvicorn API0
  startup/graceful shutdown passed. Final checks use synthetic credentials and
  isolated temporary data. Full builds, browser, Rust/native macOS/Keychain and
  live-provider checks were not run; those surfaces are unchanged.

## Recovery, CI and next steps

The shared filesystem was nearly full, so implementation and test logs used a
separate temporary local checkout. No other work or caches were deleted. The
recovery receipt identifies a small incremental Git bundle and patch, anchored to
the previously verified full-history bundle at `8f374449`, rather than duplicating
that history. These must be retained together; the receipt records restore checks
and final exact commit/tree identities.

Exact development/default commits expose no status contexts, check runs or
commit comments, and there are no Actions runs or open PRs/issues. Empty aggregate
`pending` is missing normal Woodpecker evidence, not a running build/pass/failure.
No remote blobs, trees, commits, ref updates, PR actions, CI dispatch, runner/user
computer access, settings changes, deployment or paid call were attempted.

Next: obtain direct authorization before any publication retry and preserve all
unpublished ancestry. Implement separately guarded continuation/context-reduction
work, remaining stages/skills/synthesis and human boundaries, then durable worker
scheduling and whole-consultation acceptance before enabling API1 or consultation
commands.

---

# CLI checkpoint: guarded initial Stage 1 attempts (2026-11-05)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This slice extends reviewed local `ca1bf8c0fc0af0fd89ed4063f5eaa599e596b620`
and preserves all five unpublished model-budget, skill-archive/render/plan and
Stage 1 input commits. Publication remains blocked by the earlier denied remote
commit creation; no retry or alternative publication route was attempted. The
separate recovery receipt records the final local commit/tree and artifacts.

The internal `run_stage1_execution.execute` now saves and owns one initial member
attempt before invoking an explicitly injected single-attempt provider callback.
It supplies exact pinned endpoint/timeout/payload values, supports concurrent
members and prevents duplicate live calls for one member. An uncertain attempt
requires its current per-member token; repeated consent does not dispatch another
attempt. Completed siblings are preserved. The guard rechecks source, adapter
identity and the full owned attempt before dispatch and result persistence.

Complete responses and length-limited partial text/reasoning/usage are retained
in the durable receipt, with finite JSON validation and a 2 MiB normalized-result
limit. Partial responses require a separately implemented continuation, never an
automatic initial-request replay. Compact inspection exposes per-member states
and retry tokens without private response bodies. Historical result reads are
detached and byte-pure. Cancellation/deletion prevent late commits and
resurrection; caller cancellation is preserved even if a callback suppresses it,
with ownership retained and one signal through repeated cancellation cleanup.

Research followed the existing refinement attempt/cancellation guard and the
pinned Stage 1 source contract. No HTTP adapter, legacy provider path, manager
worker, auto-loop, public API or CLI consultation command invokes this code.
Injected adapters must honor one request and the saved timeout; callback ownership
cannot enforce arbitrary code's internal network behavior. Receipt results do
not update legacy conversation stage fields or exports. Public detachment still
requires independent worker ownership. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline 677 and final 727 offline Python 3.10.22 tests passed with unmocked
  outbound networking forbidden. Fifty new tests cover exact supplied inputs,
  concurrent/out-of-order members, cross-event-loop duplicate suppression,
  explicit retry replay/stale and cross-member tokens, retained partial results,
  errors/cancellation, both sides of attempt/result persistence failures,
  lost acknowledgments, restart execution/recovery, current-setting drift versus
  historical reads, causal and adapter-identity drift, cancellation/deletion
  before and during callbacks, size/metadata validation, private error wrappers,
  read purity and malformed saved records.
- Independent review found two pre-commit issues: a callback could swallow caller
  cancellation, and changed saved adapter identity could mislabel a response.
  Shielded cleanup plus adapter/full-attempt rechecks fixed both, with regression
  tests. Final review found no remaining moderate-or-higher issue and independently
  passed all 50 execution tests, 19 input tests, 18 cancellation tests, 16 inspection
  tests, eight additional synthetic edge scenarios and whitespace checks. Final
  production/test hashes were confirmed. Review is not a correctness guarantee.
- The additional review scenarios cover late success/error during cancellation,
  repeated caller cancellation, concurrent durable cancellation, caller cancellation
  before result delivery, deleted execution state and changed consent ancestry. Ownership stays
  until cleanup, signals occur once, and no late response is saved.
- All 114 Node tests, frontend formatting/ESLint, repository-wide Ruff format/lint
  for 113 Python files and whitespace checks passed. Actual authenticated loopback
  Uvicorn API0 startup and graceful shutdown passed with temporary storage and
  synthetic credentials.
- Rust/UI, public capabilities and legacy provider execution are unchanged.
  Full builds, Rust, browser, native macOS/Keychain and live providers were not
  run. Tests do not establish actual remote-provider semantics/billing,
  whole-consultation behavior, power-loss durability or multi-process ownership.

## CI, publication and next steps

Exact development/default commits still expose zero status contexts, check runs
and commit comments; there are no Actions runs or open PRs/issues in the target
repository. No normal Woodpecker result is visible. Empty aggregate `pending`
is missing evidence, not an observed running build, pass or failure. Unpublished
local commits have no remote SHA to check. No remote blobs, trees, commits or ref
updates, PR action, CI dispatch, private-runner/user-computer access, settings
change, deployment or paid call was attempted in this iteration.

Next: obtain direct authorization before publication, preserving the genuine
unpublished ancestry without reset/force. For implementation, add a bounded
single-attempt wire adapter using saved inputs and protocol validation, then
separately guarded context reduction/continuations, remaining stage/skill/synthesis
settings and human boundaries, durable scheduling and whole-run acceptance.
API version stays 0 and public consultation commands remain unavailable.

---

# CLI checkpoint — pinned initial Stage 1 requests (2026-11-05)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This slice extends reviewed local `500c5f657dbdeb33407d94f50777d8d9c9348b7a`
and preserves its unpublished model-budget, skill-archive, renderer and skill-plan
ancestry. Changes stay local pending publication authorization; the separate
receipt records the final local commit/tree and recovery artifacts.

New internal `run_stage1_inputs.bind` persists the exact complete initial Stage 1
messages and ordered OpenRouter JSON bodies from existing response/skill/request
policies and model budgets. Skills precede interactive steering; finalized user
text is unchanged. It records endpoint, 120-second timeout, reasoning support,
output limits, source digests and the shared conservative input-token bound.
Every model must already fit. Unsupported context reduction rejects the entire
operation without partial state, truncation, metadata refresh or provider calls.

Scope is explicitly limited to the saved archived explicit-skill plan with the
reference router already disabled. Enabled routing and automatic selection are
not silently bypassed. Current setting/revision/cancellation/deletion guards apply
to initial and repeated binding. Saved history is immutable, source-validated,
independent of current settings/library and readable after cancellation. Deletion
erases it; inspection exposes only identity/counts. A returned value cannot grant
dispatch permission. See [CONTRACT.md](CONTRACT.md) for the exact limits.

## Verification and review

- Baseline 658 and final 677 offline Python 3.10.22 tests passed with unmocked
  outbound networking forbidden. Nineteen new tests cover exact messages and
  per-member payloads, actual legacy Stage 1 through mocked HTTP parity, both
  reasoning-support cases, empty system content, strict budget equality and
  one-member overflow, missing/stale sources, live-setting drift versus historical
  reads, archived content after deletion, credential exclusion, immutable copies,
  24 concurrent duplicates, write failure/lost acknowledgment, cancellation and
  deletion serialization, nested corruption, unknown runs and fresh-process reads.
- Self-review passed 258 legacy composer policy/budget combinations including
  exact Unicode/whitespace, 102 malformed-value probes through bind/load/inspect
  without writes or private-text exception leakage, and 32 cross-thread
  preparation/cancel/delete interleavings without resurrection.
- Independent read-only review found no introduced moderate-or-higher issue.
  All 677 offline tests, 19 focused tests, repository-wide Ruff/whitespace checks
  and six frozen file hashes passed independently. Extra probes covered 161
  actual legacy composer/mocked-HTTP parity cases with response options/omissions,
  skip, Unicode/whitespace, both reasoning modes and varied output caps; six
  revision-6 original/refined/user-edited question and skip cases; and 207
  malformed values through 621 bind/load/inspect calls without writes or private
  exception content. Review is not a correctness guarantee.
- All 114 Node tests, frontend format/ESLint, repository-wide Ruff format/lint
  for 111 Python files and whitespace checks passed. An actual authenticated
  loopback Uvicorn API0 startup and graceful shutdown passed with temporary storage
  and synthetic credentials. The first focused run had one test expecting the
  wrong existing unknown-run exception; the assertion was corrected before final
  reruns. No production regression was exposed by that initial failure.
- Rust/UI, legacy graph/provider code and API capability are unchanged. Rust,
  browser UI, native macOS/Keychain and live-model checks were not run. Full builds
  were intentionally not run. This is not end-to-end consultation verification,
  public polling/storage performance testing or multiprocess/power-loss proof.

## CI, publication and next steps

Exact development/default heads have no statuses, check runs or commit comments;
there are no GitHub Actions runs or open PRs. No normal Woodpecker evidence is
visible. Empty aggregate `pending` is absent evidence, not an observed running
build, pass or failure. New local commits have no published SHA to check. No
publication retry, remote write, CI dispatch, private-runner/other-environment
access, PR/settings action or paid model test occurred.

Remaining work includes automatic/model-based selection, guarded initial request
execution and recovery, separately guarded context reduction/continuations,
later-stage settings/inputs and human boundaries, scheduling and public Rust CLI
integration. The new plan supplies initial requests only; it neither executes
nor grants permission to send them. API version remains 0. Keep the genuine local
history and wait for direct publication authorization before any remote write.

---

# CLI checkpoint — pinned deterministic skill plan (2026-11-05)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This work extends reviewed local commit `a461f97762f34a092d6538ece812b23b07efcb3f`
and preserves its unpublished model-budget, skill-archive and rendering ancestry.
Changes remain local pending publication authorization. Exact final local
commit/tree and recovery artifacts belong in the separate receipt.

The archive renderer could preview content but retained no reference decision or
exact future skill block. The new internal `run_skill_plan.bind` now saves the
existing deterministic keyword-overlap reference choices plus exact Stage 1/3
skill blocks, distinct budgets and packing reports. Inputs are the archived
explicit skills and the saved finalized question/response policy. Source hashes,
revision, candidate IDs, grouped references and reference budget remain attached.
These are the skill portions of future stage messages, not full provider inputs.

This operation only supports skills enabled with the reference router already
explicitly disabled. It does not change settings or substitute fallback work for
an enabled model router. Automatic skill selection, model-based reference routing
and router-failure fallback remain unsupported. Relevant setting drift, stale
revisions, cancellation/deletion and invalid sources fail closed on binding.
No model call, queue, prompt answer or interaction revision is introduced.

Frozen historical results use saved settings/content, not the current library or
configuration. Validation recomputes the deterministic version-1 derivation and
checks exact identities, choices, blocks and reports without writing or refreshing
anything. Unsupported future semantics require explicit version handling. Reports
use immutable canonical JSON with detached dictionary access. Inspection retains
only counts, lengths and an aggregate digest; private prompt content remains in
the run receipt. Cancelled history stays readable; deleting its conversation
removes the plan through the existing tombstone. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline 639 and final 658 offline Python 3.10.22 tests passed with unmocked
  outbound networking forbidden. Nineteen new tests cover exact choices and both
  blocks/reports, actual legacy router-disabled parity, strict supported settings,
  missing/stale sources, archived reads after library deletion, immutable copies,
  current-setting drift versus historical reads, credential exclusion, zero
  budgets/caps, 24 concurrent duplicates, write failures/lost acknowledgments,
  cancellation/deletion races, malformed evidence and fresh-process restoration.
- Self-review passed 100 configuration combinations against the actual legacy
  disabled-router path and both library-rendered blocks/reports; 70 malformed
  nested-value probes through bind/load/inspect without rewrites; a private-text
  exception-chain probe; and 32 cross-thread preparation/cancel/delete
  interleavings. No introduced moderate-or-higher finding was identified in
  self-review; review is not a correctness guarantee.
- Independent read-only review found no moderate-or-higher issue and passed all
  658 offline Python tests and changed-file Ruff checks. Additional probes covered
  selection from a refined question, skipped pre-query input at revision 6, and
  legacy synthetic whole-file parity. The seven-file hash manifest was verified.
  Review clarified that the configured section cap counts selectable candidates;
  one synthetic legacy candidate can contain multiple rendered heading sections.
- All 114 frontend Node tests, frontend format/ESLint, repository-wide Ruff
  format/lint for 109 Python files and whitespace checks passed. Actual loopback
  Uvicorn startup, authenticated API0 config and graceful shutdown passed with
  temporary storage and synthetic credentials. A newly added race test initially
  missed an import; correcting that test-only issue preceded all final reruns.
- Rust/UI and legacy routing source behavior are unchanged. Rust and rendered
  browser checks were not rerun. Full builds, native macOS Keychain/platform and
  live-provider checks remain unrun. Full public polling/storage performance,
  multiprocess/power-loss safety and end-to-end consultation execution are not
  established.

## CI, publication and next steps

Remote development/default heads expose no status contexts, check runs or commit
comments; no GitHub Actions runs or open PRs are reported. No normal Woodpecker
result is observable. Empty aggregate `pending` remains missing evidence, not an
observed running build, pass or failure. No new published SHA exists for these
local changes. No publication retry, CI dispatch, private-runner/other-environment
access, PR/settings action or paid model test occurred.

Next: resolve the outstanding publication decision and reconcile fresh remote
heads before publishing reviewed work. Complete automatic/model-based selection,
remaining exact stage/transport settings and full rendered inputs, guarded council
work and human checkpoints, then durable scheduling and public run acceptance.
Loading a saved plan never authorizes dispatch, old/disabled skills or uncertain
retries. API0 and Rust consultation-command gates remain unchanged.

---

# CLI checkpoint — archived skill rendering (2026-11-05)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Default `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This work extends local reviewed commit `ed6a7844586f592110ddc850bc3449f635f69fca`,
which retains the unpublished model-budget and durable-skill-content ancestors.
All three changes remain local pending publication authorization. Exact final
local commit/tree and recovery artifacts belong in the separate receipt.

The archive previously had no renderer that could consume its checked bytes
without reopening mutable skill-library paths. The internal read-only
`run_skill_rendering.render` now produces a candidate skill block and detached
packing report using only saved content, explicit reference/section choices and
an explicit character budget. It does not route references, persist a selection
or final prompt, capture missing content, call a provider or schedule work.

The existing renderer and new archive adapter share one deterministic packer.
Legacy callers keep their original library readers and behavior. The archive
adapter validates metadata against archived text, rejects unknown or ambiguous
selections and unsafe reference path forms, and preserves the known synthetic
legacy whole-file section semantics. Whole sections are dropped before marked
body truncation, retaining original order and Unicode character-count semantics.
A positive budget too small for mandatory framing fails instead of returning an
over-budget candidate. A zero budget returns no block.

Saved source IDs/digests and renderer version accompany the report. Original
scripts/assets/evals counts were not archived, so the report explicitly says
`bundle_inventory=not_archived` rather than inventing zero counts. Library
replacement, disabling or deletion cannot change a preview. Cancelled history
can be inspected; deleted runs reject new loads. An already returned in-memory
copy is not revoked by later cancellation/deletion and never establishes runnable
state. See [CONTRACT.md](CONTRACT.md) for the complete boundaries.

## Verification and review

- Baseline 623 and final 639 offline Python 3.10.22 tests passed with unmocked
  outbound networking forbidden. Sixteen new tests cover body/whole-file/section
  parity, packing and framing floors, source isolation after library mutations,
  detached reports and byte-pure reads, reasons, strict arguments, skill order,
  bundle-count disclosure, synthetic legacy sections, missing archives,
  cancellation/deletion, corrupt archives, incoherent metadata, fresh-process
  restoration, headingless/empty/capped references and duplicate references.
- Self-review compared 384 selection/budget combinations against the actual
  pre-change renderer source and the shared library/archive implementations.
  It repeated all 312 supported combinations after library deletion without
  library reads or writes, and passed 64 concurrent previews of different
  archived runs sharing the same skill ID without mixing their content.
- Self-review found a path-validation assumption: the normalization helper
  expects ingestion to have rejected traversal already. The new archive adapter
  now explicitly rejects nested traversal, backslashes and controls, with added
  regression cases. There was no filesystem access in the rendering path.
- Independent read-only review found no moderate-or-higher issue. It passed all
  639 offline Python tests (including the 16 new tests), 720 additional parity
  comparisons against the original Git source across randomized modern and
  synthetic-legacy references, and 12 hostile reference-path probes. It verified
  all six changed-file hashes and the final checkpoint, inspecting rather than
  rerunning the supplied frontend/lint/startup/concurrency evidence.
- All 114 frontend Node tests, frontend format/ESLint, repository-wide Ruff
  format/lint for 107 Python files and whitespace checks passed. Actual loopback
  Uvicorn startup, authenticated API0 config and graceful shutdown passed with
  temporary storage and synthetic credentials. No introduced moderate-or-higher
  issue was found in self-review; review is not a correctness guarantee.
- Rust/UI source is unchanged; Rust and rendered-browser checks were not rerun.
  Full local builds, native macOS Keychain/platform and live-provider checks remain
  unrun. Full public polling/storage performance, multiprocess/power-loss safety
  and end-to-end consultation execution are not established.

## CI, publication and next steps

Remote development/default heads expose no status contexts, check runs or commit
comments; the repository has no GitHub Actions runs and no open PRs. No normal
Woodpecker result is observable. Empty aggregate `pending` means missing evidence,
not an observed running build, pass or failure. No new public commit exists for
these local changes. No publication retry, CI dispatch, private-runner or other
environment access, PR/settings action or paid model test occurred.

Next: resolve the outstanding publication decision, reconcile fresh remote heads
and publish reviewed work only when authorized. Complete retained reference
routing/automatic selection decisions, exact stage settings and final rendered
prompt provenance, guarded provider work and remaining human checkpoints before
public run APIs, durable scheduling or Rust consultation commands are enabled.

---

# CLI checkpoint — durable explicit skill content (2026-11-05)

## Baseline and scope

Remote `development` remains [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Upstream `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
This work continues local reviewed commit `14abd29558839407ab513d2f98eab16a0cf68148`,
which retains genuine history and the unpublished model-budget implementation.
The changes remain local pending publication authorization. Exact final local
commit/tree and recovery artifacts belong in the separate receipt; this file does
not claim that an unpublished commit is available on GitHub.

The prior skill identity/capture helpers could detect drift and return checked
bytes, but a process restart still depended on the current library. The new
internal `run_skill_snapshot.persist` archives prompt-readable content and routing
metadata matching a previously bound explicit uploaded-skill identity. It checks
revision, cancellation, deletion and adjacent evidence before saving. No identity
is invented, no interaction revision is advanced and no work is scheduled.

Saved copies and historical reads are immutable and independent of later library
replacement, disabling or deletion. The archive preserves ordered IDs and hashes,
strictly validates document structure and exact content, and never refreshes or
repairs corrupt saved work. Concurrent duplicates capture/write once. A lost write
acknowledgment can be recovered after the original library content disappears.
Run cancellation retains read-only history; conversation deletion removes the
content through the existing tombstone. Inspection exposes only availability and
IDs/digests, rather than bodies in every status response. See [CONTRACT.md](CONTRACT.md).

This is not permission to execute a disabled or old skill. Future consumers must
explicitly select archived content and use guarded dispatch with current run
checks. Auto-selection, external sources, reference-routing decisions, rendered
prompts, complete settings provenance and council execution remain unfinished.
The archive copies no scripts/assets/evals and adds no credential or filesystem
location, but skill text itself can contain private information. It is neither
secret detection nor encrypted storage. API0, legacy behavior and Rust
consultation-command gates remain unchanged.

## Verification and review

- Baseline 604 and final 623 offline Python 3.10.22 tests passed with unmocked
  outbound networking forbidden. Nineteen new tests cover exact contents and
  ordering, Unicode, non-prompt file exclusion, frozen/detached values, compact
  inspection/cursors, replacement/disabling/deletion, prior identity requirements,
  stale revisions, cancellation, fresh-process restore, write failures/lost
  acknowledgments, 24 concurrent duplicates, supported library/run mutation races,
  mixed external file changes, malformed evidence and credential exclusion.
- Self-review passed 110 additional malformed nested snapshot probes through
  persist/load/inspect without rewriting files, a private-text exception-chain
  probe and 32 cross-thread save/cancel/delete interleavings. No introduced
  moderate-or-higher finding was identified; review is not a correctness guarantee.
- Independent read-only review passed 141 related tests and five additional
  adversarial probes with no moderate-or-higher findings. Review is not a
  correctness guarantee.
- All 114 frontend Node tests, frontend format/ESLint, repository-wide Ruff
  format/lint for 105 Python files and whitespace checks passed. Actual loopback
  Uvicorn startup, authenticated API0 config and graceful shutdown passed with
  empty temporary storage and synthetic credentials.
- Local harness corrections: an initial Ruff invocation targeted the frontend
  rather than repository root and was rerun correctly; `npm test` is not defined,
  so the documented Node test files were run directly. An added test initially
  placed assertions after its intentional corruption; moving them into their
  intended test resolved that test-only failure. Final checks passed.
- Rust/UI source is unchanged; Rust and rendered-browser checks were not rerun.
  Full builds, macOS Keychain/platform and live-provider checks remain unrun.
  Public polling/storage performance, power-loss durability, multiple backend
  writers and end-to-end consultation execution are not established.

## CI, publication and next steps

Exact remote development/main heads expose no statuses, check runs, commit
comments or GitHub Actions runs. No normal Woodpecker result is observable.
Missing evidence is neither a passed build, a known failure nor an observed
running build. There is no published SHA for this change to check. No publication
retry, CI dispatch, private-runner/user-computer access, PR action or paid call
occurred in this work.

Next: obtain the outstanding publication decision, reconcile fresh remote heads
and publish reviewed changes only when authorized. Continue exact stage-input and
transport preparation, saved reference routing and automatic selection policy,
guarded council work, remaining human checkpoints and durable scheduling before
exposing public run operations or enabling Rust consultation commands.

---

# CLI checkpoint — pinned council model budgets (2026-11-05)

## Baseline and scope

Persistent remote `development` started at [aeee4f9f4dfddc959460f200593c4a3658ce331a](https://github.com/alehkot/llm-council/commit/aeee4f9f4dfddc959460f200593c4a3658ce331a).
Upstream `main` is [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), a retained ancestor.
This checkpoint accompanies the implementation. Exact local/published commit and
tree identities, publication verification and final CI observations belong in the
separate recovery receipt, avoiding a self-referential commit hash here.

An explicit internal async binder now retains each selected council model's
resolved context/completion limits, supported parameters and derived initial
input/output budgets and reasoning flag. Values are immutable and tied to the
saved request policy and pre-query revision. The legacy formula is covered by
parity tests. This closes one effective-request prerequisite, not the full run.

Metadata resolution uses the existing public-catalog/default/fallback resolver
outside the storage lock. Guards are checked before and after awaiting it;
cancellation, deletion, stale revisions and configuration drift prevent a late
write. Concurrent resolvers share the first persisted values; repeat binds and
historical reads never refresh metadata. Malformed evidence fails closed without
repair. A lost write acknowledgment can be recovered by reading or rebinding.

These are the resolved values, not proof of provider capacity or a retained
catalog-versus-fallback source. No completion, context-reduction or continuation
request is made. Future guarded execution must consume the pinned budgets rather
than silently resolving new ones. Exact wire inputs/timeouts, synthesis/helper
budgets, remaining skill provenance, council-stage guards, human prompts and
scheduling remain dependencies. The legacy execution path, API0 and Rust
consultation-command gates are unchanged. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline 586 and final 604 offline Python 3.10.22 tests passed with unmocked
  outbound networking forbidden. Eighteen new tests cover exact metadata and
  source identity, derived budgets, frozen/detached values, no credential capture,
  repeat-bind/read purity, current-config drift, invalid inputs, concurrent
  resolution, cancellation/deletion during resolution, failed/lost writes,
  fresh-process reads, malformed stored evidence and legacy budget parity.
- An actual resolver integration test uses HTTPX MockTransport: one unauthenticated
  metadata GET, provider/context minimum, unknown-model fallback and no repeated
  refresh. No live catalog or provider was called during verification.
- Self-review passed 32 cancellation and 32 deletion interleavings from separate
  threads while metadata was awaited, confirming the storage lock is released;
  83 additional malformed nested-value probes failed closed without rewriting.
- Independent read-only review found no moderate-or-higher findings. It
  independently passed all 18 focused tests, five additional adversarial probes,
  changed-file Ruff lint/format and whitespace checks. Probes covered unlocked
  cross-thread reads, cross-event-loop concurrent binders with one write, endpoint
  drift, external task cancellation and malformed saved values. A clean review is
  not a correctness guarantee.
- All 114 existing frontend Node tests, frontend formatting/ESLint, repository-wide
  Ruff 0.16.4 format/lint for 103 files and whitespace checks passed. Actual
  loopback Uvicorn startup, authenticated API0 config and graceful shutdown passed
  using temporary storage and synthetic credentials.
- Initial local harness invocation found a read-only default uv cache; selecting
  the existing writable cache fixed it. Ruff requested test-import formatting;
  the final source passes. A self-review probe initially expected the already
  correct `reasoning=true` value to fail; excluding unchanged values corrected the
  probe without changing implementation.
- Rust/UI source is unchanged. Rust tests were not rerun; rendered-browser checks
  are inapplicable. Full builds, native macOS Keychain/platform and live-provider
  checks remain unrun. These tests do not establish whole-council execution,
  power-loss durability, cross-process writers or exactly-once provider work.

## CI and next steps

Exact starting development and default heads expose zero status contexts, check
runs and commit comments, including no Woodpecker report. No GitHub Actions runs
exist. Empty aggregate `pending` is missing evidence, not an observed running
build, pass or failure. Full builds remain unrun under the no-local-full-build
policy. Normal final-head evidence must be inspected separately. No manual CI
run/rerun, private-runner/user-computer access, security/settings changes, PR action,
deployment or paid call occurred.

Next: observe normal exact-head CI. Retain exact first-stage inputs and remaining
request/skill/synthesis provenance, then implement guarded council stages and
remaining human-input boundaries, durable scheduling and full public acceptance.
Do not treat pinned limits as dispatch permission or enable consultation commands
before the whole agreed recovery contract works.

---

# CLI checkpoint — configured OpenRouter request policy (2026-11-04)

## Baseline and published work

Persistent remote `development` started at [dd03795d551024bd2e788054038bbe7016c8e971](https://github.com/alehkot/llm-council/commit/dd03795d551024bd2e788054038bbe7016c8e971).
Upstream `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), a retained ancestor.
Published implementation [5a777d834d78fee02c018f10f84f81c9144a3211](https://github.com/alehkot/llm-council/commit/5a777d834d78fee02c018f10f84f81c9144a3211) has tree `4ad190b46a36972d2db8799eaa8ef04643a8ea6a`, identical to tested local implementation `911a178d31724d45dfbdf04837e6e21075468452`. Its parent and all five changed blob identities were checked against reviewed source.
This documentation-only descendant records the outcome. Its final local/published SHA and exact CI observation belong to the publication receipt, avoiding a self-referential hash. A complete real-history bundle and binary patch are retained without resetting existing history.

## Scope and rationale

The configured council roster previously had no retained OpenRouter request limits. The explicit internal `council_request_policy.bind` now stores endpoint, token/context/continuation/retry ceilings and backoff/jitter settings with the saved council selection's digest and revision. Binding rejects current selection or request-setting drift; historical inspection preserves saved values without reading current settings or making requests.

The frozen value uses required fields, strict integer counts, finite ordered backoff settings and an HTTPS endpoint without URL credentials, query, fragment, whitespace or controls. No provider/local authentication keys are captured. Missing prerequisites, stale revisions, cancellation/deletion and invalid saved evidence fail closed. Concurrent duplicates write once, and an uncertain write acknowledgment can be recovered without rewriting an already-saved value.

Research in [openrouter.py](../backend/openrouter.py) and [model_context.py](../backend/model_context.py) confirmed that the legacy client also obtains changing catalog metadata, computes per-model budgets, reduces context and continues prose. Therefore these are configured limits, not complete effective wire provenance or permission to retry uncertain work. Every potentially billed request still needs durable guards respecting explicit-retry recovery. Synthesis/helper settings, skills and exact stage inputs remain separate dependencies. No legacy execution path, public run route, API1 capability or CLI consultation command is enabled or changed. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline 568 and final 586 offline Python 3.10.22 tests passed with unmocked outbound networking forbidden. Eighteen new tests cover exact loaded settings/source identity, all captured settings' drift, strict invalid values and unsafe endpoints, zero extra-call limits, frozen/detached values, credential rotation/exclusion, 24 concurrent binds, before/after-write failures, fresh-process restoration, missing/corrupt sources, stale revisions, cancellation and deletion.
- Independent whole-change review found no introduced moderate-or-higher findings. It passed 136 related offline tests, six refined-history/skip cases at revisions 6/9/12, a secret-free invalid-URL traceback probe and 98 malformed saved-policy probes without rewrites. It did not rerun Ruff because its environment lacked that executable; the implementation environment's final repository-wide Ruff checks passed. Review is not a correctness guarantee.
- Self-review found no moderate-or-higher issue and passed 32 bind/cancel plus 32 bind/delete races, historical reads with current-setting resolver failure spies, and five invalid policy-container probes. The initial Ruff check required only test-import spacing; after correction the final exact source passed all 586 tests and Ruff format/lint for 101 files.
- All 114 frontend Node tests, frontend formatting/ESLint and whitespace checks passed. Actual loopback Uvicorn startup, authenticated API0 config and graceful shutdown passed with empty temporary storage and synthetic credentials.
- Rust/UI source is unchanged. Rust tests were not rerun and rendered-browser checks are inapplicable. Full builds, macOS Keychain/platform and live-provider checks remain unrun. Tests do not establish full consultation execution, model quality, power-loss durability or concurrent backend-process writers.

## CI and next steps

Exact starting development, main and published implementation expose zero status contexts, check runs and commit comments. No normal Woodpecker result or job/platform coverage is observable. Empty aggregate `pending` is missing evidence, not an observed running build, pass or failure. Full builds remain unrun under the no-local-full-build policy. No manual CI dispatch/rerun, private-runner/user-computer access, settings/security change, PR action, deployment or paid call occurred.

Next: observe normal exact-head Woodpecker evidence; retain effective model metadata, exact inputs and remaining synthesis/skill provenance; implement guarded council stages and remaining human-input boundaries, durable scheduling and whole-run public acceptance before enabling consultation commands. Preserve explicit retry for uncertain work and deliberately extend causal validation when later workflow boundaries are added.

---

# CLI checkpoint — configured council selection (2026-11-04)

## Baseline and published work

Persistent remote `development` started at [8c0a83875951723b23caceff53c61fc575ac4abc](https://github.com/alehkot/llm-council/commit/8c0a83875951723b23caceff53c61fc575ac4abc).
Upstream `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), a retained ancestor.
Published implementation [f0dc5c7b076d43cf53f53e2073171a8d6f7c1e5a](https://github.com/alehkot/llm-council/commit/f0dc5c7b076d43cf53f53e2073171a8d6f7c1e5a) has tree `422b32a49a5266283273cc9cda9c6bc34b1601d4`, identical to tested local implementation `f1bb6d869f78b48420a2d2e2923cde24d5c85848`.
This documentation-only descendant records the outcome; its final exact local/published SHA and CI observation belong to the publication receipt and [recovery issue 17](https://github.com/alehkot/issues-tracker/issues/17), avoiding a self-referential hash. Real-history local commits, bundle and patch are preserved without resetting history.

## Scope and rationale

Future durable council execution needs to retain the configured roster and stage choices rather than silently use different choices after a restart. The new explicit `council_selection.bind` snapshots the ordered council models and configured negative/deliberation/pairwise switches, with the exact saved response-policy identity and revision. Rebinding compares current loaded settings and conflicts on drift; historical inspection remains independent of today's configuration and never writes.

The returned roster is an immutable tuple with strict booleans. Incomplete/stale/cancelled/deleted work, invalid roster, unsupported transport and inconsistent saved evidence fail closed. Concurrent duplicates share one write; a lost write acknowledgment can be recovered by reloading/rebinding. A negative prompt does not implicitly turn on a disabled stage, matching existing manager behavior.

This is configured selection, not full effective request provenance or an executor. Version 1 supports OpenRouter selection only. Endpoint/token/context/retry policies, synthesis/helper settings and skills remain separate dependencies. The legacy pipeline continues to use its own imports and context. Future execution must consume saved values and recheck cancellation/revision before guarded external work. API0, public run routes and all consultation command gates are unchanged. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline 553 and final 568 offline Python 3.10.22 tests passed with unmocked outbound networking forbidden. Fifteen new tests cover all eight stage-switch combinations, ordered roster, source identity, frozen detached return values, 24 duplicate binds with one write, every captured setting's drift, byte-pure reads, malformed/version/source evidence, fresh-process restoration, before/after-write exceptions, cancellation and deletion.
- Independent whole-change review found no introduced moderate-or-higher findings. It reran the 15 new tests and passed 64 bind/cancel races, explicit-skip preservation and six additional malformed-state probes. Self-review agreed. Clean review does not guarantee correctness.
- All 114 existing Node tests, frontend formatting/ESLint, repository-wide Ruff format/lint for 99 files and whitespace checks passed. An initial `npm test` invocation was unavailable because this repository has no such script; the documented Node test files were then run directly, without code changes.
- Actual loopback Uvicorn startup, authenticated API0 configuration and graceful shutdown passed with empty temporary storage and synthetic credentials.
- Rust/UI source is unchanged; Rust tests were not rerun and rendered-browser checks are inapplicable. Full local builds, macOS Keychain/platform and live-provider checks remain unrun. These tests do not establish full consultation orchestration, model quality, power-loss durability or concurrent backend processes.

## CI and next steps

Exact starting development, main and published implementation have zero status contexts, check runs and commit comments. No normal Woodpecker result or job/platform coverage is observable. Empty aggregate `pending` is missing evidence, not an observed running build, pass or failure. Full builds remain unrun under the no-local-full-build policy. No manual CI dispatch/rerun, private-runner access, security/settings change, deployment, PR action or paid call occurred.

Next: observe normal exact-head Woodpecker evidence. Complete request/skill/synthesis provenance and guarded stage execution using the retained input/selection; add remaining human-input boundaries, durable scheduling and whole-run public acceptance before enabling consultation commands. Keep historical read validation aligned with deliberately extended future continuations.

Research: [Pydantic model immutability](https://docs.pydantic.dev/latest/concepts/models/#faux-immutability) distinguishes frozen models from mutable nested fields; the roster uses a tuple. Concrete semantics and regression evidence come from the repository's manager and tests.

---

# CLI checkpoint — exact response-policy input (2026-11-04)

## Baseline and published work

Persistent remote `development` started at [8f7c40b3f494b663db79651e5b88722dd5dafa2d](https://github.com/alehkot/llm-council/commit/8f7c40b3f494b663db79651e5b88722dd5dafa2d).
Upstream `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), a retained ancestor.
Implementation [8b9e53436987ad2c3f77fcea52070e124ef5e553](https://github.com/alehkot/llm-council/commit/8b9e53436987ad2c3f77fcea52070e124ef5e553) has verified tree `28ec11b1750dd947743676075fe0c4047fc4caac`, identical to tested genuine local implementation `89ccdadec09ba5fbb809e9ac66cb6194c0646557`.
This documentation-only descendant records the outcome. Its final exact local/published SHA and CI observation are recorded in the publication receipt and [recovery issue17](https://github.com/alehkot/issues-tracker/issues/17), avoiding a self-referential hash. Real-history local branches, bundle and patch are retained; no existing history was reset.

## Completed scope and rationale

Completed pre-query input previously retained choices but not the concrete response modifier a future council request would consume. An explicit internal binding now saves the exact finalized question and modifier with its source prompt instance/revision and effective domain/style/priority. This closes one prompt-input prerequisite without pretending to resolve all model, transport or skill settings.

The policy uses existing legacy execution semantics. Omitted optional fields may have execution defaults, but the raw accepted answer remains unchanged; explicit skip has no modifier or effective defaults. Frozen detached values and read-only inspection preserve exact evidence. Binding shares the existing storage lock with cancellation/deletion, validates the completed causal source, and recovers an uncertain write acknowledgment idempotently. Unsupported versions, inconsistent sources and changed semantic mappings fail closed rather than silently rewriting an accepted run.

No provider request, public route, API1 capability, scheduler or Rust consultation command is enabled. The legacy web workflow is unchanged. The current validator supports the final pre-query boundary only; future prompt histories need deliberate source-validation extension. The policy is not a dispatch/cancellation token, complete Stage1 input, cryptographic integrity guarantee, or full pipeline provenance. See [CONTRACT.md](CONTRACT.md).

## Verification and review

- Baseline537 and final553 offline Python3.10.22 tests passed with unmocked outbound network forbidden. Sixteen new tests cover160 combinations of choices/omissions, skip, exact refined queries, concurrent binds, detached/frozen values, read purity, before/after-write failures, fresh-process restoration, stale/incomplete/corrupt state, cancellation/deletion and explicit version requirements.
- Self-review caught an implicit missing-version fallback before publication; making the version mandatory and adding a regression fixed it. Independent whole-change code/test/contract/plan review found no remaining introduced moderate-or-higher findings. It independently reran16 tests, compared160 combinations plus skip against the actual graph modifier, and passed64 bind/cancel races. A clean review is not a correctness guarantee.
- Repository-wide Ruff0.16.4 format/lint passed for97 files; whitespace checks passed. All114 existing frontend Node tests, frontend formatting and ESLint passed. Actual Uvicorn loopback startup, authenticated API0 config and graceful application shutdown passed using empty temporary storage and synthetic credentials.
- The first baseline harness lacked the already-installed ACP package on its import path and produced19 import errors; adding the existing package path yielded the unchanged537-test baseline. A startup harness initially expected exit0 only; Uvicorn re-raised the requested SIGTERM after logging complete graceful shutdown. The corrected harness accepted that expected signal and passed a fresh run. Neither required repository changes.
- Rust/UI source unchanged: Rust tests were not rerun and rendered-browser verification is inapplicable. No local full builds, macOS Keychain/platform or live-provider checks ran. Tests do not establish power-loss durability, multiple concurrent backend processes, full consultation orchestration or model-answer quality.

## CI limitation and next priorities

Exact starting development, upstream main and published implementation SHAs each expose zero statuses, check runs and commit comments, including no normal Woodpecker report. Empty aggregate `pending` is missing evidence, not a confirmed running build or a pass/failure. Full builds remain unrun under the no-local-full-build policy. No manual CI dispatch/rerun, private-runner access, security/settings changes, PR actions, deployment or paid calls occurred.

Next: observe normal exact-head Woodpecker evidence and actual job coverage. Bind remaining effective model/feature/skill provenance and implement guarded council execution using the saved response-policy input. Continue review/synthesis human-input boundaries and durable scheduling; complete whole-run public acceptance before enabling consultation commands or advertising API1.

---

# CLI checkpoint — durable pre-query configuration (2026-11-04)

## Baseline and published work

Persistent remote `development` started at [c9504c59dfeebb06dfb60ad07e108a1270c415e4](https://github.com/alehkot/llm-council/commit/c9504c59dfeebb06dfb60ad07e108a1270c415e4).
Upstream `main` remains [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), a retained ancestor.
Implementation [4dc222e48185b87b44f69cc76e6f98a91e0f256a](https://github.com/alehkot/llm-council/commit/4dc222e48185b87b44f69cc76e6f98a91e0f256a) has verified tree `4c6121d3a08e5703a22de4daca827b11ffaaa1be`, identical to genuine local implementation `8a9d3e99cdf1ae3d8d05b8a1eeff6dc63160b312`.
This documentation-only descendant records the result. Its final exact SHA and CI observation are recorded in the publication receipt and [recovery issue 17](https://github.com/alehkot/issues-tracker/issues/17), avoiding a self-referential hash. Real-history local branches, bundle and binary patch are retained; no existing local history was reset.

## Scope and rationale

The next legacy workflow boundary after optional refinement is the pre-query response-style prompt. Durable runs previously stopped at completed refinement. An explicit internal coordinator now saves that exact prompt and finalized question, then separately consumes its first accepted answer into `pre_query_complete`. Direct unrefined admission is supported as well. This advances the persistent-human-input prerequisite rather than enabling incomplete council execution.

The versioned continuation preserves raw accepted values, optional omissions and explicit skip. No display default becomes an answer and no current pipeline setting is inferred. The inspector retains refinement and pre-query evidence separately, validates their causal order and final source question, and rejects unsupported or inconsistent checkpoints. Revision guards serialize concurrent preparation/consumption and recover lost acknowledgments by reloading saved evidence. Waiting/completed reads are byte-pure. Refinement driver/manager re-entry is rejected, while historical completed-operation reads remain safe.

No provider request, public route, API 1 capability, worker auto-loop or Rust consultation command is enabled. The legacy web graph is unchanged. `pre_query_complete` is not whole-council completion or resolved execution provenance. [CONTRACT.md](CONTRACT.md) records exact interfaces and limits; [PLAN.md](PLAN.md) retains the unfinished milestones.

## Verification and review

- Baseline 521 and final 537 offline Python 3.10.22 tests passed with unmocked outbound connections forbidden. An initial custom runner supplied the wrong synthetic API key and caused six authorization-related test failures; correcting the runner and rerunning the untouched baseline resolved them without repository changes.
- Sixteen new regressions cover direct/refined/original paths, separate revisions, concurrent duplicates, before/after-write exceptions, fresh-process prompt restoration, omission/skip, saved display defaults, malformed schemas/continuations/answers, disconnected history, cancellation/deletion, read purity and manager/driver re-entry rejection. Synthetic refinement callbacks make no live requests.
- Independent whole-change review passed all 16 new and 135 existing related tests, plus three-round refinement, skipped-refinement query preservation and eight additional malformed-state/schema probes. No introduced moderate-or-higher findings; self-review agreed. Clean review does not guarantee correctness.
- Repository-wide Ruff formatting/lint passed for 95 files; whitespace checks passed. 114 existing frontend Node tests, frontend formatting and ESLint passed. Actual loopback Uvicorn startup, authenticated API 0 config and graceful shutdown passed with empty temporary storage and synthetic credentials.
- Rust and UI source are unchanged; Rust tests were not rerun and rendered-browser checks are inapplicable. Tests do not establish power-loss durability, multi-process concurrent writers, effective pipeline modifiers, full consultation orchestration or external provider behavior.

## CI limitations and next priorities

Exact starting development, upstream main and published implementation SHAs had zero GitHub statuses, check runs and commit comments, including no normal Woodpecker report. Empty aggregate `pending` is missing evidence, not a confirmed queued/running build or pass. Full builds remain unrun under the no-local-full-build policy; native macOS Keychain/platform and live-provider verification remain unrun. No manual CI dispatch/rerun, private-runner access, security/settings changes, PR action, deployment or paid calls occurred.

Next: observe normal exact-head Woodpecker evidence and actual job coverage. Continue resolving immutable model/feature/skill provenance and consuming accepted pre-query values without conflating omission and skip; implement guarded council stages and the remaining review/synthesis human-input boundaries. Complete durable scheduling and whole-run public acceptance before exposing public run routes or enabling CLI consultation commands. Do not replay refinement or advertise API 1 prematurely.

---

# CLI checkpoint — internal run history (2026-11-04)

## Baseline and published work

Persistent remote `development` started at [998c02fcf9dc2e7176b0fe81a34b576cb6daf969](https://github.com/alehkot/llm-council/commit/998c02fcf9dc2e7176b0fe81a34b576cb6daf969).
Upstream `main` remained [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), an ancestor. All accumulated development work is retained.
Implementation [fba3936ce5adf107a5b62cc4caa0098ced01a009](https://github.com/alehkot/llm-council/commit/fba3936ce5adf107a5b62cc4caa0098ced01a009) has verified tree `d72a5131d39504d38dbb52c3b86252820336629a`, identical to genuine local implementation `0d74ec82abc3a40595e4b6ff512f4b3c00fb8bb6`.
This documentation-only descendant records the result. Its own final SHA and CI observation are recorded in the publication receipt and [recovery issue 17](https://github.com/alehkot/issues-tracker/issues/17), avoiding a self-referential hash. Local real-history commits, patch and bundle are retained for recovery.

## Scope and rationale

The existing read-only inspector required callers to already know a run UUID. Internal `run_history.list_runs` now discovers durable CLI work through bounded summary pages, advancing the plan's history prerequisite without exposing an incomplete public contract. UTC creation time and canonical UUID establish deterministic exclusive cursor positions. Cursors remain usable after anchor deletion, and newer admissions appear when refreshing the first page.

Summaries use the existing validated inspector and preserve real internal phases, including uncertain work after restart. Reads never resume, repair, settle or dispatch work. Tombstones and legacy conversations are omitted; corrupt receipts fail explicitly rather than silently presenting an incomplete history. The output and excerpt sizes are bounded, but the file store still scans all receipt headers; this is not a scalable secondary index or a cross-process transaction. API0 and public consultation command gates remain unchanged. Refinement completion is not whole-council completion.

## Verification and review

- 509 offline Python 3.10.22 tests passed with unmocked networking forbidden, including 13 new history regressions; baseline496 passed.
- New tests cover103-record pagination, normalized timezone ordering and UUID ties, deleted cursor anchors/new admissions, Unicode excerpt bounds, read byte purity, strict cursor/limit handling, real prompt/answer/refinement/cancellation phases, corruption and missing conversations, plus uncertainty surviving transient journal deletion and a fresh Python process.
- Independent whole-change review found no moderate-or-higher introduced findings and independently reran all13 new tests. Additional adversarial checks covered125 mixed-zone/tied records and32 concurrent admission/history calls without missing or duplicated retained records. Self-review also found no moderate-or-higher introduced findings. Review is not a guarantee of correctness.
- Repository-wide Ruff formatting and lint passed for91 Python files; whitespace checks passed.114 frontend Node tests, frontend formatting and ESLint passed; frontend is unchanged.
- Actual loopback Uvicorn startup, authenticated config API0 response and graceful application shutdown passed with synthetic credentials and empty temporary storage.
- Rust source is unchanged; Rust tests were not rerun for this Python-only scope. No browser UI changed, so rendered verification is inapplicable.

## CI limitations and next steps

GitHub statuses, checks and commit comments were read for exact starting development, upstream main and published implementation SHAs. Each had zero entries; no applicable Woodpecker report was observable. The aggregate status field's `pending` with zero contexts is missing evidence, not an observed running job. Normal full-build verification remains unrun/unverified, not passed; no local full build or manual CI dispatch was performed. Native macOS Keychain and live-provider verification remain unrun.

Next: observe normal exact-head Woodpecker evidence; continue full-pipeline execution/provenance, durable scheduling and public whole-run acceptance before enabling consultation commands. Integrate this internal discovery only when the public history/completed-result contract is satisfied. Preserve explicit unsupported transport and process-local ownership limits; do not advertise API1 execution yet.

---

# CLI checkpoint — read-only skill discovery (2026-11-04)

## Baseline and published work

Persistent remote `development` baseline was [7f59e6b5bab37f777f016a443b0095cba4cd71a1](https://github.com/alehkot/llm-council/commit/7f59e6b5bab37f777f016a443b0095cba4cd71a1).
Upstream `main` was [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d), an ancestor; all accumulated work is retained.
Implementation [c2c42fc9432f00931a76e9d72e22dcd7e8f370a7](https://github.com/alehkot/llm-council/commit/c2c42fc9432f00931a76e9d72e22dcd7e8f370a7) has verified tree `b7f1681023d0966bc796b89d3622008809021bf4`, identical to local implementation `d717925502508caa51b6f8f194b3af25f5986c6d`.
This documentation-only descendant records the result. Its final SHA and CI observation are recorded in the publication receipt and [recovery issue 17](https://github.com/alehkot/issues-tracker/issues/17), avoiding a self-referential hash.

## Scope and rationale

`council skills` now performs real read-only skill discovery using the existing authenticated `/api/skills` backend endpoint. This delivers a bounded public command while whole-consultation scheduling remains unfinished. It reads the saved origin-bound credential without prompting or saving, probes config once, and requests the fixed skill-list endpoint once. API 0 and API 1 support these reads; execution remains gated and backend API stays0. There are no retries, redirects, proxies, mutations or provider calls.

Typed summaries retain IDs, names, descriptions, source, numeric version and enabled state. Terminal output escapes untrusted text; JSON preserves it. Missing credentials, unavailable endpoints, unsupported platforms and malformed responses fail explicitly. A fixture generated from `Skill.to_summary` and a Python equality regression keep the Rust wire shape aligned with the backend. Initial draft string-version mismatch was caught and corrected before final verification. Plan and README document the partial delivery, not completion of the consultation CLI.

## Verification and review

- 496 offline Python 3.10.22 tests passed with unmocked outbound networking forbidden, including the new backend summary fixture equality test. Baseline 495 passed. An existing temporary-journal deletion warning did not fail the suite.
- 62 selected Rust tests passed on final source and independently: 32 library, 16 foundation and 14 HTTP tests. A nested proxy subprocess is not counted twice. Tests cover auth/origin constraints, fixed paths, API compatibility, malformed/oversized lists, disabled/empty/unavailable lists, terminal safety and unsupported-platform failure.
- Focused Clippy and Cargo formatting passed. Repository-wide Ruff lint/format passed for 89 files. 114 frontend Node tests and frontend formatting/lint passed; frontend code is unchanged.
- Real loopback backend startup, authenticated config API 0 and graceful shutdown passed using synthetic keys and an empty temporary data directory.
- Independent whole-change review and self-review found no moderate-or-higher introduced findings; whitespace checks passed. Review is not a correctness guarantee.
- The preceding manager-handoff change `2b8f219a83a60210232037e9b705f226948908ea..7f59e6b5bab37f777f016a443b0095cba4cd71a1` also received its previously missing independent review: 47 focused tests passed, 64 simultaneous cross-thread admissions shared one worker/provider attempt, and a closed waiter loop did not strand ownership. No moderate-or-higher introduced findings were reported.

## Limits, CI and next priorities

Exact starting/main/implementation heads had zero statuses, zero check runs and zero commit comments when inspected. Expected normal Woodpecker evidence is **missing**, not a confirmed running build or a pass. No manual CI dispatch/retry, full local build, deployment or paid provider request was performed. Full application/release builds remain unrun without applicable normal CI evidence. Native macOS Keychain/runtime, minimum supported Rust version and live-provider/model-quality verification remain unrun. Rust tests use synthetic loopback transport and injected credential stores; they do not establish native macOS behavior. Browser verification is inapplicable to this CLI-only change.

Next: observe exact final normal Woodpecker reports; validate the command on native macOS with an existing local backend; then resume durable complete-consultation scheduling/provenance and public whole-run acceptance before enabling run commands. Do not advertise API 1 execution or completed consultation support until those prerequisites and end-to-end tests are satisfied. Preserve process-local worker ownership limitations and fail-closed unsupported transports.

---

# CLI checkpoint — configured refinement bridge (2026-11-03)

## Baseline, implementation and purpose

Persistent working branch `development` started at
[cd892e7c01125128a52b3abd2fe649168b3d8824](https://github.com/alehkot/llm-council/commit/cd892e7c01125128a52b3abd2fe649168b3d8824).
Upstream `main` remains
[fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d)
and is an ancestor: all its changes are retained.
Published implementation:
[4a5e9ada908b2a12c8dde55ae72f254c7d05bcb0](https://github.com/alehkot/llm-council/commit/4a5e9ada908b2a12c8dde55ae72f254c7d05bcb0),
verified tree `3d36e8b2f2052048b849e3c5efc2985ae37d56b3`, exactly matching genuine
local implementation `d7851a2d368a0a41084d3e8b9ebb34cce2d31213`.
This documentation-only descendant records the completed scope; its own exact
published SHA and final CI observation are in the publication receipt/recovery
record, avoiding a self-referential hash.

The explicit refinement adapter previously required a caller to manually choose
its settings, leaving a missing configured-transport boundary after ACP landed.
The new internal bridge reads the loaded transport/model/endpoint/token ceiling
once, rejects unsupported ACP/unknown transport before writes or HTTP, and passes
one frozen configuration through binding and the existing durable attempt guard.
Saved settings drift conflicts on retry or duplicate result reads. Caller-supplied
credentials remain ephemeral and can rotate without changing request identity.

This is a refinement-only prerequisite, not a completed plan milestone. Its fixed
v1 single-request policy differs from legacy catalog/fitting/retry behavior.
Legacy paths remain unchanged; no public route/worker invokes it and API stays 0.
Full settings/skills provenance, ACP durable execution, worker waiting/release and
public whole-consultation acceptance remain unfinished.

## Verification and review

- Final source: 445 offline Python 3.10.22 tests passed with unmocked outbound
  networking forbidden. Baseline in the correct existing environment passed 436.
  An initial attempt used an older environment missing ACP and produced 16 import
  errors; using the existing synchronized environment resolved that tooling mismatch.
- Nine new synthetic tests cover exact saved/request settings, unsupported or
  invalid configuration, settings drift, explicit same-settings retry, global
  changes after capture, stale/non-refinement/cancelled/deleted work, and credential
  exclusion. They make no paid or live provider requests.
- Independent whole-change review found no introduced moderate-or-higher issues;
  all 89 refinement-related tests (including the nine new cases) passed independently.
  Self-review agreed; clean review is not a correctness guarantee.
- 92 Node tests, frontend format/lint, repository-wide Ruff format/check and
  whitespace checks passed. Backend startup, authenticated loopback `/api/config`
  with API0, and graceful shutdown passed using empty data and synthetic unused keys.
- Full local builds, live providers, macOS/Keychain, complete CLI consultation and
  rendered-browser checks are unrun. No frontend/Rust behavior changed. Unit tests
  do not establish remote provider behavior, power-loss or multi-process durability.

## Publication, CI and next steps

Non-force publication rechecked exact development/main heads and preserved both.
Exact implementation remote commit/tree verified. At 14:59 UTC the implementation
SHA and starting heads have zero GitHub statuses, check runs and Woodpecker commit
comments. Evidence is missing, not a running build, failure or pass. No full-build
or platform coverage can be claimed. No manual dispatch/rerun, private runner,
security/settings changes or paid call occurred. Existing [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17)
retains the normal-CI blocker without creating a duplicate.

Next bind remaining effective pipeline/skills settings and guarded stage execution,
including ACP support, durable waiting/release and explicit uncertain-work retry.
Complete public idempotent API acceptance before enabling live CLI ask/resume.
Observe normal exact-head CI and investigate relevant failures before new work.
The repository's existing fixed-policy contract and new synthetic regressions are
the evidence for this bounded implementation; no external behavior is assumed.

---

# CLI checkpoint — verified explicit skill content capture (2026-11-03)

## Baseline and scope

Starting persistent `development` and freshly fast-forwarded `main`:
[fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
Local implementation commit: `a59381ddfce6100613e6e996b617ab7497996606`, tree
`58005b59e932bec3adfa2689eee934d2a309ddc6`. It is local-only at this checkpoint;
publication and exact final documentation-head SHA/CI belong in the publication
receipt. Main's changes remain preserved; further implementation is on development.

Publication verified: [16a1a6e4399588b6326b4a0b387f21f008abafd8](https://github.com/alehkot/llm-council/commit/16a1a6e4399588b6326b4a0b387f21f008abafd8) on `development`, with tree `1d63a035a27bf37e99faf61912c4bfd03d62bb4d` exactly matching reviewed local checkpoint `a8f4c69f6187488348b39d8e92a23af7d021436e`. Main remains at the baseline above and is an ancestor. At 11:53 UTC its statuses, check runs and Woodpecker comments were all absent: CI and full build remain unverified, not pending or passed. This documentation-only publication receipt follows that implementation; its exact SHA is available in Git history.

The previous guard detected changes but left consumers to reread mutable library
files. The new internal `capture_explicit_skills` returns frozen canonical bytes
whose digest was checked against the already-bound identity. Body, reference
text and routing metadata are captured together; future consumers must use this
returned snapshot instead of rereading library paths. Supported concurrent
library mutation is serialized. Independent filesystem edits are not locked,
but any mixed or changed snapshot must match the saved digest before return.

This is a bounded dependency for durable execution, not worker integration:
no public endpoint, provider call, Rust CLI change or API-version change. Saved
identity format and hashes are unchanged. Capture is memory-only, not a durable
content archive or cancellation/dispatch permission. Future executors must still
check run revision/cancellation and bind full effective settings/transport.

## Verification and review

- 26 explicit-skill tests passed, including eight new regression tests for exact
  hashed content, detached copies, cancellation/deletion, missing/corrupt identity,
  external body/reference edits, and supported concurrent library deletion.
- Full offline backend rerun: 436 tests passed with outbound networking blocked.
  The initial full run had one transient failure in the unchanged ACP descendant
  cancellation assertion (`cancel=True`). That focused test and the complete
  rerun passed without modifying ACP code. Retain this intermittent test signal.
- 92 existing Node tests, frontend Biome format and ESLint passed. Repository-wide
  Ruff lint/format and whitespace checks passed. No frontend implementation changed.
- Actual backend startup, authenticated loopback HTTP and graceful shutdown passed
  using an empty temporary data directory and unused synthetic credentials. An
  initial subprocess probe observed complete shutdown but incorrectly expected
  exit 0 rather than Uvicorn's SIGTERM return; an in-process server lifecycle
  probe then passed with controlled shutdown and exit 0.
- Independent review ran the 26 skill tests and found no moderate-or-higher
  introduced finding; whole-change self-review agreed. Clean review is not a
  guarantee of correctness.
- No local full build, live provider, paid call, browser rendering, macOS Keychain
  or Rust platform verification ran. Those results are not established by unit tests.

## CI and next steps

At the exact starting head, GitHub statuses, checks and Woodpecker commit comments
were absent. This is missing evidence, not a running build or a pass. Local-only
commits have no corresponding remote build evidence; full build remains unrun.
Normal publication must recheck both remote heads, preserve concurrent changes,
verify the published commit/tree, and inspect its exact-SHA normal CI reports.

Next bind effective settings/transport and implement guarded execution consumption
of these captured bytes, durable human waiting/release, and explicit uncertain-work
retry. Complete public idempotent API acceptance cases before connecting live CLI
ask/resume. Do not expose API version 1 prematurely.

Reference: Python [immutable bytes](https://docs.python.org/3/library/stdtypes.html#bytes)
and [frozen dataclasses](https://docs.python.org/3/library/dataclasses.html#frozen-instances).

---

# CLI checkpoint — explicit uploaded-skill identity (2026-11-03)

Starting `development` and `main`: `ed383ae07c87b9bad2a4f40a69eb35e1c62e394f`. Local equivalent implementation commit `dcdf9164f5ff6523c7c806be1e125916289f3dd8`; published implementation [604d1d97643c31318093a69d2ada866598341953](https://github.com/alehkot/llm-council/commit/604d1d97643c31318093a69d2ada866598341953) has the identical reviewed tree and adds an opt-in guard for explicitly selected uploaded skill contents and routing metadata, and read-only inspection of validated identity. It rejects unsupported auto/local selection, drift, cancellation, deletion and retrospective binding. No public run execution, worker, provider dispatch or API upgrade is added.

The identity is not a guarantee of immutable execution: same-process library operations are locked, but external filesystem writers and time between checking and use remain unprotected. Complete settings, transport, skill selection, durable waiting and executor integration remain open. Digests are change detectors, not confidentiality. Preserve API version 0 and fail closed.

Verification: 428 offline Python tests, 92 Node tests, Ruff check/format, synthetic backend startup/shutdown and whitespace checks passed. Independent review identified legacy reference routing and inspection validation gaps; fixes and regressions were reviewed with no remaining moderate-or-higher findings. No full local build, paid provider, live service, macOS Keychain or browser verification ran. Check the publication receipt for exact resulting commit and normal CI; absent CI evidence is not a pass.

Next: design atomic capture/consumption and effective settings/transport provenance, then complete guarded worker release/resumption and public durable APIs before connecting `ask` or `resume`.

---

# CLI checkpoint — read-only durable run inspection (2026-11-03)

## Exact baseline and implementation

Persistent working branch: `development`, starting at
[0ca031f29fa5a799555f2b78617f563db0377f58](https://github.com/alehkot/llm-council/commit/0ca031f29fa5a799555f2b78617f563db0377f58).
Observed upstream/default `main`:
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
Main's separate ACP/configuration/UI changes remain preserved; this isolated
internal read projection did not require upstream integration.

Published implementation:
[c94c9bfcf196693579002bf995a122fcf6e80ce2](https://github.com/alehkot/llm-council/commit/c94c9bfcf196693579002bf995a122fcf6e80ce2),
verified reviewed tree `97aa695c73c8d178af4701e9a9bff8b56ba06488`.
Genuine local equivalent `5c30657d05ddeface1cb7049d3b9847aec18c8e7` has that
identical tree. This documentation descendant's exact final local/published SHA
and tree are recorded in [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem and completed scope

Existing mutation-oriented convenience inspectors reject cancelled runs and the
cancellation recovery inspector can settle an orphan marker. Neither is suitable
for read-only monitoring of retained partial evidence. The new internal typed
projection validates a single locked receipt and returns detached prompt/answer
history, refinement results and available non-secret provider provenance.

It distinguishes preparation from queued work, accepted-but-unconsumed answers,
refinement readiness/completion, locally owned work, uncertain work requiring
explicit retry, and cancellation. Cancellation suppresses actionable prompts and
retry tokens without discarding evidence. Reads never settle cancellation, consume
an answer, generate prompts, schedule workers, call providers or repair records.
Shared pure validators preserve all existing cancelled-run mutation gates.

A deterministic content cursor includes live ownership, so a restart can change
running to uncertain without a persisted write. This is a change-detection marker,
not an authorization token or a complete public long-poll protocol. Unsupported
or corrupt checkpoints fail closed. Unknown/deleted admissions do not fabricate
work. Refinement completion is not completion of a council consultation.

This advances an internal inspect/resume dependency only. API remains **0**;
public run/history/cancel routes and Rust `ask`/`resume`/`cancel` remain unavailable.
Full pipeline/settings/skills provenance, configured ACP transport selection,
remaining workflow boundaries and actual worker release/resumption remain pending.

## Verification and review

- **397 Python 3.10.22 tests passed** with the network-forbidden runner, including
  **16 new inspection regressions**; baseline was 381.
- New tests establish byte-preserving reads, detached values, deterministic cursors,
  live ownership changes and fresh-process reopening, exact waiting/accepted/
  consumed evidence, cancelled history and suppressed actions, unchanged mutation
  gates, provenance identity checks, corrupt/unsupported state rejection and
  initializing/deleted/missing conversation distinctions.
- 91 Node tests, repository-wide Ruff format/check, whitespace, and synthetic
  empty-data backend startup/graceful shutdown passed. No live provider was invoked.
- Independent whole-change source/tests/CONTRACT/PLAN review and an independent
  397-test rerun passed, with no introduced moderate-or-higher findings. Separate
  independent probes covered read purity, cancellation, corruption and ownership.
  Self-review also clarified the distinct cancellation recovery inspector in docs.
  A clean review is not a correctness guarantee.

No full local build, paid/live provider, native macOS/Keychain, complete CLI
consultation or new rendered-browser verification ran. Frontend/Rust sources are
unchanged. Offline tests do not establish remote-provider execution/cancellation,
exactly-once semantics, cross-process concurrent writers or power-loss durability.

## CI, preservation and next priorities

Exact starting development, main and published implementation heads had zero
statuses, check runs and Woodpecker commit comments. These are missing normal
build results, not a pass, a failed build or an observed running pipeline. Final
exact-SHA CI observations and publication verification are saved in the linked
recovery receipt. No CI rerun, full local build or private-runner access occurred.

Genuine-history bundles and full patches preserve the local equivalent and remote
commits. Publication uses a non-force expected-head update after checking both
remote heads and requires the exact reviewed tree. Existing missing-CI recovery
issue #17 is reused rather than duplicated.

Next reconcile configured transport/ACP, capture complete settings/skills/model
provenance, finish guarded pipeline boundaries and worker release/resumption,
then integrate public durable APIs and acceptance tests before Rust consultation
commands. Observe normal CI for exact published heads; prioritize actual failures.

Research: [Python JSON serialization](https://docs.python.org/3.10/library/json.html)
and [hashlib](https://docs.python.org/3.10/library/hashlib.html). Tests establish
the implementation's behavior; the cursor is not a provider execution receipt.

---

# CLI checkpoint — durable run-specific cancellation (2026-11-02)

## Baseline and implementation

Persistent working branch: `development`, starting at
[7d59d2841af3e276afd77dca87ba63389bc62b32](https://github.com/alehkot/llm-council/commit/7d59d2841af3e276afd77dca87ba63389bc62b32).
Observed upstream/default `main`:
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
Separate ACP/configuration/UI changes remain preserved. The internal cancellation
modules do not overlap those upstream changes; no integration was needed here.

Implementation:
[8f9e60e9c7cd2ca4efdf072bb66751b742738b02](https://github.com/alehkot/llm-council/commit/8f9e60e9c7cd2ca4efdf072bb66751b742738b02),
reviewed tree `d6a54547acd04792b63ed04847306b924e041e26`.
The genuine local sequence `71aa30809472a77253b640f462131716b677dc27` then
`806e41fda90d7f9db45e4b343b9ec4d7ca766e4f` preserves the implementation and
pre-dispatch review fix; the latter has the identical final implementation tree.
This documentation descendant's final exact local/published SHA and verification
receipt are recorded in [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem and completed scope

Existing internal durable refinement could retain waiting prompts and uncertain
provider work but had no run-specific cancellation marker. The new cancellation
coordinator persists a marker before signalling that run's dedicated provider task.
All existing prepared-run mutation gates then reject new prompts, answers,
continuations, suggestions and retries. Accepted answers, provider provenance and
saved partial results remain in the receipt; sibling runs are untouched. Repeated
admission/cancellation cannot start work again. Normal deletion retains only its
existing minimal tombstone.

`cancelling` remains while local ownership has not ended or recovery confirmation
is pending; `cancelled` is saved only once no registered local task remains. A
provider that suppresses cancellation cannot publish its late result. A fresh
process can settle an orphan marker without replaying work. Lost write
acknowledgements and failed completion writes preserve a recoverable cancellation.
Dedicated child ownership and queued-signal checks protect unrelated caller work.
Before the child enters the provider, it rechecks the exact attempt, input and
revision so intervening cancellation, deletion or prompt mutation cannot silently
dispatch stale work.

This closes one concrete **internal** cancellation dependency, not the whole PLAN
item. Public cancel/run routes, generic council-worker integration, API1 and Rust
`ask`/`resume`/`cancel` remain unavailable. No legacy web cancellation behavior was
changed. Retained evidence is readable via `run_admission.get`/`list_receipts`;
existing refinement/interaction convenience inspectors deliberately reject a
cancelled run pending full public snapshot integration.

## Verification and review

- **381 Python 3.10.22 tests passed** with the network-forbidden runner, including
  **18 new cancellation regressions**. Baseline was 363 tests.
- Tests cover run/sibling isolation, cross-thread signalling, suppressed
  cancellation, preserved pending/accepted answers and partial results, explicit
  retry rejection, pre-dispatch cancellation/input races, deletion, malformed
  markers, lost acknowledgements, final-write repair, unknown/unprepared targets,
  fresh-interpreter recovery and delayed signal delivery after guard exit.
- 91 existing Node tests, repository-wide Ruff format/check, whitespace and
  synthetic empty-data backend startup/graceful shutdown passed.
- Whole-change self-review and independent review fixed dedicated-task scheduling
  races before publication. Final review and focused rerun found no introduced
  moderate-or-higher findings. A clean review is not a correctness guarantee.

No full local build, paid/live provider, native macOS/Keychain, complete CLI
consultation or new rendered-browser verification ran. Frontend and Rust source
are unchanged. Offline tests do not establish remote-provider cancellation,
refunds, exactly-once execution, cross-process concurrent writers or power-loss
durability. Already admitted provider work may still incur a charge.

## CI, recovery and next priorities

Starting development and main heads had zero statuses, check runs and Woodpecker
commit comments. Missing normal build evidence is not a pass, failed build or
observed running pipeline. Final exact-SHA CI observations and publication/tree
verification belong in the linked recovery receipt. No manual dispatch/rerun,
private-runner access, settings/security change or full local build occurred.

Genuine-history bundles and full binary patches preserve local work. Publication
uses a non-force expected-head update after rechecking both remote heads; source
must match the reviewed tree. The existing open recovery issue is reused rather
than duplicating the missing-CI blocker.

Next reconcile configured transport selection (including upstream ACP), capture
complete pipeline/settings/skills provenance, finish the other guarded workflow
boundaries and worker release/resumption, then integrate public durable run APIs
and their acceptance tests. Keep API0 and fail closed until the complete contract
works. Observe normal CI for the exact published head and prioritize any actual
reported failure.

Research: [Python task cancellation](https://docs.python.org/3.10/library/asyncio-task.html#asyncio.Task.cancel).
Repository tests establish this implementation's coordination behavior.

---

# CLI checkpoint — single-request refinement provider (2026-11-02)

## Exact baseline and recoverable implementation

Persistent branch: `development`, starting at
[a902c10552fe34f419a2c21dff6f3109170a6a7f](https://github.com/alehkot/llm-council/commit/a902c10552fe34f419a2c21dff6f3109170a6a7f).
Observed upstream `main`:
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
Its separate ACP/provider-routing work was inspected and preserved. This change
is explicitly OpenRouter-only and does not select a configured transport or
silently route ACP work to OpenRouter; integrating those owner changes is still
required before a generic persistent executor can dispatch runs.

Published implementation:
[3da6d08712ea4ab84396610d76a6600582bfe13e](https://github.com/alehkot/llm-council/commit/3da6d08712ea4ab84396610d76a6600582bfe13e),
verified tree `74452204175d22735bd40baa120138de763b47b1`.
Local equivalent `d5b726881cabacca20646d728873f7b3363e6e5a` retains the identical
tested tree in genuine history and a bundle. No source change needs reapplication.
This documentation descendant's final exact SHA/tree and CI receipt are saved in
[recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem, scope and completed work

The legacy refinement helper can retry HTTP requests, perform context work and
silently fall back to the original question on missing/malformed provider output.
It cannot safely serve as the previous durable guard's single-attempt callback.
The new internal `suggest_openrouter` adapter persists its non-secret, immutable
request configuration and canonical identity before admitting an attempt. It
sends one POST with the specified endpoint/model/token/reasoning settings, without
HTTP retries, redirects, environment proxies, catalog lookups, summarization or
continuations. Credentials remain invocation-only and may rotate without changing
request identity. Total timeout and response-size limits bound resource use.

The adapter rejects incomplete/malformed responses instead of manufacturing a
successful original-prompt result. Shared request text and long-input omission
normalization were extracted unchanged; existing web behavior is preserved.
Existing durable revision/in-flight/explicit-retry rules apply, including saved
result reads, concurrent duplicates and cancellation. Rejected initial requests
leave provenance untouched, null/missing-field/corrupt saved configuration fails
closed, and later configuration drift conflicts. Old injected-callback work is
not retroactively assigned provenance. See [CONTRACT.md](CONTRACT.md).

## Verification and independent review

- **363 Python tests passed** on Python 3.10.22 with the network-forbidden runner,
  up from 340. The 23 new tests include HTTP mock failures/redirects, malformed and
  truncated successes, timeout/size limits, no silent fallback, write failures and
  lost acknowledgements, retry consent, concurrent calls, cancellation, config
  validation/drift/corruption, credential rotation and fresh-process saved-result
  reopening without HTTP. Nine legacy request fingerprints cover English,
  Russian/multilingual and long-input refinement across three rounds.
- 91 existing Node tests passed. Repository-wide Ruff format/check, whitespace,
  and synthetic empty-data backend startup/graceful shutdown passed. Ruff reused
  the tooling environment; backend runtime tests used Python 3.10.
- Full self-review and independent seven-file source/docs review fixed rejected
  requests binding provenance and null provenance being overwritten. The legacy
  omission safeguard and full canonical config validation are also covered.
  Independent final rerun passed 60 provider/execution/answer tests. No introduced
  moderate-or-higher finding remained; a clean review is not a correctness guarantee.

No full local build, paid/live provider, native macOS/Keychain, complete
consultation or new browser verification ran. Frontend and Rust source are
unchanged. Mock HTTP establishes request/coordination behavior, not model quality,
actual provider routing, exactly-once remote work, cross-process concurrent writers
or power-loss durability. Explicit retry of uncertain work may repeat billed work.

## Publication, CI and remaining dependencies

Both exact remote heads were rechecked immediately before the non-force expected-
head update. The published implementation commit/tree matches the tested tree.
At 18:00 UTC, implementation SHA statuses, check runs and Woodpecker commit comments
were empty, as were the starting development and main heads. This is **missing
build evidence**, not a confirmed running pipeline, failure or pass. The normal
workflow is configured for development/main pushes; no job/platform coverage can
be claimed without its report. No CI dispatch/rerun, private runner access or
CI/security change occurred.

API remains **0** and Rust consultation commands stay unavailable. This snapshot
covers one refinement request, not full effective model/settings/skills provenance.
Next reconcile configured transport selection (including upstream ACP), resolve
and persist the remaining run provenance and provider boundaries, integrate worker
release/resumption and run-specific cancellation, then implement public durable
run API acceptance cases before connecting `ask`/`resume`. Observe normal CI for
the final exact SHA and prioritize any actual reported failure.

Research: [HTTPX transport/retry defaults](https://www.python-httpx.org/advanced/transports/)
and [OpenRouter chat response contract](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request).
Repository tests, not those references alone, establish the local behavior above.

---

# CLI checkpoint — guarded refinement suggestions (2026-11-02)

## Exact history and scope

Persistent working branch: `development`, starting at
[175a0e20b5a0536f1aea37480a341d319813d132](https://github.com/alehkot/llm-council/commit/175a0e20b5a0536f1aea37480a341d319813d132).
Observed upstream `main` remained
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
Its separate ACP/provider transport changes were inspected and preserved; this
injected-callback boundary does not require importing them.

Published implementation:
[521983712160fcdcc4c0b2860261a3cf638d98ba](https://github.com/alehkot/llm-council/commit/521983712160fcdcc4c0b2860261a3cf638d98ba),
exact tree `78739b808a7865323118013483e473b19b1906fe`.
Local equivalent `f4b63c03ae95f3134f403dd734e4e78e348c1b22` has that same tree
and is retained in real history and a recovery bundle. No unpublished source
change needs reapplication. This documentation-only checkpoint's exact final
SHA and CI receipt are recorded in
[recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

`refinement_execution.suggest` connects one finite suggestion operation to durable
waiting. It requires a specific source revision and a stable provider identity,
writes an in-flight attempt before an injected callback, and atomically records
the validated result and exact waiting prompt. Existing first-answer coordination
then derives the next finite state. Delayed normal requests and duplicate retry
consent return historical results or conflict; neither silently starts a later
round. Live concurrent invocations do not duplicate the callback.

Lost/failed persistence, provider exceptions, cancellation and abruptly terminated
processes retain uncertainty. An orphan attempt requires explicit consent naming
its retry token and source revision. Saved input, schema, reducer history and
provider identity are checked; drift or incompatible state fails closed. A late
result cannot resurrect a deleted run. This prevents implicit local replay, not
exactly-once remote execution or repeated billing after an explicitly approved
retry. See [CONTRACT.md](CONTRACT.md) for the internal API boundary.

## Verification and review

- **340 network-forbidden Python tests passed** on Python 3.10.22, including
  23 new fake-callback tests. Baseline was 317. Cases cover concurrent callers,
  stale normal requests, repeated retry consent, failed/lost writes, malformed
  results, across-round configuration drift, corrupt typed state/schema/history,
  deletion during execution and exact saved prompt reuse.
- A child process exits abruptly inside its fake provider; a fresh process sees
  the persisted uncertainty without replay, then one explicit retry saves one
  prompt. Additional fresh-process cases verify read-only saved-result recovery.
- 91 existing Node tests passed. Repository Ruff formatting/check and Git
  whitespace checks passed. Ruff used the existing Python 3.13 tooling environment;
  runtime tests used Python 3.10. Backend startup/graceful shutdown passed with
  empty temporary data and synthetic unused credentials.
- Self-review and independent whole-change review fixed stale-revision dispatch,
  across-round provider drift and insufficient saved-continuation/history checks
  before publication. Independent review reran 56 adjacent tests, then all 23
  final new tests. No introduced moderate-or-higher finding remains; this is not
  a guarantee of bug-free code.

No full local build, live provider, native macOS/Keychain, complete consultation
or new browser verification ran. Frontend and Rust source are unchanged; their
previously recorded platform limitations remain. Fake callbacks establish local
coordination, not safety of an unaudited real provider adapter or model quality.

## Publication, CI and next priorities

Both remote heads were rechecked immediately before the non-force expected-head
update. The published implementation commit/tree was independently fetched and
matched the tested tree. At 15:37 UTC its statuses, check runs and Woodpecker commit
comments were empty: **missing build evidence**, not a running, passed or failed
build. The same was true of the starting development and upstream heads. No CI
was manually dispatched, rerun or reconfigured; no PR or deployment was created.

The backend still advertises API0; `ask`/`resume` remain unavailable. Next resolve
and persist effective models/settings/skills, audit a genuinely single-attempt
provider adapter (do not directly reuse mutable legacy refinement/retry behavior),
then integrate worker release/resumption, other workflow boundaries, run-specific
cancellation and the public API acceptance cases. Observe normal exact-head CI
without assuming an unreported build is running. The original full consultation
milestones remain open rather than marking this internal dependency complete.

References: [Python cancellation semantics](https://docs.python.org/3.10/library/asyncio-exceptions.html)
and [LangGraph persistence concepts](https://docs.langchain.com/oss/python/langgraph/persistence).
These motivate boundary/recovery checks; repository tests establish this change's
behavior, not the general references.

---

# CLI checkpoint — durable refinement answer coordination (2026-11-02)

Following the setup integration below, `development` continued from
[e20e153966e97a90fd9821e9b56f45df1a855305](https://github.com/alehkot/llm-council/commit/e20e153966e97a90fd9821e9b56f45df1a855305).
Upstream `main` remained
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
Published implementation:
[497e9b06413068ee0506472c2a062fbe5ec69cf2](https://github.com/alehkot/llm-council/commit/497e9b06413068ee0506472c2a062fbe5ec69cf2),
exact tree `86e8b704f76752129309419b642a97b7571c1070`.
Equivalent local commit `1352b6069a963c401f221f7c1a76b4c404cfe2fe` is retained
with the same tree in genuine history/bundles; no unpublished source difference
requires reapplication. The final documentation SHA/CI receipt is retained in
[recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17#issuecomment-6576179704).

The next concrete backend connection is implemented:
`durable_refinement.resume_answer` reads **only the first persisted answer**,
validates its admitted request and saved refinement semantics, derives the finite
next state, and atomically marks the answer consumed with that state. This wiring
previously existed only inline in a test. Duplicate/concurrent calls and retries
after a lost write acknowledgement do not apply the answer twice. Saved prompt
wording/defaults and raw answers remain intact; incompatible fields/questions,
corrupt saved results, deleted runs and unanswered/unknown prompts fail closed.
A retry of an older consumed instance leaves any later prompt untouched.

The returned state is a historical receipt, not dispatch permission. No provider
is imported/called by this coordinator, no worker is scheduled, and no public
route or API version is enabled. The backend remains API0 and CLI consultation
commands remain unavailable. Effective settings/skills provenance, in-flight
external-operation/retry safeguards, the other finite workflow boundaries and
worker release/resumption still precede safe public run/CLI integration.

Verification: **317 network-forbidden Python tests** passed, including 14 new
coordinator regressions for fresh-process restore, concurrency, pre-write failure,
lost acknowledgement, schema/type/action mismatch, admission mismatch, deletion,
corrupt continuation, skipped answers, raw-text retention and historical replay.
91 Node tests, repository Ruff/whitespace and backend startup/graceful shutdown
passed. The published source's 14 new tests were rerun successfully. Independent
review covered the whole change and reran new/adjacent tests; no introduced
moderate-or-higher finding remained. The unchanged Rust setup source retains the
72 Rust/12 Linux PTY verification recorded below; this backend step is not native
macOS or end-to-end consultation verification.

Immediately before publication both heads were unchanged. The exact remote
implementation SHA/tree matches the reviewed local source. At 11:47 UTC its
GitHub statuses/check runs/Woodpecker commit comments were empty: **missing build
evidence**, not a running/passed/failed build. Full local builds, native macOS,
live providers and complete consultations remain unrun. No new browser checks
were needed for this internal backend-only addition; frontend behavior was not
changed. No PR, force push/reset, deployment, manual CI dispatch, credentials,
private runner or paid-provider action was used.

Next: verify normal CI for the exact final head, then implement the durable
external-operation/provenance and worker boundaries before exposing run routes.
Do not call this coordinator's historical `needs_suggestion` result directly as
permission to repeat paid work; current run revision and in-flight/retry checks
are mandatory. See [CONTRACT.md](CONTRACT.md) for the precise remaining boundary.

---

# CLI implementation checkpoint — authenticated Keychain setup (2026-11-02)

## Baseline, published change and usable scope

- Persistent working branch: `development`, starting at
  [fde4cddc6719a1a5bfed0ff2e9e1ba84b333b4b1](https://github.com/alehkot/llm-council/commit/fde4cddc6719a1a5bfed0ff2e9e1ba84b333b4b1).
- Upstream `main` remained
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Existing accumulated and owner work is preserved; no integration was needed.
- Published implementation:
  [07d19bb584cd45a3207988f465fcd088ea7aa45a](https://github.com/alehkot/llm-council/commit/07d19bb584cd45a3207988f465fcd088ea7aa45a),
  tree `fbb198292dffcb894b2e5b13a5c96dca9e2b6b5a`.
- Equivalent local implementation `82312e6af97e2b1639c92f32e258a5515e466deb`
  is preserved in genuine history and a recovery bundle with exactly the same
  source tree. No unpublished source difference needs reapplication. The final
  documentation-only checkpoint SHA is recorded in the recovery receipt below.

`council setup` now has real dispatch: hidden controlling-terminal entry, one
origin-bound authenticated `GET /api/config`, then native macOS Keychain storage
only after valid authentication/configuration. It displays the configured council
and chairman. Failed input/authentication/network/configuration checks leave the
old credential untouched; Keychain replacement uses native add/update rather
than delete-first. Keys have no argument, environment, piped-stdin or plaintext
storage fallback. Noninteractive input and unsupported platforms fail before
prompting, network or storage. JSON output is a single structured result/error;
terminal controls in configuration names are escaped in human display only.

API0 is accepted for this read-only credential setup, not as execution support.
**All consultation commands still fail closed without I/O.** The backend remains
API0; no durable run routes, worker handoff, model requests or monitor/resume loop
are enabled. Setup also reports consultation commands unavailable for an API1
server because this binary's dispatch is not connected. This completes a useful
setup dependency, not the full Rust/HTTP/Keychain/monitor plan item.

## Verification and review

- 72 distinct Rust tests passed: configuration/transport regressions, native-store
  adapter mocks, setup ordering and prior-entry preservation, plus an actual
  synthetic loopback HTTP-to-mock-Keychain setup flow. A nested proxy-test process
  repeats one test and is not counted twice. Formatting and Clippy with warnings
  denied passed, including an independent rerun and published-source rerun.
- 12 actual Linux PTY cases passed, independently rerun: hidden entry, Enter/CRLF,
  backspace/clear-line, Ctrl-C/D, Ctrl-S/Q, malformed/oversized input, exact
  terminal-flag restoration, unread-input flushing, ignored piped stdin and
  refusal without a controlling terminal. Reproduction is committed under
  [`tests/manual`](tests/manual/README.md). No real key is used or stored.
- A candidate password-input dependency was rejected before publication because
  its Ctrl-C implementation can raise SIGINT before restoring terminal settings.
  The final implementation uses safe `nix` termios calls and a restoration guard,
  with bounded input and explicit cancellation. Abrupt external SIGTERM/SIGKILL
  can bypass restoration; normal/error returns and typed cancellation are tested.
- 303 network-forbidden Python tests and 91 Node tests passed. Repository Ruff,
  whitespace, and backend startup/graceful shutdown with empty data and synthetic
  keys passed. An initial backend test invocation used a synthetic auth key that
  differed from the tests' fixed fixture, producing 401s; correcting that harness
  configuration restored all 303 passes without product-code changes.
- Self-review plus independent whole-change review found no remaining introduced
  moderate-or-higher finding. This is not a guarantee of bug-free code.
- Dependencies remain locked. `nix` is Unix-only and `security-framework` is
  macOS-only. Declared dependency compiler minimums fit Rust1.89; actual testing
  used Linux Rust1.99.1, not the declared minimum compiler.

Actual macOS compilation, native Keychain/terminal behavior, live providers,
complete consultations, full application/release builds and new rendered browser
checks remain **unrun**. No frontend code changed. Linux mocks/PTY tests do not
establish macOS compatibility; no full local build was run under the current
policy, and no real credential was installed.

## CI, recovery and next priorities

Both remote heads were rechecked immediately before non-force expected-head
publication. The published implementation's exact SHA/tree matches the reviewed
local source. At 11:33 UTC its GitHub statuses, check runs and Woodpecker commit
comments were all empty. This is **missing build evidence**, not an observed
running build, failed build or pass. Observe the final documentation-only SHA
separately; final publication and CI receipts are retained with
[recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

The published commit above is the durable recoverable source. Complete-history
bundles and a binary patch additionally retain local and published ancestry.
No reset, force push, PR action, manual CI dispatch, runner/private-network
access, settings/security change, deployment or paid provider call was used.

1. Observe exact-head normal CI and prioritize any actual applicable failure.
2. Verify setup on macOS with an explicitly authorized test credential before
   claiming native platform acceptance; do not use the user's machine implicitly.
3. Continue the durable backend dependencies: finite workflow continuations,
   settings/skills provenance, saved in-flight operations and explicit retry,
   worker release/resumption and safe public run operations before wiring
   `ask`/`resume`. Never use legacy mutation routes as a shortcut.

Research: [native Keychain API source](https://docs.rs/crate/security-framework/3.7.0/source/src/passwords.rs),
[nix termios](https://docs.rs/nix/0.30.1/nix/sys/termios/index.html),
[examined password-input source](https://docs.rs/crate/rpassword/7.5.4/source/src/lib.rs).

---

# CLI implementation checkpoint — Config HTTP transport (2026-11-02)

## Baseline, scope and publication

- Verified persistent working branch: `development`, starting at
  [a242b1fb51c9bd528bb1cf5ea58b8325fc52f27f](https://github.com/alehkot/llm-council/commit/a242b1fb51c9bd528bb1cf5ea58b8325fc52f27f).
- Upstream `main` remained
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Separate ACP/model-routing changes were inspected and preserved; no merge was
  needed for this isolated transport module.
- Published implementation:
  [c3d2adab7a555acade42bda606246993f4339f5f](https://github.com/alehkot/llm-council/commit/c3d2adab7a555acade42bda606246993f4339f5f),
  tree `30e30dcf461f8605a0a31a13171633c0a1067edc`.
- Equivalent local implementation `f085394f23fe3a23a7179e580e3a3d47406d827b`
  is retained in genuine history/bundle with the exact same tree. Connector
  publication preserved the remote ancestry; no unpublished source difference
  needs reapplication. This checkpoint's containing commit is documentation-only;
  its exact final SHA is recorded in the publication receipt/recovery issue.

The next Rust dependency is now implemented: an origin-bound authenticated
configuration probe using one reusable reqwest client. It can issue only
`GET /api/config` on a validated loopback origin; callers cannot replace its
origin or submit arbitrary paths. Proxy discovery, redirects and automatic
transport retries are disabled. TLS certificate and hostname verification remain
on. Credentials stay in memory, the header is marked sensitive, and debug/error
messages never contain the key, server body or underlying parser/transport text.

The response is bounded at 1 MiB, including chunked bodies, with a 35-second total
request timeout and five-second connection timeout. HTTP 401/403/429 stop further
requests from that client. Truncated/invalid data, unsupported API versions and
invalid limits fail closed. Connection errors give the existing backend start
command. No config probe falls back to legacy mutation endpoints.

**This is a tested transport module, not operational command integration.**
Only executable help/version work. All other commands retain the prior no-I/O
not-implemented path. No real credentials, Keychain setup, live backend/provider
requests, consultation submission, persistent credential installation or backend
API upgrade were performed. Backend API remains **0**. The larger Cargo/HTTP/
Keychain/monitor PLAN item stays open.

## Verification and review

- 46 distinct Rust tests passed on Linux Rust 1.99.1: 33 prior tests plus one
  timeout unit test and 12 HTTP integration tests. One proxy-test child repeats
  a test in an isolated environment and is not counted twice.
- Real synthetic loopback servers cover exact request path/header, connection
  reuse, proxy environment isolation, redirect destination receiving no request,
  rejected authentication/lockout latching, no retry after a lost response,
  declared/chunked oversized bodies, malformed/truncated JSON/UTF-8, stalled
  headers/body, old/future API versions and invalid config limits.
- Formatting and Clippy with warnings denied passed. The lockfile adds 102
  registry packages without removing prior packages or changing existing sources
  or checksums. No resolved package with a declared MSRV exceeds 1.89; that
  minimum compiler and macOS have not been executed.
- Independent whole-change review of all eight files found no introduced
  moderate-or-higher finding. It reran the final Rust script and two additional
  synthetic transport probes. A self-signed TLS server was rejected before any
  authenticated HTTP request; a dropped response produced one request, no retry.
  The latter probe is retained as a regression. A clean review is not a guarantee.
- 303 network-forbidden backend tests passed under Python 3.10.22; 91 Node tests,
  repository-wide Ruff format/check, whitespace and backend startup/graceful
  shutdown with empty data and synthetic keys passed. No backend/frontend code
  or CI workflow was modified in this iteration.

Full local application/release builds, rendered browser checks, macOS/Keychain,
actual Rust 1.89, live providers and complete durable consultations remain unrun.
These checks do not establish model quality, end-to-end execution or platform
coverage that was not exercised.

## CI, recovery and next priorities

Immediately before non-force expected-head publication, both remote heads were
unchanged. Exact remote implementation commit/tree matched the reviewed local
source. At 09:14 UTC, starting/upstream/implementation heads had zero GitHub
statuses, check runs and Woodpecker commit comments. This is **missing evidence**,
not an observed running build, failed build or pass. No manual rerun, private
runner access, settings/security change or charge was used. Observe the exact
final checkpoint SHA separately; publication/recovery receipts retain that result.

The full recoverable implementation is linked above. Genuine-history bundles and
binary patch retain the corresponding local history. Existing
[recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17) tracks
missing normal CI without a duplicate issue or revival of closed work.

1. Verify normal Woodpecker evidence for the final SHA and prioritize any real
   failure. If expected reports remain absent, the owner may inspect integration
   health through their authorized access; absence alone identifies no cause.
2. Implement/test macOS-specific credential-store selection and hidden key entry,
   then connect safe setup/config dispatch. Do not save a key before authenticated
   compatibility succeeds or claim Keychain support from Linux fake-server tests.
3. Complete backend finite continuations, settings/skills provenance, in-flight
   operation records/explicit retry, worker release and public durable APIs.
4. Connect ask/resume only after the complete durable contract passes acceptance;
   private submission receipts and terminal/monitor behavior remain unfinished.

Research: [reqwest client configuration](https://docs.rs/reqwest/0.13.5/reqwest/struct.ClientBuilder.html),
[retry policy](https://docs.rs/reqwest/0.13.5/reqwest/retry/index.html).
Tests and inspected pinned source establish behavior, not documentation alone.

---

## Earlier Rust foundation checkpoint (historical)

# CLI implementation checkpoint — Rust foundation (2026-11-02)

## Exact baseline and completed scope

- Persistent working branch: `development`, starting at
  [54d9482d3c5042d86bf67eaabd4d0a923dc3f85b](https://github.com/alehkot/llm-council/commit/54d9482d3c5042d86bf67eaabd4d0a923dc3f85b).
- Observed upstream `main`:
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Separate ACP/model-routing changes and all accumulated development work remain
  preserved; no upstream merge was needed for the isolated Rust package.
- Published implementation:
  [3619bc813b2407517e3f46be830689ad43d6593c](https://github.com/alehkot/llm-council/commit/3619bc813b2407517e3f46be830689ad43d6593c),
  exact reviewed tree `64851b48cb8feb8a00c281e210f81d0f8cf871ac`.
- Equivalent local implementation `4e2f30d7744913a252286fffcb61a8531dad0211`
  is retained in its genuine-history repository/bundle and has the same tree.
  Connector publication created the published commit without rewriting that
  history. A fresh native clone verified the remote implementation/tree and
  passed the focused Rust checks again. No unpublished source difference needs
  reapplication. This checkpoint's containing commit is documentation-only; the
  publication receipt/recovery issue records its exact final SHA without a
  self-referential hash.

**Rust has started.** The earlier checkpoint's wording that all backend work must
finish before *beginning Rust* was overly strict. The PLAN gates connecting run
operations, while offline command/contract work can proceed in parallel.

One Cargo package now supplies the `council` parser for the planned commands,
strict Serde draft models using existing backend fixtures, explicit safe policies,
identity/lifecycle validation, local-origin validation, exact-version/configuration
checks, bounded UTF-8 input and display-only terminal escaping. `Cargo.lock` is
committed. Normal CI now invokes focused Rust checks without removing any existing
backend/frontend checks or changing triggers, secrets or runner settings.

Only `--help` and `--version` are functional commands. Every operational command
fails closed with exit 2 and an explicit not-implemented diagnostic. It makes no
HTTP request, reads no question file, opens no editor, writes no export or
credential, and starts no consultation. JSON errors stay on stdout; ordinary
errors stay on stderr. The authenticated config/version helper is tested against
synthetic data, not connected to a real backend. **API version remains 0.**

## Verification and independent review

- 33 focused Rust tests passed using Rust/Cargo 1.99.1 on Linux, including real
  executable help/error cases. Formatting and Clippy with warnings denied passed.
  Dependency metadata requires at least Rust 1.89; that minimum and macOS were
  not separately exercised. The official Rust toolchain was used.
- Typed fixture tests cover create/waiting/answer/legacy references, unknown
  fields, strict boolean/string/list distinctions, Unicode code-point limits,
  explicit policies, ownership and lifecycle contradictions, absent legacy run
  IDs and preservation of heterogeneous stage evidence.
- Input/output tests cover bounded readers, invalid UTF-8, Python-compatible
  whitespace, code points versus graphemes, terminal controls, JSON/stderr
  separation, invalid/credential-bearing URLs and non-interactive input rules.
- 303 backend tests passed under Python 3.10.22 with outbound networking forbidden;
  91 existing Node tests passed. Repository Ruff format/check (61 files), shell
  syntax, whitespace and backend startup/graceful shutdown passed. Ruff reused
  the existing Python 3.13 tooling environment; no backend/frontend code changed.
- Independent whole-change review reran 33 Rust tests and 29 additional real-binary
  probes, including all commands, credential redaction and preservation of an
  existing export file. One Clippy redundant-closure failure was fixed; the final
  review and Clippy rerun found no moderate-or-higher introduced finding. A clean
  review is not a guarantee of correctness.

Local full application/release builds were **unrun** by policy. HTTP transport,
macOS/Keychain, interactive terminals/editors, durable end-to-end consultation,
provider calls/model quality and rendered browser checks remain unrun. These
checks establish the offline foundation only, not a usable consultation client.

## Publication, CI and recovery

Both remote heads were rechecked before non-force expected-head publication.
The exact published implementation/tree matches reviewed local source. Starting,
upstream and implementation SHAs have zero GitHub statuses, check runs and
Woodpecker commit comments as observed 2026-11-02 05:57 UTC. Empty aggregate
`pending` means **missing evidence**, not a running pipeline, failure or pass.
The configured normal pipeline includes Rust now, but actual execution/job/platform
coverage cannot be inferred. No manual dispatch/rerun or private-runner access
occurred. Observe the final checkpoint SHA separately.

The complete genuine-history bundle and binary patch retain recoverable work.
Existing [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17)
records the latest published SHA, verification and missing CI evidence. Do not
reapply equivalent local-only commits. No new publication denial occurred.

## Next priorities

1. Continue narrow Rust transport/authentication/config probing with fake HTTP
   tests, disabled redirects/proxies and no credential logging; select/test the
   macOS Keychain binding before claiming setup works. Do not imply the URL type
   alone enforces network transport policy.
2. Complete backend finite continuations and settings/skills provenance,
   in-flight external operations and explicit uncertain-work retry, then safe
   dispatch, worker release and public durable run APIs. Never replay legacy
   refinement or treat a saved `needs_suggestion` phase as permission to bill.
3. Connect ask/resume only after that full backend contract passes acceptance;
   implement private submission receipts, generic prompts and shared monitor loop.
   Keep incomplete PLAN checkboxes open and API version 0 until support is real.
4. Verify normal Woodpecker evidence for the exact published head, prioritizing
   relevant failures. Missing reports alone do not establish an implementation
   failure. If reporting remains absent, the owner can inspect integration health
   through their own authorized access.

Research: [clap](https://docs.rs/clap/latest/clap/), [Serde](https://serde.rs/),
[URL parsing](https://docs.rs/url/latest/url/),
[official Rust installation](https://rust-lang.org/tools/install/).
The repository fixtures and executable tests establish compatibility, not these
library references alone.

---

## Earlier refinement checkpoint (historical; sequencing superseded above)

# CLI implementation checkpoint — finite refinement continuation (2026-11-02)

## Baseline, scope and implementation

- Persistent branch: `development`, starting
  [ac1bd65b48772f7ff24f18f33f5c709f373de533](https://github.com/alehkot/llm-council/commit/ac1bd65b48772f7ff24f18f33f5c709f373de533).
- Observed upstream `main` remained
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Separate owner ACP/model-routing work and accumulated development changes are
  preserved; no upstream integration was needed for this shared refinement step.
- Published implementation:
  [c7fd58965f1b5bc7212f2e7ec37d340228324210](https://github.com/alehkot/llm-council/commit/c7fd58965f1b5bc7212f2e7ec37d340228324210),
  tree `d4171f6fe78a4d76f2dd34994c1e8eb3b6b04856`.
- An equivalent local-only implementation commit
  `0627b1d990c4e8a207d73044482f5a8d994f2e84` has the identical tree and is retained
  in its original repository/bundle. Native HTTPS publication lacked credentials;
  authorized connector publication succeeded without installing credentials or
  rewriting local history. A fresh genuine-history checkout verifies the
  published branch. There is no unpublished source difference to apply again.
- This document's containing commit is a documentation-only descendant. The
  separate publication receipt records the exact final local/published SHA/tree
  without a circular self-reference.

The next dependency after prompt storage was a safe refinement continuation:
replaying the old refinement function regenerates a provider suggestion before
looking for its saved answer. `backend/refinement.py` now has a complete strict,
versioned finite state and pure answer reducer. It preserves the saved suggestion,
original/working query, round, clarifications and additional context. A restored
waiting checkpoint can apply its accepted first answer without any provider call.
The legacy graph shares the reducer instead of duplicating its decision logic.

**This is not durable execution integration.** No run route, worker release,
startup dispatch or retry authorization was added. Existing legacy queues/timeouts
remain unchanged, and their recovery path must not execute durable CLI runs.
`needs_suggestion` requires an in-flight/explicit-retry guard before execution.
API version remains **0**. See [CONTRACT.md](CONTRACT.md) for the exact boundary.

## Verification and review

- 303 backend tests passed on Python 3.10.22 with outbound networking forbidden,
  including 20 new tests. Coverage includes strict/missing/corrupt state, finite
  transitions, skip/original/refined actions, implicit repeat, three-round cap,
  raw receipt preservation, detached values, fresh-interpreter restoration, and
  persisted first-answer/advance replay without regeneration.
- Six new real-graph tests use fake provider boundaries to check prompt events,
  trace/persistence and the exact Stage 1 handoff. They deliberately stop at a
  fake empty Stage 1 result: they prove refinement integration, not complete
  council execution or model-answer quality.
- 91 existing Node tests, repository-wide Ruff format/check (60 files), whitespace
  checks and backend startup/graceful shutdown passed. Runtime tests use Python
  3.10; Ruff uses the existing Python 3.13 tooling environment.
- Independent whole-change review found no moderate-or-higher introduced findings.
  62 focused tests passed independently, plus 120 randomized baseline-versus-new
  real-graph differential cases comparing metadata, query, prompt events, saved
  updates, provider arguments and normalized traces. A clean review does not
  guarantee correctness.
- Full builds unrun locally by policy. No live/paid provider, Rust runtime,
  macOS/Keychain or new rendered browser checks ran; frontend code is unchanged.
  No claim of full durable workflow execution, exactly-once provider work,
  power-loss durability or concurrent-process writer safety is made.

## Publication, CI and recoverability

Both remote heads were rechecked before non-force expected-head publication.
Published implementation SHA/tree and a fresh remote clone match the reviewed
source. Starting working/upstream heads and implementation SHA have zero commit
statuses, check runs and commit comments. Empty aggregate `pending` is missing
build evidence, not an observed running pipeline, failure or pass. Woodpecker is
configured for development pushes, but no applicable job/platform outcome is
available. No manual CI dispatch/rerun, private-runner access or security change
was performed.

The final publication receipt, complete real-history bundle and binary patch
record the final branch revision and recovery validation. Existing recovery issue
[#17](https://github.com/alehkot/issues-tracker/issues/17) records the missing
exact-head CI evidence without creating another issue. Observe normal reports for
the exact published SHA and prioritize any relevant failure when it appears.

## Next safe step

Implement the remaining workflow continuation boundaries and snapshot effective
settings/skill provenance. Persist in-flight external operations and explicit
uncertain-work retry before safe admission-to-worker dispatch. Integrate durable
waiting/worker release and saved first-answer consumption; do not replay legacy
refinement to restore its question. Only then complete the public run API and
its full acceptance cases before beginning Rust. Preserve raw responses in the
receipt even when the reducer derives trimmed working text.

Research: [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/).
The repository's concrete pre-answer provider ordering and differential tests,
not a general reference, establish the need and compatibility of this step.

---

## Earlier durable prompt checkpoint (historical)

# CLI implementation checkpoint — durable prompt storage (2026-11-02)

## Latest baseline and completed partial step

- Persistent branch: `development`, starting
  [a14be327761d069dc472650d86a804141780ebc2](https://github.com/alehkot/llm-council/commit/a14be327761d069dc472650d86a804141780ebc2).
- Upstream `main` remained
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Accumulated admission/contracts/auth work is preserved; no upstream merge was
  needed for this isolated internal storage primitive.
- Implemented and published
  [9d997a5cc3578f41416cad36a1a0b6a004e520c3](https://github.com/alehkot/llm-council/commit/9d997a5cc3578f41416cad36a1a0b6a004e520c3),
  tree `9a3ac929cb437fbf090fc7856ba0ab8bb5308695`.
  This checkpoint's containing commit is documentation-only; the publication
  receipt records its exact final local/published SHA rather than a circular hash.

The plan requires persistent human input without silently re-running a provider
or changing accepted answers. The next dependency is now implemented internally:
exact prompt-instance/schema/opaque continuation persistence, revision-guarded
new instances, schema-validated first-answer receipts, idempotent answer and
advance retries after later prompts, and deletion erasure in the admission
receipt. Type-sensitive checkpoint comparison distinguishes JSON true, 1 and 1.0.
No public routes, worker scheduling, provider execution or legacy queue behavior
changed. **API version remains 0; persistent execution is still incomplete.**

## Verification and review

- 283 backend tests passed on Python 3.10.22 using the network-forbidden offline
  runner, including 22 new durable interaction tests with synthetic data.
- New coverage: all four field types, required/optional/default/skip semantics,
  exact text/order/false values, schema ambiguity, stale revisions and instances,
  12 competing first answers, fresh-process waiting-state reads, before/after
  atomic-write interruptions, old answer retries after newer prompts, deletion
  races/bulk/partial deletion, and damaged accepted state failing closed.
- 91 existing Node tests passed; no frontend code changed. Repository-wide Ruff
  format/check (49 files), whitespace checks and backend startup/graceful shutdown
  passed. Runtime tests used Python 3.10; Ruff reused the existing tooling env.
- Independent whole-change review identified Python equality conflating boolean
  and numeric continuation values. Canonical type-sensitive JSON comparison and
  regression tests fixed it. Final independent review found no remaining
  moderate-or-higher introduced findings; 64 focused tests passed independently.
  A clean review is not a guarantee of correctness.
- Full builds unrun locally per policy. No live/paid providers, Rust runtime,
  macOS/Keychain or new rendered browser checks. These tests do not establish
  end-to-end CLI orchestration, exactly-once provider work, power-loss durability
  or concurrent backend-process writers.

## Publication, CI and recovery

Implementation publication used a non-force expected-head update after checking
both current development and main. Exact remote implementation commit/tree were
verified. Starting heads and implementation SHA each have zero commit statuses,
check runs and commit comments; empty aggregate `pending` does not prove a running
pipeline. Normal Woodpecker is configured for development pushes, but no applicable
build result or job/platform coverage is available. No CI pass is claimed and no
manual dispatch/rerun, runner access or CI/security changes were made.

The final publication receipt and genuine-history bundle/patch preserve exact
final SHA/tree, recovery verification and subsequent normal CI observations.
Existing recovery issue [#17](https://github.com/alehkot/issues-tracker/issues/17)
tracks missing exact-head evidence without duplicating the issue.

## Next safe implementation

Define finite executor continuation points and persist resolved skills/settings
provenance. Integrate admission-to-worker handoff and worker release while waiting;
checkpoint refinement suggestion, iteration and accumulated context before a pause
rather than replaying its existing provider call. Persist in-flight external work
and explicit uncertain-work retry before execution. Only then complete public
create/lookup/history/cancel/retry acceptance cases and begin the Rust client.
Storage-only `advance` never authorizes replay of an external operation.

Research: Python [atomic replacement and synchronization](https://docs.python.org/3.10/library/os.html),
Pydantic [strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/).

---

## Earlier admission checkpoint (preserved)

# CLI implementation checkpoint — 2026-11-01

## Baseline and preserved work

- Persistent working branch: `development`, starting at
  [cd2f12f1abbea95d60311c09b3b68d3ba70aa189](https://github.com/alehkot/llm-council/commit/cd2f12f1abbea95d60311c09b3b68d3ba70aa189).
- Observed upstream `main`:
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Its ACP transport/model-routing changes remain separate and preserved. No
  upstream integration was required: the manager baseline is identical, and the
  storage differences are the earlier development legacy-read fix.
- Implementation commit:
  [dcfd0770159abce0f5215e0c2772155fe336999b](https://github.com/alehkot/llm-council/commit/dcfd0770159abce0f5215e0c2772155fe336999b),
  tree `d5356dce2d104dbf26fd891e2573db47fcd67ec3`.
  This checkpoint's containing commit is a documentation-only descendant. The
  separate publication receipt records its exact final SHA, branch publication
  and final remote CI observations, avoiding a self-referential hash here.

## Completed

The original typed-contract milestone remains complete, including the pure
legacy Stage 3 read adapter and config version negotiation. The second PLAN item
is now **partially implemented**: internal durable admission/receipt persistence.

- Manager-serialized canonical request identity is persisted before creating a
  fixed conversation and its user/assistant pair. Concurrent duplicate requests
  share the same receipt; changed payloads conflict.
- Interrupted initialization can resume before conversation creation or before
  acknowledgment without duplication. Corrupt, mismatched or externally removed
  accepted state fails closed rather than being overwritten.
- Receipts survive disposable journal cleanup and synthetic terminal-result
  persistence. Reads return detached records without rewriting files, including
  when today's configurable prompt limit is lower than the accepted request.
- Single/bulk deletion writes a minimal tombstone before removing conversation
  data, erases the receipt's request payload, and prevents delayed resurrection.
  Shared storage locking covers creation/deletion races and partial admission.
- No public run endpoint, provider scheduling or new recovery execution exists.
  The internal `prepared` phase is deliberately not a legacy resumable status.
  **API version remains 0**, and no Rust client is implemented.

See [CONTRACT.md](CONTRACT.md) for boundaries and failure/recovery semantics.

## Verification

- 261 backend tests passed under Python 3.10.22 with outbound network access
  forbidden by the offline runner; 27 new admission tests use synthetic data.
- New coverage includes 32 concurrent duplicates, two fresh interpreter
  processes, both initialization interruption windows, settings changes,
  journal cleanup, malformed data, deletion races and payload-free tombstones.
- 81 existing Node tests passed; no frontend implementation changed.
- Repository-wide Ruff format/check and Git whitespace checks passed. Ruff used
  the existing Python 3.13 environment; backend runtime tests used Python 3.10.
- Backend startup and graceful shutdown passed with an empty synthetic data
  directory and unused synthetic credentials.
- Independent full-change review and adversarial probes found receipt-reference
  validation and distinct deletion-target issues during implementation; both
  were fixed and covered. Final review reported no moderate-or-higher introduced
  findings. Additional independent checks included 48 concurrent duplicate
  preparations and 47 admission/contract/legacy-recovery tests. A clean review is
  not a correctness guarantee.
- No local full builds, live-provider calls, Rust runtime, macOS Keychain or
  rendered-browser checks ran. Process-interruption tests do not establish
  power-loss durability, cross-process concurrent writers, complete consultation
  orchestration or exactly-once provider execution.

## CI and publication limits

Exact starting development and upstream heads had no commit statuses, check runs
or Woodpecker commit comments. Empty aggregate `pending` did **not** establish a
running build. The checked-in workflow expects pushes on main/development; its
normal final-SHA result must be observed separately. No build pass is claimed.
No manual dispatch/rerun, private-runner access or CI/security setting changed.

Publication uses a non-force expected-head update only after rechecking both heads;
remote commit/tree verification and final CI evidence belong in the publication
receipt. If publication cannot proceed, retain genuine-history bundle/patch and
mark branch publication unconfirmed rather than claiming remote CI.

## Next priority

Implement the durable execution continuation required for persistent human input
and explicit uncertain-work retry. Resolve/record skills and effective model
provenance, safely hand admission to execution, release workers while waiting,
and retain exact prompt identities/first-answer receipts. Only then expose the
public idempotent creation/lookup/history endpoints and complete their execution
acceptance cases. Add run-specific cancel/retry revisions before a Rust client can
submit. Never silently reuse legacy automatic recovery or advertise version 1
before the complete safety contract works.

Research: Python [file synchronization and atomic replacement](https://docs.python.org/3.10/library/os.html),
Pydantic [strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/).
