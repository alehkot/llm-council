# council CLI — setup and foundations

The Rust client now connects `council setup` to the local backend and macOS
Keychain. **It cannot run a consultation yet.** The backend still advertises
**CLI API 0**; durable run operations and the monitor/resume loop are unfinished.

## First-use setup

With the existing local backend running, execute the `council` binary in a
terminal on macOS:

```sh
council setup
# An alternate loopback backend gets a separate Keychain entry:
council --backend-url http://127.0.0.1:8002 setup
```

Setup reads the backend API key without echo from the controlling terminal,
verifies `GET /api/config` once, then creates or updates the origin-specific
macOS Keychain entry. Authentication, network, malformed configuration and
unsupported-version failures leave the previous entry untouched. It displays
the configured council and chairman, and explicitly reports that consultation
commands are unavailable. API 0 or 1 is accepted **only for credential setup**;
future versions are rejected. The separate execution-capability probe still
requires exact API 1. No legacy submission route is used.

Keys are never accepted through command arguments, environment variables or
piped stdin, and there is no plaintext credential fallback. `--non-interactive`
returns exit 3 before prompting, network access or storage. Unsupported platforms
fail before prompting or contacting the backend. `--json` emits one structured
result/error on stdout; the hidden prompt uses the controlling terminal, not
stdout. Native Keychain errors are sanitized. Actual macOS Keychain/runtime
verification remains outstanding; Linux tests use an injected credential store.

`--help` and `--version` work. Other commands (`ask`, `resume`, `show`, `history`,
`export`, `cancel`, `skills`) deliberately exit **2** with `not_implemented`,
without reading question files, sending requests, launching editors, accessing
Keychain or writing exports. Invalid configuration exits 2. Setup failures exit
1, unavailable/noninteractive input exits 3, and cancelled key entry exits 130.
Clap syntax/help diagnostics remain text, including with `--json`.

This is source implementation, not an installed or released client. Do not enable
run submission, resume, answer, retry or cancellation until the durable backend
acceptance cases in [PLAN.md](PLAN.md) pass.

## Development checks

Use a Rust toolchain with Cargo, rustfmt and Clippy (declared minimum Rust 1.89;
the initial Linux verification used 1.99.1). Dependencies are recorded in
`Cargo.lock`. From the repository root:

```sh
bash .ci/check-cli.sh
```

This runs formatting, Clippy and focused CLI tests, not a full application or
release build. For an already populated dependency cache, add `--offline` to the
Cargo commands in that script. Normal Woodpecker runs the same script after the
existing backend/frontend checks; its runner needs the Rust tools already
available. No workflow trigger, secret, permission or runner setup is changed.
Do not manually dispatch/rerun CI to obtain a result.

Offline tests cover planned command forms and invalid combinations, real binary
help/error output, strict backend-fixture decoding, request defaults and policy
opt-ins, lifecycle/identity consistency, Unicode code-point limits, bounded reads,
terminal escape display copies and fail-closed configuration/version checks.
Transport tests additionally exercise real local HTTP requests with synthetic
keys, authentication lockout, redirection refusal, response bounds and protocol
errors. Setup tests cover verification-before-storage and preservation of the
previous entry on failure. The optional [PTY check](tests/manual/README.md)
exercises real hidden input, editing, cancellation, restored terminal settings,
and refusal of piped input with synthetic keys. These checks do **not** establish
working consultation orchestration, safe provider retries,
native macOS Keychain/runtime, editor behavior, model quality, or macOS
compatibility. No real credentials or provider calls are used.

## Boundaries for the next implementation

- One package with the `council` binary; no reusable SDK/workspace split.
- Backend origins accept HTTP(S) loopback addresses only. `localhost` is pinned
  to `127.0.0.1`, avoiding host/DNS overrides. Credentials, non-root paths,
  queries, fragments and port zero are rejected without echoing the URL.
  `ConfigClient` binds an in-memory key to this validated origin. Its sole
  request is `GET /api/config`; redirects, proxies and automatic retries are
  disabled. TLS certificate/hostname verification remains enabled. No caller can
  supply an arbitrary request URL or mutate this client to another origin.
- Configuration responses are capped at 1 MiB, including chunked responses.
  The total request deadline is 35 seconds with a five-second connection timeout.
  Errors discard server bodies and underlying transport diagnostics rather than
  echoing credentials or server-controlled text. HTTP 401/403/429 latch the
  client closed: subsequent probes make no request until explicitly replaced.
  The transport itself does not save keys; setup stores only after verification.
- Wire models use the shared synthetic files in `tests/fixtures/cli`. Request
  validation must apply the authenticated backend's current limits before any
  submission; structural decoding alone has no fixed deployment-specific cap.
  Prompt field requirements/options and first-answer semantics remain backend
  responsibilities. Never infer execution support from parsing draft v1 data.
- Input keeps exact original text. Character limits match Python code points,
  not UTF-8 bytes or displayed graphemes. Display escaping must never alter
  saved model text, accepted answer receipts or exported Markdown.
- Setup now uses the configuration transport and native macOS Keychain binding.
  Next verify it on macOS, then implement private idempotent submission receipts,
  prompt rendering and the monitor loop against the completed durable backend.
  Backend continuation/provenance/in-flight recovery work may proceed alongside
  offline Rust work; it must be complete before live durable run operations.

References: [clap](https://docs.rs/clap/latest/clap/),
[Serde](https://serde.rs/), [URL parsing](https://docs.rs/url/latest/url/),
[official Rust installation](https://rust-lang.org/tools/install/),
[reqwest client configuration](https://docs.rs/reqwest/0.13.5/reqwest/struct.ClientBuilder.html).

Credential integration references: [security-framework password APIs](https://docs.rs/security-framework/3.7.0/security_framework/passwords/index.html),
[nix termios APIs](https://docs.rs/nix/0.30.1/nix/sys/termios/index.html).

## Experimental offline submission records

An **unwired Unix-only** receipt-store primitive is available for development
and tests. It creates no directory, network request, credential entry, CLI
command or API capability. It requires an already-created directory with owner-only POSIX mode bits
and pins its directory descriptor. Mode bits alone do not exclude access through
macOS ACLs or establish a secure storage root. It stores the exact serialized typed request
and loopback origin before submission could be considered, rejects conflicting
identities, and replaces the pending payload with a matching acknowledgment only
when the caller has independently validated that acknowledgment. Operations use
an advisory lock among cooperating clients; a busy lock is an error, not a cue
to send without a receipt. Stored content can contain private user questions and
must be treated accordingly. No credential field is part of the typed request;
users can nevertheless type sensitive content into a question.

This primitive is **not ready for live submission**. A crash while the first
record is being written can leave a partial file that fails closed and requires
manual recovery; it never permits a send. An interrupted acknowledgment can leave
an owner-only temporary receipt file. Logical replacement does not guarantee
physical erasure from storage, backups or snapshots. Tests simulate selected
sync errors but not actual power loss or arbitrary filesystem behavior. Linux
checks do not verify macOS: macOS `fsync` may not flush device caches, and the
additional platform-specific durability work and tests remain open. ACL validation,
trusted stable parent/root provisioning and reopening the same directory after an
ancestor is renamed are also unresolved. Do not wire
this store to `ask` or enable the run API before the backend contract and native
platform persistence semantics are complete and independently verified.

References: [Rust file locking](https://doc.rust-lang.org/std/fs/struct.File.html#method.try_lock),
[Apple fsync notes](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html).
