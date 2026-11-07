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

## Read-only archived skill rendering (internal)

`run_skill_rendering.render(run_id, selected_sections, max_chars=..., reasons=...)`
creates a candidate skill prompt block and detached packing report from the
already-persisted explicit uploaded-skill archive. It neither captures missing
content nor reads current skill files, and it writes no receipt, routing decision
or final prompt. Existing library-based rendering and archived rendering share
the same deterministic packing core; legacy callers retain their current readers
and behavior. No global reader monkeypatching is used by the implementation.

Callers must explicitly name selected references/section IDs. Unknown skills,
references or sections, duplicate/ambiguous selections, malformed reasons and
incoherent archived reference metadata fail closed rather than silently changing
the request. Empty section IDs explicitly select the whole file. A version-1
archive's known synthetic legacy section `1` also retains whole-file semantics;
other IDs in that synthetic index are rejected. Modern heading/section metadata
must match the archived text. File and section order are preserved independently
of caller ordering, and Unicode character counts follow the existing renderer.

The report keeps the existing whole-section dropping, body-truncation markers,
selected headings and reasons. It adds renderer version `1` and archived source
IDs/digests. Bundle inventory is explicitly `not_archived`, rather than inventing
zero counts for scripts/assets/evals. A zero character budget returns no block;
a positive budget too small for the mandatory framing fails instead of returning
an over-budget prompt. The legacy renderer's budget behavior is unchanged.

The same archive yields the same candidate under this renderer version after
library replacement/disabling/deletion or process restart. Cancelled run history
can be previewed; deleted runs reject new loads. A returned in-memory copy is not
revoked by a later deletion or cancellation and never proves the run is runnable.
This is neither permission to execute an old/disabled skill nor a final prompt
provenance record. Automatic/reference selection decisions, retained stage
settings and final rendered prompts, guarded dispatch and public integration
remain pending. No scripts, model calls, worker scheduling, API1 or Rust run
commands are enabled.

## Pinned deterministic skill preparation (internal, router disabled)

`run_skill_plan.bind(run_id, expected_revision=...)` completes one provider-free
preparation path for already-archived, explicitly selected uploaded skills. It
requires a saved completed pre-query response policy, an exact current revision,
`SKILLS_ENABLED=true` and `SKILLS_REFERENCE_SELECTION_ENABLED=false`. It does not
change either setting or silently bypass the normally enabled model router.
Automatic skill selection, enabled reference routing and router-failure fallback
remain unsupported by this operation.

Version 1 applies the existing deterministic keyword-overlap fallback to archived
reference candidates and the saved finalized question. It reserves the Stage 1
space already occupied by skill bodies, respects configured reference-file and
selectable-candidate caps, and retains ordered candidate IDs plus grouped
reference choices. The setting named `max_reference_sections` counts indexed
candidates: a legacy synthetic whole-file candidate counts once even when its
archived text contains several heading sections, preserving the existing behavior.
It then saves the exact Stage 1 and Stage 3 **skill blocks**, with their distinct
character budgets and packing reports. These are only the skill portions of
future prompts, not complete stage messages or provider-wire inputs. Selected
references may subsequently be dropped by either packer; each exact report shows
what was actually included. Zero caps/budgets remain explicit decisions.

The frozen result carries saved settings, response-policy and archived-content
digests, source revision, reference budget and both rendered blocks. Reports are
retained as canonical JSON strings; `RenderedSkillBlock.report()` returns a
detached dictionary. Repeat binding rejects current relevant-setting drift, stale
revisions, cancellation and deletion. It never reads the mutable skill library
or calls a provider. Concurrent supported calls serialize under the existing
storage lock and write once; lost write acknowledgments are recoverable.

`run_skill_plan.load` and inspection use historical settings/content rather than
current configuration. Validation recomputes the deterministic version-1
derivation without side effects and fails closed if saved identities, choices,
rendered text or reports differ. It neither repairs nor silently refreshes saved
work. Future changes to fallback/rendering semantics need explicit version
handling. The digest is an identity, not tamper-proof storage or proof that a
setting was authorized. Inspection exposes counts, lengths and an aggregate
digest, not copied private skill text. Cancellation retains historical reads;
conversation deletion erases the plan through the existing tombstone.

