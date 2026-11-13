"""Read-only complete reviewer ballots and their historical ranking aggregation.

This projection never advances a run, chooses a quorum, retries a provider or
persists derived rankings. Invalid ballots remain evidence but cannot vote.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from . import (
    council,
    run_admission,
    run_interaction,
    run_stage1_inputs,
    run_stage2_continuation_execution,
    run_stage2_continuation_inputs,
    run_stage2_execution,
    run_stage2_inputs,
    storage,
)
from .consultation_contract import WireModel


class ResultChunk(WireModel):
    """One unchanged reviewer chunk and its exact request/attempt provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    turn: Literal[0, 1]
    provider_key: StrictStr
    inputs_sha256: run_stage1_inputs.Digest
    attempt: UUID
    retry_of: UUID | None
    response: run_stage2_execution.MemberResponse = Field(repr=False)


class ReviewerResult(WireModel):
    """Complete text/ballot with unchanged initial response and per-turn evidence.

    The legacy attempt/provider/response fields always identify the initial
    request. Read content for the exact complete text and chunks for all turns;
    neither response metadata nor provider usage is synthesized or summed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: StrictStr
    attempt: UUID
    retry_of: UUID | None
    provider_key: StrictStr
    response: run_stage2_execution.MemberResponse = Field(repr=False)
    content: StrictStr = Field(repr=False)
    chunks: tuple[ResultChunk, ...] = Field(repr=False)
    parsed_ranking: tuple[StrictStr, ...]


class InvalidRanking(WireModel):
    model: StrictStr
    reason: StrictStr


class AggregateRanking(WireModel):
    """Existing average-rank or pairwise output, without invented score fields."""

    model: StrictStr
    average_rank: StrictFloat | StrictInt
    rankings_count: StrictInt
    pairwise_wins: StrictInt | None = None
    win_percentage: StrictFloat | None = None
    schulze_rank: StrictInt | None = None


class RankingResults(WireModel):
    """Detached all-reviewer snapshot, not proof of consultation completion."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    source_revision: Annotated[StrictInt, Field(ge=1)]
    inputs_sha256: run_stage1_inputs.Digest
    ranking_method: Literal["pairwise", "average_rank"]
    label_to_model: dict[StrictStr, StrictStr]
    reviewers: tuple[ReviewerResult, ...] = Field(repr=False)
    aggregate: tuple[AggregateRanking, ...]
    invalid_rankings: tuple[InvalidRanking, ...]


def load_complete_results(run_id: str) -> RankingResults | None:
    """Project all stop-terminated reviewers, or None for any incomplete work.

    An initial stop or initial length plus a validated first-continuation stop
    supplies exact complete text. Length-only, uncertain, in-flight and
    unstarted work never establishes a partial quorum. Complete malformed ballots
    are reported and excluded using the existing ranking algorithms. All-invalid ballots yield no winner.
    Cancellation/configuration drift permit historical reads; deletion or
    corrupt causal receipts fail closed. Sources are validated under one lock.
    """
    from . import run_inspection

    with storage._storage_lock:
        if run_inspection.inspect(run_id) is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record = run_admission._read(run_id)
        interaction = run_interaction._validate_record(record)
        inputs = run_stage2_inputs.validate_saved(record, interaction)
        if inputs is None:
            raise run_stage2_execution.ExecutionConflict(
                "Saved Stage 2 input is required"
            )
        state = run_stage2_execution.validate_saved(record, inputs)
        continuation_inputs = run_stage2_continuation_inputs.validate_saved(
            record, interaction
        )
        continuation = run_stage2_continuation_execution.validate_saved(
            record, continuation_inputs
        )
        if state is None:
            return None
        following = (
            {operation.model: operation for operation in continuation.operations}
            if continuation
            else {}
        )
        operations = {operation.model: operation for operation in state.operations}
        reviewers = []
        for request in inputs.requests:
            operation = operations.get(request.model)
            if operation is None or operation.result is None:
                return None
            chunks = [
                ResultChunk(
                    turn=0,
                    provider_key=state.provider_key,
                    inputs_sha256=state.inputs_sha256,
                    attempt=operation.attempt,
                    retry_of=operation.retry_of,
                    response=operation.result.model_copy(deep=True),
                )
            ]
            if operation.result.finish_reason == "length":
                next_op = following.get(request.model)
                if (
                    next_op is None
                    or next_op.result is None
                    or next_op.result.finish_reason != "stop"
                ):
                    return None
                chunks.append(
                    ResultChunk(
                        turn=1,
                        provider_key=continuation.provider_key,
                        inputs_sha256=next_op.inputs_sha256,
                        attempt=next_op.attempt,
                        retry_of=next_op.retry_of,
                        response=next_op.result.model_copy(deep=True),
                    )
                )
            content = "".join(chunk.response.content for chunk in chunks)
            reviewers.append(
                ReviewerResult(
                    model=request.model,
                    attempt=operation.attempt,
                    retry_of=operation.retry_of,
                    provider_key=state.provider_key,
                    response=operation.result.model_copy(deep=True),
                    content=content,
                    chunks=tuple(chunks),
                    parsed_ranking=tuple(
                        council.parse_ranking_from_text(
                            content,
                            expected_labels=list(inputs.label_to_model),
                        )
                    ),
                )
            )
        ballots = [
            {"model": reviewer.model, "parsed_ranking": list(reviewer.parsed_ranking)}
            for reviewer in reviewers
        ]
        aggregate, invalid = (
            council.calculate_pairwise_rankings
            if inputs.ranking_method == "pairwise"
            else council.calculate_aggregate_rankings
        )(ballots, inputs.label_to_model)
        return RankingResults(
            source_revision=inputs.source_revision,
            inputs_sha256=state.inputs_sha256,
            ranking_method=inputs.ranking_method,
            label_to_model=dict(inputs.label_to_model),
            reviewers=tuple(reviewers),
            aggregate=tuple(AggregateRanking.model_validate(row) for row in aggregate),
            invalid_rankings=tuple(
                InvalidRanking.model_validate(row) for row in invalid
            ),
        )
