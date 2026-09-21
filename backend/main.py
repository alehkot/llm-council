"""FastAPI backend for LLM Council."""

import asyncio
import re
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any, Deque, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import skill_sources, skills, storage
from .config import (
    API_AUTH_KEY,
    API_KEY_BRUTEFORCE_LOCKOUT_SECONDS,
    API_KEY_BRUTEFORCE_MAX_ATTEMPTS,
    API_KEY_BRUTEFORCE_WINDOW_SECONDS,
    CHAIRMAN_MODEL,
    CORS_ALLOW_ORIGINS,
    COUNCIL_MODELS,
    DELIBERATION_ENABLED,
    INTERACTIVE_MODE_ENABLED,
    MAX_NEGATIVE_PROMPT_CHARS,
    MAX_PROMPT_CHARS,
    NEGATIVE_MODE_ENABLED,
    OPENROUTER_MAX_TOKENS,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
    PROMPT_REFINEMENT_MODEL,
    SKILLS_AUTO_SELECT_ENABLED,
    SKILLS_CONTEXT_MAX_CHARS,
    SKILLS_ENABLED,
    SKILLS_MAX_REFERENCE_SECTIONS,
    SKILLS_MAX_REFERENCES,
    SKILLS_MAX_SELECTED,
    SKILLS_REFERENCE_SELECTION_ENABLED,
    SKILLS_REFERENCE_SELECTION_MODEL,
    SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS,
    SKILLS_UPLOAD_MAX_BYTES,
    STATUS_LONGPOLL_CHECK_INTERVAL_SECONDS,
    STATUS_LONGPOLL_MAX_WAIT_SECONDS,
    STREAM_TIMEOUT_SECONDS,
    SYNTHESIS_CONTEXT_MAX_CHARS,
    TITLE_MODEL,
)
from .consultation_manager import consultation_manager
from .council import build_responses_for_synthesis, stage3_synthesize_final
from .export_markdown import build_export_filename, build_markdown_export
from .interactive import (
    PromptResponse,
)
from .openrouter import query_model
from .side_chat import (
    build_side_chat_messages,
    build_side_chat_thread,
    resolve_side_chat_source,
)

# Removed streaming and interactive locks as consultation_manager now handles them


@asynccontextmanager
async def lifespan(app: FastAPI):
    consultation_manager.restore_incomplete_consultations()
    try:
        yield
    finally:
        consultation_manager.shutdown(wait=False)


app = FastAPI(title="LLM Council API", lifespan=lifespan)
API_KEY_HEADER = "X-API-Key"


@dataclass
class _BruteForceEntry:
    failures: Deque[float]
    locked_until: float = 0.0


_bruteforce_state: Dict[str, _BruteForceEntry] = {}
_bruteforce_lock = asyncio.Lock()
_side_chat_locks: Dict[tuple[str, int, str], asyncio.Lock] = {}
_side_chat_locks_guard = asyncio.Lock()


async def _get_side_chat_lock(
    conversation_id: str,
    message_index: int,
    thread_id: str,
) -> asyncio.Lock:
    """Serialize sends to the same side-chat thread to preserve turn order."""
    key = (conversation_id, message_index, thread_id)
    async with _side_chat_locks_guard:
        lock = _side_chat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _side_chat_locks[key] = lock
        return lock


# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _bruteforce_enabled() -> bool:
    return API_KEY_BRUTEFORCE_MAX_ATTEMPTS > 0


def _validate_message_lengths(content: str, negative_prompt: Optional[str]) -> None:
    if not content.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")
    if len(content) > MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Prompt too long (max {MAX_PROMPT_CHARS} characters).",
        )
    if negative_prompt and len(negative_prompt) > MAX_NEGATIVE_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Negative prompt too long (max {MAX_NEGATIVE_PROMPT_CHARS} characters).",
        )


def _validate_skill_ids(skill_ids: Optional[List[str]]) -> List[str]:
    """
    Normalize a requested skill selection for a new consultation.

    Dedupes while preserving order, caps at SKILLS_MAX_SELECTED, and requires every
    id to be a safe storage id that exists on disk and is enabled. Enabled-gating
    applies to new selections only: queued, in-flight and resumed consultations
    render whatever still exists on disk.
    """
    if not skill_ids:
        return []
    if not SKILLS_ENABLED:
        raise HTTPException(status_code=400, detail="Agent skills are disabled.")

    requested: List[str] = []
    seen: Set[str] = set()
    for raw_id in skill_ids:
        candidate = str(raw_id or "").strip().lower()
        if candidate in seen:
            continue
        seen.add(candidate)
        requested.append(candidate)

    if len(requested) > SKILLS_MAX_SELECTED:
        raise HTTPException(
            status_code=400,
            detail=f"Too many skills selected (max {SKILLS_MAX_SELECTED}).",
        )

    for candidate in requested:
        try:
            storage.validate_storage_id(candidate, "skill_id")
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid skill id: {candidate[:64]!r}"
            )

    found, missing = skills.load_skills(requested)
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown skill: {missing[0]}")
    for skill in found:
        if not skill.enabled:
            raise HTTPException(
                status_code=400, detail=f"Skill '{skill.id}' is disabled."
            )
    return [skill.id for skill in found]


