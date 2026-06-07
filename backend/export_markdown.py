"""Markdown export helpers for conversation context."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional


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
