# Backend contract milestone

The target wire types live in `backend/consultation_contract.py`, with synthetic
JSON examples in `tests/fixtures/cli`. They are **draft v1 contracts**, not newly
available operations. The Rust client has not been implemented.

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
routes or a Rust client. Keep the second PLAN item incomplete until its HTTP and
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