def _normalize_optional_text(
    value: Optional[str],
    label: str,
    max_chars: int,
) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_chars:
        raise HTTPException(
            status_code=413, detail=f"{label} too long (max {max_chars} characters)."
        )
    return normalized


def _client_id_from_request(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client:
        return request.client.host
    return "unknown"


def _prune_failures(entry: _BruteForceEntry, now: float) -> None:
    window_seconds = API_KEY_BRUTEFORCE_WINDOW_SECONDS
    while entry.failures and now - entry.failures[0] > window_seconds:
        entry.failures.popleft()


async def _check_bruteforce_lockout(client_id: str) -> Optional[JSONResponse]:
    if not _bruteforce_enabled():
        return None
    now = monotonic()
    async with _bruteforce_lock:
        entry = _bruteforce_state.get(client_id)
        if not entry:
            return None
        if entry.locked_until > now:
            retry_after = int(max(1, entry.locked_until - now))
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": "Too many invalid API key attempts. Try again later."
                },
            )
        if entry.locked_until:
            entry.locked_until = 0.0
        _prune_failures(entry, now)
        if not entry.failures:
            _bruteforce_state.pop(client_id, None)
    return None


async def _record_bruteforce_failure(client_id: str) -> Optional[JSONResponse]:
    if not _bruteforce_enabled():
        return None
    now = monotonic()
    async with _bruteforce_lock:
        entry = _bruteforce_state.get(client_id)
        if entry is None:
            entry = _BruteForceEntry(deque())
            _bruteforce_state[client_id] = entry
        _prune_failures(entry, now)
        entry.failures.append(now)
        if len(entry.failures) >= API_KEY_BRUTEFORCE_MAX_ATTEMPTS:
            entry.locked_until = now + API_KEY_BRUTEFORCE_LOCKOUT_SECONDS
            entry.failures.clear()
            retry_after = int(max(1, entry.locked_until - now))
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": "Too many invalid API key attempts. Try again later."
                },
            )
    return None