No execution path consumes this plan yet. Future dispatch must use the saved
blocks, establish complete stage/provider provenance and recheck runnable state;
loading a plan does not authorize using an old/disabled skill, model retries or
paid work. Public run APIs, Rust consultation commands, scheduler and API1 remain
gated. Existing legacy routing/execution is unchanged. Power-loss/multiprocess
safety and public polling/storage performance remain unverified.

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

### Internal initial Stage 1 inputs (partial)

`run_stage1_inputs.bind(run_id, expected_revision=...)` pins complete initial
Stage 1 messages and each council member's OpenRouter JSON body from the saved
response policy, explicit-skill plan, request policy and resolved model budgets.
All prerequisites must already exist. Version 1 supports the same archived
explicit-skill, already-disabled reference-router mode as the saved skill plan;
it does not bypass automatic selection or an enabled router, change settings, or
silently omit requested skills.

Skill instructions come first and the saved interactive modifier last, trimmed
and separated by blank lines just as in the legacy Stage 1 composer. The saved
finalized user question is preserved verbatim. Empty system instructions omit
the system message. Member order, endpoint, the initial 120-second timeout,
output budgets, and the optional high-effort reasoning field are retained.
Canonical JSON bodies are immutable strings with detached dictionary access;
authorization headers and provider credentials are not captured. These are exact
JSON values for the initial request, not serialized HTTP bytes or a guarantee
that a provider will accept them.

The existing conservative UTF-8/framing token bound must fit **every** saved
member input budget. Equality fits; a smaller budget rejects the whole bind
without writing partial requests, truncating text, reducing context or calling a
model. Metadata is not refreshed. The bound is not a model tokenizer, and saved
metadata may come from fallback limits rather than the provider. Oversized
inputs still need a separately guarded context-reduction implementation.

Initial and repeated binds compare current loaded council/request/skill settings
against the saved sources. Relevant drift, incomplete pre-query work, stale
revisions, cancellation/deletion or inconsistent evidence fail closed. Binding
holds the existing storage lock, concurrent duplicates write once, and an
uncertain write acknowledgment is recoverable by read/rebind. Historical loads
validate exact version-1 derivation using retained data only, without current
configuration, library files, metadata reads or writes. Unsupported semantic
changes require explicit version handling. Inspection adds a compact digest and
counts rather than copying private payloads. Cancellation retains historical
inputs; conversation deletion erases them through the existing tombstone. A
previously returned copy remains historical evidence, not permission to dispatch.

The internal member-attempt guard below can consume this plan explicitly; no
public API does so; the explicit internal worker described below can select the
initial member adapter. Preparation does not perform any HTTP
request, schedule a worker, advance an interaction, accept a prompt, or authorize
retries, continuation or context-reduction calls. A future durable executor must
use saved inputs and separately recheck revision/cancellation under an owned,
persisted per-call guard. Configured retry limits do not override explicit retry
consent for uncertain work. Later-stage inputs, remaining human checkpoints,
automatic/model-based selection, scheduling and full CLI integration remain
unfinished; API version stays 0. No exactly-once, power-loss or multiprocess
writer guarantee is made. As with other receipt digests, identities are not
tamper-proof storage or authorization tokens.

### Internal guarded initial Stage 1 attempts (partial)

`run_stage1_execution.execute(run_id, model, provider, provider_key=...,
expected_revision=..., retry_token=...)` explicitly guards one initial member
request from the pinned Stage 1 plan. It persists the exact plan identity,
revision, non-secret adapter version, member and unique attempt before invoking
the injected callback. The callback receives the saved endpoint, timeout and a
detached copy of the exact JSON payload. No current library/catalog lookup,
implicit input binding or legacy provider fallback is performed.

