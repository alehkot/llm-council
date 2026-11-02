# council Rust foundation

The Rust implementation has started. This package currently provides command
parsing, draft typed wire contracts, local-origin/configuration validation,
bounded UTF-8 input, display-only terminal escaping and an origin-bound HTTP
configuration probe, with synthetic regression tests. **It cannot run a
consultation yet.**

`--help` and `--version` work. All operational commands (`setup`, `ask`, `resume`,
`show`, `history`, `export`, `cancel`, `skills`) deliberately exit **2** with a
`not_implemented` error. They send no HTTP request, read no question file, launch
no editor, write no output file and access no credential store. `--json` emits one
error object on stdout; otherwise the diagnostic goes to stderr. Invalid
configuration also exits 2. Clap syntax/help diagnostics use its normal text
format, including when `--json` is supplied.

This is an implementable foundation, not an installed or released client. The
backend still advertises **CLI API 0**. Do not enable run submission, resume,
answer, retry or cancellation until the durable backend acceptance cases in
[PLAN.md](PLAN.md) work. The configuration helper accepts only exact API version 1
with positive limits; absent version means legacy version 0. The reusable
transport probes `/api/config` in synthetic loopback-server tests; command
dispatch is not connected to it yet. There is no fallback to existing unsafe legacy mutation/recovery routes.

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
errors. They do **not** establish working consultation orchestration, safe
provider retries,
macOS Keychain, interactive terminal/editor behavior, model quality, or macOS
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
  The transport does not save keys or perform Keychain setup.
- Wire models use the shared synthetic files in `tests/fixtures/cli`. Request
  validation must apply the authenticated backend's current limits before any
  submission; structural decoding alone has no fixed deployment-specific cap.
  Prompt field requirements/options and first-answer semantics remain backend
  responsibilities. Never infer execution support from parsing draft v1 data.
- Input keeps exact original text. Character limits match Python code points,
  not UTF-8 bytes or displayed graphemes. Display escaping must never alter
  saved model text, accepted answer receipts or exported Markdown.
- Next connect authentication/config probing and command dispatch only with
  explicitly tested boundaries. The HTTP config probe is implemented but
  disconnected from command dispatch. Keychain, private idempotent submission
  receipts, prompt rendering and the monitor loop are unfinished.
  Backend continuation/provenance/in-flight recovery work may proceed alongside
  offline Rust work; it must be complete before live durable run operations.

References: [clap](https://docs.rs/clap/latest/clap/),
[Serde](https://serde.rs/), [URL parsing](https://docs.rs/url/latest/url/),
[official Rust installation](https://rust-lang.org/tools/install/),
[reqwest client configuration](https://docs.rs/reqwest/0.13.5/reqwest/struct.ClientBuilder.html).