async def _clear_bruteforce_state(client_id: str) -> None:
    if not _bruteforce_enabled():
        return
    async with _bruteforce_lock:
        _bruteforce_state.pop(client_id, None)


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Require API key for all /api routes (skip preflight OPTIONS)."""
    if request.url.path.startswith("/api") and request.method != "OPTIONS":
        client_id = _client_id_from_request(request)
        lockout_response = await _check_bruteforce_lockout(client_id)
        if lockout_response:
            return lockout_response
        api_key = request.headers.get(API_KEY_HEADER)
        if not api_key or api_key != API_AUTH_KEY:
            failure_response = await _record_bruteforce_failure(client_id)
            if failure_response:
                return failure_response
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
        await _clear_bruteforce_state(client_id)
    return await call_next(request)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""

    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""

    content: str
    negative_prompt: Optional[str] = None
    skill_ids: Optional[List[str]] = None


class OpenSideChatRequest(BaseModel):
    """Open, resume, or create a persisted side-chat thread."""

    message_index: int
    source_type: str
    source_model: Optional[str] = None
    version_index: Optional[int] = None
    target_model: Optional[str] = None
    thread_id: Optional[str] = None
    start_new: bool = False


class SendSideChatMessageRequest(BaseModel):
    """Send a direct follow-up to a persisted side-chat thread."""

    message_index: int
    content: str


class ResynthesizeRequest(BaseModel):
    """Request to rerun synthesis with different settings."""

    message_index: int
    synthesis_mode: str
    focus_areas: Optional[str] = None


class InteractiveResponseRequest(BaseModel):
    """Response to an interactive prompt."""

    session_id: str
    prompt_id: str
    responses: Dict[str, Any]
    skipped: bool = False


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""

    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""

    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.get("/api/config")
async def get_config():
    """Expose selected runtime configuration to the frontend."""
    return {
        "negative_mode_enabled": NEGATIVE_MODE_ENABLED,
        "deliberation_enabled": DELIBERATION_ENABLED,
        "interactive_mode_enabled": INTERACTIVE_MODE_ENABLED,
        "prompt_refinement_enabled": PROMPT_REFINEMENT_ENABLED,
        "pairwise_ranking_enabled": PAIRWISE_RANKING_ENABLED,
        "stream_timeout_seconds": STREAM_TIMEOUT_SECONDS,
        "prompt_max_chars": MAX_PROMPT_CHARS,
        "negative_prompt_max_chars": MAX_NEGATIVE_PROMPT_CHARS,
        "synthesis_context_max_chars": SYNTHESIS_CONTEXT_MAX_CHARS,
        "openrouter_max_tokens": OPENROUTER_MAX_TOKENS,
        "council_models": COUNCIL_MODELS,
        "chairman_model": CHAIRMAN_MODEL,
        "prompt_refinement_model": PROMPT_REFINEMENT_MODEL,
        "title_model": TITLE_MODEL,
        "skills_enabled": SKILLS_ENABLED,
        "skills_max_selected": SKILLS_MAX_SELECTED,
        "skills_auto_select_enabled": SKILLS_AUTO_SELECT_ENABLED,
        "skills_upload_max_bytes": SKILLS_UPLOAD_MAX_BYTES,
        "skills_context_max_chars": SKILLS_CONTEXT_MAX_CHARS,
        "skills_synthesis_context_max_chars": SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS,
        "skills_max_references": SKILLS_MAX_REFERENCES,
        "skills_max_reference_sections": SKILLS_MAX_REFERENCE_SECTIONS,
        "skills_reference_selection_enabled": SKILLS_REFERENCE_SELECTION_ENABLED,
        "skills_reference_selection_model": SKILLS_REFERENCE_SELECTION_MODEL,
    }


# ---------------------------------------------------------------------------
# Agent Skills library
# ---------------------------------------------------------------------------

_SKILL_UPLOAD_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _sanitize_upload_filename(filename: Optional[str]) -> Optional[str]:
    """Display-only upload filename; never used as a path."""
    if not filename:
        return None
    candidate = filename.strip()
    if not _SKILL_UPLOAD_FILENAME_PATTERN.fullmatch(candidate):
        return None
    return candidate


async def _read_upload_body(request: Request, max_bytes: int) -> bytes:
    """
    Read a raw request body with a hard size ceiling.

    Content-Length is checked first, then the stream is accumulated chunk by chunk
    and abandoned the moment the running total passes the ceiling. Never
    ``await request.body()``: that buffers an arbitrarily large chunked body before
    any check can run.
    """
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_length = int(declared)
        except ValueError:
            declared_length = -1
        if declared_length > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large (max {max_bytes} bytes).",
            )

    chunks: List[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large (max {max_bytes} bytes).",
            )
        chunks.append(chunk)

    if total == 0:
        raise HTTPException(status_code=400, detail="Upload body was empty.")
    return b"".join(chunks)


def _skill_conflict_payload(error: skills.SkillError) -> Dict[str, Any]:
    """409 body: the message plus the summary of the skill already installed."""
    payload: Dict[str, Any] = {"detail": str(error), "existing": None}
    existing = getattr(error, "existing", None)
    if existing is None:
        existing_id = getattr(error, "skill_id", None)
        if existing_id:
            existing = skills.get_skill(existing_id)
    if existing is not None:
        payload["existing"] = existing.to_summary()
    return payload


def _skill_http_error(error: skills.SkillError) -> HTTPException:
    """Map a SkillError onto the status code it declares."""
    return HTTPException(
        status_code=getattr(error, "status_code", 400), detail=str(error)
    )


if SKILLS_ENABLED:

    class SetSkillEnabledRequest(BaseModel):
        """Request to enable or disable an installed skill."""

        enabled: bool

    @app.get("/api/skills")
    async def list_skills() -> Dict[str, Any]:
        """List every installed skill (manifest-derived summaries)."""
        found = await asyncio.to_thread(skills.list_skills)
        return {
            "skills": [skill.to_summary() for skill in found],
            "sources": skill_sources.settings(),
        }

    class SkillSourcesRequest(BaseModel):
        codex: bool
        claude: bool

    @app.put("/api/skills/sources")
    async def set_skill_sources(request: SkillSourcesRequest) -> Dict[str, Any]:
        sources = await asyncio.to_thread(
            skill_sources.configure, request.codex, request.claude
        )
        return {"sources": sources}

    @app.post("/api/skills")
    async def upload_skill(
        request: Request,
        filename: Optional[str] = None,
        replace: bool = False,
    ) -> JSONResponse:
        """
        Install a skill from a raw ``.zip`` request body.

        201 on a fresh install, 200 when an existing skill was replaced, 409 with the
        existing summary when the id is taken and ``replace`` was not requested.
        """
        data = await _read_upload_body(request, SKILLS_UPLOAD_MAX_BYTES)
        try:
            installed, replaced = await asyncio.to_thread(
                skills.install_skill_pack,
                data,
                _sanitize_upload_filename(filename),
                bool(replace),
            )
        except skills.SkillError as e:
            if getattr(e, "status_code", 400) == 409:
                return JSONResponse(
                    status_code=409,
                    content=_skill_conflict_payload(e),
                )
            raise _skill_http_error(e)

        return JSONResponse(
            status_code=200 if replaced else 201,
            content={
                "skill": installed[0].to_detail(),
                "skills": [skill.to_detail() for skill in installed],
                "replaced": replaced,
            },
        )

    @app.get("/api/skills/{skill_id}")
    async def get_skill(skill_id: str) -> Dict[str, Any]:
        """Full manifest detail for one installed skill."""
        skill = skills.get_skill(skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        return skill.to_detail()

    @app.get("/api/skills/{skill_id}/markdown")
    async def get_skill_markdown(skill_id: str) -> Dict[str, Any]:
        """Raw SKILL.md text - the only file-content route the library exposes."""
        markdown = skills.read_skill_markdown(skill_id)
        if markdown is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {"markdown": markdown}

    @app.patch("/api/skills/{skill_id}")
    async def set_skill_enabled(
        skill_id: str, request: SetSkillEnabledRequest
    ) -> Dict[str, Any]:
        """Enable or disable a skill without touching the rest of its manifest."""
        try:
            skill = skills.set_skill_enabled(skill_id, request.enabled)
        except skills.SkillError as e:
            raise _skill_http_error(e)
        return skill.to_summary()

    @app.delete("/api/skills/{skill_id}")
    async def delete_skill(skill_id: str) -> Dict[str, Any]:
        """Delete an installed skill and its stored bundle."""
        try:
            deleted = skills.delete_skill(skill_id)
        except skills.SkillError as e:
            raise _skill_http_error(e)
        if not deleted:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {"status": "ok", "deleted": True}


@app.post("/api/interactive-response")
async def submit_interactive_response(request: InteractiveResponseRequest):
    """Submit a response to an interactive prompt."""
    try:
        response = PromptResponse(
            prompt_id=request.prompt_id,
            responses=request.responses,
            skipped=request.skipped,
        )
        consultation_manager.submit_interactive_response(request.session_id, response)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"status": "ok"}


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a single conversation and cancel any active consultation."""
    # Cancel any active consultation first
    consultation_manager.cancel_consultation(conversation_id)

    deleted = storage.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok"}