This is an internal callback seam, **not an automatically selected HTTP adapter**. The caller
must supply a trusted adapter that makes one request, honors the supplied timeout
and performs no retries, context reduction or continuation calls. The guard
guarantees one callback invocation per owned attempt, not what arbitrary injected
code does inside it. No public routes, startup hooks or automatic member loops invoke it. The
explicit internal manager handoff below selects the bounded adapter. Configured retry limits do not override explicit uncertain-work consent.

Distinct members can run concurrently. Process-wide ownership prevents a second
live invocation of the same member, including across local event loops. A lost
process/callback error/caller cancellation or unsupported response leaves its
unowned in-flight attempt uncertain. Inspection reports an exact per-member retry
token; only that token can authorize a replacement attempt. Repeated consent can
observe its active/result state without creating another call. Stale tokens,
tokens for another member, changed adapter identities, stale revisions and source
or current-configuration drift fail closed. No configured backoff or automatic
retry runs. Completed siblings are retained and never implicitly rerun.

Normalized responses require nonblank text and a `stop` or `length` finish reason;
optional reasoning details and usage are finite JSON metadata. Stored normalized
responses are capped at 2 MiB. Unknown fields, unsupported finishes and malformed
responses fail without saving success. HTTP adapters must separately
bound wire-response reads and validate their protocol envelopes. The explicit
OpenRouter adapter below supplies these checks. A `length` result
retains its exact partial text/metadata and reports `needs_continuation`; it is
never treated as a finished answer or replayed as an uncertain initial request.
Continuation execution remains unsupported. Metadata and response text are
excluded from compact polling summaries and error wrappers.

The storage lock protects attempt admission, source checks, response persistence
and cancellation/deletion decisions. Settings/revision/cancellation are rechecked
immediately before callback entry. Completion validates the saved causal inputs
again but does not replace them with settings changed while a request was in
flight. Failed pre-dispatch persistence never invokes the callback, including a
lost write acknowledgment. Failed completion persistence leaves uncertainty;
if the write actually landed, reloading finds its completed result without replay.

Cancellation registers and signals the specific provider tasks, preserving
previously saved member results. A cancelling callback retains local ownership
until cleanup finishes; late responses cannot commit after cancellation or
deletion. Cancellation cannot prove a remote request stopped or avoid charges.
Deletion erases all receipt results and prevents resurrection. Read-only
`load_result` returns detached historical values even after cancellation or live
configuration/library changes, and performs no scheduling, repair or writes.

Inspection distinguishes running members, uncertain members, partial Stage 1,
needed continuations and completed initial Stage 1. Retry tokens live in the
member summary, not the refinement-specific top-level retry slot. Stage 1
completion is not council completion. Results remain in the durable receipt;
legacy conversation stage fields and exports are not updated by this guard.
Interaction revision remains at the saved
pre-query boundary; later prompts need deliberate causal-validation extensions.
Calling this coroutine directly does not implement client detachment: cancelling
its caller cancels the internal attempt, so future public clients require a
separate worker-owner layer. API0 and all public consultation gates remain.
The internal guard shields its owned callback wait so the callback cannot turn
caller cancellation into saved success by suppressing `CancelledError`. It
signals that callback once and retains ownership until cleanup ends, even when
the caller is cancelled again during cleanup. Any late result is discarded.
This does not establish power-loss durability, multi-process ownership,
exactly-once provider execution or complete consultation orchestration.

### Internal initial Stage 1 OpenRouter adapter (partial)

`run_stage1_provider.execute_openrouter(run_id, model, api_key=...,
expected_revision=..., retry_token=...)` explicitly invokes the member guard with
the versioned, non-secret identity `openrouter-stage1-v1`. It uses only the
guard-supplied saved endpoint, 120-second timeout and exact JSON payload. No model
catalog lookup, mutable prompt composition, new skill selection, provider
fallback or implicit input binding occurs. Existing attempts bound to another
adapter identity cannot be adopted retrospectively.

