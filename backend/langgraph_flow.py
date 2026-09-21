"""LangGraph-based orchestration for the council workflow."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypedDict,
    cast,
)

from langgraph.graph import END, START, StateGraph

from . import skills, storage
from .config import (
    CHAIRMAN_MODEL,
    COUNCIL_MODELS,
    DELIBERATION_ENABLED,
    INTERACTIVE_MODE_ENABLED,
    INTERACTIVE_TIMEOUT_SECONDS,
    NEGATIVE_MODE_ENABLED,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
    SKILLS_AUTO_SELECT_ENABLED,
    SKILLS_AUTO_SELECT_MODEL,
    SKILLS_CONTEXT_MAX_CHARS,
    SKILLS_ENABLED,
    SKILLS_MAX_REFERENCE_SECTIONS,
    SKILLS_MAX_REFERENCES,
    SKILLS_MAX_SELECTED,
    SKILLS_REFERENCE_SELECTION_ENABLED,
    SKILLS_REFERENCE_SELECTION_MODEL,
    SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS,
)
from .council import (
    SKILL_REFERENCE_ERROR_UNPARSEABLE,
    _normalize_synthesis_mode,
    build_label_mapping,
    build_responses_for_synthesis,
    calculate_aggregate_gap_scores,
    calculate_aggregate_rankings,
    calculate_pairwise_rankings,
    generate_conversation_title,
    generate_negative_prompt,
    generate_prompt_refinement,
    select_skill_references,
    select_skills,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    stage_deliberation,
    stage_negative_evaluations,
)
from .interactive import (
    InteractiveConfig,
    InteractivePrompt,
    PromptResponse,
    get_pre_query_prompt,
    get_prompt_refinement_prompt,
    get_response_review_prompt,
    get_synthesis_prefs_prompt,
    serialize_prompt_responses,
)
from .trace import TraceCollector

_DECISION_SUPPORT_RANKING_GUIDANCE = (
    "When ranking, prioritize actionability, explicit trade-offs, and clear handling of "
    "risks or uncertainty."
)
_DECISION_SUPPORT_SYNTHESIS_GUIDANCE = (
    "In the final answer, surface decision criteria, trade-offs, risks/uncertainties, and "
    "make the guidance actionable."
)
_CANCELLED_MESSAGE = "Cancelled"
_MAX_PROMPT_REFINEMENT_ROUNDS = 3


class FlowCancelled(Exception):
    """Raised when a consultation is cancelled."""


class CouncilStateRequired(TypedDict):
    """Required keys for the LangGraph council state."""

    user_query: str


class CouncilState(CouncilStateRequired, total=False):
    """Mutable state for the LangGraph council flow."""

    negative_prompt: Optional[str]
    stage1: List[Dict[str, Any]]
    labels: List[str]
    label_to_model: Dict[str, str]
    negative: Dict[str, Any]
    aggregate_gap_scores: Optional[List[Dict[str, Any]]]
    stage2: List[Dict[str, Any]]
    aggregate_rankings: List[Dict[str, Any]]
    invalid_rankings: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    responses_for_synthesis: List[Dict[str, Any]]
    stage3: Dict[str, Any]
    error: Optional[str]


@dataclass
class FlowCallbacks:
    """Optional side-effect callbacks for streaming or background updates."""

    emit_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    update_message: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    set_status: Optional[Callable[[str], Awaitable[None]]] = None

    async def _call(self, fn: Optional[Callable[..., Any]], *args: Any) -> None:
        if fn is None:
            return
        result = fn(*args)
        if inspect.isawaitable(result):
            await result

    async def emit(self, payload: Dict[str, Any]) -> None:
        await self._call(self.emit_event, payload)

    async def update(self, updates: Dict[str, Any]) -> None:
        await self._call(self.update_message, updates)

    async def status(self, status: str) -> None:
        await self._call(self.set_status, status)


@dataclass
class SkillsRuntime:
    """The run's resolved Agent Skill prompt blocks, shared by Stage 1 and Stage 3.

    Resolved once per process by ``_ensure_skills_runtime`` so both stages inject
    byte-identical skill text even if a skill is replaced mid-run. ``metadata`` is the
    whole ``message.metadata["skills"]`` object - ids, paths, versions and counts only,
    never rendered text.
    """

    stage1_block: Optional[str]
    stage3_block: Optional[str]
    metadata: Dict[str, Any]


@dataclass
class FlowContext:
    """Runtime configuration and shared data for the council flow."""

    trace: TraceCollector
    callbacks: FlowCallbacks = field(default_factory=FlowCallbacks)
    negative_enabled: bool = NEGATIVE_MODE_ENABLED
    deliberation_enabled: bool = DELIBERATION_ENABLED
    interactive_enabled: bool = INTERACTIVE_MODE_ENABLED
    prompt_refinement_enabled: bool = PROMPT_REFINEMENT_ENABLED
    interactive_queue: Optional[asyncio.Queue] = None
    session_id: Optional[str] = None
    interactive_timeout_seconds: float = INTERACTIVE_TIMEOUT_SECONDS
    collected_responses: Dict[str, PromptResponse] = field(default_factory=dict)
    interactive_config: Optional[InteractiveConfig] = None
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    is_first_message: bool = False
    is_resume: bool = False
    cancel_event: Optional[asyncio.Event] = None
    cancel_requested: Optional[Callable[[], bool]] = None
    stage_progress: Dict[str, Any] = field(default_factory=dict)
    resume_metadata: Dict[str, Any] = field(default_factory=dict)
    skill_ids: List[str] = field(default_factory=list)
    skills_runtime: Optional[SkillsRuntime] = None

    async def emit_event(self, payload: Dict[str, Any]) -> None:
        await self.callbacks.emit(payload)

    async def update_message(self, updates: Dict[str, Any]) -> None:
        await self.callbacks.update(updates)

    async def set_status(self, status: str) -> None:
        await self.callbacks.status(status)

    def progress_callback(
        self,
        stage: str,
    ) -> Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]:
        async def _callback(
            model: str,
            status: str,
            detail: Optional[Dict[str, Any]] = None,
        ) -> None:
            await self.update_stage_progress(stage, model, status, detail)

        return _callback

    def refresh_interactive_config(self) -> None:
        self.interactive_config = InteractiveConfig.from_responses(
            self.collected_responses
        )

    async def persist_resume(self, updates: Dict[str, Any]) -> None:
        self.resume_metadata = {
            **(self.resume_metadata or {}),
            **updates,
            "updated_at": datetime.utcnow().isoformat(),
        }
        await self.update_message({"metadata": {"resume": dict(self.resume_metadata)}})

    def is_cancelled(self) -> bool:
        if self.cancel_event and self.cancel_event.is_set():
            return True
        if self.cancel_requested and self.cancel_requested():
            return True
        return False

    async def init_stage_progress(self, stage: str, models: List[str]) -> None:
        now = datetime.utcnow().isoformat()
        model_entries = {model: {"status": "pending"} for model in models}
        stage_state = {
            "state": "running",
            "total": len(models),
            "completed": 0,
            "failed": 0,
            "counts": {
                "pending": len(models),
                "running": 0,
                "complete": 0,
                "failed": 0,
            },
            "models": model_entries,
            "order": list(models),
            "started_at": now,
            "updated_at": now,
        }
        self.stage_progress[stage] = stage_state
        await self._publish_stage_progress(stage, None, "pending")

    async def update_stage_progress(
        self,
        stage: str,
        model: str,
        status: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        stage_state = self.stage_progress.get(stage)
        now = datetime.utcnow().isoformat()
        if stage_state is None:
            stage_state = {
                "state": "running",
                "total": 0,
                "completed": 0,
                "failed": 0,
                "counts": {
                    "pending": 0,
                    "running": 0,
                    "complete": 0,
                    "failed": 0,
                },
                "models": {},
                "order": [],
                "started_at": now,
                "updated_at": now,
            }
            self.stage_progress[stage] = stage_state

        models = stage_state.setdefault("models", {})
        order = stage_state.setdefault("order", [])
        if model not in models:
            models[model] = {"status": "pending"}
            if model not in order:
                order.append(model)

        entry = dict(models.get(model, {}))
        entry["status"] = status
        if detail:
            entry.update(detail)
        models[model] = entry

        counts = {"pending": 0, "running": 0, "complete": 0, "failed": 0}
        for info in models.values():
            state = info.get("status") or "pending"
            counts[state] = counts.get(state, 0) + 1

        total = len(models)
        completed = counts.get("complete", 0) + counts.get("failed", 0)

        stage_state.update(
            {
                "counts": counts,
                "total": total,
                "completed": completed,
                "failed": counts.get("failed", 0),
                "updated_at": now,
            }
        )

        if total > 0 and completed >= total:
            stage_state["state"] = "complete"
            stage_state.setdefault("ended_at", now)
        else:
            stage_state["state"] = "running"

        await self._publish_stage_progress(stage, model, status)

    async def _publish_stage_progress(
        self,
        stage: str,
        model: Optional[str],
        status: str,
    ) -> None:
        await self.update_message({"metadata": {"stage_progress": self.stage_progress}})
        await self.emit_event(
            {
                "type": "stage_progress",
                "stage": stage,
                "model": model,
                "status": status,
                "progress": self.stage_progress.get(stage),
            }
        )


def _combine_parts(parts: List[str]) -> Optional[str]:
    filtered = [part.strip() for part in parts if part and part.strip()]
    return " ".join(filtered) if filtered else None


def _combine_blocks(parts: List[Optional[str]]) -> Optional[str]:
    """Join multi-line prompt blocks with a blank line between them.

    Deliberately not ``_combine_parts``: that joins with a single space, which would
    collapse a multi-kilobyte markdown skill block onto one line.
    """
    filtered = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join(filtered) if filtered else None


def _text_length(value: Optional[str]) -> int:
    return len(value or "")


def _prompt_response_active(context: FlowContext, prompt_id: str) -> bool:
    response = context.collected_responses.get(prompt_id)
    return response is not None and not response.skipped


def _pre_query_config_active(context: FlowContext) -> bool:
    return _prompt_response_active(context, "pre_query_config")


def _synthesis_prefs_active(context: FlowContext) -> bool:
    return _prompt_response_active(context, "synthesis_prefs")


def _interactive_modifier(context: FlowContext) -> Optional[str]:
    if not _pre_query_config_active(context) or not context.interactive_config:
        return None
    modifier = context.interactive_config.get_system_prompt_modifier()
    return modifier or None


def _skills_stage1_block(context: FlowContext) -> Optional[str]:
    runtime = context.skills_runtime
    return runtime.stage1_block if runtime else None


def _skills_stage3_block(context: FlowContext) -> Optional[str]:
    runtime = context.skills_runtime
    return runtime.stage3_block if runtime else None


def _persisted_skills_plan(
    persisted: Any,
    loaded: List[skills.Skill],
) -> Optional[
    Tuple[Dict[str, List[skills.SectionSelection]], Dict[str, Dict[str, str]], bool]
]:
    """Rebuild a stored reference selection so a resume re-renders it router-free.

    Returns `(selected_sections, reasons, drift)`, or `None` when the persisted
    plan cannot be trusted and the selection must be resolved from scratch. A skill
    whose version moved keeps only the persisted sections that still exist in the
    current manifest and flags drift. Legacy plans without `section_ids` keep
    whole-file semantics.
    """
    if not isinstance(persisted, dict):
        return None
    resolved = persisted.get("resolved")
    if not isinstance(resolved, list) or not resolved:
        return None

    by_id: Dict[str, Dict[str, Any]] = {}
    for entry in resolved:
        if isinstance(entry, dict) and entry.get("id"):
            by_id[str(entry.get("id"))] = entry

    selected_sections: Dict[str, List[skills.SectionSelection]] = {}
    reasons: Dict[str, Dict[str, str]] = {}
    drift = False
    for skill in loaded:
        entry = by_id.get(skill.id)
        if entry is None:
            return None
        if entry.get("version") != skill.version:
            drift = True
        by_path = {reference.path: reference for reference in skill.references}
        selections: List[skills.SectionSelection] = []
        skill_reasons: Dict[str, str] = {}
        seen_paths: Set[str] = set()
        for reference in entry.get("references") or []:
            if not isinstance(reference, dict):
                continue
            path = str(reference.get("path") or "")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            current = by_path.get(path)
            if current is None:
                drift = True
                continue
            stored_sections = reference.get("sections")
            raw_ids = reference.get("section_ids")
            # A legacy manifest has no sections of its own: its synthetic section
            # means whole file, so stored section ids cannot be validated - and
            # must not shrink the selection on resume.
            if not current.sections:
                section_ids = ()
            elif isinstance(stored_sections, list):
                # Positional ids alone are unstable across version bumps: an
                # inserted heading shifts every later position. Require the stored
                # id+heading pair to match the current manifest, so a resume can
                # shrink the selection (flagged as drift) but never silently
                # inline different content under an old position.
                current_by_id = {
                    section.section_id: section.heading
                    for section in skills.reference_sections(current)
                }
                kept: List[str] = []
                for stored in stored_sections:
                    if not isinstance(stored, dict):
                        drift = True
                        continue
                    section_id = str(stored.get("id") or "")
                    heading = str(stored.get("heading") or "")
                    if (
                        section_id
                        and section_id in current_by_id
                        and current_by_id[section_id] == heading
                    ):
                        kept.append(section_id)
                    else:
                        drift = True
                section_ids = tuple(kept)
                if stored_sections and not section_ids:
                    continue
            elif isinstance(raw_ids, list):
                available = {
                    section.section_id for section in skills.reference_sections(current)
                }
                section_ids = tuple(
                    str(section_id)
                    for section_id in raw_ids
                    if str(section_id) in available
                )
                if len(section_ids) != len(raw_ids):
                    drift = True
                if raw_ids and not section_ids:
                    continue
            else:
                section_ids = ()
            selections.append(
                skills.SectionSelection(path=path, section_ids=section_ids)
            )
            reason = str(reference.get("reason") or "").strip()
            if reason:
                skill_reasons[path] = reason
        selected_sections[skill.id] = selections
        if skill_reasons:
            reasons[skill.id] = skill_reasons

    return selected_sections, reasons, drift


def _reference_selection_from_persisted(persisted: Any) -> Optional[Dict[str, Any]]:
    """Carry the original router decision forward when a stored plan is reused."""
    if not isinstance(persisted, dict):
        return None
    stored = persisted.get("reference_selection")
    if not isinstance(stored, dict):
        return None
    return {
        "enabled": bool(stored.get("enabled")),
        "model": stored.get("model"),
        "candidate_count": int(stored.get("candidate_count") or 0),
        "selected_count": int(stored.get("selected_count") or 0),
        "fallback": stored.get("fallback"),
    }


async def _resolve_reference_selection(
    context: FlowContext,
    state: CouncilState,
    candidates: List[skills.ReferenceCandidate],
    selection_meta: Dict[str, Any],
    loaded: Sequence[skills.Skill],
) -> Tuple[List[int], Dict[int, str]]:
    """Run the two-pass router, or the deterministic fallback when it cannot be used.

    Mutates ``selection_meta["fallback"]`` with one of ``router_disabled``,
    ``router_failed``, ``router_error`` or ``router_timeout``. ``query_model``
    swallows its own timeout and returns ``None``, so a reference-selection timeout
    normally surfaces as ``router_error``; ``router_timeout`` is only reached when an
    ``asyncio.TimeoutError`` propagates from an outer wrapper.
    """
    user_query = str(state.get("user_query") or "")
    selected_ids: List[int] = []
    reason_by_id: Dict[int, str] = {}
    fallback: Optional[str] = None

    if not SKILLS_REFERENCE_SELECTION_ENABLED:
        fallback = "router_disabled"
    else:
        try:
            router = await select_skill_references(
                user_query, candidates, trace=context.trace
            )
        except asyncio.TimeoutError:
            fallback = "router_timeout"
        except Exception:
            # Skills must never kill a run; degrade to the deterministic packer.
            fallback = "router_error"
        else:
            selection_meta["model"] = router.get("model") or selection_meta.get("model")
            if router.get("parsed"):
                selected_ids = [int(item) for item in router.get("selected") or []]
                reason_by_id = {
                    int(key): str(value)
                    for key, value in (router.get("reasons") or {}).items()
                }
                # Coercion capped the section count; enforce the distinct-document
                # cap here, in the router's stated order (most important first).
                by_candidate = {
                    candidate.candidate_id: candidate for candidate in candidates
                }
                kept_ids: List[int] = []
                used_files: Set[str] = set()
                for candidate_id in selected_ids:
                    candidate = by_candidate.get(candidate_id)
                    if candidate is None:
                        continue
                    file_key = f"{candidate.skill_id}/{candidate.path}"
                    if (
                        file_key not in used_files
                        and len(used_files) >= SKILLS_MAX_REFERENCES
                    ):
                        continue
                    kept_ids.append(candidate_id)
                    used_files.add(file_key)
                selected_ids = kept_ids
                reason_by_id = {
                    candidate_id: reason
                    for candidate_id, reason in reason_by_id.items()
                    if candidate_id in selected_ids
                }
            elif router.get("error") == SKILL_REFERENCE_ERROR_UNPARSEABLE:
                fallback = "router_failed"
            else:
                fallback = "router_error"

    if fallback:
        # Budget against what is actually left once the SKILL.md bodies are in.
        # Handing the fallback the whole budget makes it pick references that
        # render_skills_context must immediately drop, so no reference is inlined
        # at all for a realistically sized skill.
        base_block, _base_report = skills.render_skills_context(
            loaded, {}, SKILLS_CONTEXT_MAX_CHARS
        )
        reference_budget = max(0, SKILLS_CONTEXT_MAX_CHARS - len(base_block or ""))
        selected_ids = skills.fallback_reference_ids(
            candidates,
            user_query,
            SKILLS_MAX_REFERENCES,
            SKILLS_MAX_REFERENCE_SECTIONS,
            reference_budget,
        )
        reason_by_id = {}
        selection_meta["fallback"] = fallback

    return selected_ids, reason_by_id


def _count_selected_sections(
    loaded: Sequence[skills.Skill],
    selected_sections: Dict[str, List[skills.SectionSelection]],
) -> int:
    """Sections actually selected and inlined, including whole-file selections.

    Whole-file selections count the re-chunked sections the renderer will inline -
    not the manifest's synthetic single section a legacy manifest carries.
    """
    total = 0
    for skill in loaded:
        by_path = {reference.path: reference for reference in skill.references}
        for selection in selected_sections.get(skill.id, []):
            if selection.path not in by_path:
                continue
            if selection.section_ids:
                total += len(selection.section_ids)
            else:
                pairs = skills.read_reference_sections(skill.id, selection.path, [])
                total += len(pairs or [])
    return total


def _persisted_skill_ids(state: CouncilState) -> Optional[List[str]]:
    """Skill ids of a stored selection, for a router-free resume.

    Returns None when the stored plan carries no usable resolved entries, so the
    caller falls through to auto-selection (or to an empty runtime).
    """
    persisted = (state.get("metadata") or {}).get("skills")
    if not isinstance(persisted, dict):
        return None
    resolved = persisted.get("resolved")
    if resolved == [] and (persisted.get("skill_selection") or {}).get("mode") in (
        "auto",
        "auto_resumed",
    ):
        return []
    if not isinstance(resolved, list) or not resolved:
        return None
    skill_ids: List[str] = []
    for entry in resolved:
        if isinstance(entry, dict) and entry.get("id"):
            skill_id = str(entry.get("id")).strip().lower()
            if skill_id and skill_id not in skill_ids:
                skill_ids.append(skill_id)
    return skill_ids or None


def _skill_selection_from_persisted(state: CouncilState) -> Optional[Dict[str, Any]]:
    """Carry the stored skill-selection decision forward when a plan is reused."""
    persisted = (state.get("metadata") or {}).get("skills")
    if not isinstance(persisted, dict):
        return None
    stored = persisted.get("skill_selection")
    if not isinstance(stored, dict):
        return None
    return {
        "mode": str(stored.get("mode") or "manual"),
        "enabled": bool(stored.get("enabled")),
        "model": stored.get("model"),
        "candidate_count": int(stored.get("candidate_count") or 0),
        "selected_count": int(stored.get("selected_count") or 0),
        "fallback": stored.get("fallback"),
        "reasons": dict(stored.get("reasons") or {}),
    }


async def _resolve_skill_selection(
    context: FlowContext,
    state: CouncilState,
    selection_meta: Dict[str, Any],
) -> List[str]:
    """Pick which enabled skills this question needs: two-pass router + fallback.

    Mutates `selection_meta` with model/fallback/reasons. Mirrors
    `_resolve_reference_selection`: the deterministic fallback runs when the
    router is disabled, fails, times out, or returns an unusable answer.
    """
    user_query = str(state.get("user_query") or "")
    candidates = [skill for skill in skills.list_skills() if skill.enabled]
    selection_meta["candidate_count"] = len(candidates)
    selected_ids: List[str] = []
    fallback: Optional[str] = None

    if not candidates:
        return []

    if not SKILLS_REFERENCE_SELECTION_ENABLED:
        fallback = "router_disabled"
    else:
        try:
            router = await select_skills(user_query, candidates, trace=context.trace)
        except asyncio.TimeoutError:
            fallback = "router_timeout"
        except Exception:
            # Skills must never kill a run; degrade to the deterministic scorer.
            fallback = "router_error"
        else:
            if router.get("parsed"):
                by_index = {
                    index: candidate.id
                    for index, candidate in enumerate(candidates, start=1)
                }
                selected_ids = sorted(
                    {
                        by_index[candidate_id]
                        for candidate_id in router.get("selected") or []
                        if candidate_id in by_index
                    }
                )
                reasons_by_id = router.get("reasons") or {}
                for candidate_id in router.get("selected") or []:
                    if candidate_id not in by_index:
                        continue
                    reason = str(reasons_by_id.get(candidate_id) or "").strip()
                    if reason:
                        selection_meta["reasons"][by_index[candidate_id]] = reason
                selection_meta["model"] = router.get("model") or selection_meta.get(
                    "model"
                )
            elif router.get("error") == SKILL_REFERENCE_ERROR_UNPARSEABLE:
                fallback = "router_failed"
            else:
                fallback = "router_error"

    if fallback:
        selected_ids = skills.fallback_skill_ids(
            candidates, user_query, SKILLS_MAX_SELECTED
        )
        selection_meta["fallback"] = fallback

    return selected_ids


async def _ensure_skills_runtime(context: FlowContext, state: CouncilState) -> None:
    """Resolve the run's Agent Skill blocks once and cache them on the context.

    Idempotent, and called from the top of both ``stage1_node`` and ``stage3_node``:
    ``_route_from_start`` sends any crash-resume that already has Stage 2 output
    straight to Stage 3, so Stage 3 is the only place some runs resolve skills at all.
    When ``state`` already carries a usable persisted plan the selection is re-rendered
    deterministically with zero router calls, so a resume cannot silently swap the
    reference set Stage 1 ran under.

    When the consultation carries no explicit skill ids and
    ``SKILLS_AUTO_SELECT_ENABLED``, the enabled skill library is routed once here
    (same two-pass pattern as references), and the stored plan makes the resume
    replay that exact selection router-free.
    """
    if context.skills_runtime is not None:
        return

    requested = [
        str(skill_id).strip()
        for skill_id in (context.skill_ids or [])
        if str(skill_id).strip()
    ]
    if not SKILLS_ENABLED:
        context.skills_runtime = SkillsRuntime(
            stage1_block=None, stage3_block=None, metadata={}
        )
        return

    skill_selection_meta: Dict[str, Any] = {
        "mode": "manual" if requested else "none",
        "enabled": bool(SKILLS_AUTO_SELECT_ENABLED),
        "model": SKILLS_AUTO_SELECT_MODEL if SKILLS_AUTO_SELECT_ENABLED else None,
        "candidate_count": 0,
        "selected_count": 0,
        "fallback": None,
        "reasons": {},
    }

    if not requested:
        # No explicit selection: replay the stored auto-selection on resume, or run
        # the auto-selector when enabled. Explicit picks always win either way.
        persisted_ids = _persisted_skill_ids(state)
        if persisted_ids is not None:
            requested = persisted_ids
            stored = _skill_selection_from_persisted(state)
            if stored is not None:
                skill_selection_meta = stored
            if skill_selection_meta.get("mode") == "auto":
                skill_selection_meta["mode"] = "auto_resumed"
            skill_selection_meta["selected_count"] = len(requested)
        elif SKILLS_AUTO_SELECT_ENABLED:
            requested = await _resolve_skill_selection(
                context, state, skill_selection_meta
            )
            skill_selection_meta["mode"] = "auto"
            skill_selection_meta["selected_count"] = len(requested)

    if not requested:
        # An auto-selection that picked nothing is still a decision the operator
        # should see: persist the selection metadata so the UI can say so.
        empty_metadata: Dict[str, Any] = {}
        if skill_selection_meta.get("mode") == "auto":
            empty_metadata = {
                "requested": [],
                "resolved": [],
                "missing": [],
                "skill_selection": skill_selection_meta,
            }
        context.skills_runtime = SkillsRuntime(
            stage1_block=None, stage3_block=None, metadata=empty_metadata
        )
        if empty_metadata:
            await context.update_message({"metadata": {"skills": empty_metadata}})
        return

    # A skill deleted while queued or in flight is skipped, never fatal.
    loaded, missing = skills.load_skills(requested)

    selection_meta: Dict[str, Any] = {
        "enabled": bool(SKILLS_REFERENCE_SELECTION_ENABLED),
        "model": (
            SKILLS_REFERENCE_SELECTION_MODEL
            if SKILLS_REFERENCE_SELECTION_ENABLED
            else None
        ),
        "candidate_count": 0,
        "selected_count": 0,
        "fallback": None,
    }
    selected_sections: Dict[str, List[skills.SectionSelection]] = {}
    reasons: Dict[str, Dict[str, str]] = {}
    drift = False
    reused_plan = False

    if loaded:
        persisted = (state.get("metadata") or {}).get("skills")
        # Reuse the persisted plan even when some ids vanished: the survivors must
        # keep the reference set Stage 1 ran under. Missing ids are reported
        # separately by skills.load_skills.
        plan = _persisted_skills_plan(persisted, loaded)
        if plan is not None:
            selected_sections, reasons, drift = plan
            reused_plan = True
            stored_selection = _reference_selection_from_persisted(persisted)
            if stored_selection is not None:
                selection_meta = stored_selection
            else:
                selection_meta["candidate_count"] = len(
                    skills.build_reference_candidates(loaded)
                )
            selection_meta["selected_count"] = _count_selected_sections(
                loaded, selected_sections
            )
        else:
            candidates = skills.build_reference_candidates(loaded)
            selection_meta["candidate_count"] = len(candidates)
            selected_ids: List[int] = []
            reason_by_id: Dict[int, str] = {}
            # No references anywhere means nothing to route: skip the call entirely.
            if candidates:
                selected_ids, reason_by_id = await _resolve_reference_selection(
                    context, state, candidates, selection_meta, loaded
                )
            by_candidate = {
                candidate.candidate_id: candidate for candidate in candidates
            }
            # Group the flat candidate ids back into (skill, path) selections,
            # preserving the router's order inside each document. Whole-file
            # selections stay explicit: every section id is listed.
            pending: Dict[str, Dict[str, List[str]]] = {}
            for candidate_id in selected_ids:
                candidate = by_candidate.get(candidate_id)
                if candidate is None:
                    continue
                ids = pending.setdefault(candidate.skill_id, {}).setdefault(
                    candidate.path, []
                )
                if candidate.section_id not in ids:
                    ids.append(candidate.section_id)
                reason = str(reason_by_id.get(candidate_id) or "").strip()
                if reason:
                    reasons.setdefault(candidate.skill_id, {})[
                        f"{candidate.path}::{candidate.section_id}"
                    ] = reason
            selected_sections = {
                skill_id: [
                    skills.SectionSelection(path=path, section_ids=tuple(ids))
                    for path, ids in by_path.items()
                ]
                for skill_id, by_path in pending.items()
            }
            selection_meta["selected_count"] = _count_selected_sections(
                loaded, selected_sections
            )

    # Rendered twice from the SAME selection: the chairman gets a tighter budget
    # because it already carries the Stage 1 evidence those answers were written under.
    stage1_block, stage1_report = skills.render_skills_context(
        loaded, selected_sections, SKILLS_CONTEXT_MAX_CHARS, reasons
    )

    stage3_block, stage3_report = skills.render_skills_context(
        loaded, selected_sections, SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS, reasons
    )

    metadata: Dict[str, Any] = {
        "requested": requested,
        "resolved": list(stage1_report.get("resolved") or []),
        "missing": list(missing),
        "skill_selection": skill_selection_meta,
        "reference_selection": selection_meta,
        "budget": {
            "stage1_chars": int(stage1_report.get("chars") or 0),
            "stage1_max": int(SKILLS_CONTEXT_MAX_CHARS),
            "stage3_chars": int(stage3_report.get("chars") or 0),
            "stage3_max": int(SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS),
            "stage3_dropped_references": list(
                stage3_report.get("dropped_references") or []
            ),
        },
        "drift": bool(drift),
    }
    context.skills_runtime = SkillsRuntime(
        stage1_block=stage1_block,
        stage3_block=stage3_block,
        metadata=metadata,
    )

    # Text-free by construction: ids, counts and sizes only, never rendered skill text.
    context.trace.add_event(
        "skills_resolved",
        stage="skill_references",
        requested=requested,
        resolved=[entry.get("id") for entry in metadata["resolved"]],
        missing=list(missing),
        skill_selection=skill_selection_meta,
        reused_persisted_plan=reused_plan,
        candidate_count=selection_meta.get("candidate_count", 0),
        selected_count=selection_meta.get("selected_count", 0),
        stage1_chars=metadata["budget"]["stage1_chars"],
        stage1_max=metadata["budget"]["stage1_max"],
        stage3_chars=metadata["budget"]["stage3_chars"],
        stage3_max=metadata["budget"]["stage3_max"],
        stage3_dropped_references=metadata["budget"]["stage3_dropped_references"],
        truncated_skills=list(stage1_report.get("truncated_skills") or []),
        fallback=selection_meta.get("fallback"),
        drift=bool(drift),
    )

    # Written whole: storage merges metadata one level deep, so a partial write here
    # would destroy the sibling keys of "skills".
    await context.update_message({"metadata": {"skills": metadata}})


def _state_metadata_with_skills(
    context: FlowContext, state: CouncilState
) -> Dict[str, Any]:
    """Graph-state metadata carrying the resolved skills plan.

    ``_ensure_skills_runtime`` persists the resolved plan straight to storage, but
    the graph state still holds the ``{"requested": [...]}`` seed written by
    ``ConsultationManager._build_resume_metadata``. Later nodes rebuild their
    metadata as ``{**state["metadata"], ...}`` and ``update_assistant_message``
    merges only one level deep, so without this the seed silently overwrites the
    resolved plan on the next write - taking resynthesis, crash-resume, the export
    and the UI summary down with it.
    """
    metadata = dict(state.get("metadata") or {})
    runtime = context.skills_runtime
    if runtime is not None and runtime.metadata:
        metadata["skills"] = runtime.metadata
    state["metadata"] = metadata
    return metadata


def _format_synthesis_prefs(config: InteractiveConfig) -> Optional[str]:
    parts = []
    if config.synthesis_mode:
        parts.append(f"Synthesis preference: {config.synthesis_mode}.")
    if config.focus_areas:
        parts.append(f"Focus areas: {config.focus_areas}.")
    return _combine_parts(parts)


def _stage1_system_prompt(context: FlowContext) -> Optional[str]:
    # Skills first, per-run interactive steering last so recency favours the steering.
    # Joined with blank lines (_combine_blocks), never _combine_parts' single space.
    # Skills are deliberately not gated on INTERACTIVE_MODE_ENABLED.
    return _combine_blocks(
        [_skills_stage1_block(context), _interactive_modifier(context)]
    )


def _stage2_prompt_modifier(context: FlowContext) -> Optional[str]:
    return _combine_parts(
        [
            _DECISION_SUPPORT_RANKING_GUIDANCE
            if _prompt_refinements_active(context)
            else "",
            _interactive_modifier(context) or "",
        ]
    )


def _stage3_prompt_modifier(context: FlowContext) -> Optional[str]:
    synthesis_prefs = (
        _format_synthesis_prefs(context.interactive_config)
        if context.interactive_config and _synthesis_prefs_active(context)
        else None
    )
    return _combine_parts(
        [
            _DECISION_SUPPORT_SYNTHESIS_GUIDANCE
            if _prompt_refinements_active(context)
            else "",
            _interactive_modifier(context) or "",
            synthesis_prefs or "",
        ]
    )


def _prompt_refinements_active(context: FlowContext) -> bool:
    return context.prompt_refinement_enabled and _pre_query_config_active(context)


def _normalize_refinement_action(action: Optional[str]) -> str:
    if action in {"use_refined", "use_original", "refine_again"}:
        return action
    return "use_refined"


def _has_stage1(state: CouncilState) -> bool:
    return state.get("stage1") is not None


def _has_negative_results(state: CouncilState) -> bool:
    negative = state.get("negative") or {}
    return bool(negative.get("enabled")) and negative.get("evaluations") is not None


def _has_deliberation_results(state: CouncilState) -> bool:
    deliberation = (state.get("metadata") or {}).get("deliberation") or {}
    return deliberation.get("results") is not None


def _has_stage2(state: CouncilState) -> bool:
    return state.get("stage2") is not None


def _has_stage3(state: CouncilState) -> bool:
    return state.get("stage3") is not None


def _collect_clarifying_answers(
    response: PromptResponse,
    questions: List[str],
) -> List[Dict[str, str]]:
    answers: List[Dict[str, str]] = []
    for idx, question in enumerate(questions):
        key = f"clarifying_answer_{idx}"
        raw = response.responses.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        answers.append({"question": question, "answer": text})
    return answers


def build_council_graph(context: FlowContext):
    """Build a LangGraph workflow with optional interactive and feature-flagged stages."""

    builder = StateGraph(CouncilState)

    def _cancel_state() -> Dict[str, Any]:
        return {"error": _CANCELLED_MESSAGE}

    def _excluded_models(state: CouncilState) -> set[str]:
        metadata_exclusions = (state.get("metadata") or {}).get("excluded_models") or []
        exclusions = set(metadata_exclusions)
        if context.interactive_config:
            exclusions.update(context.interactive_config.excluded_models)
        return exclusions

    def _active_stage1_inputs(
        state: CouncilState,
    ) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, str]]:
        excluded_models = _excluded_models(state)
        active_results = [
            result
            for result in state.get("stage1", [])
            if result.get("model")
            and result.get("model") not in excluded_models
            and not result.get("failed")
        ]
        if not active_results:
            return [], [], {}
        labels, label_to_model = build_label_mapping(active_results)
        return active_results, labels, label_to_model

    def _title_already_generated() -> bool:
        if not context.is_first_message:
            return True
        return bool(
            context.conversation_title
            and context.conversation_title != "New Conversation"
        )

    async def _persist_collected_responses(user_query: Optional[str] = None) -> None:
        updates: Dict[str, Any] = {
            "collected_responses": serialize_prompt_responses(
                context.collected_responses
            ),
        }
        if user_query is not None:
            updates["user_query"] = user_query
        await context.persist_resume(updates)

    async def _await_interactive_response(
        timeout_seconds: Optional[float] = None,
    ) -> PromptResponse:
        if not context.interactive_queue:
            raise RuntimeError("Interactive session not available")

        timeout = (
            context.interactive_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )

        if context.cancel_event is None and context.cancel_requested is None:
            return await asyncio.wait_for(
                context.interactive_queue.get(),
                timeout=timeout,
            )

        async def _poll_cancel_requested() -> None:
            while True:
                if context.cancel_requested and context.cancel_requested():
                    return
                await asyncio.sleep(0.25)

        response_task = asyncio.create_task(context.interactive_queue.get())
        cancel_tasks = set()
        if context.cancel_event is not None:
            cancel_tasks.add(asyncio.create_task(context.cancel_event.wait()))
        if context.cancel_requested is not None:
            cancel_tasks.add(asyncio.create_task(_poll_cancel_requested()))
        tasks = {response_task, *cancel_tasks}

        async def _cancel_tasks(tasks_to_cancel: set[asyncio.Task]) -> None:
            for task in tasks_to_cancel:
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception:
            await _cancel_tasks(tasks)
            raise

        if not done:
            await _cancel_tasks(pending)
            raise asyncio.TimeoutError

        if done & cancel_tasks:
            await _cancel_tasks({response_task, *pending})
            raise FlowCancelled()

        if response_task in done:
            await _cancel_tasks(pending)
            return response_task.result()

        await _cancel_tasks(pending)
        raise asyncio.TimeoutError

    async def _request_interactive_response(
        prompt: InteractivePrompt,
    ) -> PromptResponse:
        if not context.interactive_queue or not context.session_id:
            raise RuntimeError("Interactive session not available")

        await context.set_status("interactive")
        prompt_dict = prompt.to_dict()
        prompt_dict["session_id"] = context.session_id
        await context.update_message({"metadata": {"interactive_prompt": prompt_dict}})
        await context.emit_event(prompt_dict)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + context.interactive_timeout_seconds
        response = None

        while response is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await context.update_message({"metadata": {"interactive_prompt": None}})
                if prompt.skip_allowed:
                    response = PromptResponse(
                        prompt_id=prompt.prompt_id,
                        responses={},
                        skipped=True,
                    )
                    break
                raise asyncio.TimeoutError

            try:
                candidate = await _await_interactive_response(remaining)
            except FlowCancelled:
                await context.update_message({"metadata": {"interactive_prompt": None}})
                raise
            except asyncio.TimeoutError:
                await context.update_message({"metadata": {"interactive_prompt": None}})
                if prompt.skip_allowed:
                    response = PromptResponse(
                        prompt_id=prompt.prompt_id,
                        responses={},
                        skipped=True,
                    )
                    break
                raise

            if candidate.prompt_id == prompt.prompt_id:
                response = candidate
                break

        await context.emit_event(
            {
                "type": "interactive_response_received",
                "prompt_id": prompt.prompt_id,
            }
        )
        await context.update_message({"metadata": {"interactive_prompt": None}})
        return response

    async def title_node(state: CouncilState) -> Dict[str, Any]:
        if not context.is_first_message or not context.conversation_id:
            return {}
        if context.is_cancelled():
            return _cancel_state()

        context.trace.begin_stage("title", label="Title")

        title = "New Conversation"
        error_message = None
        try:
            title = await generate_conversation_title(
                state["user_query"], trace=context.trace
            )
        except Exception as exc:
            error_message = str(exc)

        unique_title = storage.update_conversation_title_unique(
            context.conversation_id,
            title,
        )

        if error_message:
            context.trace.end_stage("title", title=unique_title, error=error_message)
        else:
            context.trace.end_stage("title", title=unique_title)

        await context.emit_event(
            {
                "type": "title_complete",
                "data": {"title": unique_title},
            }
        )
        return {}

    async def pre_query_node(state: CouncilState) -> Dict[str, Any]:
        if not context.interactive_enabled:
            return {}
        if not context.interactive_queue or not context.session_id:
            return {"error": "Interactive session not available"}
        if context.is_cancelled():
            return _cancel_state()

        await context.emit_event(
            {"type": "session_start", "session_id": context.session_id}
        )

        async def run_prompt_refinement() -> Dict[str, Any]:
            if not context.prompt_refinement_enabled:
                return {}
            if context.is_cancelled():
                return _cancel_state()

            original_query = state["user_query"]
            refined_prompt = original_query
            decision = "use_original"
            clarifying_answers: List[Dict[str, str]] = []
            additional_context_parts: List[str] = []
            iteration = 1
            last_suggestion: Dict[str, Any] = {}

            context.trace.begin_stage("prompt_refinement", label="Prompt Refinement")

            while True:
                suggestion = await generate_prompt_refinement(
                    original_query,
                    clarifying_answers=clarifying_answers or None,
                    additional_context="\n\n".join(additional_context_parts) or None,
                    previous_refined_prompt=refined_prompt if iteration > 1 else None,
                    trace=context.trace,
                    iteration=iteration,
                )
                last_suggestion = suggestion
                context.trace.add_event(
                    "prompt_refinement_suggestion",
                    stage="prompt_refinement",
                    iteration=iteration,
                    intent=suggestion.get("intent") or "",
                    clarifying_question_count=len(
                        suggestion.get("clarifying_questions") or []
                    ),
                    refined_prompt_length=_text_length(
                        suggestion.get("refined_prompt") or ""
                    ),
                )

                if context.is_cancelled():
                    context.trace.end_stage("prompt_refinement", cancelled=True)
                    return _cancel_state()

                refinement_prompt = get_prompt_refinement_prompt(
                    suggestion,
                    iteration=iteration,
                )

                try:
                    response = context.collected_responses.get(
                        f"prompt_refinement_{iteration}"
                    )
                    if response is None:
                        response = await _request_interactive_response(
                            refinement_prompt
                        )
                except FlowCancelled:
                    context.trace.end_stage("prompt_refinement", cancelled=True)
                    return _cancel_state()
                except asyncio.TimeoutError:
                    context.trace.add_event(
                        "prompt_refinement_timeout",
                        stage="prompt_refinement",
                        iteration=iteration,
                    )
                    decision = "use_original"
                    refined_prompt = original_query
                    break
                except RuntimeError:
                    return {"error": "Interactive session not available"}

                context.collected_responses[f"prompt_refinement_{iteration}"] = response
                await _persist_collected_responses()
                if response.skipped:
                    context.trace.add_event(
                        "prompt_refinement_skipped",
                        stage="prompt_refinement",
                        iteration=iteration,
                    )
                    decision = "use_original"
                    refined_prompt = original_query
                    break

                user_action = _normalize_refinement_action(
                    response.responses.get("refinement_action")
                )
                action = user_action
                suggested_prompt = str(suggestion.get("refined_prompt") or "").strip()
                user_refined_prompt = str(
                    response.responses.get("refined_prompt") or ""
                ).strip()
                refined_prompt = (
                    user_refined_prompt or suggested_prompt or refined_prompt
                )

                new_answers = _collect_clarifying_answers(
                    response,
                    suggestion.get("clarifying_questions") or [],
                )
                if new_answers:
                    clarifying_answers.extend(new_answers)

                extra = str(response.responses.get("additional_insights") or "").strip()
                if extra:
                    additional_context_parts.append(extra)

                if (new_answers or extra) and action != "refine_again":
                    action = "refine_again"

                context.trace.add_event(
                    "prompt_refinement_response",
                    stage="prompt_refinement",
                    iteration=iteration,
                    user_action=user_action,
                    final_action=action,
                    refined_prompt_length=_text_length(user_refined_prompt),
                    clarifying_answer_count=len(new_answers),
                    additional_insights_length=_text_length(extra),
                )

                if action == "use_original":
                    decision = action
                    refined_prompt = original_query
                    break
                if action == "use_refined":
                    decision = action
                    refined_prompt = refined_prompt or original_query
                    break

                iteration += 1
                if iteration > _MAX_PROMPT_REFINEMENT_ROUNDS:
                    decision = "use_refined"
                    refined_prompt = refined_prompt or original_query
                    break

            iterations_used = min(iteration, _MAX_PROMPT_REFINEMENT_ROUNDS)
            context.trace.end_stage(
                "prompt_refinement",
                decision=decision,
                iterations=iterations_used,
                original_prompt_length=_text_length(original_query),
                refined_prompt_length=_text_length(refined_prompt),
                intent=last_suggestion.get("intent") or "",
                clarifying_question_count=len(
                    last_suggestion.get("clarifying_questions") or []
                ),
                clarifying_answer_count=len(clarifying_answers),
            )

            prompt_refinement_metadata = {
                "prompt_refinement": {
                    "enabled": True,
                    "decision": decision,
                    "original_prompt": original_query,
                    "refined_prompt": refined_prompt,
                    "intent": last_suggestion.get("intent") or "",
                    "clarifying_questions": last_suggestion.get("clarifying_questions")
                    or [],
                    "clarifying_answers": clarifying_answers,
                    "iterations": iterations_used,
                }
            }

            await context.update_message({"metadata": prompt_refinement_metadata})
            await _persist_collected_responses(refined_prompt or original_query)

            return {
                "user_query": refined_prompt or original_query,
                "metadata": prompt_refinement_metadata,
            }

        updated_state: Dict[str, Any] = {}
        if context.prompt_refinement_enabled:
            refinement_state = await run_prompt_refinement()
            if refinement_state.get("error"):
                return refinement_state
            if refinement_state.get("user_query"):
                updated_state["user_query"] = refinement_state["user_query"]
            if refinement_state.get("metadata"):
                merged_metadata = {
                    **(state.get("metadata") or {}),
                    **refinement_state["metadata"],
                }
                updated_state["metadata"] = merged_metadata

        pre_query_prompt = get_pre_query_prompt()
        try:
            response = context.collected_responses.get("pre_query_config")
            if response is None:
                response = await _request_interactive_response(pre_query_prompt)
        except FlowCancelled:
            return _cancel_state()
        except asyncio.TimeoutError:
            return {"error": "Interactive session timed out waiting for response"}
        except RuntimeError:
            return {"error": "Interactive session not available"}

        context.collected_responses["pre_query_config"] = response
        await _persist_collected_responses(
            updated_state.get("user_query") or state["user_query"]
        )
        context.refresh_interactive_config()
        return updated_state

    async def stage1_node(state: CouncilState) -> Dict[str, Any]:
        if context.is_cancelled():
            return _cancel_state()
        await _ensure_skills_runtime(context, state)
        _state_metadata_with_skills(context, state)
        await context.set_status("stage1")
        await context.init_stage_progress("stage1", COUNCIL_MODELS)
        refinement_meta = (state.get("metadata") or {}).get("prompt_refinement")
        refinement_trace = None
        if refinement_meta:
            refinement_trace = {
                "enabled": refinement_meta.get("enabled"),
                "decision": refinement_meta.get("decision"),
                "iterations": refinement_meta.get("iterations"),
                "original_prompt_length": _text_length(
                    refinement_meta.get("original_prompt")
                ),
                "refined_prompt_length": _text_length(
                    refinement_meta.get("refined_prompt")
                ),
            }
        context.trace.add_event(
            "prompt_finalized",
            stage="stage1",
            prompt_length=_text_length(state["user_query"]),
            refinement=refinement_trace,
        )
        context.trace.begin_stage("stage1", label="Stage 1")
        await context.emit_event({"type": "stage1_start"})

        system_prompt = _stage1_system_prompt(context)
        stage1_results = await stage1_collect_responses(
            state["user_query"],
            trace=context.trace,
            system_prompt=system_prompt,
            progress=context.progress_callback("stage1"),
        )

        if context.is_cancelled():
            context.trace.end_stage(
                "stage1",
                response_count=len(stage1_results),
                cancelled=True,
            )
            return _cancel_state()

        successful_results = [
            result for result in stage1_results if not result.get("failed")
        ]
        if not successful_results:
            context.trace.end_stage("stage1", response_count=0)
            error_message = "All models failed to respond. Please try again."
            await context.update_message({"stage1": stage1_results})
            return {"error": error_message, "stage1": stage1_results}

        context.trace.end_stage("stage1", response_count=len(stage1_results))
        labels, label_to_model = build_label_mapping(stage1_results)
        await context.update_message(
            {
                "stage1": stage1_results,
                "metadata": {"label_to_model": label_to_model},
            }
        )
        await context.emit_event(
            {
                "type": "stage1_complete",
                "data": stage1_results,
                "labels": labels,
            }
        )

        if not context.negative_enabled:
            await context.update_message({"negative": state.get("negative")})

        return {
            "stage1": stage1_results,
            "labels": labels,
            "label_to_model": label_to_model,
            "metadata": _state_metadata_with_skills(context, state),
        }

    async def response_review_node(state: CouncilState) -> Dict[str, Any]:
        if not context.interactive_enabled:
            return {}
        if not context.interactive_queue or not context.session_id:
            return {"error": "Interactive session not available"}
        if context.is_cancelled():
            return _cancel_state()

        review_prompt = get_response_review_prompt(
            state.get("stage1", []),
            state.get("label_to_model", {}),
        )

        try:
            response = context.collected_responses.get("response_review")
            if response is None:
                response = await _request_interactive_response(review_prompt)
        except FlowCancelled:
            return _cancel_state()
        except asyncio.TimeoutError:
            return {"error": "Interactive session timed out waiting for response"}
        except RuntimeError:
            return {"error": "Interactive session not available"}

        context.collected_responses["response_review"] = response
        await _persist_collected_responses(state["user_query"])
        context.refresh_interactive_config()

        metadata_update: Dict[str, Any] = {}
        if context.interactive_config:
            metadata_update["excluded_models"] = (
                context.interactive_config.excluded_models
            )
        if metadata_update:
            await context.update_message({"metadata": metadata_update})
            return {
                "metadata": {
                    **(state.get("metadata") or {}),
                    **metadata_update,
                }
            }
        return {}

    async def negative_node(state: CouncilState) -> Dict[str, Any]:
        if context.is_cancelled():
            return _cancel_state()
        await context.set_status("negative")
        context.trace.begin_stage("negative", label="Negative Mode")

        gap_concerns = None
        response_review = context.collected_responses.get("response_review")
        if response_review and not response_review.skipped:
            gap_concerns = response_review.responses.get("gap_concerns")

        resolved_negative_prompt = (
            state.get("negative_prompt")
            or gap_concerns
            or await generate_negative_prompt(state["user_query"], trace=context.trace)
        )
        await context.persist_resume({"negative_prompt": resolved_negative_prompt})

        if context.is_cancelled():
            context.trace.end_stage("negative", cancelled=True)
            return _cancel_state()

        negative_context = {
            "enabled": True,
            "prompt": resolved_negative_prompt,
            "evaluations": [],
        }

        await context.emit_event(
            {
                "type": "negative_start",
                "negative_prompt": resolved_negative_prompt,
            }
        )

        stage1_results, labels, label_to_model = _active_stage1_inputs(state)
        if not stage1_results:
            error_message = "No successful responses remain after exclusions."
            context.trace.end_stage("negative", error=error_message)
            return {"error": error_message}
        stage_models = [
            str(result["model"]) for result in stage1_results if result.get("model")
        ]
        await context.init_stage_progress("negative", stage_models)

        evaluations = await stage_negative_evaluations(
            state["user_query"],
            resolved_negative_prompt,
            stage1_results,
            labels,
            trace=context.trace,
            progress=context.progress_callback("negative"),
        )
        negative_context["evaluations"] = evaluations
        context.trace.end_stage("negative", evaluation_count=len(evaluations))

        if context.is_cancelled():
            return _cancel_state()

        aggregate_gap_scores = calculate_aggregate_gap_scores(
            evaluations,
            label_to_model,
        )

        await context.update_message(
            {
                "negative": negative_context,
                "metadata": {
                    "aggregate_gap_scores": aggregate_gap_scores,
                    "label_to_model": label_to_model,
                },
            }
        )
        await context.emit_event(
            {
                "type": "negative_complete",
                "data": evaluations,
                "negative_prompt": resolved_negative_prompt,
                "aggregate_gap_scores": aggregate_gap_scores,
            }
        )

        merged_metadata = {
            **(state.get("metadata") or {}),
            "aggregate_gap_scores": aggregate_gap_scores,
            "label_to_model": label_to_model,
        }

        return {
            "negative": negative_context,
            "aggregate_gap_scores": aggregate_gap_scores,
            "labels": labels,
            "label_to_model": label_to_model,
            "metadata": merged_metadata,
        }

    async def stage2_node(state: CouncilState) -> Dict[str, Any]:
        if context.is_cancelled():
            return _cancel_state()
        await context.set_status("stage2")
        context.trace.begin_stage("stage2", label="Stage 2")
        await context.emit_event({"type": "stage2_start"})
        await context.init_stage_progress("stage2", COUNCIL_MODELS)

        prompt_modifier = _stage2_prompt_modifier(context)
        if state.get("responses_for_synthesis"):
            responses_to_rank = state.get("responses_for_synthesis", [])
            labels, label_to_model = build_label_mapping(responses_to_rank)
        else:
            responses_to_rank, labels, label_to_model = _active_stage1_inputs(state)
        if not responses_to_rank:
            error_message = "No successful responses remain after exclusions."
            context.trace.end_stage("stage2", error=error_message)
            return {"error": error_message}

        stage2_results, label_to_model = await stage2_collect_rankings(
            state["user_query"],
            responses_to_rank,
            labels,
            label_to_model,
            trace=context.trace,
            prompt_modifier=prompt_modifier,
            progress=context.progress_callback("stage2"),
        )
        context.trace.end_stage("stage2", ranking_count=len(stage2_results))

        if context.is_cancelled():
            return _cancel_state()

        if PAIRWISE_RANKING_ENABLED:
            aggregate_rankings, invalid_rankings = calculate_pairwise_rankings(
                stage2_results,
                label_to_model,
            )
            ranking_method = "pairwise"
        else:
            aggregate_rankings, invalid_rankings = calculate_aggregate_rankings(
                stage2_results,
                label_to_model,
            )
            ranking_method = "average_rank"

        metadata: Dict[str, Any] = {
            **(state.get("metadata") or {}),
            "label_to_model": label_to_model,
            "aggregate_rankings": aggregate_rankings,
            "invalid_rankings": invalid_rankings,
            "ranking_method": ranking_method,
            "negative_mode": state.get("negative"),
        }
        if state.get("aggregate_gap_scores") is not None:
            metadata["aggregate_gap_scores"] = state.get("aggregate_gap_scores")
        if not context.deliberation_enabled:
            metadata["deliberation"] = {"enabled": False, "results": None}

        await context.update_message(
            {
                "stage2": stage2_results,
                "metadata": metadata,
            }
        )
        await context.emit_event(
            {
                "type": "stage2_complete",
                "data": stage2_results,
                "metadata": metadata,
            }
        )

        return {
            "stage2": stage2_results,
            "labels": list(label_to_model.keys()),
            "label_to_model": label_to_model,
            "aggregate_rankings": aggregate_rankings,
            "invalid_rankings": invalid_rankings,
            "metadata": metadata,
        }

    async def deliberation_node(state: CouncilState) -> Dict[str, Any]:
        if context.is_cancelled():
            return _cancel_state()
        await context.set_status("deliberation")
        context.trace.begin_stage("deliberation", label="Deliberation")
        await context.emit_event({"type": "deliberation_start"})

        stage1_results, labels, label_to_model = _active_stage1_inputs(state)
        if not stage1_results:
            error_message = "No successful responses remain after exclusions."
            context.trace.end_stage("deliberation", error=error_message)
            return {"error": error_message}
        stage_models = [
            str(result["model"]) for result in stage1_results if result.get("model")
        ]
        await context.init_stage_progress("deliberation", stage_models)

        deliberation_results = await stage_deliberation(
            state["user_query"],
            stage1_results,
            labels,
            label_to_model,
            state.get("stage2", []),
            state.get("aggregate_rankings", []),
            state.get("negative") if state.get("negative", {}).get("enabled") else None,
            state.get("aggregate_gap_scores"),
            trace=context.trace,
            progress=context.progress_callback("deliberation"),
        )
        context.trace.end_stage(
            "deliberation", revision_count=len(deliberation_results)
        )

        if context.is_cancelled():
            return _cancel_state()

        responses_for_synthesis = [
            {"model": result["model"], "response": result["revised"]}
            for result in deliberation_results
        ]

        metadata_update = {
            "deliberation": {
                "enabled": True,
                "results": deliberation_results,
            }
        }
        await context.update_message({"metadata": metadata_update})
        await context.emit_event(
            {
                "type": "deliberation_complete",
                "data": deliberation_results,
            }
        )

        merged_metadata = {
            **(state.get("metadata") or {}),
            **metadata_update,
        }

        return {
            "responses_for_synthesis": responses_for_synthesis,
            "labels": labels,
            "label_to_model": label_to_model,
            "metadata": merged_metadata,
        }

    async def synthesis_prefs_node(state: CouncilState) -> Dict[str, Any]:
        if not context.interactive_enabled:
            return {}
        if not context.interactive_queue or not context.session_id:
            return {"error": "Interactive session not available"}
        if context.is_cancelled():
            return _cancel_state()

        synth_prompt = get_synthesis_prefs_prompt()

        try:
            response = context.collected_responses.get("synthesis_prefs")
            if response is None:
                response = await _request_interactive_response(synth_prompt)
        except FlowCancelled:
            return _cancel_state()
        except asyncio.TimeoutError:
            return {"error": "Interactive session timed out waiting for response"}
        except RuntimeError:
            return {"error": "Interactive session not available"}

        context.collected_responses["synthesis_prefs"] = response
        await _persist_collected_responses(state["user_query"])
        context.refresh_interactive_config()
        return {}

    async def stage3_node(state: CouncilState) -> Dict[str, Any]:
        from datetime import datetime

        if context.is_cancelled():
            return _cancel_state()
        # Load-bearing, not a nicety: a resume that already has Stage 2 output never
        # re-enters Stage 1, so this is where those runs resolve their skills.
        await _ensure_skills_runtime(context, state)
        _state_metadata_with_skills(context, state)
        await context.set_status("stage3")
        context.trace.begin_stage("stage3", label="Stage 3")
        await context.emit_event({"type": "stage3_start"})
        await context.init_stage_progress("stage3", [CHAIRMAN_MODEL])

        using_deliberation_revisions = bool(
            ((state.get("metadata") or {}).get("deliberation") or {}).get("results")
        )
        responses_for_synthesis = state.get("responses_for_synthesis")
        if not responses_for_synthesis:
            deliberation = (state.get("metadata") or {}).get("deliberation") or {}
            if deliberation.get("results"):
                responses_for_synthesis = build_responses_for_synthesis(
                    state.get("stage1", []),
                    state.get("metadata", {}),
                )
            else:
                responses_for_synthesis, _, _ = _active_stage1_inputs(state)
        if not responses_for_synthesis:
            error_message = "No successful responses are available for final synthesis."
            context.trace.end_stage("stage3", error=error_message)
            return {"error": error_message}

        synthesis_mode = (
            context.interactive_config.synthesis_mode
            if context.interactive_config
            else None
        )
        effective_mode = _normalize_synthesis_mode(synthesis_mode)
        prompt_modifier = _stage3_prompt_modifier(context)
        try:
            stage3_result = await stage3_synthesize_final(
                user_query=state["user_query"],
                stage1_results=responses_for_synthesis,
                stage2_results=state.get("stage2", []),
                label_to_model=state.get("label_to_model", {}),
                negative_context=state.get("negative"),
                aggregate_gap_scores=state.get("aggregate_gap_scores"),
                aggregate_rankings=state.get("aggregate_rankings"),
                labels=state.get("labels"),
                trace=context.trace,
                prompt_modifier=prompt_modifier,
                synthesis_mode=effective_mode,
                using_deliberation_revisions=using_deliberation_revisions,
                progress=context.progress_callback("stage3"),
                skills_context=_skills_stage3_block(context),
            )
        except Exception as exc:
            context.trace.end_stage("stage3", error=str(exc))
            raise
        context.trace.end_stage("stage3", model=stage3_result.get("model"))

        if context.is_cancelled():
            return _cancel_state()

        stage3_metadata: Dict[str, Any] = {}
        if effective_mode:
            stage3_metadata["synthesis_mode"] = effective_mode
        merged_metadata = {
            **(state.get("metadata") or {}),
            **stage3_metadata,
        }

        # Create the new multi-version stage3 structure
        timestamp = datetime.utcnow().isoformat()
        version_entry = {
            "model": stage3_result.get("model"),
            "response": stage3_result.get("response"),
            "mode": effective_mode,
            "timestamp": timestamp,
            "finish_reason": stage3_result.get("finish_reason"),
            "usage": stage3_result.get("usage"),
        }

        stage3_with_versions = {
            "current": version_entry,
            "versions": [version_entry],
        }

        await context.update_message(
            {"stage3": stage3_with_versions, "metadata": stage3_metadata}
        )
        await context.emit_event(
            {"type": "stage3_complete", "data": stage3_with_versions}
        )

        return {"stage3": stage3_with_versions, "metadata": merged_metadata}

    def _route_from_start(state: CouncilState) -> str:
        if context.is_resume:
            if _has_stage3(state):
                return "end"
            if _has_deliberation_results(state):
                if (
                    context.interactive_enabled
                    and "synthesis_prefs" not in context.collected_responses
                ):
                    return "synthesis_prefs"
                return "stage3"
            if _has_stage2(state):
                if context.deliberation_enabled:
                    return "deliberation"
                if (
                    context.interactive_enabled
                    and "synthesis_prefs" not in context.collected_responses
                ):
                    return "synthesis_prefs"
                return "stage3"
            if _has_negative_results(state):
                return "stage2"
            if _has_stage1(state):
                if (
                    context.interactive_enabled
                    and "response_review" not in context.collected_responses
                ):
                    return "response_review"
                if context.negative_enabled:
                    return "negative"
                return "stage2"
            if context.is_first_message and not _title_already_generated():
                return "title"
            if (
                context.interactive_enabled
                and "pre_query_config" not in context.collected_responses
            ):
                return "pre_query"
            return "stage1"
        return "title"

    def _route_after_title(state: CouncilState) -> str:
        if context.interactive_enabled:
            return "pre_query"
        return "stage1"

    def _route_after_pre_query(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        return "stage1"

    def _route_after_stage1(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        if context.interactive_enabled:
            return "response_review"
        if context.negative_enabled:
            return "negative"
        return "stage2"

    def _route_after_response_review(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        if context.negative_enabled:
            return "negative"
        return "stage2"

    def _route_after_negative(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        return "stage2"

    def _route_after_deliberation(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        if context.interactive_enabled:
            return "synthesis_prefs"
        return "stage3"

    def _route_after_stage2(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        if context.deliberation_enabled and not _has_deliberation_results(state):
            return "deliberation"
        if context.interactive_enabled:
            return "synthesis_prefs"
        return "stage3"

    def _route_after_synthesis_prefs(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        return "stage3"

    def _route_after_stage3(state: CouncilState) -> str:
        if state.get("error"):
            return "end"
        return "end"

    builder.add_node("pre_query", pre_query_node)
    builder.add_node("stage1", stage1_node)
    builder.add_node("response_review", response_review_node)
    builder.add_node("negative", negative_node)
    builder.add_node("stage2", stage2_node)
    builder.add_node("deliberation", deliberation_node)
    builder.add_node("synthesis_prefs", synthesis_prefs_node)
    builder.add_node("stage3", stage3_node)
    builder.add_node("title", title_node)

    builder.add_conditional_edges(
        START,
        _route_from_start,
        {
            "title": "title",
            "pre_query": "pre_query",
            "stage1": "stage1",
            "response_review": "response_review",
            "negative": "negative",
            "deliberation": "deliberation",
            "stage2": "stage2",
            "synthesis_prefs": "synthesis_prefs",
            "stage3": "stage3",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "title",
        _route_after_title,
        {
            "pre_query": "pre_query",
            "stage1": "stage1",
        },
    )
    builder.add_conditional_edges(
        "pre_query",
        _route_after_pre_query,
        {
            "stage1": "stage1",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "stage1",
        _route_after_stage1,
        {
            "response_review": "response_review",
            "negative": "negative",
            "stage2": "stage2",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "response_review",
        _route_after_response_review,
        {
            "negative": "negative",
            "stage2": "stage2",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "negative",
        _route_after_negative,
        {
            "stage2": "stage2",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "deliberation",
        _route_after_deliberation,
        {
            "synthesis_prefs": "synthesis_prefs",
            "stage3": "stage3",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "stage2",
        _route_after_stage2,
        {
            "deliberation": "deliberation",
            "synthesis_prefs": "synthesis_prefs",
            "stage3": "stage3",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "synthesis_prefs",
        _route_after_synthesis_prefs,
        {
            "stage3": "stage3",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "stage3",
        _route_after_stage3,
        {
            "end": END,
        },
    )

    return builder.compile()


async def run_council_graph(
    user_query: str,
    negative_prompt: Optional[str],
    context: FlowContext,
    initial_state: Optional[CouncilState] = None,
) -> CouncilState:
    """Run the LangGraph council flow and return the final state."""

    if initial_state is None:
        initial_state = {
            "user_query": user_query,
            "negative_prompt": negative_prompt,
            "negative": {
                "enabled": False,
                "prompt": negative_prompt,
                "evaluations": [],
            },
            "metadata": {},
        }

    graph = build_council_graph(context)
    return cast(CouncilState, await graph.ainvoke(initial_state))