@app.delete("/api/conversations")
async def delete_all_conversations():
    """Delete all stored conversations."""
    cancelled = consultation_manager.cancel_all_consultations()
    deleted = storage.clear_conversations()
    return {"status": "ok", "deleted": deleted, "cancelled": cancelled}


class ResynthesizeAllRequest(BaseModel):
    """Request to run all missing synthesis modes."""

    message_index: int
    modes: Optional[List[str]] = None  # If None, generates all missing modes


class SetCurrentVersionRequest(BaseModel):
    """Request to set a specific version as current."""

    message_index: int
    version_index: int


SYNTHESIS_MODE_DESCRIPTIONS = {
    "consensus": (
        "Find and present only the points where all or most responses agree. "
        "Filter out minority opinions and contested claims."
    ),
    "best_of_breed": (
        "Pick the best elements from each response - the clearest explanation, "
        "the best examples, and the most thoughtful caveats."
    ),
    "comprehensive": (
        "Include all perspectives, even if they contradict. Present trade-offs "
        "and alternative viewpoints explicitly."
    ),
    "weighted": (
        "Prioritize both content and context budget from higher-ranked responses. "
        "Lower-ranked responses should contribute less to the final answer."
    ),
}

ALL_SYNTHESIS_MODES = list(SYNTHESIS_MODE_DESCRIPTIONS.keys())


