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

## Internal explicit uploaded-skill identity guard

`run_skill_provenance.bind_explicit_skills(run_id)` is an internal, opt-in
fingerprint guard for explicitly selected, available and enabled **uploaded**
skills identified by their canonical lowercase IDs. It hashes the ordered skill IDs, prompt-readable content and routing
metadata under the existing storage and skills locks, persists only IDs and
SHA-256 digests, and refuses mismatches, unsupported automatic selection,
external local skill sources, cancellation, deletion, or retroactive binding
after a saved prompt or provider configuration/work. Read-only inspection validates
the saved structure and includes detached hashes in its cursor, without reading
the current skill library. It does not schedule or invoke anything.

This is not yet complete skill or pipeline provenance. It is not called by a
worker, HTTP route, legacy flow or provider. A later executor must account for
auto-selection, all effective settings, mutable external sources and atomically
copy/consume checked content: locks do not prevent independent filesystem edits
and a check followed by later reads has a time-of-check/time-of-use gap. Never
interpret an identity check as permission to dispatch, retry, or bill. API
version remains 0.


## Verified explicit skill content capture (internal)

`run_skill_provenance.capture_explicit_skills(run_id)` requires a previously
bound uploaded-skill identity and returns an ordered tuple of frozen
`CapturedSkill` values. Each holds canonical JSON bytes containing the exact body,
references and routing metadata used for its SHA-256 comparison. Hashing and the
returned data use the same read; `document()` decodes a detached consumer copy
without rereading the library. The operation writes no receipt/content and
starts no work. Content is omitted from the value's debug representation.

Supported library mutations are serialized while capturing. An independent
filesystem writer can still change files; mixed or changed captured content must
match the previously persisted digest or capture fails closed. A successful
snapshot remains unchanged after later library replacement/deletion. This is an
in-memory execution input, not a persisted archive: a restart must capture again
and cannot proceed if the accepted content has disappeared or changed, unless
the separate durable-copy operation below has already saved that content.

Future execution must consume the returned snapshot instead of opening skill
paths again, and must separately check current run revision/cancellation at the
guarded dispatch boundary. Capture itself is not permission to execute, retry or
bill; returning content does not guarantee the run stays runnable afterward.
It does not establish automatic selection, external source support, complete
pipeline settings, a worker, public run routes, or API1. Existing legacy flows
remain unchanged.

## Durable explicit skill content (internal)

`run_skill_snapshot.persist(run_id, expected_revision=...)` copies the verified
prompt-readable content and routing metadata of previously bound, explicitly
selected uploaded skills into the run receipt. The first write uses the existing
capture helper, validates canonical document structure and checks each exact
content digest against the accepted ordered identity. It does not implicitly
bind identities, capture automatic selections, copy scripts/assets/evals, or
claim that content was archived before earlier work. The caller must supply the
current interaction revision. Cancellation, deletion, stale revisions and invalid
adjacent checkpoints block both first persistence and an idempotent return.

The saved copy is intentionally independent of the current skill library.
Repeated persistence and `run_skill_snapshot.load(run_id)` never recapture it,
even after a skill is replaced, disabled or deleted. Historical loading is
read-only, survives process restart, and returns frozen `CapturedSkill` values
with detached document copies. Cancelled runs retain readable history; deleting
the run's conversation erases this content through its existing minimal tombstone.
No credential or local filesystem location is added by the snapshot operation;
skill bodies and references remain private run data and may contain whatever text
the operator put in the skill. The archive is not a secret detector or encrypted
storage.

Read-only inspection validates the full saved content but exposes only snapshot
availability and ordered IDs/digests, not private bodies in status responses.
Malformed structure, changed content, ordering or accepted identity fails closed
without rewriting, repairing or reading current skills. Lost write acknowledgments
can be recovered without recapturing content. Supported same-process mutations
are serialized by the existing storage-then-skills lock order. This is not
tamper-proof storage, power-loss durability or coordination between backend
processes; full public polling/storage performance remains unverified.

This is an archive primitive, not permission to use an old or disabled skill in
new work. Future execution must explicitly consume the archived bytes, apply its
documented selection policy and recheck cancellation/revision at guarded dispatch.
Automatic selection, external sources, reference-routing decisions, rendered
prompt provenance, stage execution and public HTTP/CLI integration remain pending.
No worker, model call, retry, API1 capability or consultation command is enabled.

