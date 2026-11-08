"""Exact first-continuation inputs, without execution or context reduction.

Initial partial results remain immutable and separate. A prepared value is not
dispatch permission; continuation attempts need their own durable guard.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    council_request_policy,
    model_context,
    run_admission,
    run_interaction,
    run_stage1_inputs,
    run_stage2_execution,
    run_stage2_inputs,
    storage,
)
from .consultation_contract import WireModel

CONTINUATION_INSTRUCTION = (
    "Continue exactly where the previous review stopped. Do not repeat earlier "
    "text or add a new introduction. Complete the requested review and ranking."
)


class ContinuationInputConflict(ValueError):
    """A supported first continuation cannot be prepared from retained sources."""


class FirstContinuationInput(WireModel):
    """Frozen non-secret request provenance; payload() always returns a copy."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    turn: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    initial_inputs_sha256: run_stage1_inputs.Digest
    initial_attempt: UUID
    initial_operation_sha256: run_stage1_inputs.Digest
    initial_response_sha256: run_stage1_inputs.Digest
    initial_provider_key: StrictStr
    model: StrictStr
    endpoint: StrictStr
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    input_budget: Annotated[StrictInt, Field(gt=0)]
    output_budget: Annotated[StrictInt, Field(gt=0)]
    input_token_bound: Annotated[StrictInt, Field(gt=0)]
    payload_json: StrictStr = Field(repr=False)

    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


class ContinuationInputs(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    requests: tuple[FirstContinuationInput, ...]


def _sources(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> tuple[run_stage2_inputs.Stage2Inputs, run_stage2_execution.ExecutionState]:
    inputs = run_stage2_inputs.validate_saved(record, interaction)
    execution = run_stage2_execution.validate_saved(record, inputs)
    if inputs is None or execution is None:
        raise ContinuationInputConflict("Saved initial Stage 2 work is required")
    return inputs, execution


def _derive(
    record: dict[str, Any],
    interaction: run_interaction.InteractionState,
    inputs: run_stage2_inputs.Stage2Inputs,
    execution: run_stage2_execution.ExecutionState,
    model: str,
) -> FirstContinuationInput:
    policy = council_request_policy.validate_saved(record, interaction)
    if policy is None or policy.max_continuations < 1:
        raise ContinuationInputConflict("Saved policy does not allow a continuation")
    initial = next((r for r in inputs.requests if r.model == model), None)
    op = next((o for o in execution.operations if o.model == model), None)
    if (
        initial is None
        or op is None
        or op.result is None
        or op.phase != "complete"
        or op.result.finish_reason != "length"
    ):
        raise ContinuationInputConflict(
            "A saved length-limited reviewer result is required"
        )
    payload = initial.payload()
    assistant: dict[str, Any] = {"role": "assistant", "content": op.result.content}
    if op.result.reasoning_details:
        # The new canonical payload detaches the nested metadata before return.
        assistant["reasoning_details"] = op.result.reasoning_details
    payload["messages"].extend(
        [assistant, {"role": "user", "content": CONTINUATION_INSTRUCTION}]
    )
    bound = model_context.token_bound(payload["messages"])
    if bound > initial.input_budget:
        raise ContinuationInputConflict(
            "Continuation requires unsupported guarded context reduction"
        )
    return FirstContinuationInput(
        version=1,
        turn=1,
        source_revision=inputs.source_revision,
        initial_inputs_sha256=run_stage1_inputs._digest(inputs),
        initial_attempt=op.attempt,
        initial_operation_sha256=run_stage1_inputs._digest(op),
        initial_response_sha256=run_stage1_inputs._digest(op.result),
        initial_provider_key=execution.provider_key,
        model=model,
        endpoint=inputs.endpoint,
        timeout_seconds=inputs.timeout_seconds,
        input_budget=initial.input_budget,
        output_budget=initial.output_budget,
        input_token_bound=bound,
        payload_json=run_stage1_inputs._json(payload),
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> ContinuationInputs | None:
    """Reconstruct retained inputs from exact initial sources, without writes."""
    if "stage2_continuation_inputs" not in record:
        return None
    try:
        raw = record["stage2_continuation_inputs"]
        saved = ContinuationInputs.model_validate(raw)
        inputs, execution = _sources(record, interaction)
        models = [r.model for r in saved.requests]
        if (
            record["phase"] != "prepared"
            or not models
            or len(set(models)) != len(models)
            or models != [r.model for r in inputs.requests if r.model in models]
            or run_stage1_inputs._json(saved.model_dump(mode="json"))
            != run_stage1_inputs._json(raw)
            or any(
                r != _derive(record, interaction, inputs, execution, r.model)
                for r in saved.requests
            )
        ):
            raise ValueError("Continuation inputs differ from their source")
        return saved
    except (KeyError, TypeError, ValueError):
        raise run_admission.AdmissionUnavailable(
            "Saved Stage 2 continuation inputs are incompatible; repair required"
        ) from None


def summary(saved: ContinuationInputs) -> dict[str, Any]:
    """Compact identities and counts; no partial response or prompt content."""
    return {
        "version": saved.version,
        "sha256": run_stage1_inputs._digest(saved),
        "requests": [
            {
                "model": r.model,
                "turn": r.turn,
                "source_revision": r.source_revision,
                "initial_attempt": str(r.initial_attempt),
                "sha256": run_stage1_inputs._digest(r),
                "input_token_bound": r.input_token_bound,
            }
            for r in saved.requests
        ],
    }


def bind(run_id: str, model: str, *, expected_revision: int) -> FirstContinuationInput:
    """Pin one reviewer's first continuation; never retry, fit, schedule or dispatch."""
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        run_inspection.inspect(run_id)
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Continuation input revision changed")
        inputs, execution = _sources(record, interaction)
        if interaction.revision != inputs.source_revision:
            raise ContinuationInputConflict("Stage 2 interaction boundary has closed")
        run_stage1_inputs._check_current(record, interaction)
        saved = validate_saved(record, interaction)
        if saved is not None:
            known = next((r for r in saved.requests if r.model == model), None)
            if known is not None:
                return known
        prepared = _derive(record, interaction, inputs, execution, model)
        by_model = {r.model: r for r in saved.requests} if saved is not None else {}
        by_model[model] = prepared
        result = ContinuationInputs(
            version=1,
            requests=tuple(
                by_model[r.model] for r in inputs.requests if r.model in by_model
            ),
        )
        record["stage2_continuation_inputs"] = result.model_dump(mode="json")
        run_admission._write(record)
        return prepared


def load(run_id: str, model: str) -> FirstContinuationInput:
    """Read historical exact inputs, including cancelled runs; not runnable."""
    from . import run_inspection

    with storage._storage_lock:
        run_inspection.inspect(run_id)
        record = run_admission._read(run_id)
        saved = (
            validate_saved(record, run_interaction._validate_record(record))
            if record is not None
            else None
        )
        known = (
            next((r for r in saved.requests if r.model == model), None)
            if saved
            else None
        )
        if known is None:
            raise ContinuationInputConflict(
                "First continuation inputs have not been saved"
            )
        return known
