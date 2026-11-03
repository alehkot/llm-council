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
