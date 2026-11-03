"""Read-only snapshots of the currently implemented internal CLI checkpoints.

This is not the public RunSnapshot contract, a scheduler, or an API capability
upgrade. A prepared run has not been queued; completed refinement is not a
completed council consultation. All reads use the existing single-process lock.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, TypedDict

from . import (
    durable_refinement,
    refinement_execution,
    refinement_provider,
    run_admission,
    run_interaction,
    run_skill_provenance,
    storage,
)

InternalPhase = Literal[
    "initializing",
    "prepared",
    "waiting_input",
    "answer_accepted",
    "refinement_ready",
    "refinement_complete",
    "running_refinement",
    "needs_retry",
    "cancelling",
    "cancelled",
]


class _SnapshotEvidence(TypedDict):
    version: int
    run_id: str
    conversation_id: str
    created_at: str
    request: dict[str, Any]
    internal_phase: InternalPhase
    cancellation: dict[str, Any] | None
    interaction: dict[str, Any] | None
    execution: dict[str, Any] | None
    refinement_provider: dict[str, Any] | None
    explicit_skill_provenance: dict[str, Any] | None
    refinement: dict[str, Any] | None
    current_prompt: dict[str, Any] | None
    retry_token: str | None
    operation_owned: bool


class RunInspectionSnapshot(_SnapshotEvidence):
    """Internal evidence only; not the versioned public consultation contract."""

    cursor: str


def _with_cursor(snapshot: _SnapshotEvidence) -> RunInspectionSnapshot:
    # Include the complete visible projection, including ephemeral ownership.
    # A restart can change needs_retry without changing any persisted bytes.
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return {**snapshot, "cursor": hashlib.sha256(encoded.encode()).hexdigest()}


def inspect(run_id: str) -> RunInspectionSnapshot | None:
    """Return detached validated evidence without consuming, repairing or running.

    Unknown IDs return None; tombstones raise AdmissionDeleted. Invalid or
    unsupported checkpoints raise AdmissionUnavailable rather than guessing a
    resumable state. Cancellation markers take precedence over prompt/retry
    projections, but retain exact historical evidence. Even orphaned cancelling
    markers are reported as saved, never settled by this read.
    """
    with storage._storage_lock:
        record = run_admission._read(run_id)
        if record is None:
            return None
        if record["phase"] == "deleted":
            raise run_admission.AdmissionDeleted("Run was deleted")
        snapshot: _SnapshotEvidence = {
            "version": 1,
            "run_id": record["run_id"],
            "conversation_id": record["conversation_id"],
            "created_at": record["created_at"],
            "request": record["request"],
            "internal_phase": record["phase"],
            "cancellation": record.get("cancellation"),
            "interaction": None,
            "execution": None,
            "refinement_provider": None,
            "explicit_skill_provenance": run_skill_provenance.validate_saved(record),
            "refinement": None,
            "current_prompt": None,
            "retry_token": None,
            "operation_owned": False,
        }
        if record["phase"] == "initializing":
            if any(
                key in record
                for key in (
                    "interaction",
                    "refinement_execution",
                    "refinement_provider",
                )
            ):
                raise run_admission.AdmissionUnavailable(
                    "Unprepared run has execution checkpoints; repair required"
                )
            return _with_cursor(snapshot)
        run_admission._validate_conversation(record)
        interaction = run_interaction._validate_record(record)
        execution = refinement_execution._validate_record(record)
        provider = refinement_provider._validate_record(record, execution)
        latest_refinement = None
        # Validate every retained boundary, including consumed answer derivations.
        # Generic future workflows are intentionally unsupported, not runnable.
        for entry in interaction.prompts:
            latest_refinement = durable_refinement._validate_checkpoint(record, entry)
            if entry.phase in ("answered", "consumed"):
                derived = durable_refinement._derive_answer(record, entry)
                if entry.phase == "consumed":
                    latest_refinement = derived
        snapshot["interaction"] = interaction.model_dump(mode="json")
        snapshot["execution"] = execution.model_dump(mode="json")
        if provider is not None:
            snapshot["refinement_provider"] = {
                "configuration": provider.model_dump(mode="json"),
                "identity": provider.identity(),
            }
        if latest_refinement is not None:
            snapshot["refinement"] = latest_refinement.model_dump(mode="json")
        op = execution.operations[-1] if execution.operations else None
        owned = (
            op is not None
            and (record["run_id"], str(op.attempt)) in refinement_execution._active
        )
        snapshot["operation_owned"] = bool(owned)
        if record.get("cancellation") is not None:
            snapshot["internal_phase"] = record["cancellation"]["phase"]
        elif op is not None and op.phase == "in_flight":
            snapshot["internal_phase"] = (
                "running_refinement" if owned else "needs_retry"
            )
            if not owned:
                snapshot["retry_token"] = str(op.attempt)
        elif interaction.prompts:
            latest = interaction.prompts[-1]
            if latest.phase == "waiting":
                snapshot["internal_phase"] = "waiting_input"
                snapshot["current_prompt"] = latest.instance.model_dump(mode="json")
            elif latest.phase == "answered":
                snapshot["internal_phase"] = "answer_accepted"
            elif (
                latest_refinement is not None and latest_refinement.phase == "complete"
            ):
                snapshot["internal_phase"] = "refinement_complete"
            else:
                snapshot["internal_phase"] = "refinement_ready"
        return _with_cursor(snapshot)
