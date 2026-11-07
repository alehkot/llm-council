"""Explicit OpenRouter adapters share one bounded text-request implementation.

An explicit internal manager member step can select this adapter; no public
route or automatic worker loop does. Retries require the durable
member guard's current consent token; length stops are retained without issuing
continuations. The separately invoked continuation and Stage 2 adapters reuse the
same wire contract. Credentials are supplied ephemerally and never enter receipts.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import httpx

from . import run_stage1_execution

# This identity binds parser and transport semantics, not a caller's credential.
# Increment it if those semantics change; never reinterpret existing attempts.
PROVIDER_KEY = "openrouter-stage1-v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderFailure(RuntimeError):
    """No supported response was returned; the durable attempt stays uncertain."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _nonfinite(value: str) -> Any:
    raise ValueError("Nonfinite JSON number")


def _number(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON number")
    return result


def _response(body: bytes | bytearray) -> dict[str, Any]:
    """Validate the complete envelope without exposing provider-controlled text."""
    try:
        data = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_nonfinite,
            parse_float=_number,
        )
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("Invalid envelope")
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Invalid choices")
        choice = choices[0]
        if not isinstance(choice, dict) or "error" in choice:
            raise ValueError("Invalid choice")
        if choice["finish_reason"] not in ("stop", "length"):
            raise ValueError("Unsupported completion")
        message = choice["message"]
        if (
            not isinstance(message, dict)
            or "error" in message
            or message.get("role") != "assistant"
            or message.get("tool_calls") not in (None, [])
            or message.get("function_call") is not None
            or message.get("refusal") not in (None, "")
        ):
            raise ValueError("Not an assistant text response")
        normalized = {
            "content": message["content"],
            "finish_reason": choice["finish_reason"],
            "reasoning_details": message.get("reasoning_details"),
            "usage": data.get("usage"),
        }
        return run_stage1_execution.MemberResponse.model_validate(
            normalized
        ).model_dump(mode="json")
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise ProviderFailure(
            "Provider returned no supported Stage 1 response"
        ) from None


async def _request(
    *,
    endpoint: str,
    timeout_seconds: float,
    payload: dict[str, Any],
    api_key: str,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    # HTTPX defaults to retries=0. The injected transport is a trusted test seam,
    # not a supported production retry/fallback layer.
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        async with client.stream(
            "POST",
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept-Encoding": "identity",
            },
            json=payload,
        ) as response:
            if response.status_code != 200:
                raise ProviderFailure("Provider request did not succeed")
            # Refuse compressed responses before HTTPX decodes any body chunks.
            # A decoded-byte cap alone cannot bound decompression expansion.
            if response.headers.get("Content-Encoding", "identity").strip().lower() != (
                "identity"
            ):
                raise ProviderFailure(
                    "Provider returned an unsupported content encoding"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunk) > MAX_RESPONSE_BYTES - len(body):
                    raise ProviderFailure("Provider response exceeded the size limit")
                body.extend(chunk)
            return _response(body)


def _provider(
    api_key: str, transport: httpx.AsyncBaseTransport | None
) -> run_stage1_execution.Provider:
    """Create a credential-bound callback for explicit Stage 1/2 guards.

    This shares wire/parser/deadline semantics, not saved operation identities or
    retry consent. Construction performs no I/O and never persists the credential.
    """
    if (
        not isinstance(api_key, str)
        or not api_key
        or any(ord(c) < 33 or ord(c) > 126 for c in api_key)
    ):
        raise ValueError("A nonempty provider credential is required")

    async def provider(**arguments: Any) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                _request(api_key=api_key, transport=transport, **arguments),
                timeout=arguments["timeout_seconds"],
            )
        except (httpx.HTTPError, asyncio.TimeoutError):
            # Never include provider bodies, URLs, credentials or request text.
            raise ProviderFailure(
                "Provider request failed; outcome may be uncertain"
            ) from None

    return provider


async def execute_openrouter(
    run_id: str,
    model: str,
    *,
    api_key: str,
    expected_revision: int,
    retry_token: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> run_stage1_execution.MemberResponse:
    """Send one exact saved request through the member attempt/cancellation guard.

    The guard checks current configuration and saved causal inputs, persists the
    attempt before dispatch, and binds this adapter version. It refuses to adopt
    previously injected callbacks with another identity. No settings, catalogs,
    skills or credentials are resolved here. Timeout, HTTP/protocol failure and
    interruption leave explicit retry required; no potentially paid work repeats
    automatically. This is internal execution, not public CLI detach semantics.
    """
    return await run_stage1_execution.execute(
        run_id,
        model,
        _provider(api_key, _transport),
        provider_key=PROVIDER_KEY,
        expected_revision=expected_revision,
        retry_token=retry_token,
    )
