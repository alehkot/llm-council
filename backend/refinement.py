"""Finite, pure prompt-refinement transitions shared by legacy and durable work.

A saved waiting state contains the provider suggestion, not permission to call a
provider again. Executing a ``needs_suggestion`` state still requires a separate
in-flight checkpoint and explicit uncertain-work retry policy. This module never
performs I/O or schedules execution.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr, model_validator

from .consultation_contract import WireModel
from .interactive import PromptResponse
from .run_interaction import Continuation

MAX_REFINEMENT_ROUNDS = 3
_CONTINUATION_POINT = "prompt_refinement_v1"
Action = Literal["use_original", "use_refined", "refine_again"]


class RefinementSuggestion(WireModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: StrictStr
    clarifying_questions: list[StrictStr]
    refined_prompt: StrictStr


class RefinementState(WireModel):
    """A complete versioned checkpoint; absent saved fields are never defaulted."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    phase: Literal["needs_suggestion", "waiting_answer", "complete"]
    original_query: StrictStr
    refined_prompt: StrictStr
    iteration: Annotated[StrictInt, Field(ge=1, le=MAX_REFINEMENT_ROUNDS)]
    clarifying_answers: list[dict[str, StrictStr]]
    additional_context_parts: list[StrictStr]
    last_suggestion: RefinementSuggestion | None
    decision: Literal["use_original", "use_refined"] | None

    @model_validator(mode="after")
    def coherent(self) -> RefinementState:
        if any(
            set(answer) != {"question", "answer"} for answer in self.clarifying_answers
        ):
            raise ValueError("Clarification must contain question and answer")
        if (self.phase == "complete") != (self.decision is not None):
            raise ValueError("Only a complete refinement has a decision")
        if (
            self.phase in ("waiting_answer", "complete")
            and self.last_suggestion is None
        ):
            raise ValueError("Answered refinement requires its saved suggestion")
        if self.phase == "needs_suggestion":
            if (self.iteration == 1) != (self.last_suggestion is None):
                raise ValueError("Refinement round does not match suggestion history")
        if (
            self.decision == "use_original"
            and self.refined_prompt != self.original_query
        ):
            raise ValueError("Original decision must preserve original query")
        return self

    @classmethod
    def start(cls, original_query: str) -> RefinementState:
        return cls(
            version=1,
            phase="needs_suggestion",
            original_query=original_query,
            refined_prompt=original_query,
            iteration=1,
            clarifying_answers=[],
            additional_context_parts=[],
            last_suggestion=None,
            decision=None,
        )

    def _validated(self) -> RefinementState:
        # Do not trust mutated/model_construct instances at checkpoint boundaries.
        return RefinementState.model_validate(self.model_dump(mode="python"))

    def _updated(self, **updates: Any) -> RefinementState:
        payload = self.model_dump(mode="python")
        payload.update(updates)
        return RefinementState.model_validate(payload)

    def accept_suggestion(self, suggestion: dict[str, Any]) -> RefinementState:
        state = self._validated()
        if state.phase != "needs_suggestion":
            raise ValueError("Refinement is not awaiting a suggestion")
        saved = RefinementSuggestion.model_validate(suggestion)
        return state._updated(
            phase="waiting_answer", last_suggestion=saved.model_dump()
        )

    def provider_arguments(self) -> dict[str, Any]:
        state = self._validated()
        if state.phase != "needs_suggestion":
            raise ValueError("Refinement is not awaiting a suggestion")
        return {
            "user_query": state.original_query,
            "clarifying_answers": state.clarifying_answers or None,
            "additional_context": "\n\n".join(state.additional_context_parts) or None,
            "previous_refined_prompt": state.refined_prompt
            if state.iteration > 1
            else None,
            "iteration": state.iteration,
        }

    def finish_original(self) -> RefinementState:
        """Legacy timeout fallback only; durable waiting must never time out."""
        state = self._validated()
        if state.phase != "waiting_answer":
            raise ValueError("Refinement is not awaiting an answer")
        return state._updated(
            phase="complete",
            decision="use_original",
            refined_prompt=state.original_query,
        )

    def apply_response(self, response: PromptResponse) -> RefinementTransition:
        """Apply one answer, preserving legacy derived-text and action semantics.

        Durable callers must first validate/store the answer against the exact
        saved schema and pass that accepted receipt, not a new competing answer.
        Raw receipt values are not edited here. Legacy unknown actions retain the
        existing ``use_refined`` fallback.
        """
        state = self._validated()
        if state.phase != "waiting_answer":
            raise ValueError("Refinement is not awaiting an answer")
        if not isinstance(response, PromptResponse) or not isinstance(
            response.responses, dict
        ):
            raise ValueError("Expected a prompt response")
        if response.skipped:
            return RefinementTransition(
                state=state.finish_original(),
                user_action="use_original",
                action="use_original",
                edited_prompt_length=0,
                clarifying_answer_count=0,
                insights_length=0,
            )
        suggestion = state.last_suggestion
        assert suggestion is not None
        raw_action = response.responses.get("refinement_action")
        user_action: Action = (
            raw_action
            if isinstance(raw_action, str)
            and raw_action in {"use_refined", "use_original", "refine_again"}
            else "use_refined"
        )
        edited = str(response.responses.get("refined_prompt") or "").strip()
        refined = edited or suggestion.refined_prompt.strip() or state.refined_prompt
        answers = []
        for index, question in enumerate(suggestion.clarifying_questions):
            raw = response.responses.get(f"clarifying_answer_{index}")
            if raw is not None and (answer := str(raw).strip()):
                answers.append({"question": question, "answer": answer})
        extra = str(response.responses.get("additional_insights") or "").strip()
        action: Action = "refine_again" if answers or extra else user_action
        updates: dict[str, Any] = {
            "refined_prompt": refined,
            "clarifying_answers": state.clarifying_answers + answers,
            "additional_context_parts": state.additional_context_parts
            + ([extra] if extra else []),
        }
        if action == "use_original":
            updates.update(
                phase="complete",
                decision="use_original",
                refined_prompt=state.original_query,
            )
        elif action == "use_refined" or state.iteration == MAX_REFINEMENT_ROUNDS:
            updates.update(
                phase="complete",
                decision="use_refined",
                refined_prompt=refined or state.original_query,
            )
        else:
            updates.update(phase="needs_suggestion", iteration=state.iteration + 1)
        return RefinementTransition(
            state=state._updated(**updates),
            user_action=user_action,
            action=action,
            edited_prompt_length=len(edited),
            clarifying_answer_count=len(answers),
            insights_length=len(extra),
        )

    def metadata(self) -> dict[str, Any]:
        state = self._validated()
        if state.phase != "complete":
            raise ValueError("Refinement is not complete")
        suggestion = state.last_suggestion
        assert suggestion is not None
        return {
            "prompt_refinement": {
                "enabled": True,
                "decision": state.decision,
                "original_prompt": state.original_query,
                "refined_prompt": state.refined_prompt,
                "intent": suggestion.intent,
                "clarifying_questions": list(suggestion.clarifying_questions),
                "clarifying_answers": state.clarifying_answers,
                "iterations": state.iteration,
            }
        }

    def to_continuation(self) -> Continuation:
        state = self._validated()
        return Continuation(
            point=_CONTINUATION_POINT, state=state.model_dump(mode="json")
        )

    @classmethod
    def from_continuation(cls, continuation: Continuation) -> RefinementState:
        saved = Continuation.model_validate(continuation.model_dump(mode="python"))
        if saved.point != _CONTINUATION_POINT:
            raise ValueError("Unsupported refinement continuation")
        return cls.model_validate(saved.state)


class RefinementTransition(WireModel):
    state: RefinementState
    user_action: Action
    action: Action
    edited_prompt_length: Annotated[StrictInt, Field(ge=0)]
    clarifying_answer_count: Annotated[StrictInt, Field(ge=0)]
    insights_length: Annotated[StrictInt, Field(ge=0)]
