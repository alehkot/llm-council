# Plan

Build a Rust command-line client named `council` for the local LLM Council backend.
Version 1 must complete a consultation without the browser: compose and refine a
question, answer interactive prompts, follow progress, inspect member answers and
reviews, and save or reopen results. Extend the existing backend where reliable
submission, durable human input, and recovery require it.

Date: 2026-11-01. Decision status: **clear for design review**. Implementation has
not started. The decisions below were confirmed with the user; command spellings,
API shapes, and module boundaries are engineering proposals for implementation.

## Scope

- **In:** Rust, ordinary commands with interactive prompts, a backend on this Mac,
  Keychain authentication, the complete consultation loop, history and Markdown
  export, durable waiting, explicit retry, and cancellation of a particular run.
- **Out:** a full-screen terminal UI, remote deployment, backend process
  management, context-aware follow-up consultations, side chats, skill
  administration, synthesis-version management, and direct model-provider calls
  from Rust. Existing skill selection and optional council stages remain usable.

## Action items

- [ ] Define typed consultation request/response contracts, prompt-instance
  identity, lifecycle states, and compatibility fixtures in the backend. Add a
  CLI contract version to `/api/config`; distinguish current endpoints from the
  proposed additions below.
- [ ] Add durable, idempotent run creation and lookup/history by consultation ID,
  reusing `consultation_manager.py`, `consultation_journal.py`, and `storage.py`.
  Cover lost acknowledgments, concurrent duplicates, restart, completed runs,
  and deletion before connecting the Rust client.
- [ ] Implement persistent human-input checkpoints in `langgraph_flow.py` and
  the manager. Persist the exact prompt and resume location, release the worker
  while waiting, and consume each accepted answer once. Recover uncertain model
  execution as `needs_retry`, preserving saved work.
- [ ] Add run-specific cancel and explicit retry operations. Protect mutations
  against stale clients and repeated requests; cancelling one run must leave
  other queued runs intact. Preserve the behavior of existing web submissions.
- [ ] Create one Cargo package under `cli/`, with a `council` binary, HTTP client,
  typed wire data, Keychain setup, and a shared monitor/resume loop. Implement
  `setup`, `ask`, and `resume` against the real backend contract first.
- [ ] Add `show`, `history`, `export`, `cancel`, and installed-skill listing.
  Complete editor/file/stdin input, generic prompt rendering, clean stdout/stderr
  behavior, and JSON output. Reuse backend exports and results.
- [ ] Run the acceptance cases below with fake model responses, Rust formatting,
  Clippy, tests, and the repository's required backend/frontend checks. Exercise
  terminal input and Keychain behavior on macOS; perform an explicitly authorized
  small live consultation only after deterministic checks pass.
- [ ] Document installation with `cargo install --path cli --locked`, first-use
  setup, reconnect/cancel behavior, and recovery limits. Add Cargo checks to CI
  and review the implementation in scoped commits before publishing it.

## Confirmed decisions

| Decision | Choice and consequence |
| --- | --- |
| Language and boundary | Rust client over HTTP; execution, persistence, model routing, and provider credentials stay in the backend. |
| Experience | Ordinary commands with interactive questions. A full-screen UI is outside v1. |
| Completion criterion | Complete the whole consultation, including member-answer and peer-review inspection. A submit/final-only tool is insufficient. |
| Backend changes | Narrow changes for reliable submission and recovery are allowed. |
| Deployment | Local backend on this Mac. Remote connections are deferred. |
| Ctrl-C | Detach the CLI, leave backend work running, and print a reconnect command. |
| Backend unavailable | Show an actionable start command. The CLI does not start or manage Python. |
| Unanswered questions | Wait until answered; preserve pending questions across backend restarts. No silent timeout/skip for CLI runs. |
| Interrupted model work | Restore saved results and ask before retrying work whose outcome is unknown. No automatic repetition of potentially billed work. |
| Reopening | Inspect/export past results or resume unfinished runs. New questions are independent consultations. Context-aware follow-ups are deferred. |
| Default output | Final answer on stdout; progress on stderr; full answers/reviews through `show`. |
| Authentication | One-time hidden entry of the backend API key, stored in macOS Keychain. |
| Delivery milestone | Save and publish this plan and its decisions. Implementation follows a later review/instruction. |

## Proposed command interface

`RUN` means a consultation ID, not a conversation ID or an interactive session ID.
Print the full ID and a copyable reconnect command as soon as a run is submitted.
Commands that mutate work always name a particular run; avoid an implicit
"latest run" target that could change in another terminal.

