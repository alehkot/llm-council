# Validated evidence for saved negative critiques

The [raw reader](PROGRESS-NEGATIVE-PROJECTION.md) retains exact stopped
responses. A provider stop does not establish a usable score. The legacy
extractor accepts older formats, coerces values to floats, and does not bind
its JSON peer keys to the saved roster. This change leaves those legacy
callers alone.

`run_negative_validation.load_validated_results` reads the complete saved
roster once. It validates an exact bare JSON object or the requested fenced
JSON (prose notes after a fence are not parsed). It rejects repeated keys,
unexpected fields, missing or stale peer labels, nonnumeric/nonfinite values,
values outside 0 through 10, and invalid or blank summaries. Decimal bounds
are checked before binary float conversion, so a score just outside the range
cannot round to an allowed endpoint. A sole surviving reviewer supplies empty
peer maps. See the [Python JSON decoder documentation](https://docs.python.org/3/library/json.html#json.loads)
for the duplicate-key and number hooks.

Results follow the exact saved reviewer and peer order, retaining source and
attempt identities. If work is incomplete, the reader returns no result; if a
completed reviewer is malformed, it returns no partial scores and raises a
reason bound to the saved reviewer label without quoting model output. The raw
critique remains available internally. Summaries are still untrusted text.
This does not aggregate critiques, advance workflow, contact providers, add a
route, or change available CLI commands or versions.

## Verification and next step

Starting [development d84cfd0](https://github.com/alehkot/llm-council/commit/d84cfd08ca63d072fc6a46e01c249ca1bb33f8f8)
and [main d90cb29](https://github.com/alehkot/llm-council/commit/d90cb29427d17b6ffa7dec9629f89e9e4b357c00)
had exact Woodpecker #30 and #8 passes. Offline tests using saved inputs and
fake providers cover a four-answer run with the original second answer
excluded, giving the surviving original third answer the saved label B and
peer labels A/C. They also cover the solo case, exact bounds, malformed
and incomplete reviews, untouched storage, detachment, historical reads and
the legacy extraction control. No native Keychain/terminal, live model,
frontend or full local build was exercised.

Keep aggregation and the behavior of a malformed run as a separate policy
decision. Do not enable a route or advance a run from this evidence alone.