## Configured refinement bridge (internal, partial)

`refinement_provider.resolve_configured_refinement` reads the loaded backend's
transport, refinement model, OpenRouter endpoint and token ceiling into the
existing frozen, versioned adapter configuration. The internal
`suggest_configured_refinement` resolves once and passes that exact value through
durable binding and guarded execution; it does not reread globals for the request.
A changed model, endpoint or token ceiling conflicts with retained provenance on
a later call, including an explicit retry or a duplicate result read. ACP and
unknown transports fail before persistence or provider dispatch, rather than
silently treating ACP model identifiers as OpenRouter names. Credentials remain
caller-supplied and ephemeral; rotation does not alter the non-secret identity.

This bridge selects the existing **durable adapter's fixed v1 request policy**:
one attempt, no catalog query, automatic retry, input fitting or continuation.
It does not promise the legacy configured provider's adaptive token behavior.
The 60-second timeout and reasoning/high-effort defaults remain part of the saved
adapter configuration. No legacy call site, worker, public route, model catalog
or CLI command invokes the bridge. Full council settings/skills provenance, ACP
execution support, worker release/resumption and public acceptance cases remain
unfinished; API version stays 0. Backend configuration is loaded at startup, so
editing environment variables without restarting is not a runtime config change.

Nine synthetic regression tests verify saved settings match HTTP payloads,
unsupported or invalid configuration produces no writes/requests, retained
settings drift conflicts, uncertain work needs explicit same-settings retry,
post-resolution global changes do not change the request, stale/cancelled/deleted
runs do not dispatch, and credentials are absent from receipts. They do not
establish full pipeline compatibility, live-provider behavior or platform support.

## Revision-bound internal refinement driver

`refinement_driver.advance` composes the internal inspection, saved-answer
reducer and configured suggestion adapter. Each call must name the current
interaction revision and advances **one boundary only**. It returns immediately
for a waiting prompt, completed refinement or retained cancellation, without
requiring provider credentials or reinterpreting saved work under current config.
An accepted answer is consumed and returned as refinement-ready or
refinement-complete; it never dispatches another suggestion in that same call.
A caller must reload and explicitly advance the resulting revision to continue.

A prepared/refinement-ready step invokes at most one guarded configured
suggestion attempt. Uncertain work still requires its explicit retry token;
changed settings, stale revisions and competing ownership fail closed. Lost
answer-consumption acknowledgements require reloading the saved revision, not
replaying a provider call. Corrupt checkpoints are not repaired implicitly.
Cancellation is rechecked by the existing provider guard before dispatch. The
driver does not hold the storage lock across the provider await.

Direct calls to this driver remain caller-owned: cancelling that task during
provider work retains uncertainty, not guaranteed CLI detachment. The internal
manager handoff below adds separate ownership for explicitly scheduled steps.
Reads/polling must use the read-only inspector, not `advance`. Full pipeline and
skill provenance, ACP support, durable scheduling and all public HTTP/CLI
acceptance cases remain unfinished. API version stays 0.

## Internal manager-owned refinement steps (partial)

`ConsultationManager.schedule_cli_refinement_step` now schedules one named
revision on the existing thread pool and immediately returns a process-local
handle. Exact queued/active duplicates share that handle; different revision or
retry consent conflicts. Its optional async `wait()` shields the owned future:
cancelling a waiting caller does not cancel queued or running work. Each waiter
receives detached result data, and the manager retires completed ownership rather
than retaining completed futures or credentials. An explicit run cancellation
still uses the existing durable cancellation marker and owned provider task.

The worker uses `asyncio.run(refinement_driver.advance(...))`, returning its slot
at each saved prompt or answer boundary. Consuming an answer never schedules the
next suggestion. The driver rechecks revision, cancellation and deletion when a
queued step actually starts; no future handle gives permission to ignore those
checks. A run's legacy conversation cannot be scheduled concurrently through the
manager's ordinary submission path while an internal step owns it, and an
internal step refuses already owned legacy work. This is not yet complete
conversation ordering or a public compatibility contract for persistent runs.

