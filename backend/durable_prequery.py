"""Internal, provider-free pre-query checkpoint after optional refinement.

This explicitly requested boundary does not enable interactive mode globally,
queue council work, or authorize using today's settings to resume a pipeline.
It retains accepted values (including omissions and skip), not invented answers.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, StrictStr, model_validator

from . import durable_refinement, run_admission, run_interaction, storage
from .consultation_contract import (
    AnswerRequest,
    PromptInstance,
    PromptSchema,
    WireModel,
)
from .interactive import FieldType, PromptType, get_pre_query_prompt
from .refinement import RefinementState

POINT = "pre_query_v1"
_OPTIONS = {
    "domain": {
        "general",
        "coding",
        "legal",
        "medical",
        "creative",
        "technical",
        "business",
    },
    "style": {"concise", "detailed", "comprehensive"},
    "priority": {"accuracy", "clarity", "completeness", "actionability"},
}


class PreQueryState(WireModel):
    """Exact input/answer boundary; no provider or resolved pipeline configuration."""

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    phase: Literal["waiting_answer", "complete"]
    user_query: StrictStr
    answer: AnswerRequest | None

    @model_validator(mode="after")
    def coherent(self) -> PreQueryState:
        if (self.phase == "complete") != (self.answer is not None):
            raise ValueError("Pre-query phase does not match accepted input")
        return self

    def to_continuation(self) -> run_interaction.Continuation:
        state = PreQueryState.model_validate(self.model_dump(mode="python"))
        return run_interaction.Continuation(
            point=POINT, state=state.model_dump(mode="json")
        )

    @classmethod
    def from_continuation(
        cls, continuation: run_interaction.Continuation
    ) -> PreQueryState:
        if continuation.point != POINT:
            raise ValueError("Unsupported pre-query continuation")
        return cls.model_validate(continuation.state)


def _validate_prompt(prompt: PromptSchema) -> None:
    # Labels, descriptions and displayed defaults are retained as saved. Only
    # the finite v1 field/option meanings are interpreted here; no defaults are
    # inserted into an accepted response.
    if (
        prompt.prompt_id != "pre_query_config"
        or prompt.prompt_type != PromptType.PRE_QUERY
    ):
        raise ValueError("Not a pre-query configuration prompt")
    fields = {field.id: field for field in prompt.fields}
    if fields.keys() != _OPTIONS.keys():
        raise ValueError("Unsupported pre-query fields")
    for key, field in fields.items():
        if (
            field.type != FieldType.SELECT
            or {option.value for option in field.options} != _OPTIONS[key]
        ):
            raise ValueError("Unsupported pre-query field meaning")


def _source_query(
    record: dict[str, Any], previous: list[run_interaction.PromptCheckpoint]
) -> str:
    if not record["request"]["refine"]:
        if previous:
            raise ValueError("Unrefined run has earlier prompts")
        return record["request"]["content"]
    if not previous or previous[-1].phase != "consumed":
        raise ValueError("Refinement has not finished")
    refined = RefinementState.start(record["request"]["content"])
    for entry in previous:
        waiting = durable_refinement._validate_checkpoint(record, entry)
        assert waiting.last_suggestion is not None
        if (
            refined.phase != "needs_suggestion"
            or refined.accept_suggestion(waiting.last_suggestion.model_dump())
            != waiting
        ):
            raise ValueError("Refinement history does not form one continuation")
        refined = durable_refinement._derive_answer(record, entry)
    if refined.phase != "complete":
        raise ValueError("Refinement has not finished")
    return refined.refined_prompt or refined.original_query


def validate_checkpoint(
    record: dict[str, Any], interaction: run_interaction.InteractionState, index: int
) -> PreQueryState:
    """Read and validate one supported final boundary, including its derivation."""
    try:
        if index != len(interaction.prompts) - 1:
            raise ValueError("Pre-query must be the final supported boundary")
        entry = interaction.prompts[index]
        state = PreQueryState.from_continuation(entry.continuation)
        _validate_prompt(entry.instance.prompt)
        if state.phase != "waiting_answer" or state.user_query != _source_query(
            record, interaction.prompts[:index]
        ):
            raise ValueError("Pre-query input differs from saved source")
        if entry.answer is not None:
            # Keep raw accepted values, omission and explicit skip distinct.
            state = PreQueryState(
                version=1,
                phase="complete",
                user_query=state.user_query,
                answer=entry.answer,
            )
        if entry.phase == "consumed":
            assert entry.next_continuation is not None
            if not run_interaction._same_continuation(
                entry.next_continuation, state.to_continuation()
            ):
                raise ValueError("Pre-query result differs from accepted answer")
        return state
    except (ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Pre-query checkpoint is incompatible; repair required"
        ) from exc


def advance(run_id: str, *, expected_revision: int) -> dict[str, Any]:
    """Create or consume one pre-query boundary, never dispatch council work.

    Creation is allowed only for fresh unrefined admission or after complete
    refinement. Waiting and completed reads are byte-pure. Each mutation uses
    the current revision under the existing storage lock; reload after a lost
    acknowledgment. No provider credential, current settings or input queue is
    needed. Use run_inspection.inspect for ordinary reads rather than this method.
    """
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None or snapshot["internal_phase"] == "initializing":
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        interaction_data = snapshot["interaction"]
        assert interaction_data is not None
        if interaction_data["revision"] != expected_revision:
            raise run_interaction.PromptConflict(
                "Pre-query revision changed; reload this run"
            )
        # Mutating callers must not mistake retained cancelled evidence for an
        # executable boundary. get_prepared also validates conversation/deletion.
        record, interaction = run_interaction._load(run_id)
        if interaction.prompts and interaction.prompts[-1].continuation.point == POINT:
            entry = interaction.prompts[-1]
            state = validate_checkpoint(
                record, interaction, len(interaction.prompts) - 1
            )
            if entry.phase == "answered":
                run_interaction.advance(
                    run_id, str(entry.instance.instance_id), state.to_continuation()
                )
            else:
                return snapshot
        else:
            if snapshot["internal_phase"] not in ("prepared", "refinement_complete"):
                raise run_interaction.PromptConflict(
                    "Run has not reached pre-query configuration"
                )
            try:
                query = _source_query(record, interaction.prompts)
            except ValueError as exc:
                raise run_interaction.PromptConflict(
                    "Run has not reached pre-query configuration"
                ) from exc
            prompt = PromptSchema.model_validate(get_pre_query_prompt().to_dict())
            _validate_prompt(prompt)
            run_interaction.put_prompt(
                run_id,
                PromptInstance(run_id=UUID(run_id), instance_id=uuid4(), prompt=prompt),
                PreQueryState(
                    version=1, phase="waiting_answer", user_query=query, answer=None
                ).to_continuation(),
                expected_revision=expected_revision,
            )
        result = run_inspection.inspect(run_id)
        assert result is not None
        return result
