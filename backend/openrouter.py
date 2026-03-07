"""OpenRouter API client for making LLM requests."""

import asyncio
from datetime import datetime
import random
import time
from typing import List, Dict, Any, Optional

import httpx

from .config import (
    OPENROUTER_API_KEY,
    OPENROUTER_API_URL,
    OPENROUTER_MAX_TOKENS,
    OPENROUTER_MAX_RETRIES,
    OPENROUTER_RETRY_BASE_SECONDS,
    OPENROUTER_RETRY_JITTER,
    OPENROUTER_RETRY_MAX_SECONDS,
)
from .trace import TraceCollector


def _is_retryable_status(status_code: Optional[int]) -> bool:
    if status_code is None:
        return False
    if status_code in {408, 425, 429}:
        return True
    return 500 <= status_code <= 599


def _backoff_seconds(retry_index: int) -> float:
    backoff = OPENROUTER_RETRY_BASE_SECONDS * (2 ** retry_index)
    backoff = min(backoff, OPENROUTER_RETRY_MAX_SECONDS)
    if OPENROUTER_RETRY_JITTER > 0:
        jitter = backoff * OPENROUTER_RETRY_JITTER
        backoff += random.uniform(0, jitter)
    return backoff


def _extract_message_content(message: Dict[str, Any]) -> Optional[str]:
    """Normalize provider message payloads to plain text content."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        normalized = "".join(parts).strip()
        return normalized or None
    return None


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    trace: Optional[TraceCollector] = None,
    trace_stage: Optional[str] = None,
    trace_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds
        trace: Optional TraceCollector to record request/response metadata
        trace_stage: Optional stage label for trace grouping
        trace_context: Optional context metadata for trace entries

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": OPENROUTER_MAX_TOKENS,
        "reasoning": {
            "effort": "high"
        }
    }

    total_attempts = max(1, OPENROUTER_MAX_RETRIES + 1)

    for attempt in range(total_attempts):
        if attempt > 0:
            await asyncio.sleep(_backoff_seconds(attempt - 1))

        started_at = time.perf_counter()
        started_at_iso = None
        response_status = None
        response_headers = None
        response_body: Optional[Dict[str, Any]] = None
        error_message = None

        attempt_context = dict(trace_context or {})
        attempt_context["attempt"] = attempt + 1
        attempt_context["max_attempts"] = total_attempts

        try:
            if trace:
                started_at_iso = datetime.utcnow().isoformat()

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=payload
                )
                response_status = response.status_code
                response_headers = dict(response.headers)
                try:
                    data = response.json()
                except Exception:
                    data = {"raw_text": response.text}
                response_body = data

                if response_status >= 400:
                    response.raise_for_status()

                # Validate response structure before accessing nested keys
                if 'choices' not in data or not data['choices']:
                    error_message = f"Unexpected API response structure from {model}: {data}"
                    if attempt + 1 < total_attempts:
                        continue
                    print(error_message)
                    return None

                message = data['choices'][0].get('message', {})
                if not message:
                    error_message = f"No message in API response from {model}: {data}"
                    if attempt + 1 < total_attempts:
                        continue
                    print(error_message)
                    return None

                content = _extract_message_content(message)
                if content is None:
                    error_message = f"No text content in API response from {model}: {data}"
                    if attempt + 1 < total_attempts:
                        continue
                    print(error_message)
                    return None

                return {
                    'content': content,
                    'reasoning_details': message.get('reasoning_details')
                }

        except httpx.HTTPStatusError as e:
            error_message = str(e)
            response = e.response
            response_status = response.status_code if response else None
            response_headers = dict(response.headers) if response else None
            try:
                response_body = response.json() if response else None
            except Exception:
                response_body = {"raw_text": response.text} if response else None
            if _is_retryable_status(response_status) and attempt + 1 < total_attempts:
                continue
            print(f"Error querying model {model}: {e}")
            return None
        except httpx.RequestError as e:
            error_message = str(e)
            if attempt + 1 < total_attempts:
                continue
            print(f"Error querying model {model}: {e}")
            return None
        except Exception as e:
            error_message = str(e)
            print(f"Error querying model {model}: {e}")
            return None
        finally:
            if trace:
                ended_at_iso = datetime.utcnow().isoformat()
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                trace_headers = dict(headers)
                if "Authorization" in trace_headers:
                    trace_headers["Authorization"] = "Bearer ***redacted***"

                trace.add_event(
                    "llm_call",
                    stage=trace_stage,
                    model=model,
                    context=attempt_context,
                    request={
                        "url": OPENROUTER_API_URL,
                        "headers": trace_headers,
                        "payload": payload,
                        "timeout": timeout,
                    },
                    response={
                        "status_code": response_status,
                        "headers": response_headers,
                        "body": response_body,
                    },
                    timing={
                        "started_at": started_at_iso or ended_at_iso,
                        "ended_at": ended_at_iso,
                        "duration_ms": duration_ms,
                    },
                    error=error_message,
                )

    return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
    trace: Optional[TraceCollector] = None,
    trace_stage: Optional[str] = None,
    trace_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model
        trace: Optional TraceCollector to record request/response metadata
        trace_stage: Optional stage label for trace grouping
        trace_context: Optional context metadata for trace entries

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [
        query_model(
            model,
            messages,
            trace=trace,
            trace_stage=trace_stage,
            trace_context=trace_context,
        )
        for model in models
    ]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