One guarded attempt makes one HTTP POST with an ephemeral bearer credential.
The credential is validated before durable mutation and passed only as the
authorization header, not copied into request settings, receipts, adapter identity
or error messages. Credential
rotation is allowed independently of saved request inputs. HTTPX uses no default
transport retries, environment proxies or redirects. An overall asynchronous
deadline covers the request and body read in addition to HTTPX's per-operation
timeouts. Cancellation waits for transport cleanup through the existing guard;
this is not proof that the remote provider stopped or did not charge.

The adapter requests identity encoding and rejects any nonidentity
`Content-Encoding` before decoding/reading the body. Compressed responses are
deliberately unsupported so the response-size check cannot be bypassed by
decompression expansion. Retained response bytes are bounded at 2 MiB before
each append; normalized results independently retain their existing 2 MiB cap.
UTF-8 JSON must have no duplicate keys or nonfinite numbers, including overflowed
numbers in otherwise ignored fields. It must contain exactly one assistant
choice, a nonblank text response and `stop` or `length`. Error envelopes, refusal,
tool/function calls, other finish reasons and malformed reasoning/usage metadata
fail closed. Optional reasoning details and usage are preserved without
rewriting text; unrelated valid envelope metadata is ignored.

HTTP errors, redirects, timeouts, invalid/oversized responses and interruption
leave the attempt uncertain, requiring its exact retry token. No automatic
replay, context reduction or continuation runs. A `length` response is saved as
partial work, never a final answer. Repeated calls return already saved results
without another POST, subject to the guard's normal current-state checks;
historical reads still use its separate read-only interface.

The optional injected HTTP transport is a trusted offline-test seam; an arbitrary
transport could ignore cancellation or perform hidden retries, which this
adapter cannot police. No public route, startup hook, automatic configured worker or CLI
consultation command selects this adapter. The explicit internal manager handoff
below can select it for one named initial member. Public API version remains 0. The
remaining stages, context reduction/continuations, human boundaries, independent
worker ownership, receipt-to-export integration and whole-consultation acceptance
remain incomplete.

### Internal first Stage 1 continuation inputs (partial)

`run_stage1_continuation_inputs.bind(run_id, model, expected_revision=...)`
prepares only the first continuation for a specific member. It requires saved
initial inputs, a completed initial `length` response and a saved policy allowing
at least one continuation. A stopped, missing or uncertain initial response is
not eligible. The original initial operation, retry ancestry, partial text,
reasoning and usage remain unchanged. Preparing inputs is neither execution
permission nor a retry of the initial request.

The immutable per-member checkpoint binds the saved source revision, initial
input digest, full initial operation digest, attempt ID, normalized response
digest and initial adapter identity. It copies the exact original endpoint,
120-second timeout, model, output limit and reasoning setting. Its JSON payload
retains the original messages and appends the exact assistant partial and its
nonempty reasoning details, then the existing continuation instruction. Usage
does not enter the prompt. There is no mutable library/model-catalog lookup,
provider call or implicit initial input binding.

The conservative UTF-8 token bound covers the full continued messages, including
serialized reasoning metadata. It must be less than or equal to the saved input
budget; the saved output budget is not reduced. Overflow fails without writing,
truncating text or calling legacy context reduction. A first-continuation input
checkpoint does not promise later continuations or that a provider will accept
the saved fallback/catalog budgets.

Current configuration, revision, cancellation and deletion are checked before
binding, including duplicate binds. Concurrent duplicate requests write once;
different members retain separate entries in original member order. A failed
write leaves no execution, and a lost write acknowledgment can be recovered by
the same bind. No state transition, retry token or owned provider task is created.
The existing initial execution helper still returns its original partial result.

Read-only `load(run_id, model)` reconstructs and validates historical inputs from
the retained sources without current settings and returns detached payloads.
Cancellation retains these historical inputs; deletion erases them with the
rest of the receipt. Changed initial sources, result/attempt/retry ancestry or
adapter identity invalidate saved continuation inputs. Inspection validates the
checkpoint and exposes only member identities, digests and token bounds, not
partial text, reasoning or payloads. An initializing record cannot contain one.

The separately invoked first-continuation guard below adds uncertain retry
consent for one named turn, and an explicitly invoked wire adapter is described
below. Further turns, aggregation, context reduction, worker ownership, exports
and public API remain unimplemented.
API version stays 0. Public consultation commands remain gated.

