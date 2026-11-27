# council CLI

The macOS Rust client can run a **basic three-stage consultation** end to end:
parallel member answers, anonymized peer rankings, and chairman synthesis. It
supports persistent pre-query and response-review prompts, reconnect, explicit
retry, per-run cancellation, history, stage inspection, and Markdown export.

This is the separate `cli_basic_api_version: 1` capability. The broader planned
CLI API remains version 0: optional stages and the full acceptance contract are
not complete. Existing web consultations keep their original execution path.

## Build and start

From the repository root:

```sh
cargo build --manifest-path cli/Cargo.toml --locked
# Optional installation on PATH:
cargo install --path cli --locked
```

Run the backend with the supported settings below. These environment overrides
apply to this backend process; they do not edit `.env`. Your existing backend and
OpenRouter credentials remain configured in `.env`.

```sh
MODEL_BACKEND=openrouter SYNTHESIS_BACKEND=openrouter \
NEGATIVE_MODE_ENABLED=false DELIBERATION_ENABLED=false \
SKILLS_ENABLED=true SKILLS_REFERENCE_SELECTION_ENABLED=false \
SKILLS_AUTO_SELECT_ENABLED=false uv run python -m backend.main
```

In another terminal (use `cli/target/debug/council` if not installed):

```sh
council setup
council ask 'Compare these approaches…'
council ask --file question.md
cat question.md | council ask -
council ask 'Work in the background' --detach
```

`setup` verifies the backend key before saving it in the origin-specific macOS
Keychain entry. Keys are never accepted in arguments, environment variables, or
piped stdin. `--backend-url http://127.0.0.1:8002` selects another loopback backend
and its separate credential entry. The backend must already be running.

Pre-query and response-review prompts read the controlling terminal, preserving
piped question input. They remain pending across backend restarts. Skips and
defaults require an explicit choice. `--non-interactive` exits 3 at a prompt or
retry boundary and leaves the run intact; it does not silently skip anything.

## Reconnect and inspect

A reconnect command is printed before submission. Ctrl-C detaches and does not
cancel backend work. During a stalled HTTP request, detach may take up to the
35-second request deadline. After a backend restart, `resume` schedules unfinished
safe work; uncertain provider attempts remain stopped until explicit consent.

```sh
council resume RUN_UUID
council show RUN_UUID
council show RUN_UUID --stage answers
council show RUN_UUID --stage reviews
council show RUN_UUID --stage all
council history
council export RUN_UUID --output answer.md
council cancel RUN_UUID
```

`show` prints the final answer when available. Stage inspection includes saved
member/reviewer output. History lists up to 100 recent basic runs, with each run’s ID, status, date
and a short question excerpt when supplied by the backend. It does not include
stage bodies. Export reuses
the backend Markdown exporter and refuses to overwrite an existing destination.

For a run with a saved council selection, `council show RUN_UUID --json`
includes the original member roster, backend name and selected stage switches
in `provenance.council_selection`. It does not substitute the backend's current
settings, show the final reviewer set or infer a synthesis model. If selection
has not been saved yet, or could not be read, the field is absent.

An uncertain attempt produces an exact consent command:

```sh
council retry RUN_UUID --revision ATTEMPT_UUID
```

This can incur another provider charge. Each token authorizes only that uncertain
operation; completed sibling responses and stages are reused. Repeating a token
does not authorize another attempt. If synthesis reaches the model’s output
limit, the run reports that limit instead of offering a retry. The truncated
text is not shown as the final answer; earlier answers and reviews remain
available. Basic runs cannot continue that response. Cancellation targets one
run and cannot recall a request already accepted by a provider.

`--json` emits one structured result on stdout. Progress and reconnect hints use
stderr. Exit codes: 0 success/detached submission, 1 operation failure, 2 invalid
arguments/missing credential, 3 input or explicit retry needed, 130 detached by
Ctrl-C. Clap help/syntax errors remain text.

## Supported scope and recovery limits

- OpenRouter execution and synthesis, negative mode and deliberation disabled.
- Explicit uploaded skills via `--skill ID`, or no skills. Automatic selection,
  reference routing, external skill sources, and `--refine` are not supported.
- Question text, UTF-8 file, or stdin. Editor launch and multiline terminal prompt
  editing remain deferred; individual prompt lines are limited to 240 bytes.
- Context must fit the saved model budgets. Truncated answers/reviews are retained
  but stop the run; continuation and context-reduction workflows remain deferred.
- Runs use the existing single-process storage lock. Use one backend process,
  not multiple Uvicorn workers. Basic-run concurrency is bounded by
  `CONSULTATION_POOL_SIZE` independently of the legacy web worker pool.
- Private pending submissions live in `~/.council-receipts` and contain the exact
  question, backend origin, and run ID, never credentials. Acknowledgment replaces
  the pending payload. On a lost acknowledgment, resume queries the same ID;
  an absent or incompletely admitted server run permits resending the exact saved
  pending request through the idempotent creation endpoint.
  Deleted server runs remain tombstoned and cannot be recreated by that resend.
- macOS receipt files use full synchronization and reject extended ACLs, unsafe
  modes, and symlinks. Interrupted first writes fail closed; damaged receipts need
  manual recovery. Do not move/replace the receipt directory during submissions.
  No filesystem code establishes power-loss guarantees on every storage device,
  physical erasure from backups, or protection against another process running
  as the same user.

## Verification

```sh
bash .ci/check-cli.sh
uv run python -m unittest discover tests
```

The Rust suite includes an authenticated real-HTTP test against a disposable
Python backend with synthetic provider responses. It requires the repository's
`uv` environment, creates isolated receipt/data directories, and does not access
Keychain or paid providers. Backend tests cover full completion, duplicate
admission/answers, restart while waiting/in flight, explicit retry, synthesis
recovery, cancellation isolation, and deletion. Native Keychain interaction and
paid-provider end-to-end behavior still require separate verification.
