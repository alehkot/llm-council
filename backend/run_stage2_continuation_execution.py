"""Internal durable guard for one explicitly named first Stage 2 continuation.

No wire adapter, manager step, public route or automatic loop invokes this guard.
Explicitly selected
providers must make exactly one attempt using the supplied saved request. Local
ownership is process-wide, not distributed or exactly-once at a remote provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, StrictStr, model_validator

from . import (
    run_admission,
    run_cancellation,
    run_interaction,
    run_stage1_inputs,
    run_stage2_continuation_inputs,
    run_stage2_execution,
    run_stage2_inputs,
    storage,
)
from .consultation_contract import WireModel

Provider = Callable[..., Awaitable[dict[str, Any]]]
_active: set[tuple[str, str]] = set()


class ExecutionConflict(ValueError):
    """Saved request, adapter identity, revision or retry consent differs."""


class ExecutionPending(RuntimeError):
    """The member's current attempt still has a live local owner."""


class ProviderFailure(RuntimeError):
    """No validated response was saved; the attempt remains uncertain."""


class RetryRequired(RuntimeError):
    """An unowned attempt needs explicit consent for this member only."""

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(
            "First Stage 2 continuation outcome is uncertain; explicit retry required"
        )


# Reuse the same bounded normalized response contract; retain chunks separately.
MemberResponse = run_stage2_execution.MemberResponse


class Operation(WireModel):
    model: StrictStr
    turn: Annotated[StrictInt, Field(ge=1, le=1)]
    inputs_sha256: run_stage1_inputs.Digest
    attempt: UUID
    retry_of: UUID | None
    phase: Literal["in_flight", "complete"]
    result: MemberResponse | None

    @model_validator(mode="after")
    def coherent(self) -> Operation:
        if (self.phase == "complete") != (self.result is not None):
            raise ValueError("Operation result and phase disagree")
        if self.retry_of == self.attempt:
            raise ValueError("Retry must have a new attempt identity")
        return self


class ExecutionState(WireModel):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    provider_key: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    operations: list[Operation]

    @model_validator(mode="after")
    def coherent(self) -> ExecutionState:
        if not self.operations:
            raise ValueError("Execution has no operations")
        if any(ord(c) < 33 or ord(c) > 126 for c in self.provider_key):
            raise ValueError("Adapter identity must be printable without whitespace")
        if len({op.model for op in self.operations}) != len(self.operations):
            raise ValueError("Member operation was duplicated")
        ids = [
            token
            for op in self.operations
            for token in (op.attempt, op.retry_of)
            if token
        ]
        if len(set(ids)) != len(ids):
            raise ValueError("Attempt identity was reused")
        return self


def validate_saved(
    record: dict[str, Any],
    inputs: run_stage2_continuation_inputs.ContinuationInputs | None,
) -> ExecutionState | None:
    """Validate separate continuation attempts against each immutable request."""
    if "stage2_continuation_execution" not in record:
        return None
    try:
        raw = record["stage2_continuation_execution"]
        saved = ExecutionState.model_validate(raw)
        by_model = {r.model: r for r in inputs.requests} if inputs else {}
        initial = run_stage2_execution.validate_saved(
            record,
            run_stage2_inputs.validate_saved(
                record, run_interaction._validate_record(record)
            ),
        )
        initial_tokens = (
            {
                token
                for op in initial.operations
                for token in (op.attempt, op.retry_of)
                if token
            }
            if initial
            else set()
        )
        if (
            record["phase"] != "prepared"
            or inputs is None
            or initial is None
            or run_stage1_inputs._json(saved.model_dump(mode="json"))
            != run_stage1_inputs._json(raw)
            or any(
                op.model not in by_model
                or op.turn != by_model[op.model].turn
                or saved.source_revision != by_model[op.model].source_revision
                or op.inputs_sha256 != run_stage1_inputs._digest(by_model[op.model])
                or op.attempt in initial_tokens
                or op.retry_of in initial_tokens
                for op in saved.operations
            )
        ):
            raise ValueError("Continuation execution differs from its source")
        return saved
    except (KeyError, TypeError, ValueError):
        raise run_admission.AdmissionUnavailable(
            "Saved Stage 2 continuation execution is incompatible; repair required"
        ) from None