### Internal guarded first Stage 1 continuation (partial)

`run_stage1_continuation_execution.execute(run_id, model, provider, turn=1,
provider_key=..., expected_revision=..., retry_token=...)` guards exactly one
explicitly named first-continuation turn using its already saved inputs. The turn
argument is required; only the integer 1 is supported. It never infers a next
turn, binds missing inputs or invokes a legacy provider. The trusted injected
callback receives the exact saved endpoint, timeout and detached JSON payload,
and must make one request without hidden retries or further calls. The guard
owns one callback invocation, not arbitrary network behavior inside it. The
explicit wire adapter below supplies the bounded request implementation; no
worker, route or automatic loop selects this helper.

Attempts live in a separate continuation execution record. Initial operations,
partial text, reasoning, usage and retry ancestry remain unchanged. Each new
operation binds one member/turn to its exact prepared-request digest and is
persisted before callback dispatch. The collection of prepared sibling requests
may grow while an attempt runs; this does not invalidate an unchanged member's
input. Different members can run concurrently, but process-wide ownership
prevents duplicate live execution of one member/turn across local event loops.

Only the current uncertain continuation attempt token permits a replacement
attempt. Initial-attempt and sibling tokens never authorize continuation retry;
saved continuation attempt/retry identities also cannot reuse initial identities.
Repeated consent observes the same live or completed turn without starting more
work. A saved result, including `length`, is returned for that same turn without
another callback. Configured retry counts or continuation allowance never imply
automatic repeat or next-turn permission.

Admission and pre-dispatch checks cover revision, current configuration,
cancellation, exact saved request, adapter identity and full owned operation.
Completion checks the same saved causal input and owned operation, without
discarding a valid response merely because live settings changed during the
call. New sibling input/result writes are merged under the shared storage lock.
Pre-dispatch persistence failure prevents calls. Completion write failure leaves
uncertainty unless the write landed; a lost acknowledgment can be recovered by
reloading the saved result without repeating paid work.

The existing finite-JSON, nonblank-text and 2 MiB normalized-result contract is
reused. Stop and length chunks are retained separately, never concatenated with
the initial partial or declared a full consultation result. A length chunk
reports `needs_continuation`, but no second turn is implemented. Historical
`load_result(run_id, model, turn=1)` is detached and read-only after cancellation
or live configuration drift. Deletion erases chunks and prevents resurrection.

Run cancellation signals registered initial and continuation tasks without
cross-run effects. Shielded caller cancellation sends one signal, retains local
ownership through repeated cleanup cancellation, discards late responses and
re-raises cancellation even if the callback suppresses it. Durable cancellation
or deletion prevents late commits. This cannot prove that remote execution or
billing stopped; uncooperative callbacks can keep ownership during cleanup.

Inspection exposes a separate compact member/turn summary, including current
retry tokens but no response text, reasoning or usage. Owned continuation work
reports `running_stage1_continuation`; uncertain continuation work contributes to
`needs_retry`. Cancellation takes precedence, and any live initial or continuation
work remains visible as owned. Completion of this turn does not rewrite initial
results. Once no work is running or uncertain, the retained initial partial still
yields `stage1_needs_continuation` until an aggregation/stage completion contract
is implemented. No aggregate answer, exports, public detach,
API1 or CLI consultation command is enabled. Power-loss, multi-process and
remote exactly-once guarantees remain outside this step.

### Internal first Stage 1 continuation OpenRouter adapter (partial)

`run_stage1_continuation_provider.execute_openrouter(run_id, model, turn=1,
api_key=..., expected_revision=..., retry_token=...)` explicitly invokes the
separate continuation guard with identity `openrouter-stage1-continuation-v1`.
The integer turn argument is required. A missing, boolean, alternate or later
turn is not inferred or accepted. The adapter does not bind inputs or execute an
initial request. The original initial operation, partial, reasoning, usage and
retry ancestry remain unchanged.

