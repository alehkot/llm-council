"""Explicit single-request OpenRouter adapter for one saved Stage 2 reviewer.

No public route, worker scheduler or automatic roster loop selects this adapter.
It retains raw ranking text, not a validated ballot or an aggregate ranking.
"""

from __future__ import annotations

import httpx

from . import run_stage1_provider, run_stage2_execution

# Separate stage identity: sharing a wire parser never shares retry consent.
# Increment if these semantics change; never reinterpret an existing attempt.
PROVIDER_KEY = "openrouter-stage2-v1"


async def execute_openrouter(
    run_id: str,
    model: str,
    *,
    api_key: str,
    expected_revision: int,
    retry_token: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> run_stage2_execution.MemberResponse:
    """Send one exact saved ranking request through its durable reviewer guard.

    Reuse the bounded single-request wire contract: saved endpoint/timeout/body,
    strict text envelope, response-size and whole-request deadline limits, no
    redirects/proxies/retries, and ephemeral credentials. The Stage 2 guard owns
    attempts and explicit uncertain-work consent independently of Stage 1.
    Stop/length results are retained without parsing, aggregation, continuation
    or another reviewer's call. Caller cancellation is not public detachment.
    """
    return await run_stage2_execution.execute(
        run_id,
        model,
        run_stage1_provider._provider(api_key, _transport),
        provider_key=PROVIDER_KEY,
        expected_revision=expected_revision,
        retry_token=retry_token,
    )
