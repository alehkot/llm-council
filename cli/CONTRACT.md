# Backend contract milestone

The target wire types live in `backend/consultation_contract.py`, with synthetic
JSON examples in `tests/fixtures/cli`. They are **draft v1 contracts**, not newly
available operations. A Rust foundation and authenticated macOS Keychain setup command exist in this
directory; durable consultation commands remain unconnected. Native macOS
verification is still outstanding.

`GET /api/config` now returns **`cli_api_version: 0`**: the durable CLI contract is
not available. A future client requiring v1 must stop with an update message,
not fall back to unsafe legacy submission/retry. Do not change this value to 1
until durable create, lookup, human input, retry and cancellation are implemented
and tested together. `TARGET_CLI_API_VERSION` identifies the draft being developed;
it does not advertise support.

## Available versus proposed

| Available today (unchanged) | Not implemented yet |
| --- | --- |
| Authenticated config, conversations, installed skills, Markdown export | Idempotent `PUT /api/consultations/{run_id}` |
| Conversation-scoped queue/status polling | Run history and durable run-specific snapshots |
| `GET /api/consultations/status` global activity snapshot | Durable prompt-instance answer receipts |
| Process-local interactive sessions with legacy defaults | Persistent waiting that releases workers |
| Existing conversation cancellation/recovery | Run-specific cancel and explicit-retry revisions |

The existing global `/api/consultations/status` route is not run lookup. Legacy
`status` values mix pipeline stages and execution state; do not interpret them as
the draft `RunLifecycle`. In particular, an existing `interactive` state does not
establish a durable `waiting_input` checkpoint.

## Draft types and identities

- `CreateRunRequest` preserves exact question/negative text, rejects unknown
  fields and lossy primitive coercions, and requires explicit `persistent` and
  `explicit_retry` policies. Character limits use Python Unicode code points.
  Omitted options are materialized by `model_dump(mode="json")`; future
  idempotency storage must compare this accepted representation. Skill existence,
  enabled state and case normalization remain integration concerns: no endpoint
  currently accepts this draft request.
- `RunReceipt` identifies the UUID run separately from its conversation. A queued
  run may have no assistant index yet. `RunSnapshot` separates lifecycle from
  stage, validates prompt/result ownership, and requires the exact pending prompt
  or retry notice for its corresponding lifecycle. Complete runs require their
  saved result/export reference. Stage evidence and provenance currently retain
  heterogeneous backend JSON; this milestone does not enforce provider schemas.
- `PromptInstance.instance_id` differs from the reusable logical `prompt_id`.
  Repeated refinement rounds must receive distinct instance UUIDs. Creating and
  durably assigning these identities remains the checkpoint milestone's job.
- `AnswerRequest` preserves string, boolean, and string-list answers without
  coercion. A skipped response cannot contain answers. Validation against the
  actual prompt's fields/options/required/skip rules, stale-answer conflicts,
  first-answer-wins, and persistence are **not implemented** by this wire model.
- `RetryRequest` carries an opaque persisted revision; this type does not perform
  a retry. No automatic potentially billed work is introduced.
- `ResultReference` permits a missing run ID for legacy records, retaining the
  conversation ID and assistant-message index for export without inventing IDs.

## Legacy result compatibility

`backend/results.py` is the shared pure Stage 3 adapter used by storage reads and
Markdown export. It provides the current version for both flat legacy answers and
versioned data, retaining original text, timestamps and extra evidence. Missing
legacy dates remain `null`, not a newly invented read time. Returned values are
detached from the input. Reading a legacy conversation no longer rewrites its
JSON file; an explicit later mutation can save the normalized representation.
Terminal escaping belongs to the future client, not saved result normalization.

## Internal admission foundation (not an HTTP API)