One guarded attempt posts the exact saved first-continuation endpoint, timeout
and JSON payload. The original messages, partial text and reasoning, continuation
instruction, model and settings are not recomposed from mutable configuration.
The initial adapter's single-request implementation is shared without changing
its identity or wire behavior: no transport retries, redirects, environment
proxies, fallback, context reduction or extra completion request. Ephemeral
credentials are validated before durable mutation and may rotate independently;
they are not saved in receipts or surfaced in errors. Request and body reads share
the whole-request deadline. The same identity-encoding rule, 2 MiB wire and
normalized limits, finite duplicate-free UTF-8 JSON, one-choice assistant envelope
and stop/length-only text contract apply to both adapters.

The continuation attempt is saved before any HTTP call. HTTP/protocol failure,
timeout and interruption leave this turn uncertain, requiring its own current
retry token. Initial or sibling tokens cannot authorize its retry, nor can this
adapter adopt a continuation saved under another callback identity. A successful
stop or length response is a separate retained chunk, never an aggregate or a
replacement for the initial partial. Repeated invocation or repeated valid consent
returns the same saved chunk without another POST, subject to current-state guard
checks. Even a length chunk does not authorize a second turn. Historical reads
remain available through `load_result` without invoking this adapter.

The existing guard owns cancellation and deletion checks, waits for transport
cleanup and rejects late results. Cleanup may outlast a nominal timeout for an
uncooperative injected transport; local cancellation does not prove remote work
or billing stopped. The injected transport is a trusted offline-test seam.
No public route, worker, startup hook, automatic loop or CLI consultation command
selects this adapter. No aggregation, further-turn/context-reduction strategy,
exports, public detachment or whole-consultation completion is implemented here.
API version remains 0.

### Internal complete Stage 1 member projection (partial)

`run_stage1_results.load_complete_result(run_id, model)` is an explicitly invoked,
read-only historical projection. It validates the retained run, initial inputs,
execution receipts and any continuation ancestry together under the existing
storage lock. For a saved initial `stop`, it returns that exact content. For an
initial `length` followed by a validated first-continuation `stop`, it concatenates
the two content strings in order with no trimming, separator, deduplication or
rewriting. This matches the legacy content concatenation rule; a `stop` marker
establishes transport completion, not factual quality or absence of repetition.

Unstarted, uncertain or length-only work returns `None`, including a prepared but
unexecuted continuation or a first continuation that also ended with `length`.
No missing chunk is replaced by a sibling's response. Unknown members or absent
inputs conflict; unknown/deleted/corrupt runs fail closed using the existing
admission errors. Cancelled runs can still expose retained complete member text
as historical evidence. This never changes the run's cancellation or completion
state. Live settings, credentials and provider availability are not consulted.

The detached result preserves ordered original chunks with request digests,
provider identities, attempt/retry identities, finish reasons, reasoning and
usage. Reasoning and arbitrary finite-JSON usage remain per chunk: this helper
does not sum usage, splice reasoning, infer missing metadata or copy the final
chunk's metadata onto earlier work. Each source response retains its 2 MiB cap;
two valid chunks can yield more than 2 MiB of aggregate text, bounded by the two
source caps. Returned nested metadata cannot modify persisted records or future
reads. Content and chunks are excluded from the result's ordinary representation.

No record is persisted or repaired, no cancellation is settled and no provider
work is dispatched. The whole-roster projection below now distinguishes retained
initial partials from complete continued answers. Later turns, guarded context
reduction, later stages, public run operations and worker integration remain
unfinished. API version remains 0 and CLI consultation commands stay gated.


### Internal whole-roster Stage 1 state and results (partial)

`run_inspection.inspect(run_id)["stage1"]` combines the saved initial roster with
validated initial and first-continuation attempts. It is `None` until initial
inputs are saved. Each member retains council order and exposes a compact phase,
request turn, whether that request has saved inputs, and its exact retry token
only for uncertain work. Counts include every member, including unstarted ones;
no quorum or sibling substitution counts as completion. No prompts, answer text,
reasoning, usage or credentials are copied into this section.

