# Exact Stage 2 first-continuation input preparation

## Baseline and rationale

Working branch: `development`, starting at
[`367222f70075a8d055f7c78959e595070d8693bb`](https://github.com/alehkot/llm-council/commit/367222f70075a8d055f7c78959e595070d8693bb).
Upstream `main`:
[`409c67c9d1c192579231a626053299a66c61fffb`](https://github.com/alehkot/llm-council/commit/409c67c9d1c192579231a626053299a66c61fffb).
The genuine development history includes the prior ranking projection and
worker-recovery exclusion. Main is an ancestor; no integration was needed. No
active PR or conflicting branch owner was found.

The next bounded CLI-plan gap is retained length-limited reviewer output. Such a
response cannot count as a complete ballot. Before a safe continuation attempt
can exist, its exact request and ancestry must be recoverable. The existing
Stage 1 continuation input boundary is the implementation reference. This step
uses the same strict saved-policy/context-budget approach, in a separate Stage 2
namespace, without changing that prior behavior or enabling execution.

## Completed scope

- Pin/load one named reviewer's first continuation from validated initial ranking
  inputs and a retained length-limited result, with exact original prompt,
  partial text/reasoning, model/options, endpoint, timeout and saved budgets.
- Retain initial request, operation, response and provider identities. Preparing
  another reviewer preserves prior requests and original roster order.
- Reject stale, cancelled/deleted, unsupported-policy, configuration-drift,
  over-budget and corrupt-source preparation rather than modifying saved facts.
- Integrate read-only inspection validation and compact identities. No automatic
  provider calls, stages, scheduling, concatenation or ballot acceptance.
- Keep API0 and all unfinished public consultation commands gated.

## Verification and publication record

Eighteen focused synthetic tests cover exact payload/provenance, ordering,
concurrent duplicate preparation, detached nested payloads, missing/stopped and
uncertain results, revision/configuration guards, budget/policy rejection,
cancellation/deletion, corrupt sources, fresh-process historical reads,
initializing-record corruption, persistence failure and lost acknowledgments.
The focused suite passed. All 1,123 offline Python tests passed with unmocked
outbound networking forbidden. Existing 114 frontend Node tests, ESLint, Biome,
repository-wide Ruff check/format, whitespace and real authenticated API0 startup
with graceful shutdown passed. Independent full-change review found no introduced
moderate-or-higher finding; extra synthetic probes covered actual budget overflow,
a genuinely pinned zero-continuation policy, sibling preservation and coexistence
with prior Stage 1 continuation ancestry. A clean review is not a guarantee.
Exact local/published commit/tree and final CI observations are recorded in the
accompanying publication receipt, avoiding a self-referential commit hash. No full
local build, paid/live-provider test, native macOS/Keychain or Rust consultation
execution is claimed. This internal backend change has no rendered UI surface.

Both observed branch heads had zero statuses/check runs and no Woodpecker commit
comments. The aggregate empty `pending` response is missing evidence, not a
confirmed running build or a build pass. Observe normal CI on the exact final
published head separately; never dispatch/rerun it or access a private runner.

## Remaining dependencies

Add a separately guarded first-reviewer continuation attempt, bounded provider
transport and worker ownership, then validate complete text/ballot provenance.
Continue remaining negative-mode, subsequent-turn/context-reduction,
deliberation/synthesis, durable scheduling and whole-run acceptance boundaries.
An input snapshot alone must never mark the reviewer or consultation complete.
All-invalid ballot advancement still requires an explicit downstream policy.

Preserve the real-history repository, patch/bundle and receipt when publication
is unavailable. Recheck exact remote working/default heads before non-force
publication, retain others' work and distinguish local-only commits from remote
CI evidence.
