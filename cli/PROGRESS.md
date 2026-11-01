# CLI implementation checkpoint — 2026-11-01

## Baseline and preserved work

- Working branch: `development`, verified existing Repo Care branch, starting at
  [77b3250249aa791871a41eec6f5b4933eb8ef2a6](https://github.com/alehkot/llm-council/commit/77b3250249aa791871a41eec6f5b4933eb8ef2a6).
- Observed upstream `main`:
  [96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
  Its separate ACP transport/model-routing changes are not integrated or replaced
  by this milestone. Existing development reliability fixes remain intact.
- Implementation commit:
  [95221bf91053e3f5a6d44bfe2869adca3902cd4b](https://github.com/alehkot/llm-council/commit/95221bf91053e3f5a6d44bfe2869adca3902cd4b),
  tree `45c56271e8ac827e4715b9f20324911636d679d6`.
  This checkpoint's containing commit is a documentation-only descendant. The
  publication receipt/checkpoint outside Git records its exact final head and
  final remote observations, avoiding a self-referential commit hash here.

## Completed

The first PLAN action item is complete: draft typed backend request/response
contracts, run lifecycle separate from stage, prompt-instance identity, synthetic
compatibility fixtures, and `/api/config` version negotiation. See [CONTRACT.md](CONTRACT.md)
for exact scope. **Version 0 remains advertised; durable CLI operations and the
Rust client do not exist yet.**

Also resolved a confirmed legacy compatibility defect required by the plan:
reading an old flat Stage 3 record rewrote the file, replaced its timestamp, and
lost extra evidence such as usage. The shared pure normalization adapter preserves
original data and provides the normalized view without read-triggered writes.
Explicit later mutations continue to persist normalized data.

## Verification

- 234 backend tests passed with outbound networking forbidden, including 15 new
  contract/compatibility tests. Test code uses synthetic data and credentials.
- 81 existing Node tests passed.
- Repository-wide Ruff format/check and Git whitespace checks passed.
- Backend startup and graceful shutdown passed with an empty synthetic data
  directory. Python 3.13.5 / Pydantic 2.12.5, reused existing locked dependencies;
  Python 3.10 was not separately exercised.
- Negative control on the starting storage code reproduced the legacy rewrite,
  replaced timestamp and lost usage; final regressions preserve all three.
- Independent full-change review and additional adversarial probes found no
  introduced moderate-or-higher issue; final tests-only delta re-reviewed. A clean
  review is not a guarantee of correctness.
- No local full builds, live-provider calls, Rust runtime, macOS Keychain or
  browser verification ran. This backend-only change does not establish complete
  consultation orchestration or model quality.

## CI and publication limits

Exact starting development and upstream heads had no commit statuses, check runs
or Woodpecker commit comments. The empty aggregate `pending` value did **not**
represent an observed running build. The normal Woodpecker result for the final
published head must be checked separately; no build pass is claimed in this
checkpoint. No manual dispatch/rerun or CI/security setting change was made.

Publication uses a non-force expected-head update only after rechecking both heads;
remote commit/tree verification and final CI evidence belong in the publication
receipt. If publication cannot proceed, retain real-history bundle/patch and mark
these changes local-only rather than claiming remote CI.

## Next priority

Implement durable idempotent run creation/lookup using the existing manager
lock/journal/storage. Test lost replies, concurrent duplicate requests, restart,
terminal receipts after journal cleanup and deletion tombstones before a Rust
client can submit. Follow with exact durable prompt checkpoints, worker release,
first-answer-wins and explicit retry/cancel revisions. Do not advertise v1 until
its whole safety contract works. Do not add automatic paid retries.

Research references: [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/)
and [extra-field handling](https://docs.pydantic.dev/latest/concepts/models/#extra-data).
