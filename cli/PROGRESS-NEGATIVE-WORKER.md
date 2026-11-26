# Explicit negative-evaluator worker

The consultation manager can now schedule one named evaluator's already saved
negative-stage request on its worker pool. An identical active request joins
the same process-local future; different revisions or retry consent do not
replace it. The worker uses the existing durable OpenRouter adapter and attempt
guard. Cancelling a waiter only detaches that waiter; durable run cancellation
can prevent queued dispatch or cancel an active request. The manager excludes
overlap with its other CLI stages and the legacy worker for the same
conversation. It does not persist a queue receipt or claim cross-process
exactly-once execution.

Tests use an actual worker pool, saved synthetic run state, and an HTTP mock.
They cover the excluded evaluator, duplicate admission, detachment, completed
replay without another request, competing owners and durable cancellation
before and after dispatch. No live provider was called. Raw critiques are not
scores: no continuation, score projection, stage transition, API version or
public CLI command is enabled by this step.

Starting development: `786267122993521d868150b4b5d1992bb33c62ad`.
Upstream main: `d90cb29427d17b6ffa7dec9629f89e9e4b357c00`.
Next, design the read-only projection with complete critic provenance before
the negative stage can feed ranking or synthesis.