| Command | Behavior |
| --- | --- |
| `council setup` | Ask for the backend key without echoing it, verify `/api/config`, store it in Keychain, and display the configured council. |
| `council ask "QUESTION"` | Create a fresh consultation, follow it, handle questions, then print the final answer. |
| `council ask --file question.md` | Read the question from a UTF-8 file. `council ask -` reads stdin; argument-free `ask` opens the configured editor. |
| `council ask --refine --file question.md` | Explicitly request the existing backend prompt-refinement workflow. Rust renders its questions and does not perform refinement itself. |
| `council ask --skill SKILL_ID "QUESTION"` | Select an installed skill; repeat the flag for several skills. Validate against backend limits. |
| `council ask --detach --file question.md` | Submit and return a run receipt immediately. Unanswered questions remain pending on the backend. |
| `council resume RUN` | Reattach to running work, answer a pending question, or show a completed result. If interrupted work needs retry, explain what will repeat and ask first. |
| `council show RUN` | Display the saved final answer, or current state if no final answer exists. |
| `council show RUN --stage answers` | Display individual member answers. Also support `reviews`, `negative`, `deliberation`, and `all`; report stages that did not run. |
| `council history` | List saved runs with ID, date, status, and a question/title excerpt; include unfinished work. |
| `council export RUN --output report.md` | Export the backend's existing complete Markdown evidence document. Do not overwrite an existing file without explicit consent. |
| `council cancel RUN` | Cancel this run, including a queued or paused run, without clearing its conversation's entire queue. |
| `council skills` | List selectable installed skills. Uploading, deleting, or managing skill sources is deferred. |

Provide `--json` for machine-readable command results and `--non-interactive` to
suppress terminal questions. These are output/input controls, not a separate
automation workflow or event-stream protocol.

### Input and output rules

- Accept exactly one question source. Reject empty input and enforce the backend's
  character limits before submitting; use Unicode character counts correctly.
- Display progress as stage transitions and completed-member counts. The current
  backend supplies snapshots, so do not promise token-by-token streaming.
- Render `select`, `multi_select`, `text`, and `checkbox` fields from the backend
  prompt schema. Show defaults and required fields; offer skip only when allowed.
  Longer question/refinement text can use the configured editor.
- Read questions from the controlling terminal when stdin contains a piped
  question. If no terminal is available, or `--non-interactive` is set, leave the
  run waiting, print the reconnect information to stderr, and exit with a
  needs-input result. Never invent answers or silently accept defaults.
- Default stdout contains only the final Markdown answer. An explicit detached
  submission returns its receipt instead. JSON mode emits one structured result,
  including run ID and state; progress never contaminates stdout.
- Proposed exit codes: `0` for a successful command, `1` for failure, `2` for
  invalid usage/configuration, `3` for required human input or retry approval,
  and `130` for Ctrl-C detachment. A monitoring timeout never cancels the run.
- Preserve original result text in storage/exports. Neutralize terminal control
  sequences when displaying untrusted model output, prompts, titles, or errors.
  Disable decorative terminal output when redirected and honor `NO_COLOR`.

## Backend contract

The existing backend already offers authenticated configuration, conversation
storage, queued submission, snapshot long polling, interactive field schemas,
skills, and Markdown exports. It does not yet offer the run-level reliability
contract required by the confirmed decisions.

### Proposed HTTP additions

| Operation | Proposed contract |
| --- | --- |
| Create | `PUT /api/consultations/{run_id}` with a client-generated UUID and question, optional negative prompt/skills, and explicit CLI interaction policy. Creates the associated conversation once. |
| History | `GET /api/consultations` returning run summaries from saved conversations and queued/active records, with bounded results. |
| Inspect/monitor | `GET /api/consultations/{run_id}?wait=25&cursor=...` returning a full snapshot for that exact run, including saved terminal results. |
| Answer | `PUT /api/consultations/{run_id}/prompts/{instance_id}/response` accepting the schema's answer values or an allowed skip. |
| Retry | `POST /api/consultations/{run_id}/retry` requiring the current persisted interruption/revision token after explicit consent. Repeating that action cannot start another attempt. |
| Cancel | `POST /api/consultations/{run_id}/cancel`, idempotent and scoped to the named run. |

Reuse `GET /api/config`, skill listing, and the existing conversation Markdown
export route. The run response supplies its conversation ID and assistant-message
index so the CLI can call the export route without guessing an index.

The CLI opts into persistent interaction and explicit crash recovery per run.
Support a per-run refinement request for `--refine`, using the existing refinement
engine. Model selection and optional negative/deliberation settings come from
backend configuration; arbitrary per-run model overrides are outside v1.

### Idempotency and identities

- Persist the accepted request identity and canonical payload before scheduling
  model work. The same run ID and payload return the same run, whether queued,
  running, paused, or terminal. Different payload under the same ID returns `409`.
- Coordinate creation under the backend's existing manager lock and durable
  storage. Recover partially initialized run/conversation creation without making
  another conversation, assistant placeholder, or paid consultation.
