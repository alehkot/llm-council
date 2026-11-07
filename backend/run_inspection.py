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
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_prequery,
    durable_refinement,
    prequery_policy,
    refinement_execution,
    refinement_provider,
    run_admission,
    run_interaction,
    run_skill_plan,
    run_skill_provenance,
    run_skill_snapshot,
    run_stage1_continuation_inputs,
    run_stage1_execution,
    run_stage1_inputs,
    storage,
)

InternalPhase = Literal[
    "initializing",
    "prepared",
    "waiting_input",
    "answer_accepted",
    "refinement_ready",
    "refinement_complete",
    "pre_query_complete",
    "running_refinement",
    "running_stage1",
    "stage1_partial",
    "stage1_needs_continuation",
    "stage1_complete",
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
    explicit_skill_snapshot: dict[str, Any] | None
    skill_plan: dict[str, Any] | None
    stage1_inputs: dict[str, Any] | None
    stage1_execution: dict[str, Any] | None
    stage1_continuation_inputs: dict[str, Any] | None
    refinement: dict[str, Any] | None
    pre_query: dict[str, Any] | None
    pre_query_policy: dict[str, Any] | None
    council_selection: dict[str, Any] | None
    council_model_budgets: dict[str, Any] | None
    council_request_policy: dict[str, Any] | None
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
            "explicit_skill_snapshot": None,
            "skill_plan": None,
            "stage1_inputs": None,
            "stage1_execution": None,
            "stage1_continuation_inputs": None,
            "refinement": None,
            "pre_query": None,
            "pre_query_policy": None,
            "council_selection": None,
            "council_request_policy": None,
            "council_model_budgets": None,
            "current_prompt": None,
            "retry_token": None,
            "operation_owned": False,
        }
        saved_skills = run_skill_snapshot.validate_saved(record)
        if saved_skills is not None:
            # Expose availability and identities without copying private skill
            # bodies into every status response/cursor calculation.
            snapshot["explicit_skill_snapshot"] = run_skill_provenance._identity(
                saved_skills
            )
        if record["phase"] == "initializing":
            if any(
                key in record
                for key in (
                    "interaction",
                    "refinement_execution",
                    "refinement_provider",
                    "pre_query_policy",
                    "council_selection",
                    "council_request_policy",
                    "council_model_budgets",
                    "skill_plan",
                    "stage1_inputs",
                    "stage1_execution",
                    "stage1_continuation_inputs",
                )
            ):
                raise run_admission.AdmissionUnavailable(
                    "Unprepared run has execution checkpoints; repair required"
                )
            return _with_cursor(snapshot)
        run_admission._validate_conversation(record)
        interaction = run_interaction._validate_record(record)
        skill_plan = run_skill_plan.validate_saved(record, interaction)
        if skill_plan is not None:
            snapshot["skill_plan"] = run_skill_plan.summary(skill_plan)
        stage1_inputs = run_stage1_inputs.validate_saved(record, interaction)
        if stage1_inputs is not None:
            snapshot["stage1_inputs"] = run_stage1_inputs.summary(stage1_inputs)
        stage1_execution = run_stage1_execution.validate_saved(record, stage1_inputs)
        if stage1_execution is not None:
            snapshot["stage1_execution"] = run_stage1_execution.summary(
                record["run_id"], stage1_execution, stage1_inputs
            )
        continuations = run_stage1_continuation_inputs.validate_saved(
            record, interaction
        )
        if continuations is not None:
            snapshot["stage1_continuation_inputs"] = (
                run_stage1_continuation_inputs.summary(continuations)
            )
        execution = refinement_execution._validate_record(record)
        provider = refinement_provider._validate_record(record, execution)
        latest_refinement = None
        # Validate every retained boundary, including consumed answer derivations.
        # Generic future workflows are intentionally unsupported, not runnable.
        for index, entry in enumerate(interaction.prompts):
            if entry.continuation.point == durable_prequery.POINT:
                pre_query = durable_prequery.validate_checkpoint(
                    record, interaction, index
                )
                snapshot["pre_query"] = (
                    pre_query.model_dump(mode="json")
                    if entry.phase == "consumed"
                    else entry.continuation.state
                )
                continue
            latest_refinement = durable_refinement._validate_checkpoint(record, entry)
            if entry.phase in ("answered", "consumed"):
                derived = durable_refinement._derive_answer(record, entry)
                if entry.phase == "consumed":
                    latest_refinement = derived
        policy = prequery_policy.validate_saved(record, interaction)
        if policy is not None:
            snapshot["pre_query_policy"] = policy.model_dump(mode="json")
        selection = council_selection.validate_saved(record, interaction)
        if selection is not None:
            snapshot["council_selection"] = selection.model_dump(mode="json")
        request_policy = council_request_policy.validate_saved(record, interaction)
        if request_policy is not None:
            snapshot["council_request_policy"] = request_policy.model_dump(mode="json")
        budgets = council_model_budgets.validate_saved(record, interaction)
        if budgets is not None:
            snapshot["council_model_budgets"] = budgets.model_dump(mode="json")
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
        stage1_owned = snapshot["stage1_execution"] is not None and any(
            member["phase"] == "running"
            for member in snapshot["stage1_execution"]["members"]
        )
        snapshot["operation_owned"] = bool(owned or stage1_owned)
        if record.get("cancellation") is not None:
            snapshot["internal_phase"] = record["cancellation"]["phase"]
        elif snapshot["stage1_execution"] is not None:
            phases = [m["phase"] for m in snapshot["stage1_execution"]["members"]]
            snapshot["operation_owned"] = "running" in phases
            if "running" in phases:
                snapshot["internal_phase"] = "running_stage1"
            elif "needs_retry" in phases:
                snapshot["internal_phase"] = "needs_retry"
            elif "needs_continuation" in phases:
                snapshot["internal_phase"] = "stage1_needs_continuation"
            elif all(phase == "complete" for phase in phases):
                snapshot["internal_phase"] = "stage1_complete"
            else:
                snapshot["internal_phase"] = "stage1_partial"
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
            elif latest.continuation.point == durable_prequery.POINT:
                snapshot["internal_phase"] = "pre_query_complete"
            elif (
                latest_refinement is not None and latest_refinement.phase == "complete"
            ):
                snapshot["internal_phase"] = "refinement_complete"
            else:
                snapshot["internal_phase"] = "refinement_ready"
        return _with_cursor(snapshot)
