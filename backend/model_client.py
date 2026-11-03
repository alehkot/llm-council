"""Single transport choice for council stages and auxiliary model calls."""

from typing import Any

from . import acp_client, openrouter
from .config import MODEL_BACKEND
from .trace import TraceCollector


async def query_model(
    model: str,
    messages: list[dict[str, str]],
    timeout: float = 120.0,
    trace: TraceCollector | None = None,
    trace_stage: str | None = None,
    trace_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    client = acp_client if MODEL_BACKEND == "acp" else openrouter
    return await client.query_model(
        model,
        messages,
        timeout=timeout,
        trace=trace,
        trace_stage=trace_stage,
        trace_context=trace_context,
    )
