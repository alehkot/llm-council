"""FastAPI backend for LLM Council."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Deque
from collections import deque
from dataclasses import dataclass
from time import monotonic
import uuid
import json
import asyncio

from . import storage
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
    INTERACTIVE_TIMEOUT_SECONDS,
    NEGATIVE_MODE_ENABLED,
    MAX_NEGATIVE_PROMPT_CHARS,
    MAX_PROMPT_CHARS,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
    PROMPT_REFINEMENT_MODEL,
    OPENROUTER_MAX_TOKENS,
    STREAM_TIMEOUT_SECONDS,
    TITLE_MODEL,
)
from .langgraph_flow import FlowCallbacks, FlowContext, run_council_graph
from .council import stage3_synthesize_final
from .trace import TraceCollector
from .consultation_manager import consultation_manager
from .interactive import (
    PromptResponse,
)
from .export_markdown import build_markdown_export, build_export_filename

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
    if len(content) > MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Prompt too long (max {MAX_PROMPT_CHARS} characters)."
        )
    if negative_prompt and len(negative_prompt) > MAX_NEGATIVE_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Negative prompt too long (max {MAX_NEGATIVE_PROMPT_CHARS} characters)."
        )


def _client_id_from_request(request: Request) -> str:
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
                content={"detail": "Too many invalid API key attempts. Try again later."},
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
                content={"detail": "Too many invalid API key attempts. Try again later."},
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
        "openrouter_max_tokens": OPENROUTER_MAX_TOKENS,
        "council_models": COUNCIL_MODELS,
        "chairman_model": CHAIRMAN_MODEL,
        "prompt_refinement_model": PROMPT_REFINEMENT_MODEL,
        "title_model": TITLE_MODEL,
    }


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
    deleted = storage.clear_conversations()
    return {"status": "ok", "deleted": deleted}


class ResynthesizeAllRequest(BaseModel):
    """Request to run all missing synthesis modes."""
    message_index: int
    modes: Optional[List[str]] = None  # If None, generates all missing modes


class SetCurrentVersionRequest(BaseModel):
    """Request to set a specific version as current."""
    message_index: int
    version_index: int


SYNTHESIS_MODE_DESCRIPTIONS = {
    "consensus": "Find and present only the points where all or most responses agree. Filter out minority opinions and contested claims.",
    "best_of_breed": "Pick the best elements from each response - the clearest explanation, the best examples, the most thoughtful caveats.",
    "comprehensive": "Include all perspectives, even if they contradict. Present trade-offs and alternative viewpoints explicitly.",
    "weighted": "Prioritize content from higher-ranked responses. Lower-ranked responses should contribute less to the final answer.",
}

ALL_SYNTHESIS_MODES = list(SYNTHESIS_MODE_DESCRIPTIONS.keys())


def _get_assistant_message_context(conversation_id: str, message_index: int):
    """
    Helper to get assistant message and related context for resynthesis.
    Returns tuple of (conversation, original_idx, target_msg, user_query).
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
            detail=f"Invalid message_index. Found {len(assistant_messages)} assistant messages."
        )

    original_idx, target_msg = assistant_messages[message_index]

    # Find the user query from the message before this assistant message
    user_query = None
    for i in range(original_idx - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_query = messages[i].get("content")
            break

    if not user_query:
        raise HTTPException(status_code=400, detail="Could not find user query for this message")

    stage1 = target_msg.get("stage1", [])
    stage2 = target_msg.get("stage2", [])

    if not stage1 or not stage2:
        raise HTTPException(status_code=400, detail="Missing stage1 or stage2 data for resynthesis")

    return conversation, original_idx, target_msg, user_query


def _get_assistant_message_export_context(conversation_id: str, message_index: int):
    """
    Helper to get assistant message and related context for markdown export.
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
            detail=f"Invalid message_index. Found {len(assistant_messages)} assistant messages."
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


@app.get("/api/conversations/{conversation_id}/export-markdown")
async def export_markdown(conversation_id: str, message_index: int):
    """
    Export all relevant context for an assistant message as a markdown document.
    """
    conversation, _, target_msg, user_query, request_number = _get_assistant_message_export_context(
        conversation_id, message_index
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

    # Build the synthesis mode prompt modifier
    synthesis_guidance_parts = []
    mode_desc = SYNTHESIS_MODE_DESCRIPTIONS.get(synthesis_mode)
    if mode_desc:
        synthesis_guidance_parts.append(f"Synthesis mode: {synthesis_mode}. {mode_desc}")

    if focus_areas:
        synthesis_guidance_parts.append(f"Focus areas: {focus_areas}")

    prompt_modifier = " ".join(synthesis_guidance_parts) if synthesis_guidance_parts else None

    # Handle negative context if present
    negative_context = None
    if target_msg.get("negative"):
        negative_context = {
            "enabled": True,
            "evaluations": target_msg["negative"].get("evaluations", []),
        }

    new_stage3 = await stage3_synthesize_final(
        user_query=user_query,
        stage1_results=stage1,
        stage2_results=stage2,
        label_to_model=label_to_model,
        negative_context=negative_context,
        aggregate_gap_scores=aggregate_gap_scores,
        prompt_modifier=prompt_modifier,
    )

    # Create version entry
    version = {
        "model": new_stage3.get("model"),
        "response": new_stage3.get("response"),
        "mode": synthesis_mode,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return version


@app.post("/api/conversations/{conversation_id}/resynthesize")
async def resynthesize_response(conversation_id: str, request: ResynthesizeRequest):
    """
    Re-run Stage 3 synthesis with different settings.
    Appends the new synthesis as a version and sets it as current.
    """
    conversation, original_idx, target_msg, user_query = _get_assistant_message_context(
        conversation_id, request.message_index
    )

    try:
        version = await _run_synthesis(
            user_query, target_msg, request.synthesis_mode, request.focus_areas
        )
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

    # Determine which modes to run
    requested_modes = request.modes if request.modes else ALL_SYNTHESIS_MODES

    # Validate modes
    invalid_modes = [m for m in requested_modes if m not in SYNTHESIS_MODE_DESCRIPTIONS]
    if invalid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid synthesis modes: {invalid_modes}. Valid modes: {ALL_SYNTHESIS_MODES}"
        )

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
            detail=f"Invalid message_index. Found {len(assistant_messages)} assistant messages."
        )

    original_idx, _ = assistant_messages[request.message_index]

    try:
        storage.set_stage3_current_version(conversation_id, original_idx, request.version_index)
    except (IndexError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "ok"}


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

    # Submit to consultation manager
    result = consultation_manager.submit(
        conversation_id,
        request.content,
        request.negative_prompt,
    )

    return result


@app.get("/api/conversations/{conversation_id}/consultation/status")
async def get_consultation_status(conversation_id: str):
    """Get current consultation status for a conversation."""
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    status = consultation_manager.get_status(conversation_id)
    if status is None:
        return {"status": "none"}
    return status


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
async def get_global_consultation_status():
    """Get global consultation system status."""
    return consultation_manager.get_global_status()





if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
