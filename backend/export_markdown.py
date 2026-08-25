"""Markdown export helpers for conversation context."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from . import skills
from .config import SKILLS_CONTEXT_MAX_CHARS


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _markdown_code_fence(content: Optional[str]) -> str:
    text = content or ""
    matches = re.findall(r"`+", text)
    longest_match = max((len(match) for match in matches), default=0)
    return "`" * max(3, longest_match + 1)


def _response_label(index: int) -> str:
    if 0 <= index < 26:
        return f"Response {chr(ord('A') + index)}"
    return f"Response {index + 1}"


def _stage1_export_label(
    response: Dict[str, Any],
    index: int,
    label_by_model: Dict[str, str],
    used_labels: set[str],
) -> str:
    """Use active anonymous labels only when they still map to this model."""
    model = _normalize_text(response.get("model"))
    active_label = label_by_model.get(model)
    if active_label:
        used_labels.add(active_label)
        return active_label

    label = f"Stage 1 item {index + 1}"
    suffix = 2
    while label in used_labels:
        label = f"Stage 1 item {index + 1}.{suffix}"
        suffix += 1
    used_labels.add(label)
    return label


def _normalize_stage3(stage3: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if stage3 is None:
        return None
    if "current" in stage3 and "versions" in stage3:
        return stage3

    version = {
        "model": stage3.get("model"),
        "response": stage3.get("response"),
        "mode": stage3.get("mode", "best_of_breed"),
        "timestamp": stage3.get("timestamp"),
    }
    return {"current": version, "versions": [version]}


def _append_markdown_section(lines: List[str], title: str, content: Optional[str]) -> None:
    lines.append(f"### {title}")
    lines.append("")
    text = _normalize_text(content)
    lines.append(text if text else "_No content captured._")
    lines.append("")


def _append_fenced_markdown_section(
    lines: List[str],
    title: str,
    content: Optional[str],
    empty_state: str = "_No content captured._",
) -> None:
    lines.append(f"### {title}")
    lines.append("")
    text = _normalize_text(content)
    if not text:
        lines.append(empty_state)
        lines.append("")
        return

    fence = _markdown_code_fence(text)
    lines.append(fence)
    lines.append(text)
    lines.append(fence)
    lines.append("")


def _append_export_prompt_section(
    lines: List[str],
    prompt_refinement: Optional[Dict[str, Any]],
    fallback_user_query: Optional[str],
) -> None:
    refinement = prompt_refinement or {}

    original_prompt = _normalize_text(refinement.get("original_prompt"))
    refined_prompt = _normalize_text(refinement.get("refined_prompt"))
    decision = _normalize_text(refinement.get("decision"))
    clarifying_answers = refinement.get("clarifying_answers") or []

    used_prompt = original_prompt or _normalize_text(fallback_user_query)
    if decision == "use_refined":
        used_prompt = refined_prompt or used_prompt
    elif not used_prompt:
        used_prompt = refined_prompt

    lines.extend(["## Export Prompt", ""])
    text = _normalize_text(used_prompt)
    if text:
        fence = _markdown_code_fence(text)
        lines.append(fence)
        lines.append(text)
        lines.append(fence)
        lines.append("")
    else:
        lines.extend(["_Used prompt not available._", ""])

    valid_clarifications = [
        item
        for item in clarifying_answers
        if isinstance(item, dict)
        if _normalize_text(item.get("question")) or _normalize_text(item.get("answer"))
    ]
    if decision != "use_refined" or not valid_clarifications:
        return

    lines.extend(["### Clarifications Provided", ""])
    for index, item in enumerate(valid_clarifications, start=1):
        question = _normalize_text(item.get("question")) or f"Question {index}"
        answer = _normalize_text(item.get("answer"))
        lines.append(f"{index}. **{question}**")
        lines.append("")
        if answer:
            fence = _markdown_code_fence(answer)
            lines.append(f"   {fence}")
            for line in answer.splitlines():
                lines.append(f"   {line}" if line else "   ")
            lines.append(f"   {fence}")
        else:
            lines.append("   _No answer captured._")
        lines.append("")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_skill_id_list(values: Any) -> str:
    ids = [_normalize_text(value) for value in (values or [])]
    ids = [value for value in ids if value]
    if not ids:
        return "none"
    return ", ".join(f"`{value}`" for value in ids)


def _load_current_skills(skill_ids: List[str]) -> Dict[str, skills.Skill]:
    """Look up the skills as they exist on disk right now (never persisted text)."""
    if not skill_ids:
        return {}
    try:
        found, _missing = skills.load_skills(skill_ids)
    except Exception:
        return {}
    return {skill.id: skill for skill in found}


def _skills_export_caveats(
    resolved: List[Dict[str, Any]],
    current_by_id: Dict[str, skills.Skill],
) -> List[str]:
    """Italic notes for skills whose on-disk state drifted from the stored plan."""
    caveats: List[str] = []
    for entry in resolved:
        skill_id = _normalize_text(entry.get("id"))
        if not skill_id:
            continue
        stored_version = _as_int(entry.get("version"))
        current = current_by_id.get(skill_id)
        if current is None:
            caveats.append(
                f"_Skill `{skill_id}` (v{stored_version}) is no longer installed, so its "
                "instructions are missing from the block below._"
            )
            continue
        if current.version != stored_version:
            caveats.append(
                f"_Skill `{skill_id}` ran at v{stored_version} but is now v{current.version}; "
                "the block below is re-rendered from the current version._"
            )
    return caveats


def _render_skills_export_block(
    resolved: List[Dict[str, Any]],
    current_by_id: Dict[str, skills.Skill],
    max_chars: int,
) -> Optional[str]:
    """Re-render the ``<skills>`` block from the current manifests at export time."""
    ordered: List[skills.Skill] = []
    selected_sections: Dict[str, List[skills.SectionSelection]] = {}
    reasons: Dict[str, Dict[str, str]] = {}
    for entry in resolved:
        skill_id = _normalize_text(entry.get("id"))
        current = current_by_id.get(skill_id)
        if current is None:
            continue
        ordered.append(current)
        selections: List[skills.SectionSelection] = []
        skill_reasons: Dict[str, str] = {}
        for reference in entry.get("references") or []:
            if not isinstance(reference, dict):
                continue
            path = _normalize_text(reference.get("path"))
            if not path:
                continue
            raw_ids = reference.get("section_ids")
            section_ids = (
                tuple(_normalize_text(section_id) for section_id in raw_ids)
                if isinstance(raw_ids, list)
                else ()
            )
            selections.append(
                skills.SectionSelection(path=path, section_ids=section_ids)
            )
            reason = _normalize_text(reference.get("reason"))
            if reason:
                skill_reasons[path] = reason
        selected_sections[skill_id] = selections
        reasons[skill_id] = skill_reasons

    if not ordered:
        return None
    try:
        block, _report = skills.render_skills_context(
            ordered,
            selected_sections,
            max_chars,
            reasons,
        )
    except Exception:
        return None
    return block


def _append_skills_section(lines: List[str], skills_metadata: Dict[str, Any]) -> None:
    """Render the Agent Skills section for one assistant response."""
    resolved = [
        entry
        for entry in (skills_metadata.get("resolved") or [])
        if isinstance(entry, dict)
    ]
    selection = skills_metadata.get("reference_selection") or {}
    budget = skills_metadata.get("budget") or {}
    resolved_ids = [
        _normalize_text(entry.get("id")) for entry in resolved if entry.get("id")
    ]
    current_by_id = _load_current_skills([value for value in resolved_ids if value])

    lines.extend(["## Agent Skills", ""])
    lines.append(f"- Requested: {_format_skill_id_list(skills_metadata.get('requested'))}")
    missing = [value for value in (skills_metadata.get("missing") or []) if value]
    if missing:
        lines.append(f"- Unavailable at run time: {_format_skill_id_list(missing)}")
    skill_selection = skills_metadata.get("skill_selection") or {}
    mode = _normalize_text(skill_selection.get("mode"))
    if mode == "auto":
        model = _normalize_text(skill_selection.get("model")) or "n/a"
        lines.append(
            "- Skill selection: automatic - picked "
            f"{_as_int(skill_selection.get('selected_count'))} of "
            f"{_as_int(skill_selection.get('candidate_count'))} enabled skills "
            f"(model `{model}`)"
        )
    elif mode == "auto_resumed":
        lines.append("- Skill selection: automatic (replayed from the stored plan)")
    elif mode == "manual":
        lines.append("- Skill selection: manual (picked in the composer)")
    skill_fallback = _normalize_text(skill_selection.get("fallback"))
    if skill_fallback:
        lines.append(f"- Skill-selection fallback: {skill_fallback}")
    if skills_metadata.get("drift"):
        lines.append("- Skill version drift detected during this run: yes")
    lines.append("")

    for entry in resolved:
        skill_id = _normalize_text(entry.get("id")) or "unknown"
        version = _as_int(entry.get("version"))
        lines.append(f"### Skill: {skill_id} (v{version})")
        lines.append("")
        current = current_by_id.get(skill_id)
        description = _normalize_text(current.description) if current else ""
        lines.append(description if description else "_Description unavailable._")
        lines.append("")

        skill_md_chars = _as_int(entry.get("skill_md_chars"))
        skill_md_line = f"- SKILL.md: {skill_md_chars} chars"
        if entry.get("truncated"):
            skill_md_line += " (truncated to fit the budget)"
        lines.append(skill_md_line)

        bundled_unused = entry.get("bundled_unused") or {}
        bundled_parts = [
            f"scripts {_as_int(bundled_unused.get('scripts'))}",
            f"assets {_as_int(bundled_unused.get('assets'))}",
            f"evals {_as_int(bundled_unused.get('evals'))}",
        ]
        other_unused = _as_int(bundled_unused.get("other"))
        if other_unused:
            bundled_parts.append(f"other {other_unused}")
        lines.append(f"- Bundled files not injected: {', '.join(bundled_parts)}")
        lines.append("")

        references = [
            reference
            for reference in (entry.get("references") or [])
            if isinstance(reference, dict)
        ]
        lines.extend(["References inlined:", ""])
        if references:
            for reference in references:
                path = _normalize_text(reference.get("path")) or "unknown"
                chars = _as_int(reference.get("chars"))
                reason = _normalize_text(reference.get("reason"))
                section_ids = reference.get("section_ids")
                section_suffix = (
                    f", sections {', '.join(str(value) for value in section_ids)}"
                    if isinstance(section_ids, list) and section_ids
                    else ""
                )
                suffix = f" - {reason}" if reason else ""
                lines.append(f"- `{path}` ({chars} chars{section_suffix}){suffix}")
        else:
            lines.append("_None._")
        lines.append("")

        skipped = [
            reference
            for reference in (entry.get("references_skipped") or [])
            if isinstance(reference, dict)
        ]
        if skipped:
            lines.extend(["References skipped:", ""])
            for reference in skipped:
                path = _normalize_text(reference.get("path")) or "unknown"
                chars = _as_int(reference.get("chars"))
                section_id = _normalize_text(reference.get("section_id"))
                label = f"`{path}` §{section_id}" if section_id else f"`{path}`"
                lines.append(f"- {label} ({chars} chars)")
            lines.append("")

    lines.extend(["### Reference Selection", ""])
    router_enabled = "enabled" if selection.get("enabled") else "disabled"
    lines.append(f"- Router: {router_enabled}")
    selection_model = _normalize_text(selection.get("model"))
    lines.append(f"- Model: `{selection_model}`" if selection_model else "- Model: n/a")
    lines.append(
        f"- Candidates: {_as_int(selection.get('candidate_count'))}; "
        f"selected: {_as_int(selection.get('selected_count'))}"
    )
    fallback = _normalize_text(selection.get("fallback"))
    if fallback:
        lines.append(f"- Fallback: {fallback}")
    lines.append("")

    stage1_max = _as_int(budget.get("stage1_max"), SKILLS_CONTEXT_MAX_CHARS)
    lines.extend(["### Context Budget", ""])
    lines.append(f"- Stage 1: {_as_int(budget.get('stage1_chars'))} / {stage1_max} chars")
    lines.append(
        f"- Stage 3: {_as_int(budget.get('stage3_chars'))} / "
        f"{_as_int(budget.get('stage3_max'))} chars"
    )
    dropped = [
        _normalize_text(value)
        for value in (budget.get("stage3_dropped_references") or [])
    ]
    dropped = [value for value in dropped if value]
    if dropped:
        joined = ", ".join(f"`{value}`" for value in dropped)
        lines.append(f"- References dropped from synthesis: {joined}")
    lines.append("")

    if not resolved:
        lines.extend(["_No skill instructions were injected into this response._", ""])
        return

    for caveat in _skills_export_caveats(resolved, current_by_id):
        lines.extend([caveat, ""])

    block = _render_skills_export_block(
        resolved,
        current_by_id,
        stage1_max if stage1_max > 0 else SKILLS_CONTEXT_MAX_CHARS,
    )
    _append_fenced_markdown_section(
        lines,
        "Injected Skills Context",
        block,
        empty_state="_Skills context could not be re-rendered from the installed skills._",
    )


def build_export_filename(title: Optional[str], request_number: Optional[int]) -> str:
    """Build a filesystem-safe export filename."""
    base = (title or "conversation").strip().lower()
    slug = _SLUG_PATTERN.sub("-", base).strip("-")
    if not slug:
        slug = "conversation"
    slug = slug[:64]
    request_suffix = f"-request-{request_number}" if request_number is not None else ""
    return f"llm-council-{slug}{request_suffix}-context.md"


def build_markdown_export(
    conversation: Dict[str, Any],
    message: Dict[str, Any],
    user_query: Optional[str],
    message_index: int,
    request_number: Optional[int],
) -> str:
    """Build a markdown export containing all key context for one assistant response."""
    metadata = message.get("metadata") or {}
    label_to_model: Dict[str, str] = metadata.get("label_to_model") or {}
    stage1 = message.get("stage1") or []
    negative = message.get("negative")
    stage2 = message.get("stage2") or []
    stage3 = _normalize_stage3(message.get("stage3"))
    aggregate_rankings = metadata.get("aggregate_rankings") or []
    aggregate_gap_scores = metadata.get("aggregate_gap_scores") or []
    deliberation = metadata.get("deliberation") or {}
    prompt_refinement = metadata.get("prompt_refinement") or {}
    raw_skills_metadata = metadata.get("skills")
    skills_metadata: Optional[Dict[str, Any]] = (
        raw_skills_metadata if isinstance(raw_skills_metadata, dict) else None
    )
    excluded_models = set(metadata.get("excluded_models") or [])

    label_by_model: Dict[str, str] = {}
    for label, model in label_to_model.items():
        if model and model not in label_by_model:
            label_by_model[model] = label

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: List[str] = [
        "# LLM Council Context Export",
        "",
        f"- Conversation ID: `{conversation.get('id', '')}`",
        f"- Conversation Title: {conversation.get('title') or 'New Conversation'}",
        f"- Assistant Message Index: {message_index}",
        f"- Exported At: {exported_at}",
    ]
    if request_number is not None:
        lines.append(f"- Request Number: {request_number}")
    lines.append("")

    _append_export_prompt_section(lines, prompt_refinement, user_query)

    if skills_metadata is not None:
        _append_skills_section(lines, skills_metadata)

    if label_to_model:
        lines.extend(
            [
                "## Response Label Mapping",
                "",
                (
                    "These labels apply to responses included in peer ranking and synthesis. "
                    "Excluded or failed Stage 1 rows are listed separately below."
                ),
                "",
                "| Label | Model |",
                "| --- | --- |",
            ]
        )
        for label in sorted(label_to_model.keys()):
            model = (label_to_model[label] or "").replace("|", "\\|")
            lines.append(f"| {label} | `{model}` |")
        lines.append("")

    lines.extend(["## Stage 1: Individual Responses", ""])
    if stage1:
        used_stage1_labels: set[str] = set()
        for index, response in enumerate(stage1):
            model = response.get("model") or "unknown"
            label = _stage1_export_label(
                response,
                index,
                label_by_model,
                used_stage1_labels,
            )
            lines.append(f"### {label} ({model})")
            lines.append("")
            status_flags = []
            if response.get("failed"):
                status_flags.append("failed")
            if model in excluded_models:
                status_flags.append("excluded from review and synthesis")
            if status_flags:
                lines.append(f"_Status: {', '.join(status_flags)}._")
                lines.append("")
            lines.append((response.get("response") or "").strip() or "_No response content captured._")
            lines.append("")
    else:
        lines.extend(["_Stage 1 data is not available yet._", ""])

    if negative:
        lines.extend(["## Negative Mode", ""])
        lines.append(f"- Enabled: {'Yes' if negative.get('enabled') else 'No'}")
        lines.append("")
        negative_prompt = negative.get("prompt")
        if negative_prompt:
            lines.extend(["### Negative Prompt", "", negative_prompt.strip(), ""])

        evaluations = negative.get("evaluations") or []
        if evaluations:
            lines.extend(["### Evaluations", ""])
            for evaluation in evaluations:
                model = evaluation.get("model") or "unknown"
                lines.append(f"#### Evaluator: {model}")
                lines.append("")
                self_label = evaluation.get("self_label")
                if self_label:
                    lines.append(f"- Self Label: {self_label}")
                peer_labels = evaluation.get("peer_labels") or []
                if peer_labels:
                    lines.append(f"- Peer Labels: {', '.join(peer_labels)}")
                lines.append("")
                lines.append((evaluation.get("evaluation") or "").strip() or "_No evaluation text captured._")
                lines.append("")

        if aggregate_gap_scores:
            lines.extend(
                [
                    "### Aggregate Gap Scores",
                    "",
                    "| Rank | Label | Model | Avg Gap Score | Self Bias | Peer Evaluations |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for index, item in enumerate(aggregate_gap_scores, start=1):
                lines.append(
                    "| "
                    f"{index} | {item.get('label', '')} | `{item.get('model', 'unknown')}` | "
                    f"{item.get('avg_gap_score', 'n/a')} | {item.get('self_bias', 'n/a')} | "
                    f"{item.get('peer_evaluation_count', 'n/a')} |"
                )
            lines.append("")

    lines.extend(["## Stage 2: Peer Rankings", ""])
    if stage2:
        for ranking in stage2:
            model = ranking.get("model") or "unknown"
            lines.append(f"### Ranking by {model}")
            lines.append("")
            validation_status = ranking.get("validation_status")
            validation_error = ranking.get("validation_error")
            if validation_status:
                lines.append(f"- Validation Status: {validation_status}")
            if validation_error:
                lines.append(f"- Validation Error: {validation_error}")
            if validation_status or validation_error:
                lines.append("")
            lines.append((ranking.get("ranking") or "").strip() or "_No ranking text captured._")
            lines.append("")

            parsed_ranking = ranking.get("parsed_ranking") or []
            if parsed_ranking:
                lines.append("Parsed Ranking:")
                for rank_index, label in enumerate(parsed_ranking, start=1):
                    model_for_label = label_to_model.get(label)
                    if model_for_label:
                        lines.append(f"{rank_index}. {label} ({model_for_label})")
                    else:
                        lines.append(f"{rank_index}. {label}")
                lines.append("")
    else:
        lines.extend(["_Stage 2 data is not available yet._", ""])

    if aggregate_rankings:
        lines.extend(["## Aggregate Rankings", ""])
        headers = [
            "| Rank | Model | Avg Rank | Votes |",
            "| --- | --- | --- | --- |",
        ]
        if aggregate_rankings[0].get("win_percentage") is not None:
            headers = [
                "| Rank | Model | Win % | Pairwise Wins | Votes |",
                "| --- | --- | --- | --- | --- |",
            ]
        lines.extend(headers)
        for index, item in enumerate(aggregate_rankings, start=1):
            model = item.get("model", "unknown")
            votes = item.get("rankings_count", "n/a")
            if item.get("win_percentage") is not None:
                win_pct = item.get("win_percentage", 0) * 100
                pairwise_wins = item.get("pairwise_wins", "n/a")
                lines.append(f"| {index} | `{model}` | {win_pct:.1f}% | {pairwise_wins} | {votes} |")
            else:
                average_rank = item.get("average_rank", "n/a")
                lines.append(f"| {index} | `{model}` | {average_rank} | {votes} |")
        lines.append("")

    if deliberation.get("enabled"):
        lines.extend(["## Deliberation Round", ""])
        deliberation_results = deliberation.get("results") or []
        if deliberation_results:
            for index, revision in enumerate(deliberation_results):
                label = revision.get("label") or _response_label(index)
                model = revision.get("model") or "unknown"
                rank_before = revision.get("ranking_before")
                title = f"{label} ({model})"
                if rank_before is not None:
                    title += f" - Rank Before: {rank_before}"
                lines.append(f"### {title}")
                lines.append("")
                original = (revision.get("original") or "").strip()
                revised = (revision.get("revised") or "").strip()
                if original and revised and original != revised:
                    _append_markdown_section(lines, "Original", original)
                    _append_markdown_section(lines, "Revised", revised)
                elif revised:
                    lines.append(revised)
                    lines.append("")
                else:
                    lines.append("_No deliberation revision captured._")
                    lines.append("")
        else:
            lines.extend(["_Deliberation was enabled, but no revisions were captured._", ""])

    lines.extend(["## Stage 3: Synthesis", ""])
    if stage3:
        current = stage3.get("current") or {}
        current_model = current.get("model") or "unknown"
        current_mode = current.get("mode") or "unknown"
        current_timestamp = current.get("timestamp") or "n/a"
        current_finish_reason = current.get("finish_reason")
        lines.append(f"- Current Mode: {current_mode}")
        lines.append(f"- Current Model: {current_model}")
        lines.append(f"- Current Timestamp: {current_timestamp}")
        if current_finish_reason:
            lines.append(f"- Current Finish Reason: {current_finish_reason}")
        lines.append("")
        lines.append((current.get("response") or "").strip() or "_Current synthesis is empty._")
        lines.append("")

        versions = stage3.get("versions") or []
        if len(versions) > 1:
            lines.extend(["### All Synthesis Versions", ""])
            for index, version in enumerate(versions, start=1):
                mode = version.get("mode") or "unknown"
                model = version.get("model") or "unknown"
                timestamp = version.get("timestamp") or "n/a"
                finish_reason = version.get("finish_reason")
                lines.append(f"#### Version {index}: {mode} ({model})")
                lines.append("")
                lines.append(f"_Timestamp: {timestamp}_")
                if finish_reason:
                    lines.append(f"_Finish Reason: {finish_reason}_")
                lines.append("")
                lines.append((version.get("response") or "").strip() or "_No response captured._")
                lines.append("")
    else:
        lines.extend(["_Stage 3 synthesis is not available yet._", ""])

    return "\n".join(lines).rstrip() + "\n"