def summary(
    run_id: str,
    state: ExecutionState,
    inputs: run_stage2_continuation_inputs.ContinuationInputs,
) -> dict[str, Any]:
    """Compact per-turn states, not aggregate answers or consultation completion."""
    by_model = {op.model: op for op in state.operations}
    members = []
    for request in inputs.requests:
        op = by_model.get(request.model)
        owned = op is not None and (run_id, str(op.attempt)) in _active
        phase, token = "not_started", None
        if op is not None:
            if op.result is not None:
                phase = (
                    "complete"
                    if op.result.finish_reason == "stop"
                    else "needs_continuation"
                )
            elif owned:
                phase = "running"
            else:
                phase, token = "needs_retry", str(op.attempt)
        members.append(
            {
                "model": request.model,
                "turn": request.turn,
                "phase": phase,
                "retry_token": token,
            }
        )
    return {
        "version": state.version,
        "source_revision": state.source_revision,
        "provider_key": state.provider_key,
        "members": members,
    }


def _load(
    run_id: str, expected_revision: int, *, current: bool
) -> tuple[
    dict[str, Any],
    run_stage2_continuation_inputs.ContinuationInputs,
    ExecutionState | None,
]:
    from . import run_inspection

    run_inspection.inspect(run_id)
    record, interaction = run_interaction._load(run_id)
    if interaction.revision != expected_revision:
        raise ExecutionConflict("Continuation revision changed")
    inputs = run_stage2_continuation_inputs.validate_saved(record, interaction)
    if inputs is None:
        raise ExecutionConflict("Saved first-continuation inputs are required")
    if any(
        request.source_revision != interaction.revision for request in inputs.requests
    ):
        raise ExecutionConflict("Stage 2 interaction boundary has closed")
    if current:
        run_stage1_inputs._check_current(record, interaction)
    return record, inputs, validate_saved(record, inputs)


def _save(record: dict[str, Any], state: ExecutionState) -> None:
    payload = state.model_dump(mode="json")
    ExecutionState.model_validate(payload)
    record["stage2_continuation_execution"] = payload
    run_admission._write(record)


def _validate_turn(turn: int) -> None:
    if type(turn) is not int or turn != 1:
        raise ValueError("Only explicit first continuation turn 1 is supported")


def load_result(run_id: str, model: str, *, turn: int) -> MemberResponse | None:
    """Read a retained continuation chunk after cancellation/config drift, without writes."""
    from . import run_inspection

    _validate_turn(turn)
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record = run_admission._read(run_id)
        interaction = run_interaction._validate_record(record)
        inputs = run_stage2_continuation_inputs.validate_saved(record, interaction)
        if inputs is None or model not in {r.model for r in inputs.requests}:
            raise ExecutionConflict("Member does not identify saved continuation input")
        state = validate_saved(record, inputs)
        op = (
            next((op for op in state.operations if op.model == model), None)
            if state
            else None
        )
        return op.result.model_copy(deep=True) if op and op.result else None