For provider boundaries, scheduling captures the immutable supported refinement
configuration and the worker uses that exact value for existing durable binding
and dispatch. Later global configuration changes cannot silently change an
already queued request. New scheduling under changed settings still faces the
existing saved-provenance guard. Waiting, answer-only and terminal boundaries
need neither a provider credential nor usable current provider configuration.
Credentials remain ephemeral and are not part of the handle identity or receipt.

Admission into this worker queue is **not durable scheduling**. A process crash
before dispatch leaves the prior durable boundary, not proof of queued execution.
A crash after provider dispatch retains uncertainty and needs explicit retry;
startup does not automatically schedule these steps. The handle cannot survive a
process restart: read saved run evidence and explicitly schedule a current safe
boundary instead. Public routes, API1 and CLI consultation commands remain off.
Full council execution, non-refinement stages, settings/skills integration, ACP,
macOS/platform and live-provider behavior remain outside this partial step.

Offline worker regressions use the actual one-thread executor and synthetic HTTP
to establish worker release, duplicate ownership, queued and active detachment,
cancellation/deletion/stale dispatch protection, immutable queued settings,
explicit retry, saved waiting restoration, and legacy ownership exclusion. They
do not establish full consultation scheduling, cross-process concurrency,
power-loss durability or exactly-once remote provider execution.

## Internal read-only run history (partial)

`run_history.list_runs(limit=50, before=None)` discovers retained CLI receipts as
newest-first summaries. The page size is an integer from 1 to 100. Ordering uses
UTC creation time, then canonical run UUID descending; historical naive receipt
timestamps mean UTC. The opaque continuation cursor identifies an exclusive
position, survives deletion of its anchor, and is neither authorization nor a
mutation revision. Pages are separate snapshots: refresh the first page to see
newer admissions, and reload a run to see its latest state.

Each summary contains run/conversation IDs, the original creation timestamp,
a question excerpt limited to 160 Unicode code points, the inspector's actual
internal phase and its content-change cursor. `prepared` is not queued work and
`refinement_complete` is not a complete council result. The projection excludes
prompt answers, provider settings and full question text. It does not provide a
terminal escaping contract; a future client must escape untrusted text.

Reads do not write, repair, settle cancellation or dispatch work. Tombstones and
legacy conversations are omitted. Invalid/unreadable receipt headers fail the
page rather than silently hiding work; selected entries additionally receive the
full inspector validation, so corrupt checkpoints outside the page are detected
when read. The single-process storage lock coordinates supported mutations. The
file store still scans all receipt headers: only output size is bounded, not disk
I/O or directory-scan memory. This is not a persistent secondary index or a
cross-process snapshot guarantee.

Offline tests cover multi-page ordering, timezone/tie handling, deletion and new
admissions between pages, real prompt/answer/refinement/cancellation boundaries,
uncertain work after transient journal deletion and fresh-process restart, read
purity and corruption failures. No public history route, finished-conversation
lookup contract, API1 capability or CLI consultation command is enabled here.

## Internal pre-query configuration continuation (partial)

`durable_prequery.advance(run_id, expected_revision=...)` explicitly prepares or
consumes one provider-free response-style boundary. It is allowed after fresh
admission with `refine=false`, or after the final refinement answer was consumed
and refinement is complete. The saved question is the exact admitted question or
finalized refinement result. A valid refinement prefix may be followed by one
terminal `pre_query_v1` checkpoint; unsupported or disconnected history fails
closed. This helper does not infer or change the global interactive-mode setting.

Preparation persists the existing pre-query prompt with a unique instance ID,
exact schema and versioned continuation, then returns `waiting_input`. A valid
first answer is saved through `run_interaction.answer`. A separate explicit call
consumes that receipt into `pre_query_complete`, preserving the exact response
mapping and `skipped` flag. Optional omissions remain absent; an explicitly
submitted empty response differs from an explicit skip. Display defaults are
never turned into accepted answers. A future execution adapter must preserve
these distinctions when resolving effective prompt modifiers and settings.

Each mutation checks the current interaction revision under the existing storage
lock. Stale retries conflict; reload to recover a lost acknowledgment. Calls at
current waiting/completed revisions return saved evidence without writes or
schema regeneration. Read-only inspection/history retain the latest refinement
alongside the pre-query evidence and keep cancellation precedence. The refinement
driver/manager reject attempts to advance a run already at pre-query; historical
completed refinement-operation reads remain available without provider dispatch.

