"""Consume an accepted refinement answer without replaying provider work.

This internal coordinator connects durable prompt receipts to the pure reducer.
It does not schedule a worker, expose an HTTP route, or authorize execution of a
returned ``needs_suggestion`` state. Those operations still require in-flight
checkpoints and explicit uncertain-work retry handling.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from . import run_admission, run_interaction, storage
from .consultation_contract import PromptSchema
from .interactive import FieldType, PromptResponse, PromptType
from .refinement import RefinementState


def _validate_prompt(prompt: PromptSchema, state: RefinementState) -> None:
    """Check reducer semantics, not today's labels/defaults or UI wording.

    The original saved schema already validated the accepted answer. Never
    regenerate the prompt or substitute defaults when restoring its meaning.
    """
    if (
        prompt.prompt_id != "prompt_refinement"
        or prompt.prompt_type != PromptType.PROMPT_REFINEMENT
    ):
        raise ValueError("Not a refinement prompt")
    assert state.last_suggestion is not None
    questions = {
        f"clarifying_answer_{index}": question.strip()
        for index, question in enumerate(state.last_suggestion.clarifying_questions)
        if question.strip()
    }
    fields = {field.id: field for field in prompt.fields}
    if fields.keys() != {
        "refinement_action",
        "refined_prompt",
        "additional_insights",
        *questions,
    }:
        raise ValueError("Refinement fields do not match saved state")
    action = fields["refinement_action"]
    if (
        action.type != FieldType.SELECT
        or not action.required
        or {option.value for option in action.options}
        != {"use_original", "use_refined", "refine_again"}
    ):
        raise ValueError("Unsupported refinement actions")
    for field_id, field in fields.items():
        if field_id != "refinement_action" and field.type != FieldType.TEXT:
            raise ValueError("Refinement text field has incompatible type")
        # A clarification label is the actual question, not decorative UI copy.
        if field_id in questions and field.label.strip() != questions[field_id]:
            raise ValueError("Clarification does not match saved question")


def _validate_checkpoint(
    record: dict[str, Any], entry: run_interaction.PromptCheckpoint
) -> RefinementState:
    """Read and validate a retained refinement prompt without consuming input."""
    try:
        state = RefinementState.from_continuation(entry.continuation)
        if (
            not record["request"]["refine"]
            or state.original_query != record["request"]["content"]
            or state.phase != "waiting_answer"
        ):
            raise ValueError("Refinement checkpoint does not match admission")
        assert state.last_suggestion is not None
        _validate_prompt(entry.instance.prompt, state)
        return state
    except (ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Refinement checkpoint is incompatible; repair required"
        ) from exc


def _derive_answer(
    record: dict[str, Any], entry: run_interaction.PromptCheckpoint
) -> RefinementState:
    """Validate and derive accepted input without consuming or writing it."""
    try:
        state = _validate_checkpoint(record, entry)
        assert entry.answer is not None
        derived = state.apply_response(
            PromptResponse(
                prompt_id=entry.instance.prompt.prompt_id,
                responses=entry.answer.responses,
                skipped=entry.answer.skipped,
            )
        ).state
        if entry.phase == "consumed":
            assert entry.next_continuation is not None
            saved = RefinementState.from_continuation(entry.next_continuation)
            if saved != derived:
                raise ValueError("Saved refinement result does not match answer")
            return saved
    except (ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Refinement checkpoint is incompatible; repair required"
        ) from exc
    return derived


def resume_answer(run_id: str, instance_id: str) -> RefinementState:
    """Reduce only the first saved answer, atomically saving its continuation.

    No caller-supplied answer is accepted. Duplicate consumption is a read and
    verifies the saved result rather than overwriting inconsistent work. Schema
    drift fails closed: an incompatible saved prompt needs explicit migration,
    never reinterpretation under changed question/action semantics.

    The returned state is a historical answer receipt, not the current runnable
    state or permission to dispatch a provider. A later prompt may already exist.
    Future execution must check current revision and in-flight/retry protection.
    """
    identity = UUID(instance_id)
    with storage._storage_lock:
        record, interaction = run_interaction._load(run_id)
        entry = next(
            (
                item
                for item in interaction.prompts
                if item.instance.instance_id == identity
            ),
            None,
        )
        if entry is None:
            raise run_interaction.PromptConflict("Prompt instance is unknown or stale")
        if entry.phase == "waiting":
            raise run_interaction.PromptConflict("Prompt has no accepted answer")
        derived = _derive_answer(record, entry)
        if entry.phase == "consumed":
            return derived
        run_interaction.advance(run_id, str(identity), derived.to_continuation())
        return derived
