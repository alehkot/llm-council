"""Read-only complete raw critiques of the saved active answer roster.

A provider stop is transport evidence, not a valid critique or score. This
projection does not parse, advance, retry, schedule, or update a run.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    run_admission,
    run_interaction,
    run_negative_execution,
    run_negative_inputs,
    run_stage1_inputs,
    storage,
)
from .consultation_contract import WireModel


class CritiqueResult(WireModel):
    """One detached normalized response bound to its saved request and attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: StrictStr
    self_label: StrictStr
    peer_labels: tuple[StrictStr, ...]
    request_sha256: run_stage1_inputs.Digest
    attempt: UUID
    retry_of: UUID | None
    response: run_negative_execution.MemberResponse = Field(repr=False)


class NegativeResults(WireModel):
    """Stop-terminated raw evidence, not critique validity or stage completion."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    source_revision: Annotated[StrictInt, Field(ge=1)]
    inputs_sha256: run_stage1_inputs.Digest
    stage1_source_sha256: run_stage1_inputs.Digest
    review_policy_sha256: run_stage1_inputs.Digest
    negative_prompt_source: Literal["request", "review"]
    provider_key: StrictStr
    label_to_model: dict[StrictStr, StrictStr]
    critiques: tuple[CritiqueResult, ...] = Field(repr=False)


def load_complete_results(run_id: str) -> NegativeResults | None:
    """Return exact stopped responses in saved active-roster order, or None.

    Missing, uncertain, in-flight and length-only work do not establish a quorum.
    Cancellation and current settings drift preserve historical evidence; deletion
    and corrupt causal receipts fail closed. No prompt text or credentials are
    added. The explicit result contains private raw responses; it is not a
    polling or public API payload.
    """
    from . import run_inspection

    with storage._storage_lock:
        if run_inspection.inspect(run_id) is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record = run_admission._read(run_id)
        if record is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        interaction = run_interaction._validate_record(record)
        inputs = run_negative_inputs.validate_saved(record, interaction)
        if inputs is None:
            raise run_negative_execution.ExecutionConflict(
                "Saved initial negative-stage inputs are required"
            )
        state = run_negative_execution.validate_saved(record, inputs)
        if state is None:
            return None
        operations = {op.model: op for op in state.operations}
        results: list[CritiqueResult] = []
        for request in inputs.requests:
            operation = operations.get(request.model)
            if (
                operation is None
                or operation.result is None
                or operation.result.finish_reason != "stop"
            ):
                return None
            results.append(
                CritiqueResult(
                    model=request.model,
                    self_label=request.self_label,
                    peer_labels=request.peer_labels,
                    request_sha256=run_stage1_inputs._digest(request),
                    attempt=operation.attempt,
                    retry_of=operation.retry_of,
                    response=operation.result.model_copy(deep=True),
                )
            )
        return NegativeResults(
            source_revision=inputs.source_revision,
            inputs_sha256=state.inputs_sha256,
            stage1_source_sha256=inputs.stage1_source_sha256,
            review_policy_sha256=inputs.review_policy_sha256,
            negative_prompt_source=inputs.negative_prompt_source,
            provider_key=state.provider_key,
            label_to_model=dict(inputs.label_to_model),
            critiques=tuple(results),
        )
