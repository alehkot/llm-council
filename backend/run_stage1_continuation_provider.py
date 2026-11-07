"""Explicit single-request OpenRouter adapter for one saved first continuation.

No worker or public route selects this adapter. It never infers the next turn,
aggregates chunks, replaces an initial partial or automatically continues again.
"""

from __future__ import annotations

import httpx

from . import run_stage1_continuation_execution, run_stage1_provider

# Separate from the initial adapter: each guard owns its own attempt and consent.
# Increment if these semantics change; never reinterpret an existing attempt.
PROVIDER_KEY = "openrouter-stage1-continuation-v1"


async def execute_openrouter(
    run_id: str,
    model: str,
    *,
    turn: int,
    api_key: str,
    expected_revision: int,
    retry_token: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> run_stage1_continuation_execution.MemberResponse:
    """Send one pinned continuation request under its separate durable guard.

    Only explicit turn 1 is supported. The exact saved endpoint, timeout, settings
    and messages are used, without re-resolving configuration or initial output.
    The shared wire callback enforces the initial adapter's strict envelope/body
    limits, whole-request deadline, no retries/redirects/environment proxies and
    ephemeral credentials. A stop or length chunk is retained separately; replay
    never authorizes another call. Failure leaves this turn uncertain and requires
    its own current retry token. Caller cancellation is not public detachment.
    """
    return await run_stage1_continuation_execution.execute(
        run_id,
        model,
        run_stage1_provider._provider(api_key, _transport),
        turn=turn,
        provider_key=PROVIDER_KEY,
        expected_revision=expected_revision,
        retry_token=retry_token,
    )
