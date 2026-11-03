# OpenRouter terminal-outcome checkpoint

## Baseline and publication

- Repository: [alehkot/llm-council](https://github.com/alehkot/llm-council).
- Persistent working branch: `development`, starting at
  [b737aa9f77e8ab7bbb8cefcb00457f48ed0160c3](https://github.com/alehkot/llm-council/commit/b737aa9f77e8ab7bbb8cefcb00457f48ed0160c3).
- Upstream `main`:
  [fe887479cbb09d940f64dfe1d813d166f9aeac9d](https://github.com/alehkot/llm-council/commit/fe887479cbb09d940f64dfe1d813d166f9aeac9d).
  It remains an ancestor; all main changes and accumulated CLI work are preserved.
- Local implementation: `6fce9293de48cc95300670f7c88e702cb500ad08`.
- Published implementation:
  [24ef33b2cedf568ceae12194247248025c602e6f](https://github.com/alehkot/llm-council/commit/24ef33b2cedf568ceae12194247248025c602e6f),
  tree `195d641c06b40eb69b3f58680f929f71e0aeeecc`, matches the reviewed local tree.
  The exact published commit was imported and verified locally, preserving real
  history. Publication rechecked both remote heads and used a non-force,
  expected-head update. This checkpoint is a documentation-only descendant;
  its exact final publication/CI receipt is retained in [recovery issue #17](https://github.com/alehkot/issues-tracker/issues/17).

## Problem, rationale and change

The live OpenRouter client accepted HTTP 200 responses with nonempty partial
text even when the provider explicitly reported `content_filter`, `error`, or
`tool_calls`, or attached a top-level/choice error. Such text could become a
member answer, peer evidence, revision or final synthesis. Independently reproduced
through the real `query_model` path with synthetic HTTP responses.

[OpenRouter's API reference](https://openrouter.ai/docs/api_reference/overview)
defines the normalized terminal reasons and choice error field. Its
[HTTP-success error example](https://openrouter.ai/blog/tutorials/model-routing-for-support-bots-cheap-first-faq-handling/)
explains that generation errors can arrive with HTTP 200 and partial content.
These protocol sources and reproductions support this bounded repair. Broader
pipeline-provenance/CLI integration was considered but deferred rather than
expanding scope or claiming that partially implemented milestones are complete.

The client now rejects explicit terminal failure/filter/tool outcomes, embedded
errors and unresolved tool calls. It does not automatically retry them, continue
the partial prose or expose provider error text in diagnostics. Existing
HTTP-status retries (including 429/5xx), malformed/blank-response retry budgets,
normal completions, missing/provider-specific finish reasons and token-limit
continuations remain unchanged. No frontend, durable-adapter, API capability,
CLI command, model selection or ACP behavior changed.

## Verification and review

- Baseline: 445 offline Python 3.10.22 tests passed.
- Final source: 456 offline Python tests passed; outbound network calls are
  forbidden by the runner. Eleven new tests cover terminal reasons with blank
  and partial text, embedded errors, unresolved tools, single-request behavior,
  sanitized traces, stage1 failure, preserved deliberation originals, rejected
  synthesis, interrupted continuation, and successful/missing/unknown outcomes.
- The initial seven regression tests produced 12 failing subcases on the baseline;
  they pass with the repair. Additional integration/compatibility tests also pass.
- 92 Node tests, repository-wide Ruff format/check, frontend format/lint and Git
  whitespace checks passed. Backend startup, authenticated loopback config/API0
  and graceful shutdown passed using synthetic credentials and empty data.
- Independent full-change review: no introduced moderate-or-higher findings.
  The reviewer ran 33 focused OpenRouter tests and 24 additional actual HTTPX
  MockTransport cases, including trace redaction and no-retry checks. A wording
  ambiguity about HTTP-status retries was corrected. Self-review agreed; a clean
  review is not a guarantee of bug-free code.
- No full local builds, live/paid provider calls, model-quality evaluations,
  macOS Keychain, end-to-end CLI workflow or new rendered-browser verification ran.
  No frontend presentation changed. These offline tests establish handling of
  specified response shapes, not production-provider reliability or answer quality.

## CI and remaining work

At 15:59 UTC, exact starting development, main and published implementation have
zero GitHub statuses, check runs and Woodpecker commit comments. The workflow
expects normal pushes to main/development, but missing reports are not evidence
of a running build, failure or pass. No full-build or platform coverage is claimed.
No manual CI dispatch/rerun, private runner access, security/settings changes or
PR lifecycle action occurred.

Observe normal CI for the exact latest published head; diagnose relevant failures
before unrelated work. If reports remain absent, the owner can inspect normal
Woodpecker activation/webhook/worker/reporting health. Existing recovery issue #17
tracks this integration-evidence blocker without a duplicate issue. There are no
outstanding moderate-or-higher introduced review findings or unpublished code
changes for this repair. Continue the separately documented CLI plan dependencies
only after fresh branch, ownership and CI checks.

---

# Authentication replacement checkpoint — 2026-11-01

## Baseline and completed change

Persistent branch: `development`, starting at
[4a8ad5b7daa6ed617b0433559a8fdc7f37ffe987](https://github.com/alehkot/llm-council/commit/4a8ad5b7daa6ed617b0433559a8fdc7f37ffe987).
Observed upstream `main`:
[96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc](https://github.com/alehkot/llm-council/commit/96f3df7b0b7ac684e69e83ab1ee4b4751f6782bc).
The upstream API client has the same credential-response race. Its separate
ACP/model-routing changes remain preserved; integration was not required.

Implementation:
[f6335dc539b876465db0925a8ce1b3d2b01685ac](https://github.com/alehkot/llm-council/commit/f6335dc539b876465db0925a8ce1b3d2b01685ac),
tree `acf968dc889e5b4e1bb207576485a24033c5612d`.
This checkpoint's containing commit is documentation-only. The publication
receipt/recovery issue records the exact final SHA and its CI evidence separately.

A delayed old 401/403 response could clear a newly entered API key; an old 429
could latch a replacement session as rate-limited. A successful old probe could
also send a queued mutation with the replacement key. Reproduce by holding a
probe/request, clearing and replacing credentials, then releasing the old result.

Probes and requests now retain their credential generation and key. Ownership is
checked after awaited responses and before sending queued requests. Explicit
Clear and Save invalidate old generations, including re-entering the same key.
Obsolete operations reject with `AbortError` without a false auth notification;
the UI ignores that obsolete outcome. Current-key rejection/cooldown behavior
and single-flight probing remain intact. Mutations are never automatically
replayed. Existing CLI admission/durability work is unchanged.

## Verification and limitations

- Baseline: eight existing auth tests passed; all ten added regressions failed.
- Final: 91 Node tests passed, including all 18 auth cases.
- 261 backend tests passed with outbound networking forbidden under Python 3.10.
- Frontend Biome format and ESLint passed; repository Ruff format/check passed
  using the existing Python 3.13 tooling environment. Git whitespace passed.
- 20 rendered sandboxed Chromium desktop/mobile cases passed, exercising actual
  Clear/Save controls twice, stale probe/request 200/401/403/429, same-key reset,
  current-invalid-key control, and absence of unexpected mutation requests.
  Four representative screenshots were inspected. External fonts were stubbed.
- Six existing browser API-module transport recovery cases also passed.
- Independent implementation, tests and documentation review found no introduced
  moderate-or-higher findings. Published implementation head/tree matched the
  reviewed local tree; 18 focused tests passed again after publication.

No local full build, live provider, real credential, macOS Keychain or other
browser-engine verification ran. Tests do not cancel already accepted server
work or prevent delayed body parsing that started before the credential reset.
A clean review is not a guarantee of bug-free code.

## CI and next steps

The exact starting development, upstream and published implementation commits had
no statuses, check runs or Woodpecker commit comments. Empty aggregate `pending`
is missing evidence, not a running build or pass. The checked-in workflow expects
normal pushes; observe the final checkpoint SHA separately. No manual CI rerun,
private-runner access, security/settings change, release or deployment occurred.

If normal reports remain absent, inspect integration/reporting health through
authorized owner access. Do not infer a build failure from missing evidence.
Continue the separate [CLI plan](cli/PLAN.md) from its latest durability
checkpoint; this frontend fix neither completes nor changes those milestones.

Research: [React guidance on ignoring obsolete asynchronous results](https://react.dev/reference/react/useEffect#fetching-data-with-effects).
Repository reproductions establish the defect; the reference supports the
ownership approach, not an assertion that all asynchronous races are solved.
