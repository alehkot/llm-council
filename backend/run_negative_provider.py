"""Explicit single-request OpenRouter adapter for one saved negative evaluator.

No public route, scheduler or automatic roster loop selects this adapter.
It retains raw critique text, not validated scores or an aggregate result.
"""

from __future__ import annotations

import httpx

from . import run_negative_execution, run_stage1_provider

# Separate stage identity: sharing a wire parser never shares retry consent.
# Increment if these semantics change; never reinterpret an existing attempt.
PROVIDER_KEY = "openrouter-negative-v1"


async def execute_openrouter(
    run_id: str,
    model: str,
    *,
    api_key: str,
    expected_revision: int,
    retry_token: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> run_negative_execution.MemberResponse:
    """Send one exact saved critique request through its durable negative-stage guard.

    Reuse the bounded single-request wire contract: saved endpoint/timeout/body,
    strict text envelope, response-size and whole-request deadline limits, no
    redirects/proxies/retries, and ephemeral credentials. The negative-stage guard
    owns attempts and explicit uncertain-work consent independently of other stages.
    Stop/length results are retained without parsing, aggregation, continuation
    or another member's call. Caller cancellation is not public detachment.
    """
    return await run_negative_execution.execute(
        run_id,
        model,
        run_stage1_provider._provider(api_key, _transport),
        provider_key=PROVIDER_KEY,
        expected_revision=expected_revision,
        retry_token=retry_token,
    )
