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
