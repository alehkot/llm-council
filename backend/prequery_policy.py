"""Internal saved response policy, not resolved council/provider configuration.

The snapshot is a concrete future prompt input: exact query and modifier strings.
No provider, scheduler or public API consumes it yet. Version 1 follows the legacy
pre-query semantics. Changing those semantics requires an explicit version policy,
not silently recalculating a saved run's instructions.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import durable_prequery, run_admission, run_interaction, storage
from .consultation_contract import WireModel
from .interactive import InteractiveConfig, PromptResponse


class ResponsePolicy(WireModel):
    """Immutable input snapshot; effective defaults are not accepted answers."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_instance_id: StrictStr
    source_revision: Annotated[StrictInt, Field(ge=1)]
    user_query: StrictStr
    domain: (
        Literal[
            "general", "coding", "legal", "medical", "creative", "technical", "business"
        ]
        | None
    )
    style: Literal["concise", "detailed", "comprehensive"] | None
    priority: Literal["accuracy", "clarity", "completeness", "actionability"] | None
    system_prompt_modifier: StrictStr | None


def _derive(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> ResponsePolicy:
    if not interaction.prompts or interaction.prompts[-1].phase != "consumed":
        raise ValueError("Pre-query answer has not been consumed")
    entry = interaction.prompts[-1]
    state = durable_prequery.validate_checkpoint(
        record, interaction, len(interaction.prompts) - 1
    )
    if state.phase != "complete" or state.answer is None:
        raise ValueError("Pre-query answer is unavailable")
    answer = state.answer
    # The legacy graph does NOT apply any modifier for an explicit skip. For a
    # non-skipped response, omitted optional fields use execution defaults. The
    # original AnswerRequest remains untouched, including its omissions.
    effective = None
    if not answer.skipped:
        effective = InteractiveConfig.from_responses(
            {
                "pre_query_config": PromptResponse(
                    prompt_id="pre_query_config", responses=dict(answer.responses)
                )
            }
        )
    return ResponsePolicy(
        version=1,
        source_instance_id=str(entry.instance.instance_id),
        source_revision=interaction.revision,
        user_query=state.user_query,
        domain=effective.domain if effective else None,
        style=effective.style if effective else None,
        priority=effective.priority if effective else None,
        system_prompt_modifier=(effective.get_system_prompt_modifier() or None)
        if effective
        else None,
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> ResponsePolicy | None:
    """Validate exact saved policy against retained causal evidence; write nothing.

    An unsupported version, changed semantics or inconsistent source fails closed.
    This is not a claim of tamper-proof storage or a dispatch authorization token.
    """
    if "pre_query_policy" not in record:
        return None
    try:
        if record["phase"] != "prepared":
            raise ValueError("Run is not prepared")
        saved = ResponsePolicy.model_validate(record["pre_query_policy"])
        if saved != _derive(record, interaction):
            raise ValueError("Policy differs from its source or supported semantics")
        return saved
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved response policy is incompatible; repair required"
        ) from exc


def bind(run_id: str, *, expected_revision: int) -> ResponsePolicy:
    """Persist or return exact policy only after completed pre-query consumption.

    Idempotent retries and reads do not rewrite storage. Creation uses the same
    storage lock as prompt answers/cancellation/deletion. Reload after an uncertain
    write acknowledgment; no work is scheduled by this operation. A later executor
    must separately validate current run revision/cancellation before dispatch.
    """
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        # Validate adjacent evidence through the supported read projection first.
        snapshot = run_inspection.inspect(run_id)
        if snapshot is None or snapshot["interaction"] is None:
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Response-policy revision changed")
        saved = validate_saved(record, interaction)
        if saved is not None:
            return saved
        if snapshot["internal_phase"] != "pre_query_complete":
            raise run_interaction.PromptConflict(
                "Pre-query configuration is incomplete"
            )
        policy = _derive(record, interaction)
        record["pre_query_policy"] = policy.model_dump(mode="json")
        run_admission._write(record)
        return policy