`ConsultationManager.prepare_cli_run` now serializes a validated request under
its existing lock and `backend/run_admission.py` stores the canonical request and
fixed conversation reference before initializing that conversation. A repeated
identity/payload returns the same admission; a different payload conflicts. A
retry repairs interruption before conversation creation or before acknowledgment
without adding another user message or assistant placeholder. Missing or damaged
accepted state fails closed for repair rather than being overwritten.

Receipts live in `run_receipts/` beside the conversation directory, separately
from disposable execution journals. They survive journal cleanup and retain the
accepted request even when a synthetic saved result becomes terminal. Internal
`get`/`list_receipts` read detached records with bounded result counts; these are
not the proposed public lifecycle snapshots/history endpoint. The `prepared`
phase means **not scheduled**, not queued or running. It is deliberately excluded
from legacy startup recovery. No provider call, HTTP creation route or new worker
scheduling is introduced by this milestone.

Single and bulk conversation deletion serialize against preparation using the
existing storage lock. They write a minimal ID/deletion-time tombstone before
removing the conversation, including partially initialized admissions. Request
text, negative prompt and skills are removed from that receipt. A delayed retry
cannot resurrect deleted work. If the tombstone cannot be written, deletion fails
rather than removing the evidence needed to reject a retry. A later filesystem
unlink failure may leave a tombstoned conversation file for an explicit repeat
of deletion; preparation will not reuse it.

This is the existing single-process JSON architecture, using its atomic file
replacement writer. Tests establish process-interruption repair, not filesystem
power-loss durability or cross-process concurrent writers. Do not manually remove
receipt files to retry a run. Skill validation/canonicalization, effective model
provenance, execution handoff, durable prompt continuation and explicit retry must
be integrated before exposing these operations. API version remains **0**.

## Verification and next step

Offline fixtures exercise wire models and legacy normalization. Admission tests
cover duplicate identities, exact text/defaults, crash windows, corruption,
terminal journal cleanup, deletion, history and startup isolation. They do not
establish exactly-once provider execution or end-to-end CLI orchestration.

Next: persistent execution checkpoints and safe admission-to-worker handoff,
including resolved skills/settings provenance, before public create/lookup/history
routes or connecting Rust run operations. Offline parser/wire tests may proceed
in parallel. Keep the second PLAN item incomplete until its HTTP and
execution acceptance cases pass; do not advertise version 1 early.

