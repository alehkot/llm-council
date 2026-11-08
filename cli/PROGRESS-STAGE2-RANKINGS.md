# Complete Stage 2 ranking projection

## Baseline and scope

Working branch: `development`, starting at
[`a6b3ad4007c36021a11819fb1749b32c2e928226`](https://github.com/alehkot/llm-council/commit/a6b3ad4007c36021a11819fb1749b32c2e928226).
Observed upstream `main`:
[`409c67c9d1c192579231a626053299a66c61fffb`](https://github.com/alehkot/llm-council/commit/409c67c9d1c192579231a626053299a66c61fffb).
The development history includes the previous durable Stage 2 worker handoff.
No active PR or conflicting branch owner was found. No upstream integration was
needed for this additive internal projection.

`backend/run_stage2_results.py` completes the next bounded ranking step from
`cli/PLAN.md`: read and aggregate complete reviewer ballots from validated saved
inputs and attempts. It reuses `council.parse_ranking_from_text`,
`calculate_aggregate_rankings` and `calculate_pairwise_rankings`, rather than
introducing a different ballot algorithm. These existing functions are the
behavioral reference; future execution must use the pinned ranking method and
label mapping, not current configuration.

The reader requires every requested reviewer to have a stop-terminated response.
Unstarted, in-flight, uncertain and length-only responses return no complete
projection. Invalid complete ballots retain their original text and attempt
provenance, are listed with reasons, and do not vote. An all-invalid result has
no aggregate winner. Excluded Stage 1 candidates remain reviewers of surviving
candidates. Reads do not rewrite receipts, dispatch providers, select a partial
quorum or mark a run complete. Detached snapshots survive cancellation and
settings drift but reject deletion and corrupt causal receipts.

## Verification

- Nineteen focused offline tests pass across average-rank and pairwise modes,
  including invalid/duplicate/missing ballots, incomplete reviewers, retained
  provenance, cancellation, deletion, corruption and excluded candidates.
- Independent full-change review found no moderate-or-higher introduced finding.
  Additional independent probes passed for active in-flight reads, dissenting
  ballots and explicit retry attempt provenance in both ranking modes.
- Existing frontend: 114 Node tests, ESLint and Biome format checks passed.
- Python Ruff lint/format and authenticated API0 startup/graceful shutdown passed.
- All 1,096 offline Python tests passed in 146.612 seconds with unmocked outbound
  networking forbidden. The final publication receipt records exact
  local/published commit and tree, and exact-head CI observations.
- Full builds are unrun by policy. No native macOS/Keychain, live provider,
  rendered browser or Rust client execution was performed. This internal Python
  projection changes no UI, HTTP surface or CLI API version.

Both starting branch heads had no statuses, check runs or Woodpecker commit
comments. Empty aggregate `pending` is not an observed running build. Normal
Woodpecker evidence must be checked for the exact published head; no build pass
is inferred and no manual dispatch or runner-network access is authorized.

## Remaining work

This is not durable completion of Stage 2 or the consultation. Continue the
plan's remaining continuation/negative-mode, deliberation/synthesis,
workflow/context, bounded scheduling and crash-recovery boundaries. Length-only
reviewer continuation is still unresolved, and all-invalid ballots need an
explicit downstream policy before they can authorize advancement. Do not expose
public creation/resume commands or advertise CLI API 1 until the complete
execution and acceptance contract is implemented and verified.

Keep the genuine-history local checkout, bundle, patch and publication receipt.
A failed upload alone does not discard permitted local-first work. Recheck both
remote heads before a non-force publication, preserve other commits, and record
missing CI honestly in the existing recovery issue if evidence remains absent.
