"""3-stage LLM Council orchestration with optional negative evaluations."""

from typing import List, Dict, Any, Tuple, Optional, Callable, Awaitable
import asyncio
import json
from .openrouter import query_model
from .config import (
    CHAIRMAN_MODEL,
    COUNCIL_MODELS,
    DELIBERATION_ENABLED,
    INTERACTIVE_MODE_ENABLED,
    NEGATIVE_MODE_ENABLED,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
    PROMPT_REFINEMENT_MODEL,
    SYNTHESIS_CONTEXT_MAX_CHARS,
    TITLE_MODEL,
)
from .trace import TraceCollector


ProgressCallback = Optional[
    Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
]

SYNTHESIS_MODES = {"consensus", "best_of_breed", "comprehensive", "weighted"}

SYNTHESIS_CONTEXT_POLICIES: Dict[str, Dict[str, int]] = {
    "consensus": {
        "full_top": 0,
        "full_chars": 7000,
        "compact_chars": 1800,
        "fallback_chars": 1200,
        "gap_summaries_per_response": 1,
        "ranking_note_chars": 0,
    },
    "best_of_breed": {
        "full_top": 2,
        "full_chars": 8000,
        "compact_chars": 3200,
        "fallback_chars": 1800,
        "gap_summaries_per_response": 2,
        "ranking_note_chars": 450,
    },
    "comprehensive": {
        "full_top": 999,
        "full_chars": 8500,
        "compact_chars": 8500,
        "fallback_chars": 4500,
        "gap_summaries_per_response": 3,
        "ranking_note_chars": 700,
    },
    "weighted": {
        "full_top": 2,
        "full_chars": 9000,
        "compact_chars": 1200,
        "fallback_chars": 900,
        "gap_summaries_per_response": 1,
        "ranking_note_chars": 250,
    },
}


def _normalize_synthesis_mode(mode: Optional[str]) -> str:
    """Return a known synthesis mode, defaulting conservatively."""
    candidate = (mode or "best_of_breed").strip()
    return candidate if candidate in SYNTHESIS_MODES else "best_of_breed"


def _trim_text(text: str, max_chars: int) -> str:
    """Trim long text while preserving both setup and conclusion."""
    cleaned = str(text or "").strip()
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    if max_chars < 80:
        return cleaned[:max_chars].rstrip()

    marker = "\n\n[...content omitted by synthesis context packer...]\n\n"
    available = max_chars - len(marker)
    head_chars = max(40, int(available * 0.68))
    tail_chars = max(20, available - head_chars)
    return f"{cleaned[:head_chars].rstrip()}{marker}{cleaned[-tail_chars:].lstrip()}"


def _paragraphs(text: str) -> List[Tuple[int, str]]:
    """Split text into paragraph-like chunks with stable original indices."""
    chunks = []
    for index, raw in enumerate(str(text or "").replace("\r\n", "\n").split("\n\n")):
        chunk = "\n".join(line.rstrip() for line in raw.splitlines()).strip()
        if chunk:
            chunks.append((index, chunk))
    return chunks


def _paragraph_score(paragraph: str, mode: str) -> int:
    """Heuristic score for excerpts when a response must be compacted."""
    lowered = paragraph.lower()
    score = 0
    if paragraph.lstrip().startswith(("-", "*", "#")):
        score += 3
    if any(
        token in lowered for token in ("recommend", "conclusion", "therefore", "should")
    ):
        score += 3
    if any(
        token in lowered
        for token in ("risk", "caveat", "trade-off", "edge case", "limitation")
    ):
        score += 3
    if any(
        token in lowered
        for token in ("because", "evidence", "example", "step", "option")
    ):
        score += 2
    if mode == "consensus" and any(
        token in lowered for token in ("agree", "common", "consensus", "shared", "same")
    ):
        score += 3
    if mode == "weighted" and any(
        token in lowered
        for token in ("recommend", "answer", "most important", "priority")
    ):
        score += 2
    if 120 <= len(paragraph) <= 900:
        score += 1
    return score


