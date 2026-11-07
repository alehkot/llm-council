"""Compact whole-roster Stage 1 projection from already validated evidence.

This module neither schedules work nor decides whether a request may execute.
Call summary under the storage lock, after validating all supplied saved states.
"""

from __future__ import annotations

from typing import Any

from . import (
    run_stage1_continuation_execution,
    run_stage1_continuation_inputs,
    run_stage1_execution,
    run_stage1_inputs,
)


def summary(
    run_id: str,
    inputs: run_stage1_inputs.Stage1Inputs,
    initial: run_stage1_execution.ExecutionState | None,
    continuation_inputs: run_stage1_continuation_inputs.ContinuationInputs | None,
    continuation: run_stage1_continuation_execution.ExecutionState | None,
) -> dict[str, Any]:
    """Combine initial and first-continuation state in saved council order.

    `turn` names the current/needed request, or the last completed request for a
    complete member. Turn 2 is only an unmet need, not implemented execution.
    `request_prepared` reports saved input, never dispatch or retry permission.
    Per-attempt summaries remain available separately as historical evidence.
    """
    initial_members = (
        {
            m["model"]: m
            for m in run_stage1_execution.summary(run_id, initial, inputs)["members"]
        }
        if initial is not None
        else {}
    )
    continuation_members = (
        {
            m["model"]: m
            for m in run_stage1_continuation_execution.summary(
                run_id, continuation, continuation_inputs
            )["members"]
        }
        if continuation is not None
        else {}
    )
    prepared = (
        {r.model for r in continuation_inputs.requests}
        if continuation_inputs is not None
        else set()
    )
    members = []
    for request in inputs.requests:
        current = initial_members.get(request.model)
        phase = current["phase"] if current is not None else "not_started"
        token = current["retry_token"] if current is not None else None
        turn, request_prepared = 0, True
        if phase == "needs_continuation":
            turn, request_prepared = 1, request.model in prepared
            following = continuation_members.get(request.model)
            if following is not None and following["phase"] != "not_started":
                phase, token = following["phase"], following["retry_token"]
                if phase == "needs_continuation":
                    turn, request_prepared = 2, False
        members.append(
            {
                "model": request.model,
                "phase": phase,
                "turn": turn,
                "request_prepared": request_prepared,
                "retry_token": token,
            }
        )
    counts = {
        phase: sum(m["phase"] == phase for m in members)
        for phase in (
            "not_started",
            "running",
            "needs_retry",
            "needs_continuation",
            "complete",
        )
    }
    # Active work takes display precedence; all other needs remain in members
    # and counts, so a running sibling cannot hide uncertain work from callers.
    if any(m["phase"] == "running" and m["turn"] == 1 for m in members):
        phase = "running_continuation"
    elif counts["running"]:
        phase = "running_initial"
    elif counts["needs_retry"]:
        phase = "needs_retry"
    elif counts["needs_continuation"]:
        phase = "needs_continuation"
    elif counts["complete"] == len(members):
        phase = "complete"
    elif counts["not_started"] == len(members):
        phase = "ready"
    else:
        phase = "partial"
    return {
        "version": 1,
        "source_revision": inputs.source_revision,
        "inputs_sha256": run_stage1_inputs._digest(inputs),
        "phase": phase,
        "total_members": len(members),
        "counts": counts,
        "members": members,
    }
