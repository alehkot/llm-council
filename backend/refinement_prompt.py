"""Pure shared refinement request text. No provider or configuration access."""

from typing import Dict, List, Optional


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


def build_refinement_messages(
    user_query: str,
    clarifying_answers: Optional[List[Dict[str, str]]] = None,
    additional_context: Optional[str] = None,
    previous_refined_prompt: Optional[str] = None,
    iteration: int = 1,
) -> List[Dict[str, str]]:
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

    return [{"role": "user", "content": refinement_prompt}]


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