async def execute(
    run_id: str,
    model: str,
    provider: Provider,
    *,
    turn: int,
    provider_key: str,
    expected_revision: int,
    retry_token: str | None = None,
) -> MemberResponse:
    """Guard one explicit saved continuation turn and retain its separate response.

    The trusted callback must make one attempt with supplied endpoint, timeout
    and payload, without internal retries or context/continuation calls. A saved
    result is replayed for this turn, never interpreted as permission for another.
    No HTTP adapter, manager step or public route supplies this callback yet.
    Direct caller cancellation cancels the attempt; durable scheduling and
    detached waiter ownership remain separate future integrations.
    """
    _validate_turn(turn)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    if (
        not isinstance(provider_key, str)
        or not 1 <= len(provider_key) <= 128
        or any(ord(c) < 33 or ord(c) > 126 for c in provider_key)
    ):
        raise ValueError("A stable non-secret adapter identity is required")
    try:
        if retry_token is not None and not isinstance(retry_token, str):
            raise ValueError()
        consent = UUID(retry_token) if retry_token is not None else None
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Retry token must identify an attempt UUID") from None
    with storage._storage_lock:
        record, inputs, state = _load(run_id, expected_revision, current=True)
        request = next((r for r in inputs.requests if r.model == model), None)
        if request is None:
            raise ExecutionConflict("Member does not identify saved continuation input")
        if state is not None and state.provider_key != provider_key:
            raise ExecutionConflict("Continuation adapter identity changed")
        known = (
            next((op for op in state.operations if op.model == model), None)
            if state
            else None
        )
        if known is not None:
            if consent is not None and consent not in (known.attempt, known.retry_of):
                raise ExecutionConflict("Retry token is stale or names another member")
            if known.result is not None:
                return known.result.model_copy(deep=True)
            if (record["run_id"], str(known.attempt)) in _active:
                raise ExecutionPending("This member request is already running")
            if consent != known.attempt:
                raise RetryRequired(str(known.attempt))
        elif consent is not None:
            raise ExecutionConflict("Retry token does not name an uncertain member")
        op = Operation(
            model=model,
            turn=turn,
            inputs_sha256=run_stage1_inputs._digest(request),
            attempt=uuid4(),
            retry_of=known.attempt if known else None,
            phase="in_flight",
            result=None,
        )
        if state is None:
            state = ExecutionState(
                version=1,
                source_revision=expected_revision,
                provider_key=provider_key,
                operations=[op],
            )
        elif known is None:
            state.operations.append(op)
        else:
            state.operations[state.operations.index(known)] = op
        _save(record, state)
        ownership = (record["run_id"], str(op.attempt))

        async def invoke() -> dict[str, Any]:
            with storage._storage_lock:
                _, ready_inputs, ready = _load(run_id, expected_revision, current=True)
                active = (
                    next((x for x in ready.operations if x.model == model), None)
                    if ready
                    else None
                )
                if (
                    next((r for r in ready_inputs.requests if r.model == model), None)
                    != request
                    or ready is None
                    or ready.provider_key != provider_key
                    or active is None
                    or active != op
                ):
                    raise ExecutionConflict(
                        "Continuation request changed before dispatch"
                    )
            return await provider(
                endpoint=request.endpoint,
                timeout_seconds=request.timeout_seconds,
                payload=request.payload(),
            )

        _active.add(ownership)
        invocation = invoke()
        try:
            provider_task = asyncio.create_task(invocation)
        except BaseException:
            invocation.close()
            _active.discard(ownership)
            raise
        run_cancellation._register(*ownership, provider_task)
    try:
        try:
            response = await asyncio.shield(provider_task)
        except asyncio.CancelledError:
            # Shielding preserves the caller's cancellation even if the adapter
            # suppresses cancellation and returns a late response. Signal once,
            # retain ownership through cleanup, and never persist that response.
            run_cancellation._signal(ownership, provider_task)
            while True:
                try:
                    await asyncio.shield(provider_task)
                except asyncio.CancelledError:
                    if not provider_task.done():
                        continue
                except Exception:
                    pass
                break
            raise
        except Exception:
            raise ProviderFailure(
                "First Stage 2 continuation provider attempt did not complete"
            ) from None
        try:
            if not isinstance(response, dict):
                raise ValueError("Invalid response")
            result = MemberResponse.model_validate(response)
        except (TypeError, ValueError):
            raise ProviderFailure(
                "Provider returned no supported Stage 2 response"
            ) from None
        with storage._storage_lock:
            record, current_inputs, current = _load(
                run_id, expected_revision, current=False
            )
            active = (
                next((x for x in current.operations if x.model == model), None)
                if current
                else None
            )
            if (
                next((r for r in current_inputs.requests if r.model == model), None)
                != request
                or current is None
                or current.provider_key != provider_key
                or active is None
                or active != op
            ):
                raise ExecutionConflict("Continuation attempt was superseded")
            active.result, active.phase = result, "complete"
            _save(record, current)
        return result.model_copy(deep=True)
    finally:
        with storage._storage_lock:
            _active.discard(ownership)
            run_cancellation._release(*ownership)
