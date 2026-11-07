"""Read-only complete member answers from validated retained Stage 1 chunks.

This is a historical projection, not stage completion, a scheduling decision or
permission to execute/retry anything. Source receipts are never rewritten.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    run_admission,
    run_interaction,
    run_stage1_continuation_execution,
    run_stage1_continuation_inputs,
    run_stage1_execution,
    run_stage1_inputs,
    storage,
)
from .consultation_contract import WireModel


class ResultChunk(WireModel):
    """One original response and its request/attempt provenance, never merged."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    turn: Literal[0, 1]
    provider_key: StrictStr
    inputs_sha256: run_stage1_inputs.Digest
    attempt: UUID
    retry_of: UUID | None
    response: run_stage1_execution.MemberResponse = Field(repr=False)


class MemberResult(WireModel):
    """Detached complete member text; not a complete council consultation."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: StrictStr
    source_revision: Annotated[StrictInt, Field(ge=1)]
    initial_inputs_sha256: run_stage1_inputs.Digest
    content: StrictStr = Field(repr=False)
    chunks: tuple[ResultChunk, ...] = Field(repr=False)


def _complete_result(
    model: str,
    inputs: run_stage1_inputs.Stage1Inputs,
    initial: run_stage1_execution.ExecutionState | None,
    continuation: run_stage1_continuation_execution.ExecutionState | None,
) -> MemberResult | None:
    """Project one member from already validated states while holding the lock."""
    op = (
        next((o for o in initial.operations if o.model == model), None)
        if initial
        else None
    )
    if op is None or op.result is None:
        return None
    chunks = [
        ResultChunk(
            turn=0,
            provider_key=initial.provider_key,
            inputs_sha256=initial.inputs_sha256,
            attempt=op.attempt,
            retry_of=op.retry_of,
            response=op.result.model_copy(deep=True),
        )
    ]
    if op.result.finish_reason == "length":
        following = (
            next((o for o in continuation.operations if o.model == model), None)
            if continuation
            else None
        )
        if (
            following is None
            or following.result is None
            or following.result.finish_reason != "stop"
        ):
            return None
        chunks.append(
            ResultChunk(
                turn=1,
                provider_key=continuation.provider_key,
                inputs_sha256=following.inputs_sha256,
                attempt=following.attempt,
                retry_of=following.retry_of,
                response=following.result.model_copy(deep=True),
            )
        )
    return MemberResult(
        model=model,
        source_revision=inputs.source_revision,
        initial_inputs_sha256=run_stage1_inputs._digest(inputs),
        content="".join(chunk.response.content for chunk in chunks),
        chunks=tuple(chunks),
    )


def _load(
    run_id: str,
) -> tuple[
    run_stage1_inputs.Stage1Inputs,
    run_stage1_execution.ExecutionState | None,
    run_stage1_continuation_execution.ExecutionState | None,
]:
    from . import run_inspection

    if run_inspection.inspect(run_id) is None:
        raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
    record = run_admission._read(run_id)
    interaction = run_interaction._validate_record(record)
    inputs = run_stage1_inputs.validate_saved(record, interaction)
    if inputs is None:
        raise run_stage1_execution.ExecutionConflict("Saved Stage 1 input is required")
    initial = run_stage1_execution.validate_saved(record, inputs)
    continuation_inputs = run_stage1_continuation_inputs.validate_saved(
        record, interaction
    )
    continuation = run_stage1_continuation_execution.validate_saved(
        record, continuation_inputs
    )
    return inputs, initial, continuation


def load_complete_result(run_id: str, model: str) -> MemberResult | None:
    """Read exact stop-terminated text, or None while no complete answer exists.

    Supports an initial stop or initial length followed by a first-continuation
    stop. Length-only, unstarted and uncertain work never becomes a final answer.
    Historical reads survive cancellation/configuration drift, not deletion or
    corrupt causal evidence. Metadata stays per chunk; usage is not summed and
    reasoning is not spliced. Reads do not persist state or dispatch work.
    """
    with storage._storage_lock:
        inputs, initial, continuation = _load(run_id)
        if model not in {r.model for r in inputs.requests}:
            raise run_stage1_execution.ExecutionConflict(
                "Member does not identify saved Stage 1 input"
            )
        return _complete_result(model, inputs, initial, continuation)


def load_complete_results(run_id: str) -> tuple[MemberResult, ...] | None:
    """Return every complete answer in roster order, or None for incomplete work.

    One locked read gives a coherent whole-roster view, never a partial quorum or
    permission to advance. Cancellation retains historical results; deletion and
    corrupt evidence fail closed. No conversation/export/run completion is saved.
    """
    with storage._storage_lock:
        inputs, initial, continuation = _load(run_id)
        results = []
        for request in inputs.requests:
            result = _complete_result(request.model, inputs, initial, continuation)
            if result is None:
                return None
            results.append(result)
        return tuple(results)
