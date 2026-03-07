"""Async consultation manager with thread pool and queue support."""

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
import threading
import uuid
import asyncio

from .config import (
    CONSULTATION_POOL_SIZE,
    DELIBERATION_ENABLED,
    INTERACTIVE_MODE_ENABLED,
    NEGATIVE_MODE_ENABLED,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
)


class ConsultationStatus(str, Enum):
    """Status of a consultation."""
    PENDING = "pending"
    STAGE1 = "stage1"
    NEGATIVE = "negative"
    STAGE2 = "stage2"
    DELIBERATION = "deliberation"
    STAGE3 = "stage3"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class QueuedMessage:
    """A message waiting in queue for consultation."""
    id: str
    content: str
    negative_prompt: Optional[str]
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConsultationState:
    """State of an active consultation."""
    id: str
    conversation_id: str
    message_content: str
    negative_prompt: Optional[str]
    status: ConsultationStatus = ConsultationStatus.PENDING
    stage1_results: Optional[List[Dict[str, Any]]] = None
    stage2_results: Optional[List[Dict[str, Any]]] = None
    stage3_result: Optional[Dict[str, Any]] = None
    negative_context: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    loop: Optional[asyncio.AbstractEventLoop] = field(default=None, repr=False)
    interactive_queue: Optional[asyncio.Queue] = field(default=None, repr=False)
    session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response dict."""
        result = {
            "consultation_id": self.id,
            "conversation_id": self.conversation_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.stage1_results is not None:
            result["stage1"] = self.stage1_results
        if self.negative_context is not None:
            result["negative"] = self.negative_context
        if self.stage2_results is not None:
            result["stage2"] = self.stage2_results
        if self.stage3_result is not None:
            result["stage3"] = self.stage3_result
        if self.metadata is not None:
            result["metadata"] = self.metadata
        if self.error is not None:
            result["error"] = self.error
        return result


class ConsultationManager:
    """
    Singleton manager for async consultations.

    Manages a thread pool for running consultations in background,
    with per-conversation queuing support.
    """
    _instance: Optional["ConsultationManager"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "ConsultationManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._executor = ThreadPoolExecutor(
            max_workers=CONSULTATION_POOL_SIZE,
            thread_name_prefix="consultation-"
        )
        self._lock = threading.Lock()
        self._consultations: Dict[str, ConsultationState] = {}
        self._queues: Dict[str, List[QueuedMessage]] = {}
        self._futures: Dict[str, Future] = {}
        self._interactive_sessions: Dict[str, ConsultationState] = {}

    def submit(
        self,
        conversation_id: str,
        content: str,
        negative_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a message for consultation.

        Returns immediately with consultation_id and status (started/queued).
        """
        with self._lock:
            # Check if there's already an active consultation
            if conversation_id in self._consultations:
                state = self._consultations[conversation_id]
                if state.status not in (ConsultationStatus.COMPLETE, ConsultationStatus.FAILED):
                    # Queue the message
                    queued = QueuedMessage(
                        id=str(uuid.uuid4()),
                        content=content,
                        negative_prompt=negative_prompt,
                    )
                    if conversation_id not in self._queues:
                        self._queues[conversation_id] = []
                    self._queues[conversation_id].append(queued)
                    return {
                        "consultation_id": queued.id,
                        "status": "queued",
                        "queue_position": len(self._queues[conversation_id]),
                    }

            # Start new consultation
            consultation_id_str = str(uuid.uuid4())
            state = ConsultationState(
                id=consultation_id_str,
                conversation_id=conversation_id,
                message_content=content,
                negative_prompt=negative_prompt,
            )
            self._consultations[conversation_id] = state

            # Submit to thread pool
            future = self._executor.submit(self._run_consultation, state)
            self._futures[conversation_id] = future

            return {
                "consultation_id": consultation_id_str,
                "status": "started",
            }

    def submit_interactive_response(self, session_id: str, prompt_response: Any) -> None:
        """Submit a response to an interactive prompt in the worker thread's event loop."""
        with self._lock:
            state = self._interactive_sessions.get(session_id)
            if not state or not state.loop or not state.interactive_queue:
                raise ValueError("Interactive session not found or inactive")
            
            state.loop.call_soon_threadsafe(
                state.interactive_queue.put_nowait, prompt_response
            )

    def get_status(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get current consultation status for a conversation."""
        with self._lock:
            state = self._consultations.get(conversation_id)
            if state is None:
                return None
            return state.to_dict()

    def get_queue(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Get queued messages for a conversation."""
        with self._lock:
            queue = self._queues.get(conversation_id, [])
            return [
                {
                    "id": msg.id,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in queue
            ]

    def get_global_status(self) -> Dict[str, Any]:
        """Get global system status including feature flags and pool status."""
        with self._lock:
            active = []
            for conv_id, state in self._consultations.items():
                if state.status not in (ConsultationStatus.COMPLETE, ConsultationStatus.FAILED):
                    active.append({
                        "conversation_id": conv_id,
                        "consultation_id": state.id,
                        "status": state.status.value,
                        "started_at": state.created_at.isoformat(),
                    })

            # Count available workers
            active_count = len(active)

            # Count total queued messages across all conversations
            total_queued = sum(len(q) for q in self._queues.values())

            return {
                # Legacy fields for backward compatibility
                "active": active,
                "pool_size": CONSULTATION_POOL_SIZE,
                "pool_available": max(0, CONSULTATION_POOL_SIZE - active_count),
                # New structured fields
                "consultations": {
                    "active": active,
                    "active_count": active_count,
                    "queued_count": total_queued,
                },
                "pool": {
                    "size": CONSULTATION_POOL_SIZE,
                    "available": max(0, CONSULTATION_POOL_SIZE - active_count),
                    "utilization": round(active_count / CONSULTATION_POOL_SIZE, 2) if CONSULTATION_POOL_SIZE > 0 else 0,
                },
                "feature_flags": {
                    "negative_mode": NEGATIVE_MODE_ENABLED,
                    "deliberation": DELIBERATION_ENABLED,
                    "interactive_mode": INTERACTIVE_MODE_ENABLED,
                    "prompt_refinement": PROMPT_REFINEMENT_ENABLED,
                    "pairwise_ranking": PAIRWISE_RANKING_ENABLED,
                },
            }

    def cancel_consultation(self, conversation_id: str) -> bool:
        """Cancel consultation and clear queue for a conversation."""
        with self._lock:
            # Clear queue
            self._queues.pop(conversation_id, None)

            # Mark active consultation as failed
            state = self._consultations.get(conversation_id)
            if state and state.status not in (ConsultationStatus.COMPLETE, ConsultationStatus.FAILED):
                state.cancel_event.set()
                state.status = ConsultationStatus.FAILED
                state.error = "Cancelled"
                state.updated_at = datetime.utcnow()
                return True
            return False

    def _update_status(self, state: ConsultationState, status: ConsultationStatus):
        """Update consultation status (thread-safe)."""
        with self._lock:
            if state.cancel_event.is_set():
                return
            state.status = status
            state.updated_at = datetime.utcnow()

    def _run_consultation(self, state: ConsultationState):
        """Run consultation in worker thread."""
        # Import here to avoid circular imports
        from . import storage
        from .trace import TraceCollector

        conversation_id = state.conversation_id
        assistant_index: Optional[int] = None
        trace: Optional[TraceCollector] = None

        def persist_failure(error_message: str) -> None:
            if assistant_index is None:
                return

            metadata = {
                **(state.metadata or {}),
                "status": ConsultationStatus.FAILED.value,
                "error": error_message,
            }
            updates: Dict[str, Any] = {
                "metadata": metadata,
            }

            if state.stage1_results is not None:
                updates["stage1"] = state.stage1_results
            if state.negative_context is not None:
                updates["negative"] = state.negative_context
            if state.stage2_results is not None:
                updates["stage2"] = state.stage2_results
            if state.stage3_result is not None:
                updates["stage3"] = state.stage3_result
            if trace is not None:
                trace.add_event("flow_failed", error=error_message)
                trace.finish()
                updates["trace"] = trace.to_dict()

            try:
                storage.update_assistant_message(
                    conversation_id,
                    assistant_index,
                    updates,
                )
            except Exception as exc:
                print(f"Warning: Failed to persist consultation failure: {exc}")

        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                content = state.message_content
                negative_prompt = state.negative_prompt

                # Check if conversation exists
                conversation = storage.get_conversation(conversation_id)
                if conversation is None:
                    raise ValueError(f"Conversation {conversation_id} not found")

                is_first_message = len(conversation["messages"]) == 0

                trace = TraceCollector()
                trace.add_event(
                    "flow_start",
                    conversation_id=conversation_id,
                    user_message=content,
                    negative_prompt=negative_prompt,
                    async_mode=True,
                )

                # Add user message
                storage.add_user_message(conversation_id, content)

                # Add assistant placeholder for incremental updates
                assistant_index = storage.add_assistant_placeholder(
                    conversation_id,
                    metadata={"status": "pending"},
                )

                from .config import DELIBERATION_ENABLED, NEGATIVE_MODE_ENABLED, PROMPT_REFINEMENT_ENABLED, INTERACTIVE_TIMEOUT_SECONDS
                from .langgraph_flow import FlowCallbacks, FlowContext, run_council_graph

                status_map = {
                    "stage1": ConsultationStatus.STAGE1,
                    "negative": ConsultationStatus.NEGATIVE,
                    "stage2": ConsultationStatus.STAGE2,
                    "deliberation": ConsultationStatus.DELIBERATION,
                    "stage3": ConsultationStatus.STAGE3,
                }

                async def set_status(stage: str) -> None:
                    mapped = status_map.get(stage)
                    if mapped:
                        self._update_status(state, mapped)
                    
                    try:
                        storage.update_assistant_message(
                            conversation_id, assistant_index, {"metadata": {"status": stage}}
                        )
                    except Exception:
                        pass

                async def update_message(updates: Dict[str, Any]) -> None:
                    with self._lock:
                        if "stage1" in updates:
                            state.stage1_results = updates["stage1"]
                        if "negative" in updates:
                            state.negative_context = updates["negative"]
                        if "stage2" in updates:
                            state.stage2_results = updates["stage2"]
                        if "stage3" in updates:
                            state.stage3_result = updates["stage3"]
                        metadata_update = updates.get("metadata")
                        if metadata_update is not None:
                            state.metadata = {
                                **(state.metadata or {}),
                                **metadata_update,
                            }
                    
                    try:
                        storage.update_assistant_message(
                            conversation_id, assistant_index, dict(updates)
                        )
                    except Exception as exc:
                        print(f"Warning: Failed to update storage: {exc}")

                callbacks = FlowCallbacks(
                    update_message=update_message,
                    set_status=set_status,
                )
                
                with self._lock:
                    state.loop = loop
                    if INTERACTIVE_MODE_ENABLED:
                        state.session_id = str(uuid.uuid4())
                        state.interactive_queue = asyncio.Queue()
                        self._interactive_sessions[state.session_id] = state
                
                context = FlowContext(
                    trace=trace,
                    callbacks=callbacks,
                    negative_enabled=NEGATIVE_MODE_ENABLED,
                    deliberation_enabled=DELIBERATION_ENABLED,
                    interactive_enabled=INTERACTIVE_MODE_ENABLED,
                    interactive_queue=state.interactive_queue if INTERACTIVE_MODE_ENABLED else None,
                    session_id=state.session_id if INTERACTIVE_MODE_ENABLED else None,
                    interactive_timeout_seconds=INTERACTIVE_TIMEOUT_SECONDS,
                    prompt_refinement_enabled=PROMPT_REFINEMENT_ENABLED,
                    conversation_id=conversation_id,
                    is_first_message=is_first_message,
                    cancel_requested=state.cancel_event.is_set,
                )

                final_state = loop.run_until_complete(
                    run_council_graph(content, negative_prompt, context)
                )

                error_message = final_state.get("error")
                stage3_result = final_state.get("stage3")
                if error_message and not stage3_result:
                    with self._lock:
                        state.error = error_message
                        state.status = ConsultationStatus.FAILED
                        state.metadata = {
                            **(state.metadata or {}),
                            "status": ConsultationStatus.FAILED.value,
                            "error": error_message,
                        }
                        state.updated_at = datetime.utcnow()
                    persist_failure(error_message)
                    return

                stage1_results = final_state.get("stage1") or []
                negative_context = final_state.get("negative")
                stage2_results = final_state.get("stage2") or []
                stage3_result = stage3_result or {}
                final_metadata = {
                    **(state.metadata or {}),
                    **(final_state.get("metadata") or {}),
                    "status": ConsultationStatus.COMPLETE.value,
                }

                trace.add_event("flow_complete", stage3_model=stage3_result.get("model"))
                trace.finish()
                trace_payload = trace.to_dict()

                # Finalize in storage
                storage.update_assistant_message(
                    conversation_id,
                    assistant_index,
                    {
                        "stage1": stage1_results,
                        "negative": negative_context,
                        "stage2": stage2_results,
                        "stage3": stage3_result,
                        "metadata": final_metadata,
                        "trace": trace_payload,
                    }
                )
                with self._lock:
                    state.metadata = final_metadata

                self._update_status(state, ConsultationStatus.COMPLETE)
                
                if state.session_id:
                    with self._lock:
                        self._interactive_sessions.pop(state.session_id, None)

            finally:
                loop.close()

        except Exception as e:
            import traceback
            traceback.print_exc()
            with self._lock:
                state.error = str(e)
                state.status = ConsultationStatus.FAILED
                state.metadata = {
                    **(state.metadata or {}),
                    "status": ConsultationStatus.FAILED.value,
                    "error": str(e),
                }
                state.updated_at = datetime.utcnow()
                if state.session_id:
                    self._interactive_sessions.pop(state.session_id, None)
            persist_failure(str(e))

        finally:
            self._on_consultation_complete(state.conversation_id)

    def _on_consultation_complete(self, conversation_id: str):
        """Handle consultation completion - process next queued message."""
        with self._lock:
            # Clean up future reference
            self._futures.pop(conversation_id, None)

            # Check for queued messages
            queue = self._queues.get(conversation_id, [])
            if queue:
                next_msg = queue.pop(0)
                if not queue:
                    del self._queues[conversation_id]

                # Start next consultation
                state = ConsultationState(
                    id=next_msg.id,
                    conversation_id=conversation_id,
                    message_content=next_msg.content,
                    negative_prompt=next_msg.negative_prompt,
                )
                self._consultations[conversation_id] = state

                # Submit to thread pool (outside lock to avoid deadlock)
                future = self._executor.submit(self._run_consultation, state)
                self._futures[conversation_id] = future

    def shutdown(self, wait: bool = True):
        """Shutdown the thread pool."""
        self._executor.shutdown(wait=wait)


# Global instance
consultation_manager = ConsultationManager()
