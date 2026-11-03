"""One revision-bound internal refinement step, not a council worker or HTTP API.

Waiting returns immediately. Consuming an answer returns its saved continuation;
it never starts the next provider operation in that same call. A separate call
must name the new revision, and uncertain work still requires explicit consent.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from . import (
    durable_refinement,
    refinement_execution,
    refinement_provider,
    run_admission,
    run_inspection,
    storage,
)


async def advance(
    run_id: str,
    *,
    expected_revision: int,
    api_key: str | None = None,
    retry_token: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> run_inspection.RunInspectionSnapshot:
    """Advance only the named current boundary, with at most one provider call.

    Waiting, completed refinement and cancellation are returned without writes,
    credentials or current provider configuration. An accepted answer is reduced
    once and returned; a duplicate using its old revision conflicts rather than
    moving a newer round. Provider failures propagate and retain uncertainty.
    Reading/polling should use run_inspection.inspect instead of this mutation.

    No manager queue, background task or public capability is installed. This
    does not establish worker-pool release, client detachment, full pipeline
    provenance, or completion of a council consultation. The caller owns the
    await; cancelling that task has the existing uncertain-provider semantics.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    if retry_token is not None:
        UUID(retry_token)
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None or snapshot["internal_phase"] == "initializing":
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        interaction = snapshot["interaction"]
        assert interaction is not None
        if interaction["revision"] != expected_revision:
            raise refinement_execution.ExecutionConflict(
                "Refinement revision changed; reload this run"
            )
        if not snapshot["request"]["refine"]:
            raise refinement_execution.ExecutionConflict(
                "Run did not request refinement"
            )
        phase = snapshot["internal_phase"]
        if phase in ("cancelling", "cancelled"):
            return snapshot
        if retry_token is not None and phase != "needs_retry":
            raise refinement_execution.ExecutionConflict(
                "Retry token does not identify uncertain work"
            )
        if phase in ("waiting_input", "refinement_complete"):
            return snapshot
        if phase == "answer_accepted":
            instance_id = interaction["prompts"][-1]["instance"]["instance_id"]
            durable_refinement.resume_answer(run_id, instance_id)
            result = run_inspection.inspect(run_id)
            assert result is not None
            return result
        if phase == "running_refinement":
            raise refinement_execution.ExecutionPending("Suggestion is already running")
        if phase not in ("prepared", "refinement_ready", "needs_retry"):
            raise refinement_execution.ExecutionConflict(
                "Unsupported refinement boundary"
            )
        if phase == "needs_retry" and retry_token is None:
            assert snapshot["retry_token"] is not None
            raise refinement_execution.RetryRequired(snapshot["retry_token"])
    if api_key is None:
        raise ValueError("A provider credential is required for a suggestion")
    # The adapter rechecks revision, cancellation and ownership before dispatch.
    # Never hold the storage lock while awaiting external work.
    await refinement_provider.suggest_configured_refinement(
        run_id,
        api_key=api_key,
        expected_revision=expected_revision,
        retry_token=retry_token,
        _transport=_transport,
    )
    result = run_inspection.inspect(run_id)
    if result is None:
        raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
    return result
