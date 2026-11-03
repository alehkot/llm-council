"""Explicit OpenRouter-only durable refinement, not a configured run executor.

No routes/workers select this adapter. A future executor must resolve the chosen
transport (including upstream ACP), full pipeline settings and skills before
calling it. This adapter snapshots only its own request contract. No secrets are
persisted, no legacy provider fallback is used, and one attempt sends one POST.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from . import refinement_execution, run_admission, storage
from .consultation_contract import WireModel
from .refinement import RefinementState, RefinementSuggestion
from .refinement_prompt import _normalize_refined_prompt, build_refinement_messages

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderFailure(RuntimeError):
    """No usable result was saved; the guarded attempt remains uncertain."""


class OpenRouterRefinementConfig(WireModel):
    """Non-secret effective request settings, supplied explicitly by the caller.

    Version binds prompt text, parser and transport policy. Increment it whenever
    these semantics change; historical attempts cannot be silently reinterpreted.
    """

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )

    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    transport: Literal["openrouter"] = "openrouter"
    endpoint: StrictStr
    model: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    max_tokens: Annotated[StrictInt, Field(gt=0)]
    timeout_seconds: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)] = 60.0
    reasoning: StrictBool = True
    effort: Literal["low", "medium", "high"] = "high"

    @model_validator(mode="after")
    def safe_endpoint(self) -> OpenRouterRefinementConfig:
        try:
            url = urlsplit(self.endpoint)
            _ = url.port
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and not any(c.isspace() or ord(c) < 32 for c in self.endpoint)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "Refinement endpoint must be HTTPS without credentials, query or fragment"
            )
        if self.model.strip() != self.model or any(ord(c) < 32 for c in self.model):
            raise ValueError("Refinement model identifier is invalid")
        return self

    def identity(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return "openrouter-refinement-v1:" + hashlib.sha256(raw.encode()).hexdigest()


def _validated(config: OpenRouterRefinementConfig) -> OpenRouterRefinementConfig:
    # Reject bypassed model_construct/mutated values at the durable boundary.
    return OpenRouterRefinementConfig.model_validate(config.model_dump(mode="python"))


def _validate_record(
    record: dict[str, Any], execution: refinement_execution.ExecutionState
) -> OpenRouterRefinementConfig | None:
    """Validate existing provenance without binding new settings or writing."""
    if "refinement_provider" not in record:
        return None
    saved = record["refinement_provider"]
    try:
        if not isinstance(saved, dict) or set(saved) != {
            "configuration",
            "identity",
        }:
            raise ValueError("Invalid provider record")
        if not isinstance(saved["configuration"], dict) or set(
            saved["configuration"]
        ) != set(OpenRouterRefinementConfig.model_fields):
            raise ValueError("Provider configuration is not canonical")
        original = OpenRouterRefinementConfig.model_validate(saved["configuration"])
        if saved["identity"] != original.identity():
            raise ValueError("Provider identity differs")
        if any(op.provider_key != original.identity() for op in execution.operations):
            raise ValueError("Operation provider differs")
    except (ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Refinement provider checkpoint is invalid; repair required"
        ) from exc
    return original


def _bind(
    run_id: str,
    config: OpenRouterRefinementConfig,
    expected_revision: int,
    retry_token: str | None,
) -> OpenRouterRefinementConfig:
    with storage._storage_lock:
        record, execution = refinement_execution._load(run_id)
        if "refinement_provider" in record:
            original = _validate_record(record, execution)
            if original != config:
                raise refinement_execution.ExecutionConflict(
                    "Refinement provider configuration changed"
                )
            return original
        # Never attach new provenance retrospectively to old injected callbacks.
        revision, _ = refinement_execution._input(run_id)
        if execution.operations or revision != 0:
            raise refinement_execution.ExecutionConflict(
                "Existing refinement work has no provider provenance"
            )
        if expected_revision != revision or retry_token is not None:
            raise refinement_execution.ExecutionConflict(
                "Request does not identify a fresh refinement boundary"
            )
        record["refinement_provider"] = {
            "configuration": config.model_dump(mode="json"),
            "identity": config.identity(),
        }
        run_admission._write(record)
        return config


def _suggestion(data: Any, original_query: str) -> dict[str, Any]:
    """Strictly require a complete refinement, never reinterpret failure as success."""
    try:
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("Invalid response")
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Invalid choices")
        choice = choices[0]
        if choice["finish_reason"] != "stop":
            raise ValueError("Incomplete response")
        message = choice["message"]
        if message.get("tool_calls") or message.get("refusal"):
            raise ValueError("Not a refinement")
        content = message["content"]
        if not isinstance(content, str):
            raise ValueError("Invalid content")
        text = content.strip()
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        result = RefinementSuggestion.model_validate(json.loads(text))
        if (
            not result.refined_prompt.strip()
            or _normalize_refined_prompt(result.refined_prompt, original_query)
            != result.refined_prompt.strip()
            or len(result.clarifying_questions) > 3
            or any(not question.strip() for question in result.clarifying_questions)
        ):
            raise ValueError("Incomplete refinement")
        return result.model_dump()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderFailure(
            "Provider returned no complete valid refinement"
        ) from None


async def _request(
    config: OpenRouterRefinementConfig,
    api_key: str,
    arguments: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "messages": build_refinement_messages(**arguments),
        "max_tokens": config.max_tokens,
        "stream": False,
    }
    if config.reasoning:
        payload["reasoning"] = {"effort": config.effort}
    # HTTPX's default transport has retries=0. An injected transport is solely a
    # test seam; callers must not install one with retries or other side effects.
    async with httpx.AsyncClient(
        timeout=config.timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        async with client.stream(
            "POST",
            config.endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        ) as response:
            if response.status_code != 200:
                raise ProviderFailure(
                    "Provider request did not return a successful response"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProviderFailure("Provider response exceeded the size limit")
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                raise ProviderFailure("Provider returned invalid JSON") from None
            return _suggestion(data, arguments["user_query"])


async def suggest_openrouter(
    run_id: str,
    config: OpenRouterRefinementConfig,
    *,
    api_key: str,
    expected_revision: int,
    retry_token: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> RefinementState:
    """Bind non-secret settings, then execute through the durable attempt guard.

    Explicit OpenRouter selection only: no configured backend/global defaults,
    model catalog requests, context compression, continuation or automatic retry.
    The caller supplies an ephemeral credential; it is never recorded or traced.
    HTTP errors, timeout, malformed results and cancellation preserve uncertainty.
    """
    config = _validated(config)
    if (
        not isinstance(api_key, str)
        or not api_key
        or any(ord(c) < 33 or ord(c) > 126 for c in api_key)
    ):
        raise ValueError("A nonempty provider credential is required")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    if retry_token is not None:
        UUID(retry_token)
    config = _bind(run_id, config, expected_revision, retry_token)

    async def provider(**arguments: Any) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                _request(config, api_key, arguments, _transport),
                timeout=config.timeout_seconds,
            )
        except (httpx.HTTPError, asyncio.TimeoutError):
            # Never include request URLs, headers, prompts or provider error text.
            raise ProviderFailure(
                "Provider request failed; outcome may be uncertain"
            ) from None

    return await refinement_execution.suggest(
        run_id,
        provider,
        provider_key=config.identity(),
        expected_revision=expected_revision,
        retry_token=retry_token,
    )
