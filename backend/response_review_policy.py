"""Saved downstream choices from consumed review, without executing a stage.

Version 1 follows the legacy graph's exclusion and negative-prompt precedence.
The raw accepted answer stays in its prompt receipt; these are effective inputs,
not a new answer, provider request, dispatch token or public API capability.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictBool, StrictInt, StrictStr

from . import (
    council_selection,
    durable_response_review,
    run_admission,
    run_interaction,
    run_stage1_inputs,
    storage,
)
from .consultation_contract import WireModel
from .council import build_label_mapping


class ResponseReviewPolicy(WireModel):
    """Detached effective choices, bound to exact historical review evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_instance_id: UUID
    source_revision: Annotated[StrictInt, Field(ge=1)]
    stage1_source_sha256: run_stage1_inputs.Digest
    excluded_models: tuple[StrictStr, ...]
    active_models: tuple[StrictStr, ...]
    label_to_model: dict[StrictStr, StrictStr]
    negative_enabled: StrictBool
    negative_prompt_source: Literal[
        "disabled", "request", "review", "generation_required"
    ]
    negative_prompt: StrictStr | None = Field(repr=False)
    next_stage: Literal["blocked_no_active_responses", "negative", "stage2"]


def _derive(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> ResponseReviewPolicy:
    indices = [
        i
        for i, entry in enumerate(interaction.prompts)
        if entry.continuation.point == durable_response_review.POINT
    ]
    if len(indices) != 1:
        raise ValueError("Exactly one response-review boundary is required")
    index = indices[0]
    entry = interaction.prompts[index]
    if entry.phase != "consumed":
        raise ValueError("Response-review answer has not been consumed")
    review = durable_response_review.validate_checkpoint(record, interaction, index)
    if review.phase != "complete" or review.answer is None:
        raise ValueError("Response-review answer is unavailable")
    selection = council_selection.validate_saved(record, interaction)
    if selection is None:
        raise ValueError("Historical council selection is required")

    responses = {} if review.answer.skipped else review.answer.responses
    excluded = set(responses.get("exclude_responses", []))
    # Normalize effective exclusions to source order; raw order/omissions remain
    # in the accepted AnswerRequest. Relabel surviving responses as the legacy
    # graph does, rather than retaining holes or assigning labels by answer order.
    # Source order is a tuple, never JSON object insertion order. Validation of
    # the review above ties its complete results to this saved council roster.
    models = selection.council_models
    active = tuple(model for model in models if model not in excluded)
    _, labels = build_label_mapping([{"model": model} for model in active])
    prompt = None
    prompt_source = "disabled"
    if selection.negative_enabled:
        # Preserve whitespace, including whitespace-only strings, and the legacy
        # truthiness precedence. Gap concerns never enable a disabled stage.
        requested = record["request"]["negative_prompt"]
        concern = responses.get("gap_concerns")
        if requested:
            prompt, prompt_source = requested, "request"
        elif concern:
            prompt, prompt_source = concern, "review"
        else:
            prompt_source = "generation_required"
    return ResponseReviewPolicy(
        version=1,
        source_instance_id=entry.instance.instance_id,
        source_revision=(index + 1) * 3,
        stage1_source_sha256=review.source_sha256,
        excluded_models=tuple(model for model in models if model in excluded),
        active_models=active,
        label_to_model=labels,
        negative_enabled=selection.negative_enabled,
        negative_prompt_source=prompt_source,
        negative_prompt=prompt,
        next_stage=(
            "blocked_no_active_responses"
            if not active
            else "negative"
            if selection.negative_enabled
            else "stage2"
        ),
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> ResponseReviewPolicy | None:
    """Read exact saved choices without today's settings, prompts or providers."""
    if "response_review_policy" not in record:
        return None
    try:
        raw = record["response_review_policy"]
        if (
            record["phase"] != "prepared"
            or not isinstance(raw, dict)
            or set(raw) != set(ResponseReviewPolicy.model_fields)
        ):
            raise ValueError("Noncanonical response-review policy")
        saved = ResponseReviewPolicy.model_validate(raw)
        if saved != _derive(record, interaction):
            raise ValueError("Review policy differs from its source or semantics")
        return saved
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved response-review policy is incompatible; repair required"
        ) from exc


def bind(run_id: str, *, expected_revision: int) -> ResponseReviewPolicy:
    """Persist once after consumption; retries do not write or run paid work.

    Cancellation/deletion and stale revisions fail before an idempotent return.
    The storage lock serializes this with answers/cancellation/deletion. Inspect
    after an uncertain write acknowledgment. Later executors must still check
    run liveness, current revision and their own immutable request provenance.
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
            raise run_interaction.PromptConflict(
                "Response-review policy revision changed"
            )
        saved = validate_saved(record, interaction)
        if saved is not None:
            return saved
        if snapshot["internal_phase"] != "response_review_complete":
            raise run_interaction.PromptConflict("Consumed response review is required")
        policy = _derive(record, interaction)
        record["response_review_policy"] = policy.model_dump(mode="json")
        run_admission._write(record)
        return policy
