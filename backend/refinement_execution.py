"""Internal single-process durable guard for one refinement provider operation.

No HTTP route, worker, startup hook or provider is installed here. Callers inject
an explicitly selected async provider and a stable configuration identity. A
saved result can be read repeatedly, but uncertain work requires its exact retry
token. This is not exactly-once execution at the external provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, StrictStr, model_validator

from . import durable_refinement, run_admission, run_interaction, storage
from .consultation_contract import PromptInstance, WireModel
from .interactive import get_prompt_refinement_prompt
from .refinement import RefinementState

Provider = Callable[..., Awaitable[dict[str, Any]]]
# All instances in this backend process share ownership. Not a cross-process lock.
_active: set[tuple[str, str]] = set()


class ExecutionConflict(ValueError):
    """The operation/input/configuration or retry consent no longer matches."""


class ExecutionPending(RuntimeError):
    """The same operation is still owned by a live local invocation."""


class RetryRequired(RuntimeError):
    """Provider outcome is uncertain; never silently execute it again."""

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(
            "Refinement suggestion outcome is uncertain; explicit retry required"
        )


class Operation(WireModel):
    revision: Annotated[StrictInt, Field(ge=0)]
    provider_key: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    input: RefinementState
    attempt: UUID
    retry_of: UUID | None = None
    phase: Literal["in_flight", "complete"]
    result: RefinementState | None = None
    prompt_instance_id: UUID | None = None

    @model_validator(mode="after")
    def coherent(self) -> Operation:
        if self.input.phase != "needs_suggestion":
            raise ValueError("Operation input is not runnable")
        if (self.phase == "complete") != (self.result is not None):
            raise ValueError("Operation phase does not match result")
        if (self.phase == "complete") != (self.prompt_instance_id is not None):
            raise ValueError("Completed operation must retain its prompt identity")
        if self.retry_of == self.attempt:
            raise ValueError("Retry must have a distinct attempt")
        if self.result is not None:
            if (
                self.result.last_suggestion is None
                or self.result
                != self.input.accept_suggestion(
                    self.result.last_suggestion.model_dump()
                )
            ):
                raise ValueError("Saved suggestion changed its input state")
        return self


class ExecutionState(WireModel):
    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    operations: list[Operation] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> ExecutionState:
        revisions = [op.revision for op in self.operations]
        if revisions != sorted(set(revisions)):
            raise ValueError("Operation revisions must increase")
        if any(op.phase != "complete" for op in self.operations[:-1]):
            raise ValueError("Only the last operation may be in flight")
        if len({op.provider_key for op in self.operations}) > 1:
            raise ValueError("Operation provider identity changed")
        if len({op.attempt for op in self.operations}) != len(self.operations):
            raise ValueError("Attempt identity was reused")
        return self


def _load(run_id: str) -> tuple[dict[str, Any], ExecutionState]:
    record = run_admission.get_prepared(run_id)
    try:
        state = ExecutionState.model_validate(record.get("refinement_execution", {}))
        if any(
            op.input.original_query != record["request"]["content"]
            or not record["request"]["refine"]
            for op in state.operations
        ):
            raise ValueError("Operation does not match admission")
        _, interaction = run_interaction._load(run_id)
        for op in state.operations:
            source_index, remainder = divmod(op.revision, 3)
            if remainder or source_index > len(interaction.prompts):
                raise ValueError("Operation source revision is invalid")
            if source_index == 0:
                expected_input = RefinementState.start(record["request"]["content"])
            else:
                previous = interaction.prompts[source_index - 1]
                if previous.phase != "consumed":
                    raise ValueError("Operation source answer is not consumed")
                expected_input = durable_refinement.resume_answer(
                    run_id, str(previous.instance.instance_id)
                )
            if op.input != expected_input:
                raise ValueError("Operation input differs from its saved source")
            if op.phase == "in_flight" and interaction.revision != op.revision:
                raise ValueError("In-flight operation source changed")
            if op.phase == "complete":
                prompt = next(
                    (
                        p
                        for p in interaction.prompts
                        if p.instance.instance_id == op.prompt_instance_id
                    ),
                    None,
                )
                if (
                    prompt is None
                    or op.result is None
                    or not run_interaction._same_continuation(
                        prompt.continuation, op.result.to_continuation()
                    )
                ):
                    raise ValueError("Saved result does not match its durable prompt")
                durable_refinement._validate_prompt(prompt.instance.prompt, op.result)
                if interaction.prompts.index(prompt) * 3 != op.revision:
                    raise ValueError(
                        "Saved operation revision does not match its prompt"
                    )
    except (ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Refinement execution checkpoint is invalid; repair required"
        ) from exc
    return record, state


def _save(record: dict[str, Any], state: ExecutionState) -> None:
    payload = state.model_dump(mode="json")
    ExecutionState.model_validate(payload)
    record["refinement_execution"] = payload
    run_admission._write(record)


def _input(run_id: str) -> tuple[int, RefinementState]:
    record, interaction = run_interaction._load(run_id)
    if not record["request"]["refine"]:
        raise ExecutionConflict("Run did not request refinement")
    try:
        if not interaction.prompts:
            state = RefinementState.start(record["request"]["content"])
        else:
            latest = interaction.prompts[-1]
            if latest.phase != "consumed":
                raise ExecutionConflict("Refinement is waiting for accepted input")
            assert latest.next_continuation is not None
            state = durable_refinement.resume_answer(
                run_id, str(latest.instance.instance_id)
            )
        if state.original_query != record["request"]["content"]:
            raise ValueError("Refinement does not match admitted question")
        if state.phase != "needs_suggestion":
            raise ExecutionConflict("Refinement needs no suggestion")
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ExecutionConflict):
            raise
        raise run_admission.AdmissionUnavailable(
            "Invalid refinement continuation"
        ) from exc
    return interaction.revision, state


def inspect(run_id: str) -> dict[str, Any] | None:
    """Read latest operation; an orphaned in-flight attempt needs explicit retry.

    This never mutates saved state or infers success from a provider exception.
    The token identifies the smallest uncertain operation (one suggestion).
    """
    with storage._storage_lock:
        record, state = _load(run_id)
        if not state.operations:
            return None
        op = state.operations[-1]
        live = (record["run_id"], str(op.attempt)) in _active
        return {
            "revision": op.revision,
            "phase": "needs_retry"
            if op.phase == "in_flight" and not live
            else op.phase,
            "retry_token": str(op.attempt)
            if op.phase == "in_flight" and not live
            else None,
            "provider_key": op.provider_key,
            "result": op.result.model_dump(mode="json") if op.result else None,
        }


async def suggest(
    run_id: str,
    provider: Provider,
    *,
    provider_key: str,
    expected_revision: int,
    retry_token: str | None = None,
) -> RefinementState:
    """Persist before calling, then atomically save the validated suggestion.

    provider_key must identify the effective provider configuration, not a secret.
    It is a mismatch guard, not full model/skills provenance. The injected
    callback must perform one attempt, without automatic provider retries. The future executor
    must resolve and snapshot that provenance before making this callable live.
    Cancellation/errors/write failures leave uncertainty, not automatic retries.
    A duplicate retry token can observe its result but cannot start another call.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    consent = UUID(retry_token) if retry_token is not None else None
    with storage._storage_lock:
        record, execution = _load(run_id)
        if any(item.provider_key != provider_key for item in execution.operations):
            raise ExecutionConflict("Run provider configuration changed")
        # A retry or delayed normal request names its original boundary. It may
        # read that result but must never dispatch a later refinement round.
        known = next(
            (
                item
                for item in execution.operations
                if item.revision == expected_revision
            ),
            None,
        )
        if known is not None:
            if known.provider_key != provider_key:
                raise ExecutionConflict("Saved provider configuration changed")
            if consent is not None and consent not in (known.attempt, known.retry_of):
                raise ExecutionConflict("Retry token is stale")
            if known.phase == "complete":
                assert known.result is not None
                return known.result.model_copy(deep=True)
        revision, incoming = _input(run_id)
        if revision != expected_revision:
            raise ExecutionConflict("Refinement revision changed; reload this run")
        op = execution.operations[-1] if execution.operations else None
        if op is not None and op.revision == revision:
            if op.input != incoming or op.provider_key != provider_key:
                raise ExecutionConflict("Saved operation input or provider changed")
            if consent is not None and consent not in (op.attempt, op.retry_of):
                raise ExecutionConflict("Retry token is stale")
            if op.phase == "complete":
                assert op.result is not None
                return op.result.model_copy(deep=True)
            if (record["run_id"], str(op.attempt)) in _active:
                raise ExecutionPending("Suggestion is already running")
            if consent != op.attempt:
                raise RetryRequired(str(op.attempt))
            replacement = Operation(
                revision=revision,
                provider_key=provider_key,
                input=incoming,
                attempt=uuid4(),
                retry_of=op.attempt,
                phase="in_flight",
            )
            execution.operations[-1] = replacement
            op = replacement
        else:
            if consent is not None:
                raise ExecutionConflict(
                    "Retry token does not identify current operation"
                )
            if op is not None and (op.revision > revision or op.phase != "complete"):
                raise ExecutionConflict("Previous suggestion has not completed")
            op = Operation(
                revision=revision,
                provider_key=provider_key,
                input=incoming,
                attempt=uuid4(),
                phase="in_flight",
            )
            execution.operations.append(op)
        # If this write fails (even after replacing the file), do not call provider.
        _save(record, execution)
        ownership = (record["run_id"], str(op.attempt))
        _active.add(ownership)
    try:
        result = incoming.accept_suggestion(
            await provider(**incoming.provider_arguments())
        )
        with storage._storage_lock:
            record, execution = _load(run_id)
            current = execution.operations[-1]
            if current.attempt != op.attempt or current.phase != "in_flight":
                raise ExecutionConflict("Suggestion attempt was superseded")
            # No other interaction mutation may redirect this operation mid-call.
            current_revision, current_input = _input(run_id)
            if current_revision != revision or current_input != incoming:
                raise ExecutionConflict(
                    "Refinement continuation changed during execution"
                )
            _, interaction = run_interaction._load(run_id)
            assert result.last_suggestion is not None
            instance = PromptInstance(
                run_id=record["run_id"],
                instance_id=uuid4(),
                prompt=get_prompt_refinement_prompt(
                    result.last_suggestion.model_dump(), iteration=result.iteration
                ).to_dict(),
            )
            interaction.prompts.append(
                run_interaction.PromptCheckpoint(
                    instance=instance,
                    continuation=result.to_continuation(),
                    phase="waiting",
                )
            )
            interaction.revision += 1
            record["interaction"] = interaction.model_dump(mode="json")
            run_interaction.InteractionState.model_validate(record["interaction"])
            current.result = result
            current.prompt_instance_id = instance.instance_id
            current.phase = "complete"
            _save(record, execution)
        return result.model_copy(deep=True)
    finally:
        with storage._storage_lock:
            _active.discard(ownership)
