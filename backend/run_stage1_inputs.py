"""Pinned initial Stage 1 requests, not a dispatcher or a complete run plan.

Version 1 requires an archived explicit-skill plan and messages that already fit
every saved model budget. Context reduction, continuation and retries need their
own durable guards; no legacy provider function is invoked by this module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    council_model_budgets,
    council_request_policy,
    council_selection,
    model_context,
    prequery_policy,
    run_admission,
    run_interaction,
    run_skill_plan,
    storage,
)
from .consultation_contract import WireModel


class Stage1InputConflict(ValueError):
    """Initial Stage 1 requests cannot safely be prepared for this run."""


class _Frozen(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class PromptMessage(_Frozen):
    role: Literal["system", "user"]
    content: StrictStr = Field(repr=False)


class InitialRequest(_Frozen):
    model: StrictStr
    input_budget: Annotated[StrictInt, Field(gt=0)]
    output_budget: Annotated[StrictInt, Field(gt=0)]
    payload_json: StrictStr = Field(repr=False)

    def payload(self) -> dict[str, Any]:
        """Return a detached copy of the exact canonical provider JSON body."""
        return json.loads(self.payload_json)


Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class Stage1Inputs(_Frozen):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    response_policy_sha256: Digest
    skill_plan_sha256: Digest
    model_budgets_sha256: Digest
    request_policy_sha256: Digest
    endpoint: StrictStr
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    input_token_bound: Annotated[StrictInt, Field(gt=0)]
    messages: tuple[PromptMessage, ...] = Field(repr=False)
    requests: tuple[InitialRequest, ...] = Field(repr=False)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: WireModel) -> str:
    return hashlib.sha256(_json(value.model_dump(mode="json")).encode()).hexdigest()


def _sources(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> tuple[
    prequery_policy.ResponsePolicy,
    run_skill_plan.SkillPlan,
    council_model_budgets.CouncilModelBudgets,
    council_request_policy.OpenRouterRequestPolicy,
]:
    response = prequery_policy.validate_saved(record, interaction)
    skills = run_skill_plan.validate_saved(record, interaction)
    budgets = council_model_budgets.validate_saved(record, interaction)
    request = council_request_policy.validate_saved(record, interaction)
    if response is None or skills is None or budgets is None or request is None:
        raise Stage1InputConflict(
            "Saved response, explicit skill plan, request policy and model budgets are required"
        )
    return response, skills, budgets, request


def _derive(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> Stage1Inputs:
    response, skills, budgets, request = _sources(record, interaction)
    # Match _stage1_system_prompt: skill instructions first, steering last,
    # trimmed independently and separated by blank lines. User text is exact.
    blocks = [
        part.strip()
        for part in (skills.stage1.text, response.system_prompt_modifier)
        if part and part.strip()
    ]
    messages = []
    if blocks:
        messages.append({"role": "system", "content": "\n\n".join(blocks)})
    messages.append({"role": "user", "content": response.user_query})
    bound = model_context.token_bound(messages)
    requests = []
    for budget in budgets.models:
        if bound > budget.input_budget:
            raise Stage1InputConflict(
                "Stage 1 messages require unsupported guarded context reduction"
            )
        payload = {
            "model": budget.model,
            "messages": messages,
            "max_tokens": budget.output_budget,
        }
        if budget.reasoning:
            payload["reasoning"] = {"effort": "high"}
        requests.append(
            InitialRequest(
                model=budget.model,
                input_budget=budget.input_budget,
                output_budget=budget.output_budget,
                payload_json=_json(payload),
            )
        )
    return Stage1Inputs(
        version=1,
        source_revision=interaction.revision,
        response_policy_sha256=_digest(response),
        skill_plan_sha256=_digest(skills),
        model_budgets_sha256=_digest(budgets),
        request_policy_sha256=_digest(request),
        endpoint=request.endpoint,
        timeout_seconds=120.0,
        input_token_bound=bound,
        messages=tuple(PromptMessage.model_validate(m) for m in messages),
        requests=tuple(requests),
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> Stage1Inputs | None:
    """Validate version-1 requests from retained sources, without live settings."""
    if "stage1_inputs" not in record:
        return None
    try:
        raw = record["stage1_inputs"]
        saved = Stage1Inputs.model_validate(raw)
        if (
            record["phase"] != "prepared"
            or saved.model_dump(mode="json") != raw
            or saved != _derive(record, interaction)
        ):
            raise ValueError("Stage 1 inputs differ from their saved source")
        return saved
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved Stage 1 inputs are incompatible; repair required"
        ) from exc


def summary(saved: Stage1Inputs) -> dict[str, Any]:
    """Expose identity and counts, without copying private messages or payloads."""
    return {
        "version": saved.version,
        "source_revision": saved.source_revision,
        "sha256": _digest(saved),
        "request_count": len(saved.requests),
        "message_count": len(saved.messages),
        "input_token_bound": saved.input_token_bound,
    }


def bind(run_id: str, *, expected_revision: int) -> Stage1Inputs:
    """Pin initial requests atomically; never resolve, fit, schedule or dispatch."""
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Stage 1 input revision changed")
        if snapshot is None or snapshot["internal_phase"] != "pre_query_complete":
            raise Stage1InputConflict("Completed pre-query input is required")
        response, skills, _, request = _sources(record, interaction)
        selection = council_selection.validate_saved(record, interaction)
        try:
            if (
                selection != council_selection._current(response)
                or request != council_request_policy._current(selection)
                or skills.settings != run_skill_plan._current()
            ):
                raise Stage1InputConflict("Stage 1 input configuration changed")
        except (
            council_selection.CouncilSelectionConflict,
            council_request_policy.RequestPolicyConflict,
            run_skill_plan.SkillPlanConflict,
        ) as exc:
            raise Stage1InputConflict(
                "Current Stage 1 input configuration is unsupported"
            ) from exc
        saved = validate_saved(record, interaction)
        if saved is not None:
            return saved
        prepared = _derive(record, interaction)
        record["stage1_inputs"] = prepared.model_dump(mode="json")
        run_admission._write(record)
        return prepared


def load(run_id: str) -> Stage1Inputs:
    """Read historical initial requests, including cancelled runs; not runnable."""
    from . import run_inspection

    with storage._storage_lock:
        run_inspection.inspect(run_id)
        record = run_admission._read(run_id)
        saved = (
            validate_saved(record, run_interaction._validate_record(record))
            if record is not None
            else None
        )
        if saved is None:
            raise Stage1InputConflict("Stage 1 inputs have not been saved")
        return saved
