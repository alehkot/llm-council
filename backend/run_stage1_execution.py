"""Internal durable guard for one initial Stage 1 member request at a time.

No route, worker or automatic loop invokes this module. Explicitly selected
providers must make exactly one attempt using the supplied saved request. Local
ownership is process-wide, not distributed or exactly-once at a remote provider.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, JsonValue, StrictInt, StrictStr, model_validator

from . import (
    run_admission,
    run_cancellation,
    run_interaction,
    run_stage1_inputs,
    storage,
)
from .consultation_contract import WireModel

Provider = Callable[..., Awaitable[dict[str, Any]]]
_active: set[tuple[str, str]] = set()
MAX_RESULT_BYTES = 2 * 1024 * 1024


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
            "Initial Stage 1 outcome is uncertain; explicit retry required"
        )


class MemberResponse(WireModel):
    """Normalized initial response; a length stop is retained, not a final answer.

    Nested metadata is detached on every return. Any transport adapter must
    normalize its response to this contract without adding credentials or retries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    content: Annotated[StrictStr, Field(min_length=1, repr=False)]
    finish_reason: Literal["stop", "length"]
    reasoning_details: list[dict[str, JsonValue]] | None = Field(
        default=None, repr=False
    )
    usage: dict[str, JsonValue] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def valid_response(self) -> MemberResponse:
        if not self.content.strip():
            raise ValueError("Provider response has no text")
        # JSON-compatible finite metadata only. No arbitrary objects or NaN.
        encoded = json.dumps(
            self.model_dump(mode="python"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            raise ValueError("Provider response exceeds the saved result limit")
        return self


class Operation(WireModel):
    model: StrictStr
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
    inputs_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
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
    record: dict[str, Any], inputs: run_stage1_inputs.Stage1Inputs | None
) -> ExecutionState | None:
    """Validate persisted attempts against exact retained inputs without writes."""
    if "stage1_execution" not in record:
        return None
    try:
        raw = record["stage1_execution"]
        saved = ExecutionState.model_validate(raw)
        if (
            record["phase"] != "prepared"
            or inputs is None
            or saved.model_dump(mode="json") != raw
            or saved.source_revision != inputs.source_revision
            or saved.inputs_sha256 != run_stage1_inputs._digest(inputs)
            or any(
                op.model not in {r.model for r in inputs.requests}
                for op in saved.operations
            )
        ):
            raise ValueError("Initial Stage 1 execution differs from its source")
        return saved
    except (KeyError, TypeError, ValueError):
        raise run_admission.AdmissionUnavailable(
            "Saved Stage 1 execution is incompatible; repair required"
        ) from None


def summary(
    run_id: str, state: ExecutionState, inputs: run_stage1_inputs.Stage1Inputs
) -> dict[str, Any]:
    """Compact member states; no response text, reasoning or usage in polling."""
    by_model = {op.model: op for op in state.operations}
    members = []
    for request in inputs.requests:
        op = by_model.get(request.model)
        owned = op is not None and (run_id, str(op.attempt)) in _active
        phase = "not_started"
        token = None
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
        members.append({"model": request.model, "phase": phase, "retry_token": token})
    return {
        "version": state.version,
        "source_revision": state.source_revision,
        "inputs_sha256": state.inputs_sha256,
        "provider_key": state.provider_key,
        "members": members,
    }


def _load(
    run_id: str, expected_revision: int, *, current: bool
) -> tuple[dict[str, Any], run_stage1_inputs.Stage1Inputs, ExecutionState | None]:
    from . import run_inspection

    run_inspection.inspect(run_id)
    record, interaction = run_interaction._load(run_id)
    if interaction.revision != expected_revision:
        raise ExecutionConflict("Stage 1 revision changed")
    inputs = run_stage1_inputs.validate_saved(record, interaction)
    if inputs is None:
        raise ExecutionConflict("Saved initial Stage 1 inputs are required")
    if current:
        run_stage1_inputs._check_current(record, interaction)
    return record, inputs, validate_saved(record, inputs)


def _save(record: dict[str, Any], state: ExecutionState) -> None:
    payload = state.model_dump(mode="json")
    ExecutionState.model_validate(payload)
    record["stage1_execution"] = payload
    run_admission._write(record)


def load_result(run_id: str, model: str) -> MemberResponse | None:
    """Read a retained result, including after cancellation/config drift; no dispatch."""
    from . import run_inspection

    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record = run_admission._read(run_id)
        interaction = run_interaction._validate_record(record)
        inputs = run_stage1_inputs.validate_saved(record, interaction)
        if inputs is None or model not in {r.model for r in inputs.requests}:
            raise ExecutionConflict("Member does not identify saved Stage 1 input")
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
    provider_key: str,
    expected_revision: int,
    retry_token: str | None = None,
) -> MemberResponse:
    """Guard one saved member request and preserve its normalized response.

    The provider must make one attempt with endpoint/timeout_seconds/payload as
    supplied, with no internal retries or context/continuation calls. provider_key
    is a stable non-secret adapter version, never a credential. No HTTP adapter is
    selected automatically here. Caller cancellation cancels this internal
    attempt; public client detachment requires a separate worker-owner layer,
    not this coroutine.
    """
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
            raise ExecutionConflict("Member does not identify saved Stage 1 input")
        if state is not None and state.provider_key != provider_key:
            raise ExecutionConflict("Stage 1 adapter identity changed")
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
            attempt=uuid4(),
            retry_of=known.attempt if known else None,
            phase="in_flight",
            result=None,
        )
        if state is None:
            state = ExecutionState(
                version=1,
                source_revision=expected_revision,
                inputs_sha256=run_stage1_inputs._digest(inputs),
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
                    ready_inputs != inputs
                    or ready is None
                    or ready.provider_key != provider_key
                    or active is None
                    or active != op
                ):
                    raise ExecutionConflict("Stage 1 request changed before dispatch")
            return await provider(
                endpoint=inputs.endpoint,
                timeout_seconds=inputs.timeout_seconds,
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
                "Initial Stage 1 provider attempt did not complete"
            ) from None
        try:
            if not isinstance(response, dict):
                raise ValueError("Invalid response")
            result = MemberResponse.model_validate(response)
        except (TypeError, ValueError):
            raise ProviderFailure(
                "Provider returned no supported Stage 1 response"
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
                current_inputs != inputs
                or current is None
                or current.provider_key != provider_key
                or active is None
                or active != op
            ):
                raise ExecutionConflict("Stage 1 attempt was superseded")
            active.result, active.phase = result, "complete"
            _save(record, current)
        return result.model_copy(deep=True)
    finally:
        with storage._storage_lock:
            _active.discard(ownership)
            run_cancellation._release(*ownership)
