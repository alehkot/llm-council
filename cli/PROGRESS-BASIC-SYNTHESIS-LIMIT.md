# Basic CLI: completed but truncated synthesis

A valid OpenRouter response with a `length` finish reason shows that the
provider returned a truncated answer. It does not mean the outcome was lost
or that a final answer exists. The basic CLI previously dropped that evidence
and offered the generic uncertain-work retry, which would repeat the same
synthesis request under the same budget.

Save the normalized response and usage before recording a failure. Reopening
a run after this result was persisted does not call the provider again, even
if the previous process did not get to save the failed lifecycle. The
existing failure payload explains the output limit. Member answers and peer
reviews remain inspectable and exportable; raw truncated synthesis is
internal evidence and is not published as a final answer. A call with no
validated saved result still follows the existing explicit-retry path. No
new route, continuation, model limit, API version, or legacy web path changes.

[OpenRouter response documentation](https://learn.openrouter.ai/courses/first-request/read-the-response)
distinguishes the token-limit finish reason from a normal stop.

## Verification

[Development b00403d](https://github.com/alehkot/llm-council/commit/b00403d21baa2fdf90f43f1f67e698040486b1d7)
and [main d90cb29](https://github.com/alehkot/llm-council/commit/d90cb29427d17b6ffa7dec9629f89e9e4b357c00)
had exact Woodpecker #31 and #8 passes at the start. Two new synthetic
end-to-end backend cases failed before this fix and now pass. They cover
durable evidence, status and export, failed replay attempts, idempotent
reopening and the persistence-to-status interruption window. Existing
uncertain-transport and completed-run cases still pass. No local full build,
live provider, native Keychain, or rendered browser check was performed.
Verified head and normal Woodpecker evidence belong to the resulting commit.
