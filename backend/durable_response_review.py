"""Provider-free, durable review of a complete initial council roster.

Explicit calls save or consume one human boundary. No later stage, provider,
worker loop, public endpoint or capability upgrade is enabled here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, StrictStr, model_validator

from . import (
    durable_prequery,
    run_admission,
    run_interaction,
    run_response_review,
    run_stage1_inputs,
    storage,
)
from .consultation_contract import (
    AnswerRequest,
    PromptInstance,
    PromptSchema,
    WireModel,
)
from .interactive import FieldType, PromptType

POINT = "response_review_v1"


class ResponseReviewState(WireModel):
    """Retain source identity and raw accepted input, not downstream policy."""

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    phase: Literal["waiting_answer", "complete"]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    source_sha256: run_stage1_inputs.Digest
    label_to_model: dict[StrictStr, StrictStr]
    answer: AnswerRequest | None

    @model_validator(mode="after")
    def coherent(self) -> ResponseReviewState:
        if (self.phase == "complete") != (self.answer is not None):
            raise ValueError("Response-review phase does not match accepted input")
        return self

    def to_continuation(self) -> run_interaction.Continuation:
        state = ResponseReviewState.model_validate(self.model_dump(mode="python"))
        return run_interaction.Continuation(
            point=POINT, state=state.model_dump(mode="json")
        )


def _validate_prompt(prompt: PromptSchema, models: list[str]) -> None:
    # Display wording/defaults are historical. Only finite field/option meaning
    # is interpreted; accepted omissions, order and whitespace remain untouched.
    fields = {field.id: field for field in prompt.fields}
    if (
        prompt.prompt_id != "response_review"
        or prompt.prompt_type != PromptType.RESPONSE_REVIEW
        or fields.keys() != {"exclude_responses", "gap_concerns"}
        or fields["exclude_responses"].type != FieldType.MULTI_SELECT
        or [option.value for option in fields["exclude_responses"].options] != models
        or fields["gap_concerns"].type != FieldType.TEXT
    ):
        raise ValueError("Unsupported response-review field meaning")


def validate_checkpoint(
    record: dict[str, Any], interaction: run_interaction.InteractionState, index: int
) -> ResponseReviewState:
    """Validate the exact historical source, schema and accepted derivation."""
    try:
        if (
            index != len(interaction.prompts) - 1
            or index < 1
            or interaction.prompts[index - 1].continuation.point
            != durable_prequery.POINT
            or interaction.prompts[index - 1].phase != "consumed"
        ):
            raise ValueError("Response review must follow completed pre-query")
        entry = interaction.prompts[index]
        if entry.continuation.point != POINT:
            raise ValueError("Unsupported review continuation")
        state = ResponseReviewState.model_validate(entry.continuation.state)
        prepared = run_response_review._prepare_record(
            record, interaction, prompt=entry.instance.prompt
        )
        if (
            prepared is None
            or state.phase != "waiting_answer"
            or state.source_revision != prepared.source_revision
            or state.source_sha256 != prepared.source_sha256
            or state.label_to_model != prepared.label_to_model
        ):
            raise ValueError("Response-review source differs from complete Stage 1")
        _validate_prompt(
            entry.instance.prompt, [result.model for result in prepared.results]
        )
        if entry.answer is not None:
            state = ResponseReviewState(
                **{
                    **state.model_dump(mode="python"),
                    "phase": "complete",
                    "answer": entry.answer,
                }
            )
        if entry.phase == "consumed":
            assert entry.next_continuation is not None
            if not run_interaction._same_continuation(
                entry.next_continuation, state.to_continuation()
            ):
                raise ValueError("Response-review result differs from accepted answer")
        return state
    except (KeyError, ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Response-review checkpoint is incompatible; repair required"
        ) from exc


def advance(run_id: str, *, expected_revision: int) -> dict[str, Any]:
    """Save or consume one explicit boundary, never run the next council stage.

    Reload after a lost acknowledgment. Waiting/completed reads preserve the
    saved instance and bytes. Generic run_interaction.answer retains the first
    valid answer; this coordinator consumes it once without applying defaults.
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
                "Response-review revision changed; reload this run"
            )
        if interaction.prompts and interaction.prompts[-1].continuation.point == POINT:
            entry = interaction.prompts[-1]
            state = validate_checkpoint(
                record, interaction, len(interaction.prompts) - 1
            )
            if entry.phase != "answered":
                return snapshot
            run_interaction.advance(
                run_id, str(entry.instance.instance_id), state.to_continuation()
            )
        else:
            if snapshot["internal_phase"] != "stage1_complete":
                raise run_interaction.PromptConflict(
                    "Complete Stage 1 results are required"
                )
            prepared = run_response_review._prepare_record(record, interaction)
            if prepared is None:
                raise run_interaction.PromptConflict(
                    "Complete Stage 1 results are required"
                )
            _validate_prompt(
                prepared.prompt, [result.model for result in prepared.results]
            )
            run_interaction.put_prompt(
                run_id,
                PromptInstance(
                    run_id=UUID(run_id), instance_id=uuid4(), prompt=prepared.prompt
                ),
                ResponseReviewState(
                    version=1,
                    phase="waiting_answer",
                    source_revision=prepared.source_revision,
                    source_sha256=prepared.source_sha256,
                    label_to_model=prepared.label_to_model,
                    answer=None,
                ).to_continuation(),
                expected_revision=expected_revision,
            )
        result = run_inspection.inspect(run_id)
        assert result is not None
        return result
