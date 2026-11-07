"""Pinned ranking requests after review, without dispatch or stage completion.

Version 1 supports the path with negative mode disabled and no context reduction.
It retains the entire reviewer roster, even when answers have been excluded.
Later executors must guard liveness, configuration and uncertain external work.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_prequery,
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
from .council import (
    build_label_mapping,
    build_stage2_prompt_modifier,
    build_stage2_ranking_prompt,
)


class Stage2InputConflict(ValueError):
    """Ranking requests cannot safely be prepared at this boundary."""


class Stage2Inputs(WireModel):
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
    ranking_method: Literal["pairwise", "average_rank"]
    endpoint: StrictStr
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    input_token_bound: Annotated[StrictInt, Field(gt=0)]
    messages: tuple[run_stage1_inputs.PromptMessage, ...] = Field(repr=False)
    requests: tuple[run_stage1_inputs.InitialRequest, ...] = Field(repr=False)


def _derive(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> Stage2Inputs:
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
        raise Stage2InputConflict(
            "Saved review, response and council policies are required"
        )
    if review.next_stage != "stage2" or review.negative_enabled:
        raise Stage2InputConflict(
            "Active responses and a disabled negative stage are required"
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
        raise Stage2InputConflict("Exact complete Stage 1 source is required")
    # Reuse the validated source roster, never mapping insertion order. Answer
    # exclusions do not remove reviewers: the legacy stage asks every member.
    results_by_model = {result.model: result for result in prepared.results}
    responses = [
        {"model": model, "response": results_by_model[model].content}
        for model in review.active_models
    ]
    labels, _ = build_label_mapping(responses)
    prequery_index = next(
        index
        for index, entry in enumerate(interaction.prompts)
        if entry.continuation.point == durable_prequery.POINT
    )
    prequery = durable_prequery.validate_checkpoint(record, interaction, prequery_index)
    modifier = build_stage2_prompt_modifier(
        response.system_prompt_modifier,
        decision_support=record["request"]["refine"] and not prequery.answer.skipped,
    )
    messages = [
        {
            "role": "user",
            "content": build_stage2_ranking_prompt(
                response.user_query, responses, labels, modifier
            ),
        }
    ]
    bound = model_context.token_bound(messages)
    requests = []
    for budget in budgets.models:
        if bound > budget.input_budget:
            raise Stage2InputConflict(
                "Stage 2 messages require unsupported guarded context reduction"
            )
        payload = {
            "model": budget.model,
            "messages": messages,
            "max_tokens": budget.output_budget,
        }
        if budget.reasoning:
            payload["reasoning"] = {"effort": "high"}
        requests.append(
            run_stage1_inputs.InitialRequest(
                model=budget.model,
                input_budget=budget.input_budget,
                output_budget=budget.output_budget,
                payload_json=run_stage1_inputs._json(payload),
            )
        )
    return Stage2Inputs(
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
        ranking_method="pairwise"
        if selection.pairwise_ranking_enabled
        else "average_rank",
        endpoint=request.endpoint,
        timeout_seconds=120.0,
        input_token_bound=bound,
        messages=tuple(
            run_stage1_inputs.PromptMessage.model_validate(m) for m in messages
        ),
        requests=tuple(requests),
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> Stage2Inputs | None:
    """Validate retained requests without current settings, providers or writes."""
    if "stage2_inputs" not in record:
        return None
    try:
        raw = record["stage2_inputs"]
        saved = Stage2Inputs.model_validate(raw)
        if (
            record["phase"] != "prepared"
            or saved.model_dump(mode="json") != raw
            or saved != _derive(record, interaction)
        ):
            raise ValueError("Stage 2 input differs from its historical sources")
        return saved
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved Stage 2 inputs are incompatible; repair required"
        ) from exc


def summary(saved: Stage2Inputs) -> dict[str, Any]:
    """Expose identity and counts without duplicating private answer/prompt text."""
    return {
        "version": saved.version,
        "source_revision": saved.source_revision,
        "sha256": run_stage1_inputs._digest(saved),
        "request_count": len(saved.requests),
        "response_count": len(saved.active_models),
        "input_token_bound": saved.input_token_bound,
        "ranking_method": saved.ranking_method,
    }


def bind(run_id: str, *, expected_revision: int) -> Stage2Inputs:
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
            raise run_interaction.PromptConflict("Stage 2 input revision changed")
        saved = validate_saved(record, interaction)
        if saved is not None:
            # Execution changes inspection phase, not the pinned source revision.
            # Recovery may repeat preparation without writing or dispatching.
            return saved
        if snapshot is None or snapshot["internal_phase"] != "response_review_complete":
            raise Stage2InputConflict("Consumed response review is required")
        prepared = _derive(record, interaction)
        record["stage2_inputs"] = prepared.model_dump(mode="json")
        run_admission._write(record)
        return prepared