def _get_assistant_message_base_context(conversation_id: str, message_index: int):
    """
    Resolve an assistant message by ordinal index within a conversation.

    Returns tuple of (conversation, original_idx, target_msg, user_query, request_number).
    """
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = conversation.get("messages", [])
    assistant_messages = [
        (i, m) for i, m in enumerate(messages) if m.get("role") == "assistant"
    ]

    if message_index < 0 or message_index >= len(assistant_messages):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid message_index. Found {len(assistant_messages)} assistant messages.",
        )

    original_idx, target_msg = assistant_messages[message_index]

    user_query: Optional[str] = None
    request_number = 0
    for index, msg in enumerate(messages):
        if msg.get("role") == "user":
            request_number += 1
        if index == original_idx:
            break

    for i in range(original_idx - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_query = messages[i].get("content")
            break

    return conversation, original_idx, target_msg, user_query, request_number or None


def _get_assistant_message_context(conversation_id: str, message_index: int):
    """
    Helper to get assistant message and related context for resynthesis.
    Returns tuple of (conversation, original_idx, target_msg, user_query).
    """
    conversation, original_idx, target_msg, user_query, _ = (
        _get_assistant_message_base_context(
            conversation_id,
            message_index,
        )
    )

    if not user_query:
        raise HTTPException(
            status_code=400, detail="Could not find user query for this message"
        )

    stage1 = target_msg.get("stage1", [])
    stage2 = target_msg.get("stage2", [])

    if not stage1 or not stage2:
        raise HTTPException(
            status_code=400, detail="Missing stage1 or stage2 data for resynthesis"
        )
    if not target_msg.get("stage3"):
        raise HTTPException(
            status_code=400, detail="Missing stage3 data for resynthesis"
        )

    return conversation, original_idx, target_msg, user_query


def _get_assistant_message_export_context(conversation_id: str, message_index: int):
    """
    Helper to get assistant message and related context for markdown export.
    Returns tuple of (conversation, original_idx, target_msg, user_query, request_number).
    """
    return _get_assistant_message_base_context(conversation_id, message_index)


@app.get("/api/conversations/{conversation_id}/export-markdown")
async def export_markdown(conversation_id: str, message_index: int):
    """
    Export all relevant context for an assistant message as a markdown document.
    """
    conversation, _, target_msg, user_query, request_number = (
        _get_assistant_message_export_context(conversation_id, message_index)
    )

    markdown = build_markdown_export(
        conversation=conversation,
        message=target_msg,
        user_query=user_query,
        message_index=message_index,
        request_number=request_number,
    )
    filename = build_export_filename(conversation.get("title"), request_number)

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render_persisted_skills_context(metadata: Dict[str, Any]) -> Optional[str]:
    """
    Re-render the Stage 3 skills block from a message's persisted skill plan.

    Deterministic and LLM-free: the reference selection recorded when the message was
    produced is replayed against the current manifests, so every resynthesis - including
    the parallel modes of /resynthesize-all - runs under the same skill rules that
    shaped the original answer.
    """
    if not SKILLS_ENABLED:
        return None

    persisted = (metadata or {}).get("skills") or {}
    resolved = persisted.get("resolved")
    if not isinstance(resolved, list):
        return None

    skill_ids: List[str] = []
    selected_sections: Dict[str, List[skills.SectionSelection]] = {}
    reasons: Dict[str, Dict[str, str]] = {}
    for entry in resolved:
        if not isinstance(entry, dict):
            continue
        skill_id = str(entry.get("id") or "").strip().lower()
        if not skill_id or skill_id in selected_sections:
            continue
        selections: List[skills.SectionSelection] = []
        entry_reasons: Dict[str, str] = {}
        for reference in entry.get("references") or []:
            if not isinstance(reference, dict):
                continue
            path = str(reference.get("path") or "")
            if not path:
                continue
            raw_ids = reference.get("section_ids")
            section_ids = (
                tuple(str(section_id) for section_id in raw_ids)
                if isinstance(raw_ids, list)
                else ()
            )
            selections.append(
                skills.SectionSelection(path=path, section_ids=section_ids)
            )
            reason = reference.get("reason")
            if reason:
                entry_reasons[path] = str(reason)
        skill_ids.append(skill_id)
        selected_sections[skill_id] = selections
        reasons[skill_id] = entry_reasons

    if not skill_ids:
        return None

    loaded, _missing = skills.load_skills(skill_ids)
    if not loaded:
        return None

    block, _report = skills.render_skills_context(
        loaded,
        {skill.id: selected_sections.get(skill.id, []) for skill in loaded},
        SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS,
        reasons,
    )
    return block


async def _run_synthesis(
    user_query: str,
    target_msg: Dict[str, Any],
    synthesis_mode: str,
    focus_areas: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run synthesis with specified mode and return the version dict.
    """
    from datetime import datetime

    stage1 = target_msg.get("stage1", [])
    stage2 = target_msg.get("stage2", [])
    metadata = target_msg.get("metadata", {})
    label_to_model = metadata.get("label_to_model", {})
    aggregate_gap_scores = metadata.get("aggregate_gap_scores")
    aggregate_rankings = metadata.get("aggregate_rankings")
    using_deliberation_revisions = bool(
        (metadata.get("deliberation") or {}).get("results")
    )
    responses_for_synthesis = build_responses_for_synthesis(stage1, metadata)
    focus_areas = _normalize_optional_text(focus_areas, "Focus areas", MAX_PROMPT_CHARS)

    # Build the synthesis mode prompt modifier
    synthesis_guidance_parts = []
    mode_desc = SYNTHESIS_MODE_DESCRIPTIONS.get(synthesis_mode)
    if mode_desc:
        synthesis_guidance_parts.append(
            f"Synthesis mode: {synthesis_mode}. {mode_desc}"
        )

    if focus_areas:
        synthesis_guidance_parts.append(f"Focus areas: {focus_areas}")

    prompt_modifier = (
        " ".join(synthesis_guidance_parts) if synthesis_guidance_parts else None
    )

    # Handle negative context if present
    negative_context = None
    if target_msg.get("negative"):
        negative_context = {
            "enabled": True,
            "prompt": target_msg["negative"].get("prompt", ""),
            "evaluations": target_msg["negative"].get("evaluations", []),
        }

    # Replay the persisted skill plan; blocking disk reads stay off the event loop.
    skills_context = await asyncio.to_thread(_render_persisted_skills_context, metadata)

    new_stage3 = await stage3_synthesize_final(
        user_query=user_query,
        stage1_results=responses_for_synthesis,
        stage2_results=stage2,
        label_to_model=label_to_model,
        negative_context=negative_context,
        aggregate_gap_scores=aggregate_gap_scores,
        aggregate_rankings=aggregate_rankings,
        prompt_modifier=prompt_modifier,
        synthesis_mode=synthesis_mode,
        using_deliberation_revisions=using_deliberation_revisions,
        skills_context=skills_context,
    )

    # Create version entry
    version = {
        "model": new_stage3.get("model"),
        "response": new_stage3.get("response"),
        "mode": synthesis_mode,
        "timestamp": datetime.utcnow().isoformat(),
        "finish_reason": new_stage3.get("finish_reason"),
        "usage": new_stage3.get("usage"),
    }

    return version


@app.post("/api/conversations/{conversation_id}/resynthesize")
async def resynthesize_response(conversation_id: str, request: ResynthesizeRequest):
    """
    Re-run Stage 3 synthesis with different settings.
    Appends the new synthesis as a version and sets it as current.
    """
    if request.synthesis_mode not in SYNTHESIS_MODE_DESCRIPTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid synthesis mode: {request.synthesis_mode}. "
                f"Valid modes: {ALL_SYNTHESIS_MODES}"
            ),
        )

    conversation, original_idx, target_msg, user_query = _get_assistant_message_context(
        conversation_id, request.message_index
    )

    try:
        version = await _run_synthesis(
            user_query, target_msg, request.synthesis_mode, request.focus_areas
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {str(e)}")

    # Append version to storage
    version_index = storage.append_stage3_version(
        conversation_id,
        original_idx,
        version,
        set_as_current=True,
    )

    # Update metadata with current synthesis mode
    storage.update_assistant_message(
        conversation_id,
        original_idx,
        {"metadata": {"synthesis_mode": request.synthesis_mode}},
    )

    return {
        "status": "ok",
        "version": version,
        "version_index": version_index,
    }


@app.post("/api/conversations/{conversation_id}/resynthesize-all")
async def resynthesize_all(conversation_id: str, request: ResynthesizeAllRequest):
    """
    Run synthesis for multiple modes in parallel.
    If modes is not specified, generates all modes that don't already exist.
    """
    conversation, original_idx, target_msg, user_query = _get_assistant_message_context(
        conversation_id, request.message_index
    )

    # Determine which modes to run, preserving explicit empty lists and de-duplicating.
    raw_modes = request.modes if request.modes is not None else ALL_SYNTHESIS_MODES
    requested_modes = []
    seen_modes = set()
    for mode in raw_modes:
        if mode in seen_modes:
            continue
        seen_modes.add(mode)
        requested_modes.append(mode)

    # Validate modes
    invalid_modes = [m for m in requested_modes if m not in SYNTHESIS_MODE_DESCRIPTIONS]
    if invalid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid synthesis modes: {invalid_modes}. Valid modes: {ALL_SYNTHESIS_MODES}",
        )

    if request.modes is not None and not requested_modes:
        return {
            "status": "ok",
            "versions": [],
            "message": "No synthesis modes requested",
        }

    # If no specific modes requested, find modes that don't exist yet
    if request.modes is None:
        stage3 = target_msg.get("stage3", {})
        existing_modes = set()
        if "versions" in stage3:
            existing_modes = {v.get("mode") for v in stage3.get("versions", [])}
        requested_modes = [m for m in ALL_SYNTHESIS_MODES if m not in existing_modes]

    if not requested_modes:
        return {"status": "ok", "versions": [], "message": "All modes already exist"}

    # Run syntheses in parallel
    async def run_mode(mode: str):
        try:
            return await _run_synthesis(user_query, target_msg, mode)
        except Exception as e:
            return {"mode": mode, "error": str(e)}

    results = await asyncio.gather(*[run_mode(m) for m in requested_modes])

    # Separate successful versions from errors
    versions = []
    errors = []
    for result in results:
        if "error" in result:
            errors.append(result)
        else:
            versions.append(result)

    # Append all successful versions to storage
    version_indices = []
    for version in versions:
        # Don't set as current - keep existing current
        idx = storage.append_stage3_version(
            conversation_id,
            original_idx,
            version,
            set_as_current=False,
        )
        version_indices.append(idx)

    response = {
        "status": "ok",
        "versions": versions,
        "version_indices": version_indices,
    }

    if errors:
        response["errors"] = errors

    return response


@app.post("/api/conversations/{conversation_id}/set-current-version")
async def set_current_version(conversation_id: str, request: SetCurrentVersionRequest):
    """
    Set a specific synthesis version as the current one.
    """
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = conversation.get("messages", [])
    assistant_messages = [
        (i, m) for i, m in enumerate(messages) if m.get("role") == "assistant"
    ]

    if request.message_index < 0 or request.message_index >= len(assistant_messages):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid message_index. Found {len(assistant_messages)} assistant messages.",
        )

    original_idx, _ = assistant_messages[request.message_index]

    try:
        storage.set_stage3_current_version(
            conversation_id, original_idx, request.version_index
        )
    except (IndexError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "ok"}


@app.post("/api/conversations/{conversation_id}/side-chats/open")
async def open_side_chat(conversation_id: str, request: OpenSideChatRequest):
    """Open, resume, or create a persisted side-chat thread."""
    _, original_idx, assistant_message, user_query, _ = (
        _get_assistant_message_base_context(
            conversation_id,
            request.message_index,
        )
    )

    try:
        source = resolve_side_chat_source(
            assistant_message,
            user_query,
            request.source_type,
            source_model=request.source_model,
            version_index=request.version_index,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    target_model = request.target_model or source.get("default_target_model") or ""
    if target_model not in COUNCIL_MODELS:
        raise HTTPException(
            status_code=400,
            detail="target_model must be one of the configured council_models",
        )

    try:
        threads = storage.list_side_chat_threads(conversation_id, original_idx)
    except (IndexError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    if request.thread_id:
        thread = next(
            (item for item in threads if item.get("id") == request.thread_id), None
        )
        if thread is None:
            raise HTTPException(status_code=404, detail="Side chat thread not found")
        if thread.get("source_key") != source["source_key"]:
            raise HTTPException(
                status_code=400, detail="Requested thread does not match the source"
            )
        if thread.get("target_model") != target_model:
            raise HTTPException(
                status_code=400, detail="Requested thread does not match the model"
            )
        return {"status": "ok", "created": False, "thread": thread}

    if not request.start_new:
        matching = [
            item
            for item in threads
            if item.get("source_key") == source["source_key"]
            and item.get("target_model") == target_model
        ]
        if matching:
            matching.sort(
                key=lambda item: item.get("updated_at") or item.get("created_at") or "",
                reverse=True,
            )
            return {"status": "ok", "created": False, "thread": matching[0]}

    thread = build_side_chat_thread(source, target_model)
    try:
        storage.save_side_chat_thread(conversation_id, original_idx, thread)
    except (IndexError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "created": True, "thread": thread}


@app.post("/api/conversations/{conversation_id}/side-chats/{thread_id}/message")
async def send_side_chat_message(
    conversation_id: str,
    thread_id: str,
    request: SendSideChatMessageRequest,
):
    """Send a direct follow-up message inside a persisted side-chat thread."""
    _, original_idx, _, _, _ = _get_assistant_message_base_context(
        conversation_id,
        request.message_index,
    )
    _validate_message_lengths(request.content, None)

    lock = await _get_side_chat_lock(conversation_id, original_idx, thread_id)
    async with lock:
        try:
            thread = storage.get_side_chat_thread(
                conversation_id, original_idx, thread_id
            )
        except (IndexError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

        if thread is None:
            raise HTTPException(status_code=404, detail="Side chat thread not found")

        target_model = str(thread.get("target_model") or "").strip()
        if target_model not in COUNCIL_MODELS:
            raise HTTPException(
                status_code=400, detail="Side chat target model is no longer available"
            )

        response = await query_model(
            target_model,
            build_side_chat_messages(thread, request.content),
            trace_stage="side_chat",
            trace_context={
                "conversation_id": conversation_id,
                "thread_id": thread_id,
                "source_type": thread.get("source_type"),
                "source_key": thread.get("source_key"),
            },
        )
        if response is None or not response.get("content"):
            raise HTTPException(
                status_code=502, detail=f"{target_model} failed to respond"
            )

        timestamp = datetime.utcnow().isoformat()
        user_message = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": request.content.strip(),
            "created_at": timestamp,
        }
        assistant_message = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": str(response.get("content") or "").strip(),
            "created_at": timestamp,
        }

        thread_messages = list(thread.get("messages") or [])
        thread_messages.append(user_message)
        thread_messages.append(assistant_message)
        thread["messages"] = thread_messages
        thread["updated_at"] = timestamp

        try:
            storage.save_side_chat_thread(conversation_id, original_idx, thread)
        except (IndexError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {"status": "ok", "thread": thread}


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and start async consultation.
    Returns immediately with consultation_id and status.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    _validate_message_lengths(request.content, request.negative_prompt)
    skill_ids = _validate_skill_ids(request.skill_ids)

    # Submit to consultation manager
    result = consultation_manager.submit(
        conversation_id,
        request.content,
        request.negative_prompt,
        skill_ids=skill_ids,
    )

    return result


async def _hold_for_change(
    request: Request,
    fetch_snapshot,
    cursor: Optional[str],
    wait: float,
) -> Dict[str, Any]:
    """Return the current snapshot, long-polling when the client sent a cursor.

    Holds the request (up to the clamped wait) until the snapshot's cursor
    differs from the one the client last saw, then returns the full snapshot.
    On timeout the unchanged snapshot is returned; the client simply re-polls,
    so a dropped or timed-out request never loses state.
    """

    snapshot = fetch_snapshot()
    if not (wait > 0) or cursor is None or snapshot.get("cursor") != cursor:
        return snapshot

    deadline = monotonic() + min(wait, STATUS_LONGPOLL_MAX_WAIT_SECONDS)
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0 or await request.is_disconnected():
            return snapshot
        await asyncio.sleep(min(STATUS_LONGPOLL_CHECK_INTERVAL_SECONDS, remaining))
        snapshot = fetch_snapshot()
        if snapshot.get("cursor") != cursor:
            return snapshot


@app.get("/api/conversations/{conversation_id}/consultation/status")
async def get_consultation_status(
    conversation_id: str,
    request: Request,
    wait: float = 0,
    cursor: Optional[str] = None,
):
    """Get consultation status plus queue for a conversation.

    With ``wait`` and ``cursor`` query params the request long-polls: it is held
    until the snapshot changes from ``cursor`` or the wait window lapses.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return await _hold_for_change(
        request,
        lambda: consultation_manager.get_sync_snapshot(conversation_id),
        cursor,
        wait,
    )


@app.post("/api/conversations/{conversation_id}/consultation/cancel")
async def cancel_consultation(conversation_id: str):
    """Cancel an in-progress consultation and clear any queued messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    cancelled = consultation_manager.cancel_consultation(conversation_id)

    return {"status": "ok", "cancelled": cancelled}


@app.get("/api/conversations/{conversation_id}/queue")
async def get_conversation_queue(conversation_id: str):
    """Get queued messages for a conversation."""
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    queue = consultation_manager.get_queue(conversation_id)
    return {"queue": queue}


@app.get("/api/consultations/status")
async def get_global_consultation_status(
    request: Request,
    wait: float = 0,
    cursor: Optional[str] = None,
):
    """Get global consultation system status, long-polling when a cursor is sent."""
    return await _hold_for_change(
        request,
        consultation_manager.get_global_snapshot,
        cursor,
        wait,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
