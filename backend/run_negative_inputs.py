"""Pinned negative critiques for active answers, without dispatch.

Only saved explicit request/review prompts and already-fitting budgets are
supported. Prompt generation, context reduction and guarded execution remain
separate prerequisites; no preparation authorizes a provider call.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_response_review,
    model_context,
    prequery_policy,
    response_review_policy,
    run_admission,
    run_interaction,
    run_response_review,
    run_stage1_inputs,
    storage,
)
from .consultation_contract import WireModel
from .council import build_label_mapping, build_negative_evaluation_prompt


class NegativeInputConflict(ValueError):
    """Negative requests cannot safely be prepared at this boundary."""


class NegativeRequest(run_stage1_inputs.InitialRequest):
    self_label: StrictStr
    peer_labels: tuple[StrictStr, ...]
    input_token_bound: Annotated[StrictInt, Field(gt=0)]


class NegativeInputs(WireModel):
    """Exact detached provider inputs, not execution or retry authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    response_policy_sha256: run_stage1_inputs.Digest
    review_policy_sha256: run_stage1_inputs.Digest
    stage1_source_sha256: run_stage1_inputs.Digest
    selection_sha256: run_stage1_inputs.Digest
    model_budgets_sha256: run_stage1_inputs.Digest
    request_policy_sha256: run_stage1_inputs.Digest
    active_models: tuple[StrictStr, ...]
    label_to_model: dict[StrictStr, StrictStr]
    negative_prompt_source: Literal["request", "review"]
    endpoint: StrictStr
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    requests: tuple[NegativeRequest, ...] = Field(repr=False)


def _derive(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> NegativeInputs:
    response = prequery_policy.validate_saved(record, interaction)
    review = response_review_policy.validate_saved(record, interaction)
    selection = council_selection.validate_saved(record, interaction)
    budgets = council_model_budgets.validate_saved(record, interaction)
    request = council_request_policy.validate_saved(record, interaction)
    if (
        response is None
        or review is None
        or selection is None
        or budgets is None
        or request is None
    ):
        raise NegativeInputConflict(
            "Saved review, response and council policies are required"
        )
    if review.next_stage != "negative" or not review.negative_enabled:
        raise NegativeInputConflict(
            "Active responses and an enabled negative stage are required"
        )
    if (
        review.negative_prompt_source not in ("request", "review")
        or not review.negative_prompt
    ):
        raise NegativeInputConflict(
            "A saved explicit negative prompt is required; generation is unsupported"
        )
    review_entry = next(
        entry
        for entry in interaction.prompts
        if entry.continuation.point == durable_response_review.POINT
    )
    prepared = run_response_review._prepare_record(
        record, interaction, prompt=review_entry.instance.prompt
    )
    if prepared is None or prepared.source_sha256 != review.stage1_source_sha256:
        raise NegativeInputConflict("Exact complete Stage 1 source is required")
    # Unlike ranking, legacy negative critiques ask only active answer owners.
    # Preserve their source order and relabel surviving responses without holes.
    results_by_model = {result.model: result for result in prepared.results}
    responses = [
        {"model": model, "response": results_by_model[model].content}
        for model in review.active_models
    ]
    labels, _ = build_label_mapping(responses)
    budgets_by_model = {budget.model: budget for budget in budgets.models}
    requests = []
    for index, model in enumerate(review.active_models):
        budget = budgets_by_model[model]
        messages = [
            {
                "role": "user",
                "content": build_negative_evaluation_prompt(
                    response.user_query,
                    review.negative_prompt,
                    responses,
                    labels,
                    index,
                ),
            }
        ]
        bound = model_context.token_bound(messages)
        if bound > budget.input_budget:
            raise NegativeInputConflict(
                "Negative messages require unsupported guarded context reduction"
            )
        payload = {
            "model": budget.model,
            "messages": messages,
            "max_tokens": budget.output_budget,
        }
        if budget.reasoning:
            payload["reasoning"] = {"effort": "high"}
        requests.append(
            NegativeRequest(
                self_label=labels[index],
                peer_labels=tuple(
                    label for i, label in enumerate(labels) if i != index
                ),
                input_token_bound=bound,
                model=budget.model,
                input_budget=budget.input_budget,
                output_budget=budget.output_budget,
                payload_json=run_stage1_inputs._json(payload),
            )
        )
    return NegativeInputs(
        version=1,
        source_revision=review.source_revision,
        response_policy_sha256=run_stage1_inputs._digest(response),
        review_policy_sha256=run_stage1_inputs._digest(review),
        stage1_source_sha256=review.stage1_source_sha256,
        selection_sha256=run_stage1_inputs._digest(selection),
        model_budgets_sha256=run_stage1_inputs._digest(budgets),
        request_policy_sha256=run_stage1_inputs._digest(request),
        active_models=review.active_models,
        label_to_model=review.label_to_model,
        negative_prompt_source=review.negative_prompt_source,
        endpoint=request.endpoint,
        timeout_seconds=120.0,
        requests=tuple(requests),
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> NegativeInputs | None:
    """Validate retained requests without current settings, providers or writes."""
    if "negative_inputs" not in record:
        return None
    try:
        raw = record["negative_inputs"]
        saved = NegativeInputs.model_validate(raw)
        if (
            record["phase"] != "prepared"
            or saved.model_dump(mode="json") != raw
            or saved != _derive(record, interaction)
        ):
            raise ValueError("Negative input differs from its historical sources")
        return saved
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved negative inputs are incompatible; repair required"
        ) from exc


def summary(saved: NegativeInputs) -> dict[str, Any]:
    """Expose identity and counts without duplicating private answer/prompt text."""
    return {
        "version": saved.version,
        "source_revision": saved.source_revision,
        "sha256": run_stage1_inputs._digest(saved),
        "request_count": len(saved.requests),
        "response_count": len(saved.active_models),
        "input_token_bounds": [request.input_token_bound for request in saved.requests],
        "negative_prompt_source": saved.negative_prompt_source,
    }


def bind(run_id: str, *, expected_revision: int) -> NegativeInputs:
    """Persist deterministic preparation once, without scheduling any model work.

    Current settings do not replace historical choices. A later execution guard
    must explicitly reconcile incompatible settings before dispatch, in addition
    to checking cancellation and its own exact attempt/request identity.
    """
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Negative input revision changed")
        saved = validate_saved(record, interaction)
        if saved is not None:
            # Execution changes inspection phase, not the pinned source revision.
            # Recovery may repeat preparation without writing or dispatching.
            return saved
        if snapshot is None or snapshot["internal_phase"] != "response_review_complete":
            raise NegativeInputConflict("Consumed response review is required")
        prepared = _derive(record, interaction)
        record["negative_inputs"] = prepared.model_dump(mode="json")
        run_admission._write(record)
        return prepared