def _extract_response_excerpt(text: str, max_chars: int, mode: str) -> str:
    """Build a compact but evidence-rich excerpt from a response."""
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned

    paragraphs = _paragraphs(cleaned)
    if not paragraphs:
        return _trim_text(cleaned, max_chars)

    selected: Dict[int, str] = {}
    # Preserve the answer setup and conclusion; fill the middle with high-signal paragraphs.
    selected[paragraphs[0][0]] = paragraphs[0][1]
    if len(paragraphs) > 1:
        selected[paragraphs[-1][0]] = paragraphs[-1][1]

    scored = sorted(
        (
            (
                _paragraph_score(paragraph, mode),
                -abs(index - len(paragraphs) // 2),
                index,
                paragraph,
            )
            for index, paragraph in paragraphs[1:-1]
        ),
        reverse=True,
    )

    for _, _, index, paragraph in scored:
        candidate = "\n\n".join(
            part for _, part in sorted({**selected, index: paragraph}.items())
        )
        if len(candidate) <= max_chars:
            selected[index] = paragraph

    excerpt = "\n\n".join(part for _, part in sorted(selected.items()))
    return _trim_text(excerpt, max_chars)


def _label_by_model(label_to_model: Dict[str, str]) -> Dict[str, str]:
    return {model: label for label, model in label_to_model.items()}


def _rank_sort_key(entry: Dict[str, Any]) -> Tuple[int, str]:
    rank = entry.get("rank")
    return (int(rank) if isinstance(rank, int) else 10_000, entry.get("label") or "")


def _format_rank_detail(rank_item: Optional[Dict[str, Any]]) -> str:
    if not rank_item:
        return "not ranked"
    bits = []
    if rank_item.get("win_percentage") is not None:
        bits.append(
            f"{float(rank_item.get('win_percentage') or 0) * 100:.0f}% pairwise win rate"
        )
    if rank_item.get("average_rank") is not None:
        bits.append(f"avg rank {rank_item.get('average_rank')}")
    if rank_item.get("rankings_count") is not None:
        bits.append(f"{rank_item.get('rankings_count')} valid votes")
    return ", ".join(bits) or "ranked"


def _build_response_entries(
    stage1_results: List[Dict[str, Any]],
    labels: List[str],
    label_to_model: Dict[str, str],
    aggregate_rankings: Optional[List[Dict[str, Any]]],
    aggregate_gap_scores: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Attach labels, ranking, and gap metadata to synthesis responses."""
    model_to_label = _label_by_model(label_to_model)
    rank_by_model: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(aggregate_rankings or [], start=1):
        model = item.get("model")
        if model:
            rank_by_model[model] = {**item, "rank": index}

    gap_by_model = {
        item.get("model"): item
        for item in aggregate_gap_scores or []
        if item.get("model")
    }

    entries = []
    for index, result in enumerate(stage1_results):
        model = result.get("model", "unknown")
        label = model_to_label.get(model)
        if not label and index < len(labels):
            label = labels[index]
        entries.append(
            {
                "label": label or f"Response {chr(65 + index)}",
                "model": model,
                "response": result.get("response", ""),
                "rank_item": rank_by_model.get(model),
                "rank": rank_by_model.get(model, {}).get("rank"),
                "gap_item": gap_by_model.get(model),
            }
        )

    return entries


def _resolve_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    aggregate_rankings: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Use persisted aggregate rankings, falling back to local recomputation."""
    if aggregate_rankings:
        return aggregate_rankings
    if not stage2_results or not label_to_model:
        return []
    if PAIRWISE_RANKING_ENABLED:
        calculated, _ = calculate_pairwise_rankings(stage2_results, label_to_model)
    else:
        calculated, _ = calculate_aggregate_rankings(stage2_results, label_to_model)
    return calculated


def build_synthesis_response_context(
    stage1_results: List[Dict[str, Any]],
    labels: List[str],
    label_to_model: Dict[str, str],
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    aggregate_gap_scores: Optional[List[Dict[str, Any]]] = None,
    synthesis_mode: Optional[str] = None,
    max_chars: int = SYNTHESIS_CONTEXT_MAX_CHARS,
) -> Tuple[str, str]:
    """
    Build a mode-aware response section for Stage 3.

    The goal is to keep the chairman prompt focused on evidence needed for the
    selected synthesis mode instead of blindly forwarding every artifact at full
    length. This is deterministic by design; an LLM distillation step can be
    added later for overflow cases without changing Stage 3's public contract.
    """
    mode = _normalize_synthesis_mode(synthesis_mode)
    policy = SYNTHESIS_CONTEXT_POLICIES[mode]
    entries = _build_response_entries(
        stage1_results,
        labels,
        label_to_model,
        aggregate_rankings,
        aggregate_gap_scores,
    )

    has_rankings = any(entry.get("rank") is not None for entry in entries)

    if has_rankings and mode in {"weighted", "consensus"}:
        entries = sorted(entries, key=_rank_sort_key)
    elif mode == "best_of_breed":
        ranked = sorted(entries, key=_rank_sort_key)
        unranked = [entry for entry in entries if entry.get("rank") is None]
        entries = ranked if has_rankings and len(unranked) != len(entries) else entries

    full_labels = set()
    if mode == "comprehensive":
        full_labels = {entry["label"] for entry in entries}
    elif not has_rankings and len(entries) <= policy["full_top"]:
        full_labels = {entry["label"] for entry in entries}
    elif has_rankings:
        for entry in sorted(entries, key=_rank_sort_key)[: policy["full_top"]]:
            full_labels.add(entry["label"])
    else:
        full_labels = set()

    blocks = []
    full_included = []
    compact_included = []
    compacted_by_global_budget = False
    for entry in entries:
        is_full = entry["label"] in full_labels
        char_limit = policy["full_chars"] if is_full else policy["compact_chars"]
        text = entry["response"]
        included_text = (
            _trim_text(text, char_limit)
            if is_full
            else _extract_response_excerpt(text, char_limit, mode)
        )
        if is_full and len(str(text or "")) <= char_limit:
            inclusion = "full"
        elif is_full:
            inclusion = "full-priority, trimmed"
        else:
            inclusion = "compact excerpt"
        if inclusion == "full":
            full_included.append(entry["label"])
        else:
            compact_included.append(f"{entry['label']} ({inclusion})")

        rank_text = f"rank #{entry['rank']}: {_format_rank_detail(entry['rank_item'])}"
        if entry.get("rank") is None:
            rank_text = _format_rank_detail(entry.get("rank_item"))
        gap_item = entry.get("gap_item") or {}
        gap_text = ""
        if gap_item:
            gap_text = (
                f"; gap score {gap_item.get('avg_gap_score', 'n/a')}/10 "
                f"from {gap_item.get('peer_evaluation_count', 'n/a')} peer reviews"
            )

        blocks.append(
            f"{entry['label']} ({entry['model']}; {rank_text}{gap_text}; {inclusion}):\n"
            f"{included_text}"
        )

    response_text = "\n\n".join(blocks)
    if len(response_text) > max_chars:
        compacted_by_global_budget = True
        headers = [
            f"{entry['label']} ({entry['model']}; compressed for global context budget):\n"
            for entry in entries
        ]
        header_budget = sum(len(header) for header in headers)
        header_budget += max(0, len(entries) - 1) * 2
        available_for_text = max(0, max_chars - header_budget)
        per_entry_chars = max(
            120,
            min(policy["fallback_chars"], available_for_text // max(1, len(entries))),
        )
        response_text = "\n\n".join(
            f"{header}{_extract_response_excerpt(entry['response'], per_entry_chars, mode)}"
            for header, entry in zip(headers, entries)
        )
        if len(response_text) > max_chars:
            overage_per_entry = (
                (len(response_text) - max_chars) // max(1, len(entries))
            ) + 20
            per_entry_chars = max(80, per_entry_chars - overage_per_entry)
            response_text = "\n\n".join(
                f"{header}{_extract_response_excerpt(entry['response'], per_entry_chars, mode)}"
                for header, entry in zip(headers, entries)
            )
        if len(response_text) > max_chars:
            response_text = _trim_text(response_text, max_chars)

    note_parts = [
        f"Synthesis mode: {mode}.",
        "Responses are packed by mode before the chairman sees them.",
    ]
    if mode == "weighted":
        note_parts.append(
            "Higher-ranked responses receive full-priority space; "
            "lower-ranked responses are compacted."
        )
    elif mode == "consensus":
        note_parts.append(
            "All responses remain represented, but compacted responses emphasize "
            "shared conclusions and caveats."
        )
    elif mode == "best_of_breed":
        note_parts.append(
            "Top-ranked responses receive full-priority space while other "
            "responses keep high-signal excerpts."
        )
    elif mode == "comprehensive":
        note_parts.append(
            "All responses receive broad space, subject only to the global context budget."
        )
    if not has_rankings and mode != "comprehensive":
        note_parts.append(
            "No aggregate rankings were available, so responses use equal compact "
            "evidence instead of rank-prioritized space."
        )
    if compacted_by_global_budget:
        note_parts.append("The global context budget required extra compression.")
    if full_included:
        note_parts.append(f"Full responses included: {', '.join(full_included)}.")
    if compact_included:
        note_parts.append(f"Compacted artifacts: {', '.join(compact_included)}.")

    return response_text, " ".join(note_parts)


def build_synthesis_ranking_context(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    synthesis_mode: Optional[str] = None,
) -> str:
    """Build a compact ranking context for Stage 3."""
    mode = _normalize_synthesis_mode(synthesis_mode)
    policy = SYNTHESIS_CONTEXT_POLICIES[mode]
    aggregate_rankings = _resolve_aggregate_rankings(
        stage2_results,
        label_to_model,
        aggregate_rankings,
    )
    label_by_model = _label_by_model(label_to_model)

    lines = ["Aggregate ranking summary:"]
    if aggregate_rankings:
        for index, item in enumerate(aggregate_rankings, start=1):
            model = item.get("model", "unknown")
            label = label_by_model.get(model, "unlabeled")
            lines.append(
                f"{index}. {label} ({model}): {_format_rank_detail({**item, 'rank': index})}"
            )
    else:
        lines.append("- No valid aggregate ranking was available.")

    final_rank_lines = []
    rationale_lines = []
    expected_labels = list(label_to_model.keys())
    for result in stage2_results:
        parsed = result.get("parsed_ranking")
        if parsed is None:
            parsed = parse_ranking_from_text(result.get("ranking", ""), expected_labels)
        if parsed:
            final_rank_lines.append(
                f"- {result.get('model', 'unknown')}: {' > '.join(parsed)}"
            )
        elif result.get("validation_error"):
            final_rank_lines.append(
                f"- {result.get('model', 'unknown')}: invalid ({result.get('validation_error')})"
            )

        rationale_limit = policy["ranking_note_chars"]
        if rationale_limit > 0:
            ranking_text = str(result.get("ranking") or "").strip()
            rationale = ranking_text.split("FINAL RANKING:", 1)[0].strip()
            if rationale:
                rationale_lines.append(
                    f"- {result.get('model', 'unknown')}: {_trim_text(rationale, rationale_limit)}"
                )

    if final_rank_lines:
        lines.extend(["", "Reviewer final ranking lines:", *final_rank_lines])
    if rationale_lines:
        lines.extend(["", "Compact reviewer rationale excerpts:", *rationale_lines])

    return "\n".join(lines)


def build_label_mapping(
    stage1_results: List[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, str]]:
    """Create consistent labels (Response A, B, C) for stage outputs."""
    labels = [f"Response {chr(65 + i)}" for i in range(len(stage1_results))]
    label_to_model = {
        label: result["model"] for label, result in zip(labels, stage1_results)
    }
    return labels, label_to_model


def build_responses_for_synthesis(
    stage1_results: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Prefer deliberation revisions for synthesis when they are available.

    Stage 2 rankings are preserved by label/model, so keeping the original model
    keys lets the chairman align revised responses with existing rankings.
    """
    metadata = metadata or {}
    excluded_models = set(metadata.get("excluded_models") or [])
    deliberation = metadata.get("deliberation") or {}
    deliberation_results = deliberation.get("results") or []

    revised_responses: List[Dict[str, Any]] = []
    for result in deliberation_results:
        model = result.get("model")
        if not model or model in excluded_models:
            continue
        response_text = (
            result.get("revised")
            or result.get("response")
            or result.get("original")
            or ""
        )
        revised_responses.append(
            {
                "model": model,
                "response": response_text,
            }
        )

    if revised_responses:
        return revised_responses

    return [
        result
        for result in stage1_results
        if result.get("model") not in excluded_models and not result.get("failed")
    ]


def _ranking_position_counts(
    label: str,
    stage2_results: List[Dict[str, Any]],
    expected_label_count: int,
) -> Dict[int, int]:
    """Count how often a response label appeared at each rank."""
    counts: Dict[int, int] = {}
    for ranking in stage2_results:
        parsed_ranking = ranking.get("parsed_ranking")
        if parsed_ranking is None:
            parsed_ranking = parse_ranking_from_text(ranking.get("ranking", ""))
        if label not in parsed_ranking:
            continue
        position = parsed_ranking.index(label) + 1
        if 1 <= position <= expected_label_count:
            counts[position] = counts.get(position, 0) + 1
    return counts


def build_peer_review_feedback(
    model: str,
    label: str,
    stage2_results: Optional[List[Dict[str, Any]]] = None,
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    expected_label_count: int = 0,
) -> str:
    """Build concise peer-ranking feedback for one model's deliberation prompt."""
    if not stage2_results and not aggregate_rankings:
        return ""

    lines = ["Peer-ranking feedback:"]
    matching_aggregate = None
    aggregate_rank = None
    for index, item in enumerate(aggregate_rankings or [], start=1):
        if item.get("model") == model:
            matching_aggregate = item
            aggregate_rank = index
            break

    if matching_aggregate:
        vote_count = matching_aggregate.get("rankings_count", "n/a")
        if matching_aggregate.get("win_percentage") is not None:
            win_pct = float(matching_aggregate.get("win_percentage") or 0) * 100
            lines.append(
                f"- Aggregate peer rank: #{aggregate_rank} of "
                f"{len(aggregate_rankings or [])} ({win_pct:.0f}% pairwise win rate, "
                f"{vote_count} votes)."
            )
        else:
            lines.append(
                f"- Aggregate peer rank: #{aggregate_rank} of "
                f"{len(aggregate_rankings or [])} "
                f"(average rank {matching_aggregate.get('average_rank', 'n/a')}, "
                f"{vote_count} votes)."
            )

    if label and stage2_results and expected_label_count:
        counts = _ranking_position_counts(label, stage2_results, expected_label_count)
        if counts:
            distribution = ", ".join(
                f"rank {rank}: {count}" for rank, count in sorted(counts.items())
            )
            lines.append(f"- Position distribution for your response: {distribution}.")

    return "\n".join(lines)


async def stage1_collect_responses(
    user_query: str,
    trace: Optional[TraceCollector] = None,
    system_prompt: Optional[str] = None,
    progress: ProgressCallback = None,
) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.

    Args:
        user_query: The user's question
        system_prompt: Optional system prompt to steer model responses

    Returns:
        List of dicts with 'model' and 'response' keys
    """
    if system_prompt:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]
    else:
        messages = [{"role": "user", "content": user_query}]

    async def query_with_progress(model: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        if progress:
            await progress(model, "running", None)
        response = await query_model(
            model,
            messages,
            trace=trace,
            trace_stage="stage1",
            trace_context={"kind": "responses"},
        )
        if progress:
            await progress(
                model, "complete" if response is not None else "failed", None
            )
        return model, response

    tasks = [query_with_progress(model) for model in COUNCIL_MODELS]
    responses_list = await asyncio.gather(*tasks)
    responses = {model: response for model, response in responses_list}

    # Format results - include all models, marking failures
    stage1_results = []
    for model, response in responses.items():
        if response is not None:
            stage1_results.append(
                {
                    "model": model,
                    "response": response.get("content", ""),
                    "failed": False,
                }
            )
        else:
            # Include failed models with indicator for transparency
            stage1_results.append(
                {
                    "model": model,
                    "response": "[Model failed to respond]",
                    "failed": True,
                }
            )

    # Check minimum success threshold (at least 50% must succeed)
    success_count = len([r for r in stage1_results if not r.get("failed")])
    if success_count < len(stage1_results) / 2:
        print(
            f"Warning: Only {success_count}/{len(stage1_results)} models responded successfully"
        )

    return stage1_results


async def generate_negative_prompt(
    user_query: str,
    trace: Optional[TraceCollector] = None,
) -> str:
    """
    Generate a negative evaluation prompt that highlights gaps/risks.

    Args:
        user_query: The original user request

    Returns:
        A concise negative prompt string
    """
    prompt = f"""Create a concise "negative" evaluation prompt for the following user request.
The negative prompt should instruct a reviewer to find gaps, weaknesses, risks, or missing edge cases in proposed answers,
and to score how well the answer anticipates those issues.

User request: {user_query}

Return just the negative evaluation prompt in 2-3 sentences."""

    messages = [{"role": "user", "content": prompt}]

    response = await query_model(
        CHAIRMAN_MODEL,
        messages,
        timeout=60.0,
        trace=trace,
        trace_stage="negative_prompt",
        trace_context={"kind": "prompt_generation"},
    )
    if response is None or not response.get("content"):
        fallback = (
            f"Identify blind spots, missing safety checks, weak assumptions, and likely omissions when responding to: {user_query}. "
            "Score whether the answer anticipates these issues (0=ignores them, 10=handles them thoroughly)."
        )
        return fallback

    return response.get("content", "").strip()


def _format_prompt_refinement_context(
    clarifying_answers: Optional[List[Dict[str, str]]],
    additional_context: Optional[str],
    previous_refined_prompt: Optional[str],
) -> str:
    parts = []

    if clarifying_answers:
        lines = []
        for item in clarifying_answers:
            question = (item.get("question") or "").strip()
            answer = (item.get("answer") or "").strip()
            if question and answer:
                lines.append(f"- Q: {question}\n  A: {answer}")
            elif answer:
                lines.append(f"- {answer}")
        if lines:
            parts.append("Clarifying answers from the user:\n" + "\n".join(lines))

    if additional_context:
        extra = additional_context.strip()
        if extra:
            parts.append("Additional insights from the user:\n" + extra)

    if previous_refined_prompt:
        draft = previous_refined_prompt.strip()
        if draft:
            parts.append("Current refined prompt draft:\n" + draft)

    return "\n\n".join(parts)


def _coerce_prompt_refinement_payload(
    payload: Any,
    fallback_prompt: str,
) -> Dict[str, Any]:
    intent = ""
    clarifying_questions: List[str] = []
    refined_prompt = fallback_prompt.strip()

    if isinstance(payload, dict):
        intent = str(payload.get("intent") or "").strip()
        raw_questions = payload.get("clarifying_questions")
        if isinstance(raw_questions, list):
            clarifying_questions = [
                str(item).strip() for item in raw_questions if str(item).strip()
            ]
        elif isinstance(raw_questions, str):
            clarifying_questions = [
                line.strip(" -") for line in raw_questions.splitlines() if line.strip()
            ]

        refined_candidate = payload.get("refined_prompt")
        if refined_candidate:
            refined_prompt = str(refined_candidate).strip()

    refined_prompt = _normalize_refined_prompt(refined_prompt, fallback_prompt)

    return {
        "intent": intent,
        "clarifying_questions": clarifying_questions[:3],
        "refined_prompt": refined_prompt,
    }


def _normalize_refined_prompt(refined_prompt: str, fallback_prompt: str) -> str:
    """Guard against truncated or empty refinements by falling back to the original prompt."""
    fallback = (fallback_prompt or "").strip()
    candidate = (refined_prompt or "").strip()

    if not candidate:
        return fallback

    trimmed = candidate.rstrip()
    if trimmed.endswith("...") or trimmed.endswith("…"):
        return fallback

    if len(fallback) >= 2000:
        min_length = int(len(fallback) * 0.6)
        if len(candidate) < min_length:
            return fallback

    return candidate


async def generate_prompt_refinement(
    user_query: str,
    clarifying_answers: Optional[List[Dict[str, str]]] = None,
    additional_context: Optional[str] = None,
    previous_refined_prompt: Optional[str] = None,
    trace: Optional[TraceCollector] = None,
    iteration: int = 1,
) -> Dict[str, Any]:
    """
    Generate a prompt refinement suggestion using the chairman model.

    Returns a dict with keys: intent, clarifying_questions, refined_prompt.
    """
    context_block = _format_prompt_refinement_context(
        clarifying_answers=clarifying_answers,
        additional_context=additional_context,
        previous_refined_prompt=previous_refined_prompt,
    )

    refinement_prompt = (
        "You are the Chairman of an LLM Council. Your job is to refine a user's prompt before it "
        "is answered.\n\n"
        "Return JSON with keys:\n"
        '- "intent": a concise 1-2 sentence summary of what the user wants\n'
        '- "clarifying_questions": an array of 0-3 essential questions needed to answer accurately\n'
        '- "refined_prompt": a rewritten version of the prompt in clear English\n\n'
        "Rules:\n"
        "- Ask 1-3 clarifying questions when any critical details are missing (ingredients, constraints,"
        " preferences, equipment, desired output format). Only return an empty array when the request"
        " is fully specified.\n"
        "- Always return a refined_prompt even if minimal; fix grammar and clarity.\n"
        "- Do not summarize or omit details. Preserve all constraints, examples, lists, and structure.\n"
        "- Keep the refined_prompt roughly the same length as the original; never use ellipses to skip content.\n"
        "- If the original prompt is already clear, return it unchanged.\n"
        "- Do not repeat questions that were already answered.\n"
        "- Use only information from the user inputs; do not invent requirements.\n\n"
        f"Original prompt:\n{user_query.strip()}\n"
    )

    if context_block:
        refinement_prompt += f"\n\n{context_block}\n"

    if iteration > 1:
        refinement_prompt += "\nFollow-up round: ask up to 3 new clarifying questions only if essential.\n"

    refinement_prompt += "\nReturn only JSON."

    messages = [{"role": "user", "content": refinement_prompt}]

    response = await query_model(
        PROMPT_REFINEMENT_MODEL,
        messages,
        timeout=60.0,
        trace=trace,
        trace_stage="prompt_refinement",
        trace_context={"kind": "prompt_refinement", "iteration": iteration},
    )

    if response is None or not response.get("content"):
        return {
            "intent": "",
            "clarifying_questions": [],
            "refined_prompt": user_query.strip(),
        }

    payload = extract_json_object(response.get("content", ""))
    return _coerce_prompt_refinement_payload(payload, user_query)


def extract_json_object(text: str) -> Optional[dict]:
    """
    Extract JSON object from text using multiple strategies.

    Handles cases where text contains braces before the actual JSON,
    markdown code blocks, and multiple JSON objects.

    Args:
        text: Text potentially containing JSON

    Returns:
        Parsed JSON dict or None if extraction fails
    """
    import re

    # Strategy 1: Look for ```json code blocks (common in LLM responses)
    json_block = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if json_block:
        try:
            return json.loads(json_block.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find complete JSON objects using bracket matching
    # This handles cases like "Consider {this} approach... {"actual": "json"}"
    depth = 0
    start = -1
    candidates = []

    for i, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidates.append(text[start : i + 1])
                start = -1

    # Try candidates from last to first (JSON is usually at the end)
    for candidate in reversed(candidates):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def parse_negative_scores(
    text: str, peer_labels: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Parse scores and summaries from a model's negative evaluation text.

    Supports both legacy single-peer format and new multi-peer format.

    Args:
        text: The evaluation text from the model
        peer_labels: List of peer labels (e.g., ["Response A", "Response C"]) for multi-peer parsing

    Returns:
        Dict with 'scores' and 'summaries' keys.
        For multi-peer: scores["peers"] is a dict mapping label -> score
                        summaries["peers"] is a dict mapping label -> summary
    """
    import re

    # Initialize with multi-peer structure
    scores = {"self": None, "peers": {}}
    summaries = {"self": None, "peers": {}}

    try:
        # Use robust JSON extraction instead of fragile find/rfind
        data = extract_json_object(text)
        if data is not None and isinstance(data, dict):
            # Parse self score
            if "self_score" in data:
                scores["self"] = float(data["self_score"])
            summaries["self"] = data.get("self_summary")

            # Parse multi-peer scores (new format)
            if "peer_scores" in data and isinstance(data["peer_scores"], dict):
                for label, score in data["peer_scores"].items():
                    try:
                        scores["peers"][label] = float(score)
                    except (ValueError, TypeError):
                        pass

            if "peer_summaries" in data and isinstance(data["peer_summaries"], dict):
                summaries["peers"] = data["peer_summaries"]

            # Legacy single-peer format fallback
            if not scores["peers"] and "peer_score" in data:
                scores["peer"] = float(data["peer_score"])
                summaries["peer"] = data.get("peer_summary")
    except Exception as e:
        print(f"Warning: Failed to parse negative scores: {e}")

    # Regex fallback for self score
    if scores["self"] is None:
        match = re.search(
            r"self[_\s-]*score[^0-9]*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE
        )
        if match:
            scores["self"] = float(match.group(1))

    # Regex fallback for peer scores when we have peer_labels
    if peer_labels and not scores["peers"]:
        for label in peer_labels:
            # Look for patterns like "Response A: 7" or "Response A score: 7"
            label_escaped = re.escape(label)
            pattern = rf"{label_escaped}[:\s]*(?:score[:\s]*)?([0-9]+(?:\.[0-9]+)?)"
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    scores["peers"][label] = float(match.group(1))
                except ValueError:
                    pass

    # Legacy single-peer regex fallback
    if "peer" not in scores and not scores["peers"]:
        match = re.search(
            r"peer[_\s-]*score[^0-9]*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE
        )
        if match:
            scores["peer"] = float(match.group(1))

    # Last resort: use context-aware score patterns (only for legacy single-peer mode)
    # Avoid the overly broad "any number 0-10" pattern which causes false matches
    if scores["self"] is None and not scores["peers"] and "peer" not in scores:
        # Try patterns that indicate a score with context
        score_patterns = [
            r"(?:score|rating|grade)[:\s]*(\d+(?:\.\d+)?)",  # "score: 7"
            r"(\d+(?:\.\d+)?)\s*(?:out of|\/)\s*10",  # "7 out of 10" or "7/10"
            r"(?:gave|assign|rate)[^\d]*(\d+(?:\.\d+)?)",  # "gave it a 7"
        ]
        for pattern in score_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    score = float(match.group(1))
                    if 0 <= score <= 10:  # Validate score range
                        if scores["self"] is None:
                            scores["self"] = score
                        break
                except ValueError:
                    pass

    return {"scores": scores, "summaries": summaries}


async def stage_negative_evaluations(
    user_query: str,
    negative_prompt: str,
    stage1_results: List[Dict[str, Any]],
    labels: List[str],
    trace: Optional[TraceCollector] = None,
    progress: ProgressCallback = None,
) -> List[Dict[str, Any]]:
    """
    Negative evaluation stage with full peer coverage.

    Each model scores its own response AND all peer responses, providing
    complete coverage for robust gap-finding aggregation.

    Args:
        user_query: The original user question
        negative_prompt: Prompt describing what to critique
        stage1_results: Responses from Stage 1
        labels: Label list aligned with stage1_results

    Returns:
        List of evaluation dicts with raw output and parsed scores for all peers
    """
    if not stage1_results:
        return []

    async def evaluate_model(
        index: int,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Ask a model to critique its own response and ALL peer responses."""
        self_label = labels[index]
        if progress:
            await progress(result["model"], "running", {"label": self_label})

        # Build peer responses section
        peer_labels = []
        peer_responses_text = []
        for i, (label, peer_result) in enumerate(zip(labels, stage1_results)):
            if i != index:
                peer_labels.append(label)
                peer_responses_text.append(f"- {label}:\n{peer_result['response']}")

        peers_section = "\n\n".join(peer_responses_text)

        # Build JSON example with actual peer labels
        peer_scores_example = ", ".join([f'"{lbl}": <0-10>' for lbl in peer_labels])
        peer_summaries_example = ", ".join(
            [f'"{lbl}": "gaps summary"' for lbl in peer_labels]
        )

        prompt = f"""You will act as a rigorous reviewer using the NEGATIVE PROMPT below.

Original user request: {user_query}

NEGATIVE PROMPT (use this to judge the responses):
{negative_prompt}

YOUR RESPONSE (you are {self_label} / {result["model"]}):
{result["response"]}

PEER RESPONSES TO EVALUATE:
{peers_section}

Tasks:
1) For EACH response (yours and all peers), identify the biggest gaps, risks, or likely omissions based on the negative prompt.
2) Assign a score from 0 to 10 for each response where 10 = addresses concerns thoroughly and 0 = fails badly.
3) Return a compact JSON block with scores and one-sentence summaries for all responses.

IMPORTANT: You must score ALL responses listed above.

Format:
```json
{{
  "self_score": <0-10 number>,
  "self_summary": "one sentence about main weaknesses in your response",
  "peer_scores": {{{peer_scores_example}}},
  "peer_summaries": {{{peer_summaries_example}}}
}}
```
After the JSON you may add brief notes if helpful."""

        messages = [{"role": "user", "content": prompt}]
        response = await query_model(
            result["model"],
            messages,
            trace=trace,
            trace_stage="negative_eval",
            trace_context={
                "kind": "gap_review_full",
                "self_label": self_label,
                "peer_labels": peer_labels,
            },
        )
        if progress:
            await progress(
                result["model"], "complete" if response is not None else "failed", None
            )
        evaluation_text = response.get("content", "") if response else ""
        parsed = parse_negative_scores(evaluation_text, peer_labels=peer_labels)

        return {
            "model": result["model"],
            "self_label": self_label,
            "peer_labels": peer_labels,
            "evaluation": evaluation_text,
            "scores": parsed["scores"],
            "summaries": parsed["summaries"],
        }

    tasks = [evaluate_model(idx, result) for idx, result in enumerate(stage1_results)]
    evaluations = await asyncio.gather(*tasks)
    return evaluations


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    labels: Optional[List[str]] = None,
    label_to_model: Optional[Dict[str, str]] = None,
    trace: Optional[TraceCollector] = None,
    prompt_modifier: Optional[str] = None,
    progress: ProgressCallback = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1
        prompt_modifier: Optional extra guidance appended to the ranking prompt

    Returns:
        Tuple of (rankings list, label_to_model mapping)
    """
    if labels is None or label_to_model is None:
        labels, label_to_model = build_label_mapping(stage1_results)

    # Build the ranking prompt
    responses_text = "\n\n".join(
        [
            f"{label}:\n{result['response']}"
            for label, result in zip(labels, stage1_results)
        ]
    )

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

"""

    if prompt_modifier:
        ranking_prompt += f"\nAdditional guidance:\n{prompt_modifier}\n"
        ranking_prompt += (
            "\nIMPORTANT: The FINAL RANKING section must still be last and must follow the exact"
            " format above, even when considering the additional guidance.\n"
        )

    ranking_prompt += "\nNow provide your evaluation and ranking:"

    messages = [{"role": "user", "content": ranking_prompt}]

    async def query_with_progress(model: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        if progress:
            await progress(model, "running", None)
        response = await query_model(
            model,
            messages,
            trace=trace,
            trace_stage="stage2",
            trace_context={"kind": "rankings"},
        )
        if progress:
            await progress(
                model, "complete" if response is not None else "failed", None
            )
        return model, response

    tasks = [query_with_progress(model) for model in COUNCIL_MODELS]
    responses_list = await asyncio.gather(*tasks)
    responses = {model: response for model, response in responses_list}

    # Format results with validation status
    stage2_results = []
    expected_labels = list(label_to_model.keys()) if label_to_model else labels

    for model, response in responses.items():
        if response is not None:
            full_text = response.get("content", "")
            parsed = parse_ranking_from_text(full_text, expected_labels)

            # Validate the parsed ranking
            validation_error = None
            if not parsed:
                validation_error = "no labels parsed"
            else:
                seen = []
                for lbl in parsed:
                    if lbl not in expected_labels:
                        validation_error = f"unknown label {lbl}"
                        break
                    if lbl in seen:
                        validation_error = f"duplicate label {lbl}"
                        break
                    seen.append(lbl)
                if not validation_error and len(seen) != len(expected_labels):
                    missing = set(expected_labels) - set(seen)
                    validation_error = f"missing labels: {', '.join(sorted(missing))}"

            stage2_results.append(
                {
                    "model": model,
                    "ranking": full_text,
                    "parsed_ranking": parsed,
                    "validation_status": "invalid" if validation_error else "valid",
                    "validation_error": validation_error,
                }
            )

    return stage2_results, label_to_model


async def stage_deliberation(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    labels: List[str],
    label_to_model: Dict[str, str],
    stage2_results: Optional[List[Dict[str, Any]]] = None,
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    negative_context: Optional[Dict[str, Any]] = None,
    aggregate_gap_scores: Optional[List[Dict[str, Any]]] = None,
    trace: Optional[TraceCollector] = None,
    progress: ProgressCallback = None,
) -> List[Dict[str, Any]]:
    """
    Deliberation round: Models see feedback and can revise their responses.

    Each model receives:
    1. Its original response
    2. Peer-ranking feedback (if available)
    3. Gap-finding feedback (if available)

    Then submits a revised response addressing identified weaknesses.

    Args:
        user_query: The original user question
        stage1_results: Original responses from Stage 1
        labels: Label list aligned with stage1_results
        label_to_model: Mapping from labels to model names
        stage2_results: Optional peer-ranking results
        aggregate_rankings: Optional aggregate ranking summary
        negative_context: Optional gap-finding data
        aggregate_gap_scores: Optional aggregated gap scores

    Returns:
        List of revised response dicts with 'model', 'original', and 'revised' keys
    """
    if not stage1_results:
        return []

    # Build model-to-label mapping (reverse of label_to_model)
    model_to_label = {v: k for k, v in label_to_model.items()}

    async def deliberate_model(result: Dict[str, Any]) -> Dict[str, Any]:
        """Ask a model to revise its response based on peer feedback."""
        model = result["model"]
        original_response = result["response"]
        label = model_to_label.get(model, "Unknown")
        if progress:
            await progress(model, "running", {"label": label})

        peer_feedback = build_peer_review_feedback(
            model,
            label,
            stage2_results=stage2_results,
            aggregate_rankings=aggregate_rankings,
            expected_label_count=len(labels),
        )

        # Build gap-finding feedback if available (anonymized - no label references)
        gap_text = ""
        if aggregate_gap_scores:
            gap_text = "\nGap-Finding Analysis:\n"
            for gap_data in aggregate_gap_scores:
                if gap_data["model"] == model:
                    gap_text += "\nYour response:\n"
                    gap_text += f"- Average gap score: {gap_data['avg_gap_score']}/10\n"
                    if gap_data.get("gap_summaries"):
                        gap_text += "- Key gaps identified:\n"
                        for summary_item in gap_data["gap_summaries"][:3]:
                            gap_text += f"  - {summary_item['summary']}\n"
                    break
        feedback_text = "\n\n".join(
            part for part in [peer_feedback, gap_text.strip()] if part
        )
        if not feedback_text:
            feedback_text = (
                "No specific peer-ranking or gap-finding feedback was available."
            )

        # Build the deliberation prompt (anonymized - no label references to preserve blind review)
        prompt = f"""You previously answered this question:

ORIGINAL QUESTION: {user_query}

YOUR ORIGINAL RESPONSE:
{original_response}

FEEDBACK:
{feedback_text}

Based on this feedback from your peers, you now have the opportunity to revise your response.

INSTRUCTIONS:
1. Address any specific gaps, weaknesses, or ranking signals identified in the feedback
2. Maintain the strengths of your original response while improving weak areas
3. Be concise - don't add unnecessary padding just to seem more complete

Provide your REVISED response to the original question. If you believe your original response was already optimal, you may keep it largely the same with minor refinements:"""

        messages = [{"role": "user", "content": prompt}]

        response = await query_model(
            model,
            messages,
            trace=trace,
            trace_stage="deliberation",
            trace_context={
                "kind": "revision",
                "label": label,
            },
        )
        if progress:
            await progress(
                model, "complete" if response is not None else "failed", None
            )

        revised_text = response.get("content", "") if response else original_response

        return {
            "model": model,
            "label": label,
            "original": original_response,
            "revised": revised_text,
        }

    # Run deliberation for all models in parallel
    tasks = [deliberate_model(result) for result in stage1_results]
    revised_results = await asyncio.gather(*tasks)

    return revised_results


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    negative_context: Optional[Dict[str, Any]] = None,
    aggregate_gap_scores: Optional[List[Dict[str, Any]]] = None,
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    labels: Optional[List[str]] = None,
    trace: Optional[TraceCollector] = None,
    prompt_modifier: Optional[str] = None,
    synthesis_mode: str = "best_of_breed",
    using_deliberation_revisions: bool = False,
    progress: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2
        label_to_model: Mapping from Response labels to model identifiers
        negative_context: Optional negative evaluation data
        aggregate_gap_scores: Optional aggregated gap-finding scores per response
        aggregate_rankings: Optional aggregated peer-ranking summary
        labels: Label list aligned with stage1_results
        prompt_modifier: Optional extra guidance appended to the synthesis prompt
        synthesis_mode: How to pack and guide synthesis context
        using_deliberation_revisions: Whether responses are revised answers
            from the deliberation round

    Returns:
        Dict with 'model' and 'response' keys

    Raises:
        RuntimeError: If the chairman model fails or returns an empty synthesis
    """
    # Rebuild the labels used in Stage 2 so the chairman can align feedback
    if not stage1_results:
        raise RuntimeError("No successful responses are available for final synthesis.")

    labels = labels or [f"Response {chr(65 + i)}" for i in range(len(stage1_results))]

    # Map labels to models, falling back to stage1 ordering if mapping is missing
    label_mapping = [
        f"{label}: {label_to_model.get(label, result['model'])}"
        for label, result in zip(labels, stage1_results)
    ]

    mode = _normalize_synthesis_mode(synthesis_mode)
    policy = SYNTHESIS_CONTEXT_POLICIES[mode]
    aggregate_rankings = _resolve_aggregate_rankings(
        stage2_results,
        label_to_model,
        aggregate_rankings,
    )

    stage1_text, packing_note = build_synthesis_response_context(
        stage1_results,
        labels,
        label_to_model,
        aggregate_rankings=aggregate_rankings,
        aggregate_gap_scores=aggregate_gap_scores,
        synthesis_mode=mode,
    )
    stage2_text = build_synthesis_ranking_context(
        stage2_results,
        label_to_model,
        aggregate_rankings=aggregate_rankings,
        synthesis_mode=mode,
    )

    response_heading = (
        "RESPONSES FOR SYNTHESIS - Deliberation Revisions"
        if using_deliberation_revisions
        else "STAGE 1 - Individual Responses"
    )
    negative_heading = (
        "NEGATIVE MODE - Pre-Deliberation Gap/Risk Checklist"
        if using_deliberation_revisions
        else "NEGATIVE MODE - Gap/Risk Analysis"
    )
    negative_instruction = (
        "These evaluations were produced before deliberation. Use them as a "
        "verification checklist against the revised responses above: confirm "
        "whether the revisions fixed the flagged gaps, and do not repeat a "
        "criticism as unresolved merely because it appears below."
        if using_deliberation_revisions
        else "Use these evaluations as active gap/risk analysis for the responses."
    )
    negative_bullet = (
        "- When provided, use negative evaluations and scores as a "
        "pre-deliberation checklist: verify whether revised responses fixed the "
        "flagged risks or blind spots before treating them as unresolved"
        if using_deliberation_revisions
        else "- When provided, the negative evaluations and scores that highlight "
        "missing risks or blind spots"
    )

    negative_text = ""
    if negative_context and negative_context.get("enabled"):
        # Build aggregate gap scores summary (structured, quantitative)
        if aggregate_gap_scores:
            gap_rankings = []
            for i, gap_data in enumerate(aggregate_gap_scores, 1):
                bias_note = ""
                if gap_data.get("self_bias") is not None:
                    bias = gap_data["self_bias"]
                    if bias > 1:
                        bias_note = f" [self-inflation: +{bias}]"
                    elif bias < -1:
                        bias_note = f" [self-critical: {bias}]"

                gap_rankings.append(
                    f"{i}. {gap_data['label']} ({gap_data['model']}): "
                    f"avg={gap_data['avg_gap_score']}/10 "
                    f"(std={gap_data['std_gap_score']}, n={gap_data['peer_evaluation_count']})"
                    f"{bias_note}"
                )

            # Collect common gaps across responses
            common_gaps = []
            summary_limit = policy["gap_summaries_per_response"]
            for gap_data in aggregate_gap_scores:
                for summary_item in gap_data.get("gap_summaries", [])[:summary_limit]:
                    common_gaps.append(
                        f"- {gap_data['label']}: {summary_item['summary']}"
                    )

            negative_text = f"""
{negative_heading}:

{negative_instruction}

Negative prompt used:
{negative_context.get("prompt", "")}

AGGREGATE GAP SCORES (higher = addresses concerns better, sorted best to worst):
{chr(10).join(gap_rankings)}

KEY GAPS IDENTIFIED BY PEER REVIEWERS:
{chr(10).join(common_gaps[:10]) if common_gaps else "No specific gaps extracted."}
"""
        else:
            # Fallback to old format if no aggregate scores
            evaluations = negative_context.get("evaluations", [])
            eval_texts = []
            for evaluation in evaluations:
                scores = evaluation.get("scores", {})
                summaries = evaluation.get("summaries", {})
                # Handle new multi-peer format
                peer_scores = scores.get("peers", {})
                peer_summaries = summaries.get("peers", {})
                if peer_scores:
                    peer_text = ", ".join(
                        [f"{lbl}: {s}" for lbl, s in peer_scores.items()]
                    )
                else:
                    peer_text = f"{scores.get('peer', 'n/a')}"

                eval_texts.append(
                    f"Evaluator: {evaluation.get('model')}\n"
                    f"Self: {evaluation.get('self_label')} ({scores.get('self', 'n/a')}) - {summaries.get('self') or 'no summary'}\n"
                    f"Peers: {peer_text}"
                )

            negative_text = f"""
{negative_heading}:

{negative_instruction}

Negative prompt used:
{negative_context.get("prompt", "")}

Peer/self evaluations (scores 0-10, higher = addresses concerns better):
{chr(10).join(eval_texts)}
"""

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.{" Negative mode is enabled with gap-finding analysis from full peer coverage." if negative_context and negative_context.get("enabled") else ""}

Original Question: {user_query}

Response labels (used in the rankings):
{chr(10).join(label_mapping)}

SYNTHESIS CONTEXT PACKING:
{packing_note}
The response evidence below may be excerpted according to the selected mode. Do not infer
that an omitted detail was absent from the original model output; use omitted lower-priority
artifacts cautiously and rely on the included evidence.

{response_heading}:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

{negative_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement
{negative_bullet}

Write a complete answer to the original question, not a brief recap of the council
process. Preserve substantive depth from the strongest responses and deliberation
revisions. Compress duplicated points, but do not discard important reasoning,
caveats, trade-offs, examples, or implementation details merely for brevity. If
the user's request explicitly asks for a concise answer, respect that; otherwise
make the synthesis thorough enough to stand alone.

"""

    if prompt_modifier:
        chairman_prompt += f"\nAdditional guidance:\n{prompt_modifier}\n"

    chairman_prompt += "\nProvide a clear, well-reasoned final answer that represents the council's collective wisdom:"

    messages = [{"role": "user", "content": chairman_prompt}]

    # Query the chairman model
    if progress:
        await progress(CHAIRMAN_MODEL, "running", None)

    response = await query_model(
        CHAIRMAN_MODEL,
        messages,
        trace=trace,
        trace_stage="stage3",
        trace_context={"kind": "synthesis"},
    )

    if progress:
        await progress(
            CHAIRMAN_MODEL, "complete" if response is not None else "failed", None
        )

    if response is None:
        raise RuntimeError("Chairman model failed to generate final synthesis.")

    content = response.get("content", "")
    if not str(content).strip():
        raise RuntimeError("Chairman model returned an empty final synthesis.")
    finish_reason = str(response.get("finish_reason") or "").strip().lower()
    if finish_reason in {"length", "max_tokens", "token_limit"}:
        raise RuntimeError(
            "Chairman model hit the output token limit before completing final synthesis."
        )

    return {
        "model": CHAIRMAN_MODEL,
        "response": content,
        "finish_reason": response.get("finish_reason"),
        "usage": response.get("usage"),
    }


def parse_ranking_from_text(
    ranking_text: str, expected_labels: Optional[List[str]] = None
) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model
        expected_labels: Optional list of expected labels for precise matching

    Returns:
        List of response labels in ranked order
    """
    import re

    if not isinstance(ranking_text, str) or not ranking_text:
        return []

    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]

            # If we have expected labels, build a precise pattern
            if expected_labels:
                escaped = [re.escape(lbl) for lbl in expected_labels]
                pattern = r"\d+\.\s*(" + "|".join(escaped) + r")"
                matches = re.findall(pattern, ranking_section)
                if matches:
                    return matches

            # Try to extract numbered list format with flexible labels
            # Supports: Response A, Response AA, Response A1, Response 1, etc.
            numbered_matches = re.findall(
                r"\d+\.\s*(Response\s+[A-Za-z0-9]+)", ranking_section
            )
            if numbered_matches:
                return [m.strip() for m in numbered_matches]

            # Fallback: Extract all "Response X" patterns in order (flexible)
            matches = re.findall(r"Response\s+[A-Za-z0-9]+", ranking_section)
            return matches

    # Ultimate fallback: try to find any "Response X" patterns in order
    matches = re.findall(r"Response\s+[A-Za-z0-9]+", ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]], label_to_model: Dict[str, str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        Tuple of (aggregate rankings list, invalid rankings list)
    """
    from collections import defaultdict

    expected_labels = list(label_to_model.keys())
    invalid_rankings = []
    # Track positions for each model
    model_positions = defaultdict(list)

    def normalize_ranking(parsed_ranking: List[str]) -> Tuple[List[str], str]:
        """Ensure the ranking has exactly one entry per expected label."""
        if not parsed_ranking:
            return [], "no labels parsed"

        seen = []
        for label in parsed_ranking:
            if label not in label_to_model:
                return [], f"unknown label {label}"
            if label in seen:
                return [], f"duplicate label {label}"
            seen.append(label)

        if len(seen) != len(expected_labels):
            missing = set(expected_labels) - set(seen)
            return [], f"missing labels: {', '.join(sorted(missing))}"

        return seen, ""

    for ranking in stage2_results:
        parsed_ranking = ranking.get("parsed_ranking")
        if parsed_ranking is None:
            parsed_ranking = parse_ranking_from_text(ranking.get("ranking", ""))

        normalized, error = normalize_ranking(parsed_ranking)
        if error:
            invalid_rankings.append({"model": ranking["model"], "reason": error})
            continue

        for position, label in enumerate(normalized, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    # Calculate average position for each model
    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append(
                {
                    "model": model,
                    "average_rank": round(avg_rank, 2),
                    "rankings_count": len(positions),
                }
            )

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x["average_rank"])

    return aggregate, invalid_rankings


def calculate_pairwise_rankings(
    stage2_results: List[Dict[str, Any]], label_to_model: Dict[str, str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    Calculate aggregate rankings using the Schulze method (Condorcet voting).

    Converts ordinal rankings to pairwise preferences, then uses the Schulze
    algorithm to determine the strongest path between each pair of candidates.
    This approach is resistant to outlier rankings and theoretically principled.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        Tuple of (aggregate rankings list, invalid rankings list)
    """

    expected_labels = list(label_to_model.keys())
    num_candidates = len(expected_labels)
    invalid_rankings = []

    if num_candidates == 0:
        return [], []

    # Create label-to-index mapping for matrix operations
    label_to_idx = {label: i for i, label in enumerate(expected_labels)}

    def normalize_ranking(parsed_ranking: List[str]) -> Tuple[List[str], str]:
        """Ensure the ranking has exactly one entry per expected label."""
        if not parsed_ranking:
            return [], "no labels parsed"

        seen = []
        for label in parsed_ranking:
            if label not in label_to_model:
                return [], f"unknown label {label}"
            if label in seen:
                return [], f"duplicate label {label}"
            seen.append(label)

        if len(seen) != len(expected_labels):
            missing = set(expected_labels) - set(seen)
            return [], f"missing labels: {', '.join(sorted(missing))}"

        return seen, ""

    # Phase 1: Build pairwise preference matrix
    # d[i][j] = number of voters who prefer candidate i over candidate j
    d = [[0] * num_candidates for _ in range(num_candidates)]
    valid_ranking_count = 0

    for ranking in stage2_results:
        parsed_ranking = ranking.get("parsed_ranking")
        if parsed_ranking is None:
            parsed_ranking = parse_ranking_from_text(ranking.get("ranking", ""))
        normalized, error = normalize_ranking(parsed_ranking)

        if error:
            invalid_rankings.append({"model": ranking["model"], "reason": error})
            continue

        valid_ranking_count += 1

        # For each pair, the higher-ranked candidate gets a vote
        for pos_i, label_i in enumerate(normalized):
            idx_i = label_to_idx[label_i]
            for pos_j, label_j in enumerate(normalized):
                if pos_i < pos_j:  # label_i is ranked higher (better)
                    idx_j = label_to_idx[label_j]
                    d[idx_i][idx_j] += 1

    if valid_ranking_count == 0:
        return [], invalid_rankings

    # Phase 2: Calculate strongest paths using Floyd-Warshall variant
    # p[i][j] = strength of strongest path from i to j
    p = [[0] * num_candidates for _ in range(num_candidates)]

    # Initialize with direct preferences
    for i in range(num_candidates):
        for j in range(num_candidates):
            if i != j:
                if d[i][j] > d[j][i]:
                    p[i][j] = d[i][j]

    # Floyd-Warshall: find strongest paths
    for k in range(num_candidates):
        for i in range(num_candidates):
            if i != k:
                for j in range(num_candidates):
                    if j != i and j != k:
                        # Strength of path through k is min of the two links
                        path_through_k = min(p[i][k], p[k][j])
                        p[i][j] = max(p[i][j], path_through_k)

    # Phase 3: Count pairwise victories for each candidate
    wins = [0] * num_candidates
    for i in range(num_candidates):
        for j in range(num_candidates):
            if i != j and p[i][j] > p[j][i]:
                wins[i] += 1

    # Build results
    aggregate = []
    for label in expected_labels:
        idx = label_to_idx[label]
        model = label_to_model[label]
        pairwise_wins = wins[idx]
        win_percentage = (
            pairwise_wins / (num_candidates - 1) if num_candidates > 1 else 1.0
        )

        aggregate.append(
            {
                "model": model,
                "pairwise_wins": pairwise_wins,
                "win_percentage": round(win_percentage, 3),
                "rankings_count": valid_ranking_count,
            }
        )

    # Sort by pairwise wins (descending), then model name for deterministic ties
    aggregate.sort(key=lambda x: (-x["pairwise_wins"], x["model"]))

    # Add schulze_rank after sorting
    for rank, item in enumerate(aggregate, start=1):
        item["schulze_rank"] = rank
        # Also include average_rank for backward compatibility with frontend
        item["average_rank"] = rank

    return aggregate, invalid_rankings


def calculate_aggregate_gap_scores(
    evaluations: List[Dict[str, Any]], label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate gap-finding scores across all peer evaluations.

    With full peer coverage, each response receives (N-1) peer evaluations
    where N is the number of council models. This function aggregates those
    scores to produce a single gap score per response.

    Args:
        evaluations: List of evaluation dicts from stage_negative_evaluations()
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        List of aggregate gap score dicts, sorted by average score (descending).
        Higher scores indicate better gap coverage.
    """
    from collections import defaultdict
    import statistics

    # Collect all peer scores for each response label
    scores_by_label: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for evaluation in evaluations:
        evaluator_model = evaluation.get("model", "unknown")
        scores = evaluation.get("scores", {})
        summaries = evaluation.get("summaries", {})

        # Collect peer scores (more reliable than self-scores)
        peer_scores = scores.get("peers", {})
        peer_summaries = summaries.get("peers", {})

        for label, score in peer_scores.items():
            if score is not None and label in label_to_model:
                scores_by_label[label].append(
                    {
                        "score": score,
                        "evaluator": evaluator_model,
                        "summary": peer_summaries.get(label),
                    }
                )

        # Also track self-scores separately for bias detection
        self_label = evaluation.get("self_label")
        self_score = scores.get("self")
        if self_label and self_score is not None:
            # Mark as self-evaluation for later analysis
            scores_by_label[self_label].append(
                {
                    "score": self_score,
                    "evaluator": evaluator_model,
                    "summary": summaries.get("self"),
                    "is_self": True,
                }
            )

    # Calculate aggregates for each response
    aggregates = []
    for label, score_entries in scores_by_label.items():
        if label not in label_to_model:
            continue

        # Separate peer and self scores
        peer_entries = [e for e in score_entries if not e.get("is_self")]
        self_entries = [e for e in score_entries if e.get("is_self")]

        peer_scores = [e["score"] for e in peer_entries]
        self_score = self_entries[0]["score"] if self_entries else None

        if not peer_scores:
            continue

        avg_peer_score = statistics.mean(peer_scores)
        std_peer_score = statistics.stdev(peer_scores) if len(peer_scores) > 1 else 0.0

        # Calculate self-bias: positive means self-inflation
        self_bias = None
        if self_score is not None:
            self_bias = round(self_score - avg_peer_score, 2)

        # Collect unique gap summaries (deduplicated)
        gap_summaries = []
        seen_summaries = set()
        for entry in peer_entries:
            summary = entry.get("summary")
            if summary and summary not in seen_summaries:
                gap_summaries.append(
                    {
                        "summary": summary,
                        "evaluator": entry["evaluator"],
                    }
                )
                seen_summaries.add(summary)

        aggregates.append(
            {
                "label": label,
                "model": label_to_model[label],
                "avg_gap_score": round(avg_peer_score, 2),
                "std_gap_score": round(std_peer_score, 2),
                "peer_evaluation_count": len(peer_scores),
                "self_score": self_score,
                "self_bias": self_bias,
                "gap_summaries": gap_summaries,
            }
        )

    # Sort by average gap score (descending - higher is better)
    aggregates.sort(key=lambda x: -x["avg_gap_score"])

    return aggregates


async def generate_conversation_title(
    user_query: str,
    trace: Optional[TraceCollector] = None,
) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Args:
        user_query: The first user message

    Returns:
        A short title (3-5 words)
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]

    response = await query_model(
        TITLE_MODEL,
        messages,
        timeout=30.0,
        trace=trace,
        trace_stage="title",
        trace_context={"kind": "title_generation"},
    )

    if response is None:
        # Fallback to a generic title
        return "New Conversation"

    title = response.get("content", "New Conversation").strip()

    # Clean up the title - remove quotes, limit length
    title = title.strip("\"'")

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    return title


async def run_full_council(
    user_query: str,
    negative_prompt: Optional[str] = None,
    trace: Optional[TraceCollector] = None,
) -> Tuple[List, Optional[Dict[str, Any]], List, Dict, Dict]:
    """
    Run the complete 3-stage council process.

    Args:
        user_query: The user's question
        negative_prompt: Optional negative evaluation prompt

    Returns:
        Tuple of (stage1_results, negative_context, stage2_results, stage3_result, metadata)
    """
    # Stage 1: Collect individual responses
    if trace:
        trace.begin_stage("stage1", label="Stage 1")
    stage1_results = await stage1_collect_responses(user_query, trace=trace)
    if trace:
        trace.end_stage("stage1", response_count=len(stage1_results))

    # If no models responded successfully, return error
    active_stage1_results = [
        result for result in stage1_results if not result.get("failed")
    ]
    if not active_stage1_results:
        return (
            [],
            None,
            [],
            {
                "model": "error",
                "response": "All models failed to respond. Please try again.",
            },
            {},
        )

    labels, label_to_model = build_label_mapping(active_stage1_results)

    # Optional negative evaluation stage
    negative_context: Dict[str, Any] = {
        "enabled": False,
        "prompt": negative_prompt,
        "evaluations": [],
    }
    if NEGATIVE_MODE_ENABLED:
        if trace:
            trace.begin_stage("negative", label="Negative Mode")
        resolved_negative_prompt = negative_prompt or await generate_negative_prompt(
            user_query,
            trace=trace,
        )
        negative_evaluations = await stage_negative_evaluations(
            user_query,
            resolved_negative_prompt,
            active_stage1_results,
            labels,
            trace=trace,
        )
        if trace:
            trace.end_stage("negative", evaluation_count=len(negative_evaluations))
        negative_context = {
            "enabled": True,
            "prompt": resolved_negative_prompt,
            "evaluations": negative_evaluations,
        }

    # Stage 2: Collect rankings
    if trace:
        trace.begin_stage("stage2", label="Stage 2")
    stage2_results, label_to_model = await stage2_collect_rankings(
        user_query,
        active_stage1_results,
        labels,
        label_to_model,
        trace=trace,
    )
    if trace:
        trace.end_stage("stage2", ranking_count=len(stage2_results))

    # Calculate aggregate rankings
    if PAIRWISE_RANKING_ENABLED:
        aggregate_rankings, invalid_rankings = calculate_pairwise_rankings(
            stage2_results, label_to_model
        )
    else:
        aggregate_rankings, invalid_rankings = calculate_aggregate_rankings(
            stage2_results, label_to_model
        )

    # Calculate aggregate gap scores if negative mode is enabled
    aggregate_gap_scores = None
    if negative_context.get("enabled"):
        aggregate_gap_scores = calculate_aggregate_gap_scores(
            negative_context.get("evaluations", []), label_to_model
        )

    # Optional deliberation round
    deliberation_results = None
    responses_for_synthesis = active_stage1_results  # Default to original responses
    if DELIBERATION_ENABLED:
        if trace:
            trace.begin_stage("deliberation", label="Deliberation")
        deliberation_results = await stage_deliberation(
            user_query,
            active_stage1_results,
            labels,
            label_to_model,
            stage2_results,
            aggregate_rankings,
            negative_context if negative_context.get("enabled") else None,
            aggregate_gap_scores,
            trace=trace,
        )
        if trace:
            trace.end_stage("deliberation", revision_count=len(deliberation_results))

        # Use revised responses for synthesis
        responses_for_synthesis = [
            {"model": r["model"], "response": r["revised"]}
            for r in deliberation_results
        ]

    # Stage 3: Synthesize final answer
    if trace:
        trace.begin_stage("stage3", label="Stage 3")
    stage3_result = await stage3_synthesize_final(
        user_query=user_query,
        stage1_results=responses_for_synthesis,  # Use revised responses if deliberation happened
        stage2_results=stage2_results,
        label_to_model=label_to_model,
        negative_context=negative_context,
        aggregate_gap_scores=aggregate_gap_scores,
        aggregate_rankings=aggregate_rankings,
        labels=labels,
        trace=trace,
        synthesis_mode="best_of_breed",
        using_deliberation_revisions=bool(deliberation_results),
    )
    if trace:
        trace.end_stage("stage3", model=stage3_result.get("model"))

    # Prepare metadata
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings,
        "invalid_rankings": invalid_rankings,
        "negative_mode": negative_context,
        "aggregate_gap_scores": aggregate_gap_scores,
        "deliberation": {
            "enabled": DELIBERATION_ENABLED,
            "results": deliberation_results,
        }
        if deliberation_results
        else {"enabled": False, "results": None},
        "pairwise_ranking_enabled": PAIRWISE_RANKING_ENABLED,
        "feature_flags": {
            "negative_mode": NEGATIVE_MODE_ENABLED,
            "deliberation": DELIBERATION_ENABLED,
            "interactive_mode": INTERACTIVE_MODE_ENABLED,
            "prompt_refinement": PROMPT_REFINEMENT_ENABLED,
            "pairwise_ranking": PAIRWISE_RANKING_ENABLED,
        },
    }

    return stage1_results, negative_context, stage2_results, stage3_result, metadata
