"""Internal council roster/stage-selection provenance, not an execution config.

This captures loaded backend choices for a future durable executor. It does not
supply provider request policy, synthesis settings, skill routing or dispatch.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from . import config, prequery_policy, run_admission, run_interaction, storage
from .consultation_contract import WireModel


class CouncilSelectionConflict(ValueError):
    """Current selection cannot safely replace the saved choice."""


class CouncilSelection(WireModel):
    """Immutable selected models and stage switches, never a dispatch token."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    response_policy_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    model_backend: Literal["openrouter"]
    council_models: tuple[StrictStr, ...]
    negative_enabled: StrictBool
    deliberation_enabled: StrictBool
    pairwise_ranking_enabled: StrictBool

    @model_validator(mode="after")
    def valid_roster(self) -> CouncilSelection:
        if not self.council_models or len(set(self.council_models)) != len(
            self.council_models
        ):
            raise ValueError("Council roster must be nonempty and distinct")
        if any(
            not model
            or len(model) > 256
            or model.strip() != model
            or any(ord(c) < 32 or ord(c) == 127 for c in model)
            for model in self.council_models
        ):
            raise ValueError("Council model identifier is invalid")
        return self


def _policy_identity(policy: prequery_policy.ResponsePolicy) -> str:
    raw = json.dumps(
        policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _current(policy: prequery_policy.ResponsePolicy) -> CouncilSelection:
    try:
        # Resolve the loaded settings once; environment edits alone cannot change
        # a running backend. The returned tuple is detached from mutable lists.
        if not isinstance(config.COUNCIL_MODELS, (list, tuple)):
            raise ValueError("Council roster is invalid")
        return CouncilSelection(
            version=1,
            source_revision=policy.source_revision,
            response_policy_sha256=_policy_identity(policy),
            model_backend=config.MODEL_BACKEND,
            council_models=tuple(config.COUNCIL_MODELS),
            negative_enabled=config.NEGATIVE_MODE_ENABLED,
            deliberation_enabled=config.DELIBERATION_ENABLED,
            pairwise_ranking_enabled=config.PAIRWISE_RANKING_ENABLED,
        )
    except (ValueError, TypeError) as exc:
        raise CouncilSelectionConflict(
            "Current council selection is unsupported"
        ) from exc


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> CouncilSelection | None:
    """Inspect historical selection without consulting current configuration."""
    if "council_selection" not in record:
        return None
    try:
        raw = record["council_selection"]
        if record["phase"] != "prepared" or not isinstance(raw, dict):
            raise ValueError("Invalid run phase or selection")
        if set(raw) != set(CouncilSelection.model_fields):
            raise ValueError("Noncanonical selection")
        saved = CouncilSelection.model_validate(raw)
        policy = prequery_policy.validate_saved(record, interaction)
        if (
            policy is None
            or saved.source_revision != interaction.revision
            or saved.source_revision != policy.source_revision
            or saved.response_policy_sha256 != _policy_identity(policy)
        ):
            raise ValueError("Selection source changed")
        return saved
    except (KeyError, ValueError, TypeError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved council selection is incompatible; repair required"
        ) from exc


def bind(run_id: str, *, expected_revision: int) -> CouncilSelection:
    """Bind loaded settings after a saved policy; compare retries, never overwrite.

    No caller-supplied model overrides are accepted. Configuration drift conflicts
    even for a repeated bind, whereas ordinary inspection retains historical data.
    Cancellation/deletion and stale revisions fail before an idempotent return.
    """
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None or snapshot["interaction"] is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Council selection revision changed")
        policy = prequery_policy.validate_saved(record, interaction)
        if snapshot["internal_phase"] != "pre_query_complete" or policy is None:
            raise CouncilSelectionConflict(
                "Completed saved response policy is required"
            )
        current = _current(policy)
        saved = validate_saved(record, interaction)
        if saved is not None:
            if saved != current:
                raise CouncilSelectionConflict("Council selection changed")
            return saved
        record["council_selection"] = current.model_dump(mode="json")
        run_admission._write(record)
        return current
