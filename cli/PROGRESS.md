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
