"""Pinned model-limit evidence for future durable council requests.

The existing resolver may use its public catalog, checked-in defaults or an
unknown-model fallback. These are the values actually resolved, not a claim of
provider-verified limits. No completion, context reduction or retry is executed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from pydantic import ConfigDict, Field, StrictBool, StrictInt, StrictStr

from . import (
    council_request_policy,
    council_selection,
    model_context,
    prequery_policy,
    run_admission,
    run_interaction,
    storage,
)
from .consultation_contract import WireModel


class ModelBudgetConflict(ValueError):
    """Model-limit resolution cannot safely bind to this run."""


class ModelBudget(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: StrictStr
    context_length: Annotated[StrictInt, Field(gt=0)]
    max_completion_tokens: Annotated[StrictInt, Field(gt=0)]
    supported_parameters: tuple[StrictStr, ...]
    output_budget: Annotated[StrictInt, Field(gt=0)]
    input_budget: Annotated[StrictInt, Field(gt=0)]
    reasoning: StrictBool


class CouncilModelBudgets(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    request_policy_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    models: tuple[ModelBudget, ...]


def _identity(policy: council_request_policy.OpenRouterRequestPolicy) -> str:
    raw = json.dumps(
        policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _budget(model: str, raw: Any, max_tokens: int) -> ModelBudget:
    if not isinstance(raw, dict) or set(raw) != {
        "context_length",
        "max_completion_tokens",
        "supported_parameters",
    }:
        raise ValueError("Model limits are not canonical")
    context, completion = raw["context_length"], raw["max_completion_tokens"]
    parameters = raw["supported_parameters"]
    if (
        type(context) is not int
        or context <= 0
        or type(completion) is not int
        or completion <= 0
        or not isinstance(parameters, (list, tuple))
        or any(
            not isinstance(p, str)
            or not p
            or p.strip() != p
            or any(ord(c) < 32 or ord(c) == 127 for c in p)
            for p in parameters
        )
        or len(set(parameters)) != len(parameters)
    ):
        raise ValueError("Model limits are invalid")
    # Version 1 mirrors openrouter.query_model's initial request budgeting.
    output = min(max_tokens, completion, context // 4)
    return ModelBudget(
        model=model,
        context_length=context,
        max_completion_tokens=completion,
        supported_parameters=tuple(parameters),
        output_budget=output,
        input_budget=context - output - max(256, context // 20),
        reasoning="reasoning" in parameters,
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> CouncilModelBudgets | None:
    """Validate historical values and their derivation without resolving metadata."""
    if "council_model_budgets" not in record:
        return None
    try:
        raw = record["council_model_budgets"]
        if (
            record["phase"] != "prepared"
            or not isinstance(raw, dict)
            or set(raw) != set(CouncilModelBudgets.model_fields)
            or not isinstance(raw["models"], list)
            or any(
                not isinstance(m, dict)
                or set(m) != set(ModelBudget.model_fields)
                or not isinstance(m["supported_parameters"], list)
                for m in raw["models"]
            )
        ):
            raise ValueError("Noncanonical model budgets")
        saved = CouncilModelBudgets.model_validate(raw)
        policy = council_request_policy.validate_saved(record, interaction)
        selection = council_selection.validate_saved(record, interaction)
        if (
            policy is None
            or selection is None
            or saved.source_revision != interaction.revision
            or saved.source_revision != policy.source_revision
            or saved.request_policy_sha256 != _identity(policy)
            or tuple(m.model for m in saved.models) != selection.council_models
        ):
            raise ValueError("Model budgets source changed")
        for model in saved.models:
            expected = _budget(
                model.model,
                {
                    "context_length": model.context_length,
                    "max_completion_tokens": model.max_completion_tokens,
                    "supported_parameters": model.supported_parameters,
                },
                policy.max_tokens,
            )
            if model != expected:
                raise ValueError("Model budget derivation changed")
        return saved
    except (KeyError, ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved model budgets are incompatible; repair required"
        ) from exc


def _load_current(
    run_id: str, expected_revision: int
) -> tuple[
    dict[str, Any],
    run_interaction.InteractionState,
    council_selection.CouncilSelection,
    council_request_policy.OpenRouterRequestPolicy,
]:
    from . import run_inspection

    snapshot = run_inspection.inspect(run_id)
    if snapshot is None or snapshot["interaction"] is None:
        raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
    record, interaction = run_interaction._load(run_id)
    if interaction.revision != expected_revision:
        raise run_interaction.PromptConflict("Model budgets revision changed")
    selection = council_selection.validate_saved(record, interaction)
    policy = council_request_policy.validate_saved(record, interaction)
    response = prequery_policy.validate_saved(record, interaction)
    if (
        snapshot["internal_phase"] != "pre_query_complete"
        or selection is None
        or policy is None
        or response is None
    ):
        raise ModelBudgetConflict("Completed saved request policy is required")
    try:
        if council_selection._current(response) != selection:
            raise ModelBudgetConflict("Council selection changed")
        if council_request_policy._current(selection) != policy:
            raise ModelBudgetConflict("Request policy changed")
        if model_context.OPENROUTER_API_URL != policy.endpoint:
            raise ModelBudgetConflict("Model-limit resolver endpoint changed")
    except (
        council_selection.CouncilSelectionConflict,
        council_request_policy.RequestPolicyConflict,
    ) as exc:
        raise ModelBudgetConflict("Current model configuration is unsupported") from exc
    return record, interaction, selection, policy


async def bind(run_id: str, *, expected_revision: int) -> CouncilModelBudgets:
    """Pin resolved metadata once; repeat binds do not refresh catalog values.

    Catalog I/O happens outside the lock. All guards are rechecked after awaiting
    resolution; concurrent binders share the first saved value. Resolution failure
    writes nothing. There is no provider-completion request or dispatch authority.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        record, interaction, selection, policy = _load_current(
            run_id, expected_revision
        )
        saved = validate_saved(record, interaction)
        if saved is not None:
            return saved
    try:
        models = tuple(
            [
                _budget(
                    model, await model_context.model_limits(model), policy.max_tokens
                )
                for model in selection.council_models
            ]
        )
        resolved = CouncilModelBudgets(
            version=1,
            source_revision=expected_revision,
            request_policy_sha256=_identity(policy),
            models=models,
        )
    except (ValueError, TypeError) as exc:
        raise ModelBudgetConflict("Resolved model limits are unsupported") from exc
    with storage._storage_lock:
        record, interaction, current_selection, current_policy = _load_current(
            run_id, expected_revision
        )
        if current_selection != selection or current_policy != policy:
            raise ModelBudgetConflict("Model configuration changed during resolution")
        saved = validate_saved(record, interaction)
        if saved is not None:
            # Another resolver may see newer catalog metadata. First persisted
            # evidence wins, just as a repeat bind reuses the pinned values.
            return saved
        record["council_model_budgets"] = resolved.model_dump(mode="json")
        run_admission._write(record)
        return resolved
