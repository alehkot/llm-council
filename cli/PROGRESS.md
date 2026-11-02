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