This is a durable input prerequisite, **not** council execution, resolved pipeline
configuration, worker scheduling, or completion of a consultation. No provider
credential/call, implicit next stage, public run route, API1 capability or Rust
consultation command is enabled. The legacy web graph is unchanged. Full model,
feature and skill provenance; guarded council stages; remaining review/synthesis
prompts; durable scheduling; and whole-run public acceptance remain required.
Offline tests establish synthetic single-process concurrency, write-boundary
recovery, fresh-process restoration, source/answer validation and cancellation/
deletion behavior, not power-loss or multi-process concurrency guarantees.

### Internal response-policy snapshot (partial)

`prequery_policy.bind(run_id, expected_revision=...)` now binds a provider-free,
immutable response-policy input only after a durable pre-query answer is consumed.
Version 1 retains the exact finalized query and generated system modifier, its
source prompt instance/revision, and effective domain/style/priority. It reuses
`InteractiveConfig` semantics, not the prompt schema's display defaults. An
omitted optional field has a backend execution default, but that default is **not
an accepted user answer**: the original answer and its omissions remain unchanged.
Explicit skip applies no modifier and has no effective domain/style/priority.

The receipt persists these exact strings before any future council dispatch.
Binding is serialized with answer/cancellation/deletion operations. Repeated binds
are byte-pure and return a frozen, detached value; a lost write acknowledgment is
recovered by inspecting/rebinding the same run and revision. The interaction
revision does not advance because no new prompt/answer/continuation is created;
the inspection cursor does change to include the newly saved policy. Cancellation
retains read-only evidence but prevents binding even when a policy already exists;
deletion erases it with the receipt payload.

Inspection validates the saved policy against the completed source checkpoint,
including exact query and modifier bytes. Unsupported versions, mismatched source
identity/revision, corrupt values or changed interpretation fail closed without
rewriting the record. Version 1 derives its meaning from today's legacy mapping;
changing that mapping requires an explicit version/migration policy rather than
silently reinterpreting an old run. This validator currently supports only the
final pre-query boundary; later durable workflow boundaries must extend source
lookup deliberately instead of treating a changed interaction revision as safe.

This is a concrete future prompt input, **not** effective model/transport/skill
configuration, a complete Stage 1 system prompt, scheduling, provider execution,
or permission to dispatch. It is not a cryptographic tamper-proof record. A future
executor must recheck run state/revision/cancellation and separately resolve
remaining provenance before using it. No public route, API1 capability, legacy
web behavior, Rust consultation command, or provider call changes.

### Internal configured council selection (partial)

`council_selection.bind(run_id, expected_revision=...)` saves the ordered,
nonempty, distinct council roster and configured negative-mode, deliberation and
pairwise-ranking switches after a completed saved response policy. Version 1 is
OpenRouter-only; ACP and malformed configuration fail closed. These are loaded
backend configuration choices, not per-run model overrides or a declaration that
any stage ran. A supplied negative prompt is retained separately and does not
implicitly enable the negative-stage switch, matching the legacy manager. Later
human review can still exclude member results; this snapshot does not resolve
those future choices.

The immutable return value uses a tuple roster and strict booleans. Storage binds
its source revision and a digest of the exact response-policy value. Concurrent
binds serialize under the storage lock, and identical retries do not rewrite the
receipt. Rebinding compares today's loaded choices to the saved selection and
conflicts on drift rather than silently replacing it. Ordinary inspection never
reads current settings: it preserves historical selection across restart/config
changes, including after cancellation. Cancellation, deletion and stale revisions
prevent binding; malformed or unsupported saved evidence fails without repair.
The inspection cursor includes the selection, but the interaction revision does
not advance because no prompt or answer has been added.

This is **not a complete execution configuration or dispatch token**. Endpoint,
credential, token/context/retry policy, synthesis model/CLI configuration, skills,
other helper-model settings and remaining stage input boundaries are not covered.
The legacy pipeline continues to use its own imports and context; no executor
consumes this snapshot yet. Future durable execution must use the frozen values,
resolve all remaining provenance, and recheck cancellation/revision immediately
before guarded external work. The current source validator supports the final
pre-query boundary only; extending durable histories requires a deliberate causal
validation update. Digests are identities, not tamper-proof storage. API0 and all
public consultation gates remain unchanged. No provider call or legacy behavior
changes, and tests do not establish multi-process writers or power-loss durability.

