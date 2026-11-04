"""Internal configured OpenRouter limits, not an effective request or executor.

The legacy client also resolves model catalog limits and can issue multiple
requests. This snapshot neither resolves that metadata nor permits those calls.
Durable dispatch must guard each potentially billed operation separately.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, StrictInt, StrictStr, model_validator

from . import (
    config,
    council_selection,
    prequery_policy,
    run_admission,
    run_interaction,
    storage,
)
from .consultation_contract import WireModel


class RequestPolicyConflict(ValueError):
    """Current request configuration cannot replace the saved policy."""


class OpenRouterRequestPolicy(WireModel):
    """Immutable configuration ceilings; not resolved per-model wire payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    council_selection_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    endpoint: StrictStr
    max_tokens: Annotated[StrictInt, Field(gt=0)]
    max_context_calls: Annotated[StrictInt, Field(ge=0)]
    max_continuations: Annotated[StrictInt, Field(ge=0)]
    max_retries: Annotated[StrictInt, Field(ge=0)]
    retry_base_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    retry_max_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    retry_jitter: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def valid_configuration(self) -> OpenRouterRequestPolicy:
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
                and not any(
                    c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.endpoint
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "Request endpoint must be HTTPS without credentials, query or fragment"
            )
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("Retry maximum must not be less than its base")
        return self


def _selection_identity(selection: council_selection.CouncilSelection) -> str:
    raw = json.dumps(
        selection.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _current(
    selection: council_selection.CouncilSelection,
) -> OpenRouterRequestPolicy:
    try:
        return OpenRouterRequestPolicy(
            version=1,
            source_revision=selection.source_revision,
            council_selection_sha256=_selection_identity(selection),
            endpoint=config.OPENROUTER_API_URL,
            max_tokens=config.OPENROUTER_MAX_TOKENS,
            max_context_calls=config.OPENROUTER_MAX_CONTEXT_CALLS,
            max_continuations=config.OPENROUTER_MAX_CONTINUATIONS,
            max_retries=config.OPENROUTER_MAX_RETRIES,
            retry_base_seconds=config.OPENROUTER_RETRY_BASE_SECONDS,
            retry_max_seconds=config.OPENROUTER_RETRY_MAX_SECONDS,
            retry_jitter=config.OPENROUTER_RETRY_JITTER,
        )
    except (ValueError, TypeError) as exc:
        raise RequestPolicyConflict("Current request policy is unsupported") from exc


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> OpenRouterRequestPolicy | None:
    """Read historical configuration and causal identity without current settings."""
    if "council_request_policy" not in record:
        return None
    try:
        raw = record["council_request_policy"]
        if record["phase"] != "prepared" or not isinstance(raw, dict):
            raise ValueError("Invalid run phase or request policy")
        if set(raw) != set(OpenRouterRequestPolicy.model_fields):
            raise ValueError("Noncanonical request policy")
        saved = OpenRouterRequestPolicy.model_validate(raw)
        selection = council_selection.validate_saved(record, interaction)
        if (
            selection is None
            or saved.source_revision != interaction.revision
            or saved.source_revision != selection.source_revision
            or saved.council_selection_sha256 != _selection_identity(selection)
        ):
            raise ValueError("Request policy source changed")
        return saved
    except (KeyError, ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved request policy is incompatible; repair required"
        ) from exc


def bind(run_id: str, *, expected_revision: int) -> OpenRouterRequestPolicy:
    """Save configured request limits after council selection; schedule nothing.

    Rebinding checks both current council selection and request configuration.
    Cancellation/deletion/revision guards apply even to an idempotent return.
    No credential, model catalog request or implicit selection binding occurs.
    """
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None or snapshot["interaction"] is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Request policy revision changed")
        selection = council_selection.validate_saved(record, interaction)
        if snapshot["internal_phase"] != "pre_query_complete" or selection is None:
            raise RequestPolicyConflict("Completed saved council selection is required")
        policy = prequery_policy.validate_saved(record, interaction)
        if policy is None:
            raise RequestPolicyConflict("Completed saved response policy is required")
        # A saved selection must not make changed live configuration look safe.
        # Resolve without binding: a failed request-policy bind writes nothing.
        try:
            current_selection = council_selection._current(policy)
        except council_selection.CouncilSelectionConflict as exc:
            raise RequestPolicyConflict(
                "Current council selection is unsupported"
            ) from exc
        if current_selection != selection:
            raise RequestPolicyConflict("Council selection changed")
        current = _current(selection)
        saved = validate_saved(record, interaction)
        if saved is not None:
            if saved != current:
                raise RequestPolicyConflict("Request policy changed")
            return saved
        record["council_request_policy"] = current.model_dump(mode="json")
        run_admission._write(record)
        return current
