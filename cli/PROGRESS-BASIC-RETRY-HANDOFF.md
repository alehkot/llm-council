# Basic CLI: resume an accepted retry after a lost worker handoff

Development base: `a0f589a2a475056b830535294170ff2e6845188d` (Woodpecker #38).
Upstream main: `d90cb29427d17b6ffa7dec9629f89e9e4b357c00` (Woodpecker #8).
Both exact commits had matching success statuses and reports. This change
addresses the crash/retry acceptance case in [the plan](PLAN.md#acceptance-checks)
within the existing basic API.

A retry can persist its consent before the request hands it to a worker.
Previously, sending the same retry again only returned the snapshot, even
when the same uncertain attempt was still waiting and had no worker. Now that
replay can start the handoff for the exact persisted consent. Replaying while
a worker owns the run, after completion or cancellation, or after a distinct
attempt has become uncertain does not start work. Completed sibling responses
are reused. No new retry consent, endpoint, API version or optional workflow
is introduced.

Injected failure immediately after the consent write reproduced the stuck
member and reviewer cases. Three regression tests cover recovery and duplicate
delivery, no handoff from an old token for another operation, and no consent
for a later failure of the same operation. All 21 focused Basic tests and all 1,369
backend tests passed; the latter ran with outbound networking blocked. Backend
Ruff and an isolated authenticated app startup/shutdown check also passed.
Self-reviewed; no independent review, live provider, native Keychain, local
full build or browser verification. Check the normal Woodpecker report for
the exact published commit; it is separate from local verification.

Next: continue the plan's remaining durable workflow and end-to-end acceptance
cases; this fix does not establish full CLI API 1 or exactly-once provider
execution.
