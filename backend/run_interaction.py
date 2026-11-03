"""Internal durable prompt checkpoints, not yet connected to execution or HTTP.

A receipt owns all prompt history so deletion erases questions and answers in
one tombstone write. The existing storage lock serializes one backend process.
No queue, provider call, worker scheduling, or automatic continuation occurs here.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from . import run_admission, storage
from .consultation_contract import (
    AnswerRequest,
    Identifier,
    PromptInstance,
    PromptSchema,
    WireModel,
)
from .interactive import FieldType


class PromptConflict(ValueError):
    """A stale or conflicting mutation must not change accepted input."""


class Continuation(WireModel):
    """Opaque workflow-owned checkpoint; never interpreted as executable code."""

    point: Identifier
    state: dict[str, Any]

    @model_validator(mode="after")
    def finite_json(self) -> "Continuation":
        # Reject non-JSON state before writing, including NaN and lossy keys.
        def check(value: Any) -> None:
            if isinstance(value, dict):
                if any(type(key) is not str for key in value):
                    raise ValueError("Checkpoint keys must be strings")
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
            elif value is not None and type(value) not in (str, bool, int, float):
                raise ValueError("Checkpoint state must be JSON")

        check(self.state)
        json.dumps(self.state, allow_nan=False)
        return self


class PromptCheckpoint(WireModel):
    instance: PromptInstance
    continuation: Continuation
    phase: Literal["waiting", "answered", "consumed"]
    answer: AnswerRequest | None = None
    next_continuation: Continuation | None = None

    @model_validator(mode="after")
    def coherent(self) -> "PromptCheckpoint":
        validate_schema(self.instance.prompt)
        if (self.phase == "waiting") != (self.answer is None):
            raise ValueError("Prompt phase does not match answer receipt")
        if (self.phase == "consumed") != (self.next_continuation is not None):
            raise ValueError("Consumed prompt must retain its next checkpoint")
        if self.answer is not None:
            validate_answer(self.instance.prompt, self.answer)
        return self


class InteractionState(WireModel):
    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    revision: Annotated[StrictInt, Field(ge=0)] = 0
    prompts: list[PromptCheckpoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> "InteractionState":
        ids = [entry.instance.instance_id for entry in self.prompts]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate prompt identity")
        if any(entry.phase != "consumed" for entry in self.prompts[:-1]):
            raise ValueError("Only the latest prompt may be active")
        expected_revision = sum(
            {"waiting": 1, "answered": 2, "consumed": 3}[entry.phase]
            for entry in self.prompts
        )
        if self.revision != expected_revision:
            raise ValueError("Checkpoint revision does not match history")
        return self


def validate_schema(prompt: PromptSchema) -> None:
    """Reject ambiguous generated schemas rather than accepting unclear input."""
    ids = [field.id for field in prompt.fields]
    if len(ids) != len(set(ids)):
        raise ValueError("Prompt field IDs must be unique")
    for field in prompt.fields:
        values = [option.value for option in field.options]
        if len(values) != len(set(values)):
            raise ValueError("Prompt option values must be unique")
        if field.type in (FieldType.SELECT, FieldType.MULTI_SELECT):
            if not values:
                raise ValueError("Selection fields require options")
        elif values:
            raise ValueError("Only selection fields have options")
        if field.default is not None:
            _validate_value(field, field.default, required=False)


def _validate_value(field: Any, value: Any, *, required: bool) -> None:
    if field.type == FieldType.TEXT:
        valid = type(value) is str and (not required or bool(value.strip()))
    elif field.type == FieldType.CHECKBOX:
        # A required checkbox requires an explicit boolean, not agreement.
        valid = type(value) is bool
    elif field.type == FieldType.SELECT:
        valid = type(value) is str and value in {o.value for o in field.options}
    else:
        valid = (
            type(value) is list
            and all(type(item) is str for item in value)
            and len(value) == len(set(value))
            and set(value) <= {o.value for o in field.options}
            and (not required or bool(value))
        )
    if not valid:
        raise ValueError(f"Invalid answer for field {field.id}")


def validate_answer(prompt: PromptSchema, answer: AnswerRequest) -> None:
    """Check exact saved schema without inserting defaults or inventing answers."""
    if answer.skipped:
        if not prompt.skip_allowed:
            raise ValueError("This prompt cannot be skipped")
        return
    fields = {field.id: field for field in prompt.fields}
    if set(answer.responses) - fields.keys():
        raise ValueError("Unknown prompt response field")
    for field in prompt.fields:
        if field.id not in answer.responses:
            if field.required:
                raise ValueError(f"Missing required field {field.id}")
            continue
        _validate_value(field, answer.responses[field.id], required=field.required)


def _validate_record(record: dict[str, Any]) -> InteractionState:
    """Validate saved history without requiring a runnable (uncancelled) run."""
    try:
        state = InteractionState.model_validate(record.get("interaction", {}))
        if any(
            str(entry.instance.run_id) != record["run_id"] for entry in state.prompts
        ):
            raise ValueError("Prompt belongs to another run")
    except (ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Prompt checkpoint is invalid; repair required"
        ) from exc
    return state


def _load(run_id: str) -> tuple[dict[str, Any], InteractionState]:
    record = run_admission.get_prepared(run_id)
    return record, _validate_record(record)


def _save(record: dict[str, Any], state: InteractionState) -> dict[str, Any]:
    payload = state.model_dump(mode="json")
    # Revalidate mutations as well as reads before touching the accepted file.
    InteractionState.model_validate(payload)
    record["interaction"] = payload
    run_admission._write(record)
    return payload


def _same_continuation(left: Continuation, right: Continuation) -> bool:
    """Compare JSON types exactly: Python equality conflates true, 1 and 1.0."""
    return json.dumps(
        left.model_dump(mode="json"), sort_keys=True, allow_nan=False
    ) == json.dumps(right.model_dump(mode="json"), sort_keys=True, allow_nan=False)


def get(run_id: str) -> dict[str, Any]:
    """Return detached durable history; no file rewrite and no execution."""
    with storage._storage_lock:
        _, state = _load(run_id)
        return state.model_dump(mode="json")


def put_prompt(
    run_id: str,
    instance: PromptInstance,
    continuation: Continuation,
    *,
    expected_revision: int,
) -> dict[str, Any]:
    """Persist an exact waiting boundary before a future worker can release it.

    A known instance retries idempotently only with its original schema/state.
    New instances require the latest revision and a consumed previous answer.
    """
    # Revalidate even model_construct/mutated models supplied by internal callers.
    instance = PromptInstance.model_validate(instance.model_dump(mode="json"))
    continuation = Continuation.model_validate(continuation.model_dump(mode="python"))
    validate_schema(instance.prompt)
    if str(instance.run_id) != str(UUID(run_id)):
        raise ValueError("Prompt belongs to another run")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        record, state = _load(run_id)
        for entry in state.prompts:
            if entry.instance.instance_id == instance.instance_id:
                if entry.instance != instance or not _same_continuation(
                    entry.continuation, continuation
                ):
                    raise PromptConflict(
                        "Prompt instance already has another checkpoint"
                    )
                return state.model_dump(mode="json")
        if state.revision != expected_revision or (
            state.prompts and state.prompts[-1].phase != "consumed"
        ):
            raise PromptConflict("Prompt checkpoint changed; reload this run")
        state.prompts.append(
            PromptCheckpoint(
                instance=instance, continuation=continuation, phase="waiting"
            )
        )
        state.revision += 1
        return _save(record, state)


def answer(run_id: str, instance_id: str, response: AnswerRequest) -> dict[str, Any]:
    """Persist first valid answer before continuation; identical retries are reads."""
    identity = UUID(instance_id)
    response = AnswerRequest.model_validate(response.model_dump(mode="json"))
    with storage._storage_lock:
        record, state = _load(run_id)
        for entry in state.prompts:
            if entry.instance.instance_id != identity:
                continue
            if entry.answer is not None:
                if entry.answer != response:
                    raise PromptConflict("A different answer was already accepted")
                return entry.model_dump(mode="json")
            validate_answer(entry.instance.prompt, response)
            entry.answer = response
            entry.phase = "answered"
            state.revision += 1
            _save(record, state)
            return entry.model_dump(mode="json")
        raise PromptConflict("Prompt instance is unknown or stale")


def advance(
    run_id: str, instance_id: str, continuation: Continuation
) -> dict[str, Any]:
    """Atomically acknowledge input with its next saved continuation, never run it.

    This is not permission to repeat external operations. The future workflow
    must persist in-flight operations and explicit retry before executing them.
    """
    identity = UUID(instance_id)
    continuation = Continuation.model_validate(continuation.model_dump(mode="python"))
    with storage._storage_lock:
        record, state = _load(run_id)
        for entry in state.prompts:
            if entry.instance.instance_id != identity:
                continue
            if entry.phase == "waiting":
                raise PromptConflict("Prompt has no accepted answer")
            if entry.phase == "consumed":
                if not _same_continuation(entry.next_continuation, continuation):
                    raise PromptConflict("A different continuation was already saved")
                return state.model_dump(mode="json")
            entry.next_continuation = continuation
            entry.phase = "consumed"
            state.revision += 1
            return _save(record, state)
        raise PromptConflict("Prompt instance is unknown or stale")
