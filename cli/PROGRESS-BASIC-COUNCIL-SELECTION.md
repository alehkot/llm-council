# Basic CLI: inspect the saved council selection

The CLI plan calls for model and pipeline provenance rather than replacing a
historical selection with whatever happens to be configured now. The basic
run's detailed snapshot, including `council show RUN_UUID --json`, now exposes
the validated saved member roster, backend name and selected switches under
`provenance.council_selection`. These are the original member/stage choices,
not the subsequently filtered reviewers or a claim about synthesis. Compact
history still omits the detailed selection. An unbound or unavailable
selection is omitted without changing access to existing saved results.
The projection does not expose endpoint details, provider credentials,
request payloads, prompt policy hashes or internal error text.

Starting [development 8e41fad](https://github.com/alehkot/llm-council/commit/8e41fadbeedcedddd62d1bc094161b91ffcffe44)
and [main d90cb29](https://github.com/alehkot/llm-council/commit/d90cb29427d17b6ffa7dec9629f89e9e4b357c00)
had exact Woodpecker #34 and #8 passes; no open issues/PRs. A synthetic
regression failed before the projection was present, then covered absence
before selection, saved values under config drift, compact history and
completed runs. The second test covers unavailable selection while preserving
saved evidence. Focused tests, formatting, lint and disposable authenticated
startup passed. No independent review, local full build, local Rust or
rendered-browser run, native macOS verification, or live paid calls. Existing
wire container, versions, worker scheduling and persisted records are
unchanged; exact published CI evidence is separate.
