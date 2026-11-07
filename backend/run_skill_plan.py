"""Pinned deterministic skill preparation for an explicitly disabled router.

No model router is bypassed: an enabled reference router is unsupported here.
Only already-archived explicit skills and a completed saved response policy are
consumed. This is not full stage input, scheduling or dispatch authorization.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    config,
    prequery_policy,
    run_admission,
    run_interaction,
    run_skill_provenance,
    run_skill_rendering,
    run_skill_snapshot,
    skills,
    storage,
)
from .consultation_contract import WireModel


class SkillPlanConflict(ValueError):
    """This run cannot safely prepare or replace its saved skill plan."""


class _Frozen(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class SkillPlanSettings(_Frozen):
    selection_mode: Literal["router_disabled"]
    max_reference_files: Annotated[StrictInt, Field(ge=0)]
    max_reference_sections: Annotated[StrictInt, Field(ge=0)]
    stage1_max_chars: Annotated[StrictInt, Field(ge=0)]
    stage3_max_chars: Annotated[StrictInt, Field(ge=0)]


class SelectedReference(_Frozen):
    skill_id: StrictStr
    path: StrictStr
    section_ids: tuple[StrictStr, ...]


class RenderedSkillBlock(_Frozen):
    text: StrictStr | None = Field(repr=False)
    report_json: StrictStr = Field(repr=False)

    def report(self) -> dict[str, Any]:
        """Return a detached report; immutable canonical JSON is retained."""
        return json.loads(self.report_json)


class SkillPlan(_Frozen):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    source_revision: Annotated[StrictInt, Field(ge=1)]
    response_policy_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    archive_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    settings: SkillPlanSettings
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    reference_budget: Annotated[StrictInt, Field(ge=0)]
    selected_candidates: tuple[StrictInt, ...]
    selected_references: tuple[SelectedReference, ...]
    stage1: RenderedSkillBlock
    stage3: RenderedSkillBlock


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _current() -> SkillPlanSettings:
    if (
        config.SKILLS_ENABLED is not True
        or config.SKILLS_REFERENCE_SELECTION_ENABLED is not False
    ):
        raise SkillPlanConflict(
            "Enabled skills with an explicitly disabled reference router are required"
        )
    try:
        return SkillPlanSettings(
            selection_mode="router_disabled",
            max_reference_files=config.SKILLS_MAX_REFERENCES,
            max_reference_sections=config.SKILLS_MAX_REFERENCE_SECTIONS,
            stage1_max_chars=config.SKILLS_CONTEXT_MAX_CHARS,
            stage3_max_chars=config.SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS,
        )
    except (TypeError, ValueError) as exc:
        raise SkillPlanConflict(
            "Current skill preparation settings are unsupported"
        ) from exc


def _derive(
    record: dict[str, Any],
    interaction: run_interaction.InteractionState,
    settings: SkillPlanSettings,
) -> SkillPlan:
    policy = prequery_policy.validate_saved(record, interaction)
    captured = run_skill_snapshot.validate_saved(record)
    if policy is None or captured is None:
        raise SkillPlanConflict(
            "Saved response policy and explicit skill archive are required"
        )
    models = run_skill_rendering._models(captured)
    candidates = skills.build_reference_candidates(models)
    base_block, _ = run_skill_rendering._render_captured(
        captured, {}, max_chars=settings.stage1_max_chars
    )
    reference_budget = max(0, settings.stage1_max_chars - len(base_block or ""))
    selected_ids = skills.fallback_reference_ids(
        candidates,
        policy.user_query,
        settings.max_reference_files,
        settings.max_reference_sections,
        reference_budget,
    )
    by_id = {item.candidate_id: item for item in candidates}
    pending: dict[str, dict[str, list[str]]] = {}
    for candidate_id in selected_ids:
        item = by_id[candidate_id]
        pending.setdefault(item.skill_id, {}).setdefault(item.path, []).append(
            item.section_id
        )
    selected = {
        skill_id: [
            skills.SectionSelection(path, tuple(ids)) for path, ids in paths.items()
        ]
        for skill_id, paths in pending.items()
    }

    def render(limit: int) -> RenderedSkillBlock:
        text, report = run_skill_rendering._render_captured(
            captured, selected, max_chars=limit
        )
        return RenderedSkillBlock(text=text, report_json=_json(report))

    return SkillPlan(
        version=1,
        source_revision=interaction.revision,
        response_policy_sha256=_digest(policy.model_dump(mode="json")),
        archive_sha256=_digest(run_skill_provenance._identity(captured)),
        settings=settings,
        candidate_count=len(candidates),
        reference_budget=reference_budget,
        selected_candidates=tuple(selected_ids),
        selected_references=tuple(
            SelectedReference(
                skill_id=skill_id, path=choice.path, section_ids=choice.section_ids
            )
            for skill_id, choices in selected.items()
            for choice in choices
        ),
        stage1=render(settings.stage1_max_chars),
        stage3=render(settings.stage3_max_chars),
    )


def validate_saved(
    record: dict[str, Any], interaction: run_interaction.InteractionState
) -> SkillPlan | None:
    """Validate exact saved derivation using historical inputs, never live config."""
    if "skill_plan" not in record:
        return None
    try:
        raw = record["skill_plan"]
        saved = SkillPlan.model_validate(raw)
        if (
            record["phase"] != "prepared"
            or saved.model_dump(mode="json") != raw
            or saved != _derive(record, interaction, saved.settings)
        ):
            raise ValueError("Skill plan differs from its saved source")
        return saved
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved skill plan is incompatible; repair required"
        ) from exc


def summary(plan: SkillPlan) -> dict[str, Any]:
    """Compact status evidence without copying the private prompt/report text."""
    return {
        "version": plan.version,
        "source_revision": plan.source_revision,
        "sha256": _digest(plan.model_dump(mode="json")),
        "selection_mode": plan.settings.selection_mode,
        "candidate_count": plan.candidate_count,
        "selected_count": len(plan.selected_candidates),
        "stage1_chars": len(plan.stage1.text or ""),
        "stage3_chars": len(plan.stage3.text or ""),
    }


def bind(run_id: str, *, expected_revision: int) -> SkillPlan:
    """Pin reference choices and both skill blocks without any provider calls."""
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        snapshot = run_inspection.inspect(run_id)
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Skill-plan revision changed")
        if snapshot is None or snapshot["internal_phase"] != "pre_query_complete":
            raise SkillPlanConflict("Completed pre-query input is required")
        settings = _current()
        saved = validate_saved(record, interaction)
        if saved is not None:
            if saved.settings != settings:
                raise SkillPlanConflict("Skill preparation settings changed")
            return saved
        try:
            plan = _derive(record, interaction, settings)
        except (KeyError, TypeError, ValueError) as exc:
            raise SkillPlanConflict("Skill preparation inputs are unsupported") from exc
        record["skill_plan"] = plan.model_dump(mode="json")
        run_admission._write(record)
        return plan


def load(run_id: str) -> SkillPlan:
    """Read exact historical inputs, including cancelled runs; not runnable state."""
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
            raise SkillPlanConflict("Skill plan has not been saved")
        return saved