### Internal configured OpenRouter request policy (partial)

`council_request_policy.bind(run_id, expected_revision=...)` saves the loaded
OpenRouter endpoint, output-token ceiling, context-reduction and continuation
call limits, retry count and backoff/jitter settings after a completed saved
council selection. Its versioned immutable value includes the exact selection's
digest and source revision. All fields are required. Counts are strict integers;
backoff values must be finite, nonnegative/positive as appropriate and ordered.
The endpoint must be HTTPS without URL credentials, query, fragment, whitespace
or control characters. No provider key or local authentication key is retained.

Both initial binding and rebinding check current loaded council selection as
well as request settings. Missing prerequisites are not implicitly created.
Configuration drift conflicts instead of replacing saved choices. Binding uses
the same storage lock as cancellation/deletion and rejects stale revisions even
on an otherwise identical retry. Concurrent duplicate binds write once, and an
uncertain write acknowledgment can be recovered by inspecting/rebinding. Adding
this provenance changes the inspection cursor, not the interaction revision.
Historical inspection uses saved values without current settings or network
access, validates their causal source, and never repairs or rewrites. Cancelled
runs retain read-only evidence; deletion erases it.

These are **configured limits, not a complete effective request policy or
permission to execute retries**. The legacy OpenRouter path also obtains model
catalog metadata, computes per-model budgets, decides reasoning support, reduces
context and continues prose. None of that metadata, exact wire messages, timeout
policy or external-operation state is resolved here. In particular, a configured
retry count cannot override the accepted `explicit_retry` recovery policy:
future durable execution must guard each potentially billed request and require
explicit consent for uncertain work, including context-reduction/continuation
calls. It must consume saved settings rather than the legacy module's imported
globals. This snapshot is not used by that legacy path and does not change it.

Synthesis/helper configuration, skills, guarded stages, remaining human-input
boundaries, scheduling and whole-run acceptance are still required. The current
causal validator supports only the completed pre-query boundary; later workflow
histories need deliberate validation extensions. Digests identify values but do
not make storage tamper-proof. No provider/catalog request, API1 capability,
public run route or CLI consultation command is enabled. Offline tests establish
single-process and fresh-process restoration behavior, not power-loss durability
or concurrent backend-process writers.

### Internal resolved council model budgets (partial)

`await council_model_budgets.bind(run_id, expected_revision=...)` pins each
selected council model's resolved context length, maximum completion tokens and
supported parameters, plus the initial input/output budgets and reasoning flag
used by the legacy OpenRouter budgeting formula. The immutable versioned value
retains model order, source revision and the exact saved request-policy digest.
All fields and nested fields are required; invalid or nonpositive budgets fail
closed. No credentials are captured.

A first bind uses `model_context.model_limits` outside the storage lock. That
resolver may refresh the public catalog or use checked-in/default fallback values.
This snapshot records the values actually returned, **not provider-verified
capacity or the catalog-versus-fallback source**. A future request must use these
pinned values and handle provider rejection; it must not silently refresh them.
This internal operation may perform public metadata GETs, but never a completion,
context reduction or other billed request. Offline tests replace metadata reads.

The binder checks completed pre-query/request-policy prerequisites, current loaded
selection/settings, and the resolver's loaded endpoint before resolution, then
rechecks them after awaiting. Stale revisions, cancellation, deletion or changed
configuration prevent a late write. Partial/failed resolution persists nothing.
Concurrent resolvers share the first persisted snapshot even if catalog values
differ; repeated binds never refresh metadata. Failed write acknowledgments are
recoverable through read/rebind. Current configuration is still checked on a
repeat bind, while historical inspection uses only retained evidence and makes
no network calls or writes. Inspection validates source identity, roster and
budget derivation; cancellation retains it and deletion erases it.

These are initial model budgets, not exact wire messages, context-reduction or
continuation checkpoints, request timeout policy, synthesis/helper budgets, full
skill provenance or dispatch permission. The existing legacy execution path is
unchanged and does not consume this snapshot. The current causal validator only
supports the completed pre-query boundary. Remaining guarded execution,
human-input boundaries, scheduling and whole-run acceptance must precede public
run operations and CLI consultation commands; API version stays 0. No claim of
exactly-once provider work, power-loss durability or concurrent process writers
is added.
