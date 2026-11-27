# Basic history filters before applying its limit

`council history` lists up to 100 recent Basic runs. The admission store can also
hold receipts for other work. Previously, 100 recent receipts of any kind were
selected first, which could hide older Basic runs even when fewer than 100
Basic runs existed. Exact run IDs remained usable; no stored runs were lost.

Select eligible Basic receipts before sorting newest first and taking the
existing limit. The general internal listing, deterministic tie-break,
tombstone handling, saved question, compact stage-free response and public
API shape are unchanged. This is a read-path fix, not new pagination or
durable orchestration. History does not schedule or retry work.

Development base: `82b4297afe44b86890a65523527e51261f295827` (#35).
Upstream main: `d90cb29427d17b6ffa7dec9629f89e9e4b357c00` (#8).
The added persistence regression reproduced the older Basic run disappearing
behind 100 intervening non-Basic receipts, then verified both Basic entries,
correct order, and no provider calls. All 45 focused tests passed, as did
Ruff lint/format and guarded FastAPI startup, authentication and shutdown.
All 1,366 offline backend tests passed with external HTTP and sockets blocked.
No local full build, live provider, native macOS or browser check. Consult the
normal Woodpecker evidence for the exact published commit.

## Normal CI follow-up

[Woodpecker #36](https://github.com/alehkot/llm-council/commit/0516f8fc2773485c4c8d9aad600203e7c608ecd0#commitcomment-212137378)
reported a 10-second wait timeout in the existing fully mocked legacy web
Codex synthesis persistence test. The test exercises persisted output, not
the speed of a particular worker. It now uses the status endpoint's cursor
long poll, with short per-request waits and a 45-second overall test budget.
The same completion, stage, output and no-network assertions remain in force;
no runtime timeout, pipeline or provider behavior changed. The exact CI
worker delay was not measured. With an artificial 11-second delay in the
fake synthesis command, the previous fixture timed out and the new one
passed. All 32 focused tests and Ruff checks passed.
The guarded full backend suite then passed all 1,366 tests on the corrected source.