- Preserve the receipt after the existing active-job journal is cleaned up;
  completed assistant metadata can retain the necessary identity. Deletion must
  leave a minimal tombstone so a delayed retry cannot resurrect deleted work.
- Keep a private local submission receipt before the first request. Retain the
  pending payload only until the backend acknowledges it, enabling recovery from
  a lost reply without relying on shell scrollback. Store no credentials there.
- A consultation ID identifies the run for its lifetime. Prompt instance IDs
  identify particular questions, including repeated refinement rounds; the
  logical name `prompt_refinement` is insufficient for stale-answer protection.
- Accept a prompt answer durably before resuming execution. The same response
  repeated for an already consumed instance returns its receipt; a conflicting
  or stale response returns `409`. First accepted answer wins if two clients act.
- Publish the run's selected models and effective pipeline settings as provenance.
  Detect configuration changes that invalidate a checkpoint; require resolving
  that conflict rather than silently continuing with different models.

### Lifecycle and recovery

Expose lifecycle separately from the current council stage. Suggested states are
`queued`, `running`, `waiting_input`, `needs_retry`, `complete`, `failed`,
`cancelling`, and `cancelled`.

```mermaid
stateDiagram-v2
    [*] --> queued: accepted durably
    queued --> running: worker available
    running --> waiting_input: question persisted
    waiting_input --> queued: answer accepted durably
    running --> needs_retry: interrupted execution
    needs_retry --> queued: explicit retry consent
    running --> complete: final result saved
    running --> failed: unrecoverable failure
    queued --> cancelling: cancel this run
    running --> cancelling: cancel this run
    waiting_input --> cancelling: cancel this run
    needs_retry --> cancelling: cancel this run
    cancelling --> cancelled: local work stopped
```

Waiting is durable state, not a worker thread held forever on an asyncio queue.
Save the exact prompt, its instance ID, collected responses, and the continuation
point before releasing the worker. Restart must expose that same question without
rerunning title generation, refinement, or completed model stages. Waiting runs
must not block unrelated conversations from using the worker pool; ordering
within the same conversation remains intact.

Before an external model operation, record that it is in flight. On recovery,
retain every persisted result and mark uncertain work as `needs_retry`. A retry
notice names the smallest resumable operation or stage and explains whether any
unsaved work will repeat. Do not claim exactly-once provider execution. Errors
that cannot be repaired by retry remain failures with their saved partial results.

Ctrl-C, closing the terminal, a broken output pipe, or losing the network only
disconnects the client. `resume` does not itself repeat paid work. Cancellation
requests stop local tasks and child processes; an already accepted provider
request may still incur a charge.

### Compatibility

- Preserve existing web routes and submission defaults. Map new persistent runs
  into legacy status responses coherently; do not expose a fake live session ID
  backed only by a lost process-local queue.
- Route legacy interactive responses to durable prompt instances when applicable,
  using the same stale-answer and first-answer rules. An unsupported old client
  must receive an actionable error instead of a false success.
- Normalize saved final answers in one place: current data uses `stage3.current`,
  while older records may contain a flat Stage 3 response. Old history without a
  consultation ID remains readable/exportable through its conversation/message
  reference; do not silently rewrite legacy files during a read.
- Add `cli_api_version` to configuration. A CLI requiring this contract should
  explain that the backend needs updating instead of falling back to unsafe
  submission/retry behavior.
- Keep the existing JSON storage and single-backend-process architecture. A new
  database, distributed worker coordination, or streaming protocol is outside v1.

## Rust implementation proposal

Use one Cargo package. Start with small modules for arguments/dispatch, HTTP and
wire types, monitoring/recovery, terminal prompts/output, and Keychain/setup.
Keep the CLI responsible for presentation and transport; reuse backend business
logic and persistence. A separate reusable SDK crate is unnecessary for v1.