Implementation references: Pydantic [strict fields](https://docs.pydantic.dev/latest/concepts/strict_mode/),
[forbidden extra data](https://docs.pydantic.dev/latest/concepts/models/#extra-data),
and Python [file synchronization and atomic replacement](https://docs.python.org/3.10/library/os.html).

## Internal persistent prompt foundation (not execution integration)

`backend/run_interaction.py` stores exact typed prompt instances and opaque
workflow-owned continuation state inside the accepted run receipt. A prompt must
be persisted before a future executor releases its worker. Each new instance
requires the current interaction revision; a previous answer must be explicitly
consumed before another instance can be stored. Logical `prompt_id` reuse does
not reuse an instance UUID. Retrying the same instance/schema/state is a read;
changing any accepted schema or checkpoint conflicts.

The first schema-valid answer is saved before any continuation. Identical answer
retries remain successful after consumption or later prompts; conflicting answers
cannot replace it. An explicit `advance` atomically records the next continuation
and marks that answer consumed, retaining both for retry checks. Neither operation
schedules work or runs a provider. A continuation is an opaque identifier and
finite JSON object, **not executable code or an authorization to repeat external
work**. Future integration must define the finite set of supported continuation
points and persist in-flight operations/explicit retry before provider execution.

Validation uses the saved schema, not regenerated fields. Field IDs and option
values must be unique; selections must match saved options, multi-selections
cannot duplicate values, required text must contain non-whitespace content, and a
required checkbox needs an explicit boolean (including `false`, not mandatory
agreement). Optional omitted fields stay omitted. Defaults are display values,
never silently accepted answers. Skip is accepted only when the saved prompt
allows it. Accepted strings, selection order, schema and continuation values are
preserved rather than trimmed or reinterpreted.

All reads and mutations require an intact prepared admission. The existing
single/bulk deletion tombstone removes prompt questions, continuation state and
answers together with the original request. The storage lock covers same-process
first-answer/deletion races. Atomic replacement supports interrupted-process
retry; this does not establish power-loss durability or cross-process writers.

This storage foundation is intentionally disconnected from legacy prompt queues,
HTTP routes, startup recovery and execution. In particular, the existing prompt
refinement flow calls a provider before looking up its saved response: do **not**
replay that flow to restore a durable prompt. Integration must checkpoint its
suggestion, iteration, accumulated clarifications/context and original/refined
query at the exact continuation boundary. Worker release, safe resumed stages,
effective settings/provenance and uncertain-operation recovery remain unfinished.
API version stays **0**; the PLAN persistent-input milestone is not complete.

## Finite refinement continuation (shared reducer, not durable execution)

`backend/refinement.py` now defines the versioned `prompt_refinement_v1`
continuation. Its complete typed state retains the original and working queries,
round, exact normalized provider suggestion, accumulated clarifying answers and
additional context. Unknown versions/fields, missing state and invalid phase/type
combinations fail validation instead of silently reconstructing a different
question. The finite phases are `needs_suggestion`, `waiting_answer`, and
`complete`.

The existing legacy graph uses this same pure answer reducer. Its behavior is
preserved: skip keeps the original query; edited working text is trimmed without
altering the raw answer receipt; clarifications or insights request another round;
three rounds is the existing cap. A restored `waiting_answer` state can apply the
already accepted answer and produce the next continuation without invoking a
provider to recreate the suggestion. Read the first accepted answer from the
saved prompt checkpoint, never from a competing client response; retain the
existing `run_interaction.advance` idempotency checks. These transitions do not
validate an answer against a prompt by themselves: durable callers must use the
saved-schema validation and first-answer receipt first.

`needs_suggestion` describes the next operation, **not permission to execute it**.
The future executor still must persist in-flight external work and obtain explicit
consent when its outcome is uncertain. No HTTP route, worker pause/release,
startup recovery or durable executor dispatch is connected here. The legacy graph
still uses its existing process-local interaction queue and timeout policy; it
still cannot safely replay a partially completed refinement as a durable CLI run.
Do not route durable runs through that legacy recovery path. Other interactive
phases, resolved settings/skills provenance, safe worker handoff and uncertain-work
retry remain unfinished. API version stays **0**.

Tests cover strict state roundtrips, fresh-interpreter restoration, first-answer
storage/advance replay, and real legacy graph refinement with fake provider
boundaries. They do not establish complete persistent consultation execution,
exactly-once provider calls, model quality or a functioning Rust client.

## Durable refinement answer coordinator (internal, no provider dispatch)

`backend/durable_refinement.py::resume_answer(run_id, instance_id)` now connects
stored prompt receipts to the finite refinement reducer and atomic continuation
consumption. It accepts no caller-supplied answer: it loads the first validated
answer already persisted by `run_interaction.answer`, verifies the admitted
refinement request and original question, then saves the derived next state under
the same storage lock. A failed write leaves the accepted answer available for a
safe retry. Missing/waiting/deleted/incompatible checkpoints fail without repair
or replacement.

Compatibility checks protect semantic field IDs/types/action values and the
saved clarification questions. They do not regenerate the UI prompt or insert
current labels/defaults. Existing saved wording, defaults and raw answer text are
retained. A consumed-instance retry verifies and returns its saved result without
rewriting the receipt; that result is historical state, not authorization to
schedule another provider call or override a later prompt.

This is production coordination code for the internal durable boundary, not a
public endpoint or worker loop. It does not call providers, generate suggestions,
release/resume workers, add retry consent, or advertise CLI API1. A returned
`needs_suggestion` still requires current-run/revision checks and persisted
in-flight/explicit-retry handling before execution. Admission provenance and the
other durable workflow boundaries remain required before connecting `ask` or
`resume`.

## Internal guarded refinement execution (not public run execution)

`backend/refinement_execution.py` adds one concrete external-operation boundary:
`suggest` requires the intended interaction revision, a stable non-secret provider
configuration identity and an injected **single-attempt** async callback. It
reads the admitted question/current validated continuation, saves an in-flight
attempt identity before calling, and atomically saves the validated suggestion
and its exact waiting prompt in the same receipt write. A completed operation at
the requested revision is a historical read, never permission to start a later
round. The existing first-answer coordinator consumes that prompt's response.

An in-process owner prevents duplicate concurrent calls. After an exception,
cancellation, lost persistence acknowledgement or restart, an unowned in-flight
attempt is reported as `needs_retry`; inspection does not call a provider or
rewrite the receipt. Explicit consent names that attempt and its original source
revision. A repeated/stale consent cannot start another attempt, and a changed
provider identity conflicts, including across refinement rounds. Completion
rechecks current input/revision and deletion before publishing a result. Deletion
removes operation inputs/results with the rest of the receipt. Saved prompt
schemas and reducer history are validated, not regenerated on read.

This does **not** install a provider, public route, startup recovery hook, worker
loop or API1 support. The callback must not hide automatic retries. In particular,
do not simply pass the existing `generate_prompt_refinement` adapter: its mutable
configuration, failure fallback and underlying retry behavior require an explicit
adapter/provenance review first. The stored identity is a mismatch guard, not a
snapshot of effective models, settings or skills. Those snapshots, other workflow
boundaries, run-specific cancellation, scheduling/release and public API acceptance
remain required before connecting `ask`/`resume`.

Guarantees apply to the existing single-backend-process storage lock, not multiple
concurrent backend processes or exactly-once remote execution. A provider may have
accepted work whose reply was never persisted; explicit retry can repeat that
work and may incur another charge. Offline fake-callback tests establish local
coordination only, not live-provider quality or native macOS operation.

## Explicit single-request refinement adapter (internal, OpenRouter only)

`refinement_provider.suggest_openrouter` now binds an explicitly supplied
`OpenRouterRefinementConfig` to the admission receipt before the first attempt.
The immutable, versioned snapshot includes transport, HTTPS endpoint, requested
model, maximum output tokens, timeout and reasoning options. Its canonical digest
is the guarded operation's provider identity. Credentials are supplied only for
the invocation and are neither saved nor hashed into that identity. Credential
rotation does not alter saved request settings. Invalid/stale first requests do
not bind a snapshot; corrupt provenance or settings drift fails closed, even when
reading a completed operation or starting a later refinement round. Provenance
cannot be retrospectively attached to old injected-callback work.

The production HTTP path sends **one POST per admitted attempt** using HTTPX with
no retries, redirects or environment proxies. It makes no model-catalog lookup,
context-summary request or continuation call. A total timeout and response-size
limit bound the request. HTTP failures, incomplete finish reasons, invalid JSON or
refinement fields and the existing long-input omission/ellipsis safeguards reject
rather than silently returning the original question. Failures or cancellation
leave the attempt uncertain under the existing explicit-retry guard. No provider
body or transport exception text is included in adapter error messages. Shared
prompt construction and omission normalization are extracted unchanged; legacy
web refinement retains its previous retry/fallback behavior.

This is **not a generic configured provider or complete run provenance**. There
is no config-global factory, route, worker or automatic selection of this adapter.
Upstream `main` has separate ACP routing work; that choice must be reconciled by
future executor integration, never silently mapped to OpenRouter. Full pipeline
model/settings/skills resolution, other provider boundaries, worker release and
public API acceptance remain incomplete. API version stays **0**, and Rust
consultation commands remain unavailable. Only the requested model is recorded;
this is not evidence of a remote service's actual routing or exactly-once remote
execution. Explicitly retrying uncertain work may repeat a billed operation.

Offline MockTransport tests establish request construction, one-request behavior,
persistence/retry coordination and cancellation, not a live provider, model quality
or native platform pass. Reference: [HTTPX transports and retry defaults](https://www.python-httpx.org/advanced/transports/)
and [OpenRouter chat response contract](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request).

## Internal run-specific cancellation (prepared/refinement runs only)

`run_cancellation.request(run_id)` records cancellation in that run's durable
admission receipt before signalling its owned refinement task. The marker blocks
new prompts, answers, continuations, suggestions and explicit retries through the
shared prepared-run guard. It retains accepted inputs, saved prompts/results and
provider provenance; `run_admission.get`/`list_receipts` can still read this evidence.
The existing refinement/interaction convenience inspectors require runnable
admission and therefore reject a cancelled run. The dedicated read-only internal
inspection projection described below retains that evidence; public lifecycle/result
snapshots remain future work. Repeating identical admission returns the retained receipt,
not new work. Normal deletion still erases the payload and retains a tombstone.

Cancellation is scoped to the named run; sibling admissions and provider tasks
are unaffected. `cancelling` means local work may still be owned or recovery confirmation is
pending; `cancelled` is recorded only after no registered local task remains. A provider
that suppresses cancellation keeps the run `cancelling`; its late result cannot
replace the cancellation or publish another prompt. The cancellation coordinator’s
`run_cancellation.inspect` recovery check after process restart settles an orphaned
cancellation without replaying provider work. The read-only projection below does
not perform this settlement. Failed
marker persistence does not signal tasks, and retrying a request whose write
acknowledgement was lost observes the saved marker rather than restarting work.

The executor uses a dedicated provider task, not the caller's task, and checks
ownership before delivering a queued cancellation signal. This avoids cancelling
unrelated caller work after an operation ends. These are single-backend-process
coordination guarantees, not distributed ownership, remote-provider cancellation,
refunds or exactly-once execution. An already admitted request may incur a charge.

No public cancel route, legacy conversation-wide cancellation change, generic
council worker integration or API version upgrade is enabled. The remaining
pipeline boundaries and public acceptance cases must be implemented before Rust
`ask`, `resume` or `cancel` can be connected. API remains **0**.

Reference: [Python task cancellation](https://docs.python.org/3.10/library/asyncio-task.html#asyncio.Task.cancel)
explains why requesting cancellation is not proof that a task has stopped.

## Read-only internal run inspection

The internal inspection projection reads prepared admissions and refinement
checkpoints under the existing single-process storage lock. It does not install
a public route or advertise API1. Its phases distinguish unscheduled preparation,
waiting input, accepted input still awaiting consumption, refinement readiness,
owned provider work, uncertain work and cancellation. A finished refinement is
not a completed council consultation. It must not be passed off as the proposed
public `RunSnapshot` until scheduling and all stage boundaries are integrated.

Inspection preserves exact saved prompt history, accepted answers, refinement
results and available non-secret provider provenance. Cancellation takes priority
over actionable prompt/retry projections while retaining historical evidence.
Reads do not settle orphaned cancellation, consume an answer, regenerate a prompt,
start a worker, call a provider or retry uncertain work. Existing mutation guards
continue to reject cancelled admissions. Invalid or incompatible records fail
closed instead of being migrated or replaced during a read.

Returned data is detached. A deterministic content cursor includes the visible
local ownership state, so a provider losing ownership changes the observation
from running to uncertain without requiring a receipt write. The cursor is for
change detection, not authorization, a mutation revision token, a proof of remote
provider execution or a complete public long-poll contract. Unknown/deleted
admissions never receive invented runnable work. Full settings/skills provenance,
configured transport reconciliation, public history/monitor routes and Rust
consultation commands remain unfinished.

Research: [Python JSON serialization](https://docs.python.org/3.10/library/json.html)
and [hashlib](https://docs.python.org/3.10/library/hashlib.html) document the stable
serialization and SHA-256 primitives used for change detection; repository tests
establish the projection's actual behavior.