An initial `stop`, or initial `length` plus first-continuation `stop`, makes that
member complete. Initial and continuation evidence remain separately visible
without mutation. A length-only first continuation reports needed turn 2 with no
prepared input or retry token; later turns remain unimplemented. Saved input is
not execution permission, and a reported need does not establish that policy or
context budgets allow another request. The guarded bind/execute operations still
perform their own validation.

The combined phase is `ready`, `running_initial`, `running_continuation`,
`needs_retry`, `needs_continuation`, `partial` or `complete`. Active continuations
then active initial requests take display precedence, followed by uncertainty,
continuation needs and all-member completion. Per-member states and counts retain
other simultaneous needs. The inspector's internal Stage 1 phase now uses this
combined view, so a completed continuation cannot leave its old initial partial
incorrectly blocking Stage 1 completion. Cancellation/cancelling still overrides
the run-level phase while retained evidence stays readable. `stage1_complete` is
not whole-council or run completion and does not advance execution.

`run_stage1_results.load_complete_results(run_id)` reads one coherent whole-roster
view under the existing storage lock. It returns a detached tuple of the exact
member results described above in saved council order, or `None` if any member
is incomplete. Validation of every saved source occurs before partial-return
logic, so an incomplete first member cannot hide corrupt sibling evidence.
Historical reads use no current settings/library/catalog, write nothing, schedule
nothing and preserve cancellation. Deleted or incompatible runs fail closed.
Existing single-member reads reuse the same projection logic.

This is read-only integration, not a persisted stage transition, scheduler,
public API, export update or authorization to call another model. Ephemeral local
ownership changes polling cursors without rewriting receipts; it is not a
cross-process ownership guarantee. Full guarded remaining stages, human-input
boundaries and acceptance remain pending. API version stays 0.


### Internal manager-owned initial Stage 1 member (partial)

`ConsultationManager.schedule_cli_stage1_member(run_id, model,
expected_revision=..., api_key=..., retry_token=...)` explicitly schedules one
named initial-member boundary on the existing thread pool. Inputs must already
be pinned. The worker invokes the bounded OpenRouter adapter through its existing
durable attempt guard and returns a detached inspection snapshot, not a new
public run capability. It never loops over the roster, binds missing inputs,
automatically retries uncertainty, dispatches continuations or starts later stages.
A partial response releases the slot and stays partial. A repeated completed
member call uses the guard's saved-result path without another provider request.

Ownership is keyed by run and member. Exact active revision/consent duplicates
share the original handle and its ephemeral credential; conflicting revision or
consent fails. Other members can be explicitly scheduled separately. Handle
`wait()` shields executor ownership from caller cancellation and returns detached
copies. A later failed worker remains observable through the handle even when an
earlier waiter detached. Completed handles are removed from the manager. Credentials are not fields of the handle or durable receipt; execution objects
and retained failure tracebacks may still reference the caller-supplied key in
process memory. This is not a secure memory-erasure guarantee.

Queueing does not change or rebind saved inputs. The existing guard rechecks
revision, runnable admission, cancellation/deletion, configuration and exact saved
causal inputs when the queued worker starts. Relevant configuration changes while
queued therefore fail closed rather than selecting new wire inputs. Run-specific
cancellation still persists its marker and signals active provider ownership;
queued cancelled/deleted work cannot dispatch. It cannot guarantee remote request
cancellation or avoid charges after a provider accepted a request.

A member step refuses legacy conversation work/queues or an owned refinement
step. Legacy submission refuses a conversation with active/queued member work.
This is the single manager's process-local exclusion, not a complete public
conversation-ordering contract or cross-process coordination. Direct internal
guard calls keep their own duplicate-attempt protection.

The queue/handle is not durable: a crash before dispatch leaves the original saved
boundary; after dispatch it retains completed evidence or explicit uncertainty.
There is no startup scheduling, public HTTP route, version upgrade, CLI run-command
connection or full consultation acceptance. Later turns, context reduction,
remaining guarded stages and human input, durable scheduling and export integration
remain pending. Full builds, live-provider, macOS and Rust verification are unrun.
