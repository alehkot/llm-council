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

## Verification and next step

Offline fixtures exercise all four prompt field types, repeated prompt identities,
strict request/answer types, lifecycle ownership constraints, legacy export
references, stable normalization, byte-for-byte read preservation, and explicit
writes. These are not end-to-end durable run or CLI acceptance tests.

Next: implement durable idempotent run creation/lookup using the existing manager
lock/journal/storage, with lost-reply, duplicate, restart, terminal receipt and
deletion-tombstone tests **before** connecting a Rust client. Keep version 0 until
the complete advertised contract is safe.

Implementation reference: Pydantic [strict fields](https://docs.pydantic.dev/latest/concepts/strict_mode/)
and [forbidden extra data](https://docs.pydantic.dev/latest/concepts/models/#extra-data).
