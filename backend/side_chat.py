"""Helpers for persisted one-model side chats launched from council outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from .config import COUNCIL_MODELS


SIDE_CHAT_SYSTEM_PROMPT = (
    "You are continuing a focused side conversation about one result from an LLM Council run. "
    "Use the provided council context as background, answer the user's follow-up directly, "
    "and mention brief uncertainty only when you need to infer beyond that context."
)


def format_model_name(model: Optional[str]) -> str:
    """Return a short display label for an OpenRouter model identifier."""
    if not model:
        return "unknown"
    return model.split("/", 1)[1] if "/" in model else model


def normalize_stage3(stage3: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize stage3 to the multi-version format used by the frontend."""
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
    return {
        "current": version,
        "versions": [version],
    }


def resolve_effective_prompt(
    original_prompt: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> str:
    """Reuse the same prompt-refinement decision logic used by the main council flow."""
    metadata = metadata or {}
    prompt_refinement = metadata.get("prompt_refinement") or {}
    refined_prompt = str(prompt_refinement.get("refined_prompt") or "").strip()
    decision = str(prompt_refinement.get("decision") or "").strip()
    original = str(
        prompt_refinement.get("original_prompt")
        or original_prompt
        or ""
    ).strip()

    if decision == "use_refined" and refined_prompt:
        return refined_prompt
    return original or refined_prompt


def _default_target_model(source_model: Optional[str]) -> str:
    """Default to the source model when available, otherwise fall back to the first council model."""
    if source_model and source_model in COUNCIL_MODELS:
        return source_model
    if COUNCIL_MODELS:
        return COUNCIL_MODELS[0]
    return source_model or ""


def _build_context_message(
    effective_prompt: str,
    source_label: str,
    source_note: str,
    source_content: str,
) -> str:
    prompt_block = effective_prompt.strip() or "Unavailable"
    source_block = source_content.strip() or "Unavailable"
    return (
        "You are continuing a side conversation spun out from an LLM Council run.\n\n"
        f"Effective prompt used for that run:\n{prompt_block}\n\n"
        f"Source artifact:\n{source_label}\n\n"
        f"Why this artifact matters:\n{source_note}\n\n"
        f"Artifact content:\n{source_block}\n\n"
        "The user will now ask follow-up questions about this artifact. Treat the artifact as "
        "prior work product and continue the discussion naturally."
    )


def resolve_side_chat_source(
    message: Dict[str, Any],
    original_prompt: Optional[str],
    source_type: str,
    source_model: Optional[str] = None,
    version_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve a launch source into a persisted side-chat context bundle."""
    metadata = message.get("metadata") or {}
    effective_prompt = resolve_effective_prompt(original_prompt, metadata)

    if source_type == "stage1":
        responses = message.get("stage1") or []
        match = next((item for item in responses if item.get("model") == source_model), None)
        if match is None:
            raise ValueError("Stage 1 source not found for the requested model")
        if match.get("failed"):
            raise ValueError("Cannot start a side chat from a failed Stage 1 response")

        resolved_model = str(match.get("model") or "").strip()
        label = f"Stage 1 response from {format_model_name(resolved_model)}"
        note = "This is the original individual answer produced before peer review or synthesis."
        content = str(match.get("response") or "").strip()
        source_key = f"stage1::{resolved_model}"

    elif source_type == "deliberation":
        deliberation = metadata.get("deliberation") or {}
        results = deliberation.get("results") or []
        match = next((item for item in results if item.get("model") == source_model), None)
        if match is None:
            raise ValueError("Deliberation source not found for the requested model")

        resolved_model = str(match.get("model") or "").strip()
        label = f"Deliberation revision from {format_model_name(resolved_model)}"
        note = (
            "This is the model's deliberation-round answer after it reviewed peer feedback and "
            "decided whether to revise its earlier response."
        )
        content = str(
            match.get("revised")
            or match.get("response")
            or match.get("original")
            or ""
        ).strip()
        source_key = f"deliberation::{resolved_model}"

    elif source_type == "stage3":
        stage3 = normalize_stage3(message.get("stage3"))
        if not stage3 or not stage3.get("versions"):
            raise ValueError("Stage 3 synthesis data is not available")
        versions = stage3.get("versions") or []
        if version_index is None or version_index < 0 or version_index >= len(versions):
            raise ValueError("Requested synthesis version is out of range")

        version = versions[version_index]
        resolved_model = str(version.get("model") or "").strip()
        mode = str(version.get("mode") or "best_of_breed").strip()
        timestamp = str(version.get("timestamp") or version_index).strip()
        label = f"Stage 3 synthesis ({mode})"
        note = (
            f"This is a synthesis result produced in {mode} mode by "
            f"{format_model_name(resolved_model) or 'the chairman'}."
        )
        content = str(version.get("response") or "").strip()
        source_key = f"stage3::{mode}::{timestamp}"
        return {
            "source_key": source_key,
            "source_type": source_type,
            "source_label": label,
            "source_note": note,
            "source_model": resolved_model,
            "source_mode": mode,
            "source_version_index": version_index,
            "source_content": content,
            "effective_prompt": effective_prompt,
            "default_target_model": _default_target_model(resolved_model),
            "initial_context_message": _build_context_message(
                effective_prompt=effective_prompt,
                source_label=label,
                source_note=note,
                source_content=content,
            ),
        }

    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

    return {
        "source_key": source_key,
        "source_type": source_type,
        "source_label": label,
        "source_note": note,
        "source_model": resolved_model,
        "source_mode": None,
        "source_version_index": version_index,
        "source_content": content,
        "effective_prompt": effective_prompt,
        "default_target_model": _default_target_model(resolved_model),
        "initial_context_message": _build_context_message(
            effective_prompt=effective_prompt,
            source_label=label,
            source_note=note,
            source_content=content,
        ),
    }


def build_side_chat_thread(
    source: Dict[str, Any],
    target_model: str,
) -> Dict[str, Any]:
    """Create a persisted side-chat thread payload."""
    timestamp = datetime.utcnow().isoformat()
    return {
        "id": str(uuid.uuid4()),
        "source_key": source["source_key"],
        "source_type": source["source_type"],
        "source_label": source["source_label"],
        "source_note": source["source_note"],
        "source_model": source.get("source_model"),
        "source_mode": source.get("source_mode"),
        "source_version_index": source.get("source_version_index"),
        "target_model": target_model,
        "created_at": timestamp,
        "updated_at": timestamp,
        "context": {
            "effective_prompt": source.get("effective_prompt"),
            "source_content": source.get("source_content"),
            "initial_context_message": source.get("initial_context_message"),
        },
        "messages": [],
    }


def build_side_chat_messages(
    thread: Dict[str, Any],
    next_user_message: str,
) -> List[Dict[str, str]]:
    """Materialize the prompt history for a single-model side-chat turn."""
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SIDE_CHAT_SYSTEM_PROMPT},
    ]

    initial_context = str(
        ((thread.get("context") or {}).get("initial_context_message"))
        or ""
    ).strip()
    if initial_context:
        messages.append({"role": "user", "content": initial_context})

    for item in thread.get("messages") or []:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": next_user_message.strip()})
    return messages