| Responsibility | Suggested implementation |
| --- | --- |
| Commands, validation, help | [`clap` derive](https://docs.rs/clap/latest/clap/) |
| HTTP and connection reuse | One [`reqwest::Client`](https://docs.rs/reqwest/latest/reqwest/) |
| Async monitoring, timers, signals | [`Tokio`](https://docs.rs/tokio/latest/tokio/) |
| Typed JSON contracts | [`serde`](https://docs.rs/serde/latest/serde/) and `serde_json` |
| Select/text/checkbox/editor prompts | [`inquire`](https://docs.rs/inquire/latest/inquire/), with a plain-text fallback where terminal capabilities require it |
| Backend key | macOS Keychain through a native credential-store binding; select the macOS-specific integration rather than an all-platform credential CLI dependency ([Keyring integration guidance](https://docs.rs/keyring/latest/keyring/)) |

Select compatible crate versions and commit `Cargo.lock` during implementation.
The package should produce a normal `council` executable; installation and release
automation beyond the local Cargo install can follow later.

Default to `http://127.0.0.1:8001`; an explicit URL override may select another
loopback address/port. V1 refuses non-loopback destinations. Bind the Keychain
entry to the backend origin, send `X-API-Key`, disable redirects/proxies for this
local client, and never include the key in arguments, receipts, logs, or errors.
Probe authentication once before submission; stop on authentication/lockout
errors instead of repeatedly polling with a rejected key.

Reuse snapshot long polling with an opaque cursor. A request timeout must exceed
the server wait window; for example, wait 25 seconds with a 35-second read timeout.
Retry safe reads with bounded backoff and visible reconnect status. Retry creation
only with the original persisted run ID and identical payload. Do not blindly
retry mutating requests. On a cold connection failure, show the backend start
command: `uv run python -m backend.main` from the project root.

## Acceptance checks

1. **Whole workflow:** using fake providers, create a run, refine the question,
   answer every prompt type, complete answers/reviews/synthesis, inspect member
   results, export Markdown, and reopen the same saved result in a new CLI process.
2. **Lost replies:** drop the creation response and submit the same identity again,
   concurrently and after restart. Exactly one run/conversation/initial council
   execution is scheduled. A different payload conflicts. Check again after
   completion and journal cleanup; a deleted run is not recreated.
3. **Detach:** Ctrl-C prints the run ID/reconnect command and leaves work running.
   Reattach without another submission. Closing stdout or losing a poll must not
   trigger cancellation or repeat generation.
4. **Human waiting:** detach before a question, wait beyond the former timeout,
   restart the backend, then answer. Recover the exact question; consume the answer
   once; rerun no completed work. Unrelated conversations can run while it waits.
5. **Crashes and retries:** inject failures before/after provider completion and
   persistence. Uncertain execution requires consent. Repeating the retry action
   must schedule one attempt. Persisted results and model provenance remain intact.
6. **Concurrent clients/cancellation:** stale or conflicting answers fail clearly.
   Cancel one queued, waiting, or running consultation; retain siblings and their
   results. Cancel/retry races cannot restart a cancelled run.
7. **Terminal/authentication:** test redirected stdout, piped UTF-8 input, no
   controlling terminal, JSON output, terminal escape sequences, wrong/rotated
   keys, Keychain lookup failure, and an offline backend. No credential appears in
   output, arguments, or local receipts; no auth retry storm occurs.
8. **Compatibility:** exercise existing web submissions, legacy final-answer
   shapes, legacy export references, and configuration-version mismatch. Fixture
   tests must make no live model calls. The final manual smoke must distinguish
   successful API orchestration from model-answer quality.

Run `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, and
`cargo test --locked` for the CLI. Run repository-required Ruff, unittest, backend
startup, and frontend format/lint/build checks for implementation changes. This
planning-only change requires document/link review and `git diff --check`.

## Open questions

No blocking product decisions remain. These engineering checks belong to the
first implementation milestone:

- Verify the smallest reliable continuation mechanism for each interactive phase;
  saved stage results alone are not a substitute for an exact pending-prompt
  checkpoint, especially during repeated refinement.
- Confirm terminal-library behavior with piped stdin and a separate controlling
  terminal, and select the macOS Keychain binding before pinning dependencies.
- Finalize typed wire fixtures and legacy-record representation while preserving
  the lifecycle and compatibility guarantees above.

## Inspected implementation anchors

- [API routes and request models](../backend/main.py): authentication, config,
  submission, prompts, status polling, cancellation, and export.
- [Consultation manager](../backend/consultation_manager.py): run IDs, queues,
  worker ownership, snapshots, restoration, and conversation-wide cancellation.
- [Consultation journal](../backend/consultation_journal.py): durable job records
  and atomic JSON replacement; terminal journal cleanup needs special care.
- [Workflow](../backend/langgraph_flow.py): interactive timeout/skip behavior,
  collected responses, resume routing, and saved synthesis versions.
- [Interactive schemas](../backend/interactive.py): the four field types and
  refinement/review/synthesis prompts.
- [Storage](../backend/storage.py) and [Markdown exporter](../backend/export_markdown.py):
  authoritative saved conversations, results, and existing export formatting.
- [Web API client](../frontend/src/api.js): current wire usage and authentication
  failure handling.
- [Long-poll tests](../tests/test_status_longpoll.py) and
  [workflow tests](../tests/test_council_e2e.py): existing regression patterns.
- [Existing Python CLI](../backend/cli.py): maintenance-only commands; it does not
  already provide the proposed HTTP consultation client.

The scope and behavior above were resolved through six challenge-me question
rounds. The remaining risks are implementation risks, principally durable pause
and recovery; they do not change the confirmed workflow or authorize silent
retries of potentially billed work.
