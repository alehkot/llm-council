"""Async consultation manager with durable recovery support."""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, cast

from . import consultation_journal, storage
from .config import (
    CONSULTATION_POOL_SIZE,
    DELIBERATION_ENABLED,
    INTERACTIVE_MODE_ENABLED,
    INTERACTIVE_TIMEOUT_SECONDS,
    NEGATIVE_MODE_ENABLED,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
)
from .interactive import deserialize_prompt_responses


class ConsultationStatus(str, Enum):
    """Status of a consultation."""

    PENDING = "pending"
    INTERACTIVE = "interactive"
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
    assistant_index: Optional[int] = None
    stage1_results: Optional[List[Dict[str, Any]]] = None
    stage2_results: Optional[List[Dict[str, Any]]] = None
    stage3_result: Optional[Dict[str, Any]] = None
    negative_context: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    resume_from_storage: bool = False
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
            "assistant_index": self.assistant_index,
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

    Keeps queue state in memory during runtime and journals queued/in-flight work
    to disk so server restarts can resume from the last durable stage boundary.
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

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._executor = ThreadPoolExecutor(
            max_workers=CONSULTATION_POOL_SIZE,
            thread_name_prefix="consultation-",
        )
        self._lock = threading.Lock()
        self._consultations: Dict[str, ConsultationState] = {}
        self._queues: Dict[str, List[QueuedMessage]] = {}
        self._futures: Dict[str, Future] = {}
        self._interactive_sessions: Dict[str, ConsultationState] = {}
        self._restored = False

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> datetime:
        if not value:
            return datetime.utcnow()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.utcnow()

    @staticmethod
    def _coerce_status(value: Optional[str]) -> ConsultationStatus:
        try:
            return ConsultationStatus(value or ConsultationStatus.PENDING.value)
        except ValueError:
            return ConsultationStatus.PENDING

    @staticmethod
    def _build_resume_metadata(
        consultation_id: str,
        content: str,
        negative_prompt: Optional[str],
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing_resume = dict((existing or {}).get("resume") or {})
        resume = {
            **existing_resume,
            "user_query": existing_resume.get("user_query")
            or (existing or {}).get("user_query")
            or content,
            "negative_prompt": negative_prompt,
            "collected_responses": dict(
                existing_resume.get("collected_responses") or {}
            ),
            "updated_at": datetime.utcnow().isoformat(),
        }
        return {
            "consultation_id": consultation_id,
            "status": (existing or {}).get("status")
            or ConsultationStatus.PENDING.value,
            "resume": resume,
        }

    @staticmethod
    def _find_persisted_assistant_index(
        conversation: Dict[str, Any],
        consultation_id: str,
    ) -> Optional[int]:
        messages = conversation.get("messages", [])
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("role") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            if metadata.get("consultation_id") == consultation_id:
                return index
        return None

    def _ensure_assistant_placeholder(
        self,
        consultation_id: str,
        conversation_id: str,
        content: str,
        negative_prompt: Optional[str],
    ) -> int:
        """Persist the user message and assistant placeholder for a job."""

        conversation = storage.get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        existing_index = self._find_persisted_assistant_index(
            conversation, consultation_id
        )
        if existing_index is not None:
            return existing_index

        messages = conversation.get("messages", [])
        should_add_user_message = not (
            messages
            and messages[-1].get("role") == "user"
            and messages[-1].get("content") == content
        )
        if should_add_user_message:
            storage.add_user_message(conversation_id, content)

        metadata = self._build_resume_metadata(
            consultation_id,
            content,
            negative_prompt,
            existing={"status": ConsultationStatus.PENDING.value},
        )
        assistant_index = storage.add_assistant_placeholder(
            conversation_id,
            metadata=metadata,
        )
        return assistant_index

    def _load_assistant_message(
        self,
        conversation_id: str,
        assistant_index: int,
    ) -> Optional[Dict[str, Any]]:
        conversation = storage.get_conversation(conversation_id)
        if conversation is None:
            return None
        messages = conversation.get("messages", [])
        if assistant_index < 0 or assistant_index >= len(messages):
            return None
        message = messages[assistant_index]
        if message.get("role") != "assistant":
            return None
        return message

    @staticmethod
    def _resolve_effective_user_query(
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        metadata = metadata or {}
        resume = metadata.get("resume") or {}
        if resume.get("user_query"):
            return str(resume["user_query"])

        prompt_refinement = metadata.get("prompt_refinement") or {}
        if prompt_refinement.get("decision") == "use_refined":
            refined_prompt = prompt_refinement.get("refined_prompt")
            if refined_prompt:
                return str(refined_prompt)

        return content

    @staticmethod
    def _build_initial_state(
        content: str,
        negative_prompt: Optional[str],
        assistant_message: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from .council import build_label_mapping, build_responses_for_synthesis

        message = assistant_message or {}
        metadata = dict(message.get("metadata") or {})
        effective_user_query = ConsultationManager._resolve_effective_user_query(
            content, metadata
        )
        resume = metadata.get("resume") or {}

        initial_state: Dict[str, Any] = {
            "user_query": effective_user_query,
            "negative_prompt": resume.get("negative_prompt", negative_prompt),
            "negative": message.get("negative")
            or {
                "enabled": False,
                "prompt": resume.get("negative_prompt", negative_prompt),
                "evaluations": [],
            },
            "metadata": metadata,
        }

        stage1_results = message.get("stage1")
        if stage1_results is not None:
            initial_state["stage1"] = stage1_results

        label_to_model = metadata.get("label_to_model")
        if stage1_results is not None:
            if not label_to_model:
                labels, label_to_model = build_label_mapping(stage1_results)
            else:
                labels = list(label_to_model.keys())
            initial_state["labels"] = labels
            initial_state["label_to_model"] = label_to_model

        if metadata.get("aggregate_gap_scores") is not None:
            initial_state["aggregate_gap_scores"] = metadata.get("aggregate_gap_scores")
        if message.get("stage2") is not None:
            initial_state["stage2"] = message.get("stage2")
        if metadata.get("aggregate_rankings") is not None:
            initial_state["aggregate_rankings"] = metadata.get("aggregate_rankings")
        if metadata.get("invalid_rankings") is not None:
            initial_state["invalid_rankings"] = metadata.get("invalid_rankings")
        if message.get("stage3") is not None:
            initial_state["stage3"] = message.get("stage3")

        deliberation = metadata.get("deliberation") or {}
        results = deliberation.get("results") or []
        if results:
            initial_state["responses_for_synthesis"] = build_responses_for_synthesis(
                stage1_results or [],
                metadata,
            )

        return initial_state

    @staticmethod
    def _conversation_title(conversation_id: str) -> str:
        conversation = storage.get_conversation(conversation_id)
        if conversation is None:
            return "New Conversation"
        return conversation.get("title", "New Conversation")

    @staticmethod
    def _is_first_message(conversation_id: str, assistant_index: Optional[int]) -> bool:
        if assistant_index is None:
            return False
        conversation = storage.get_conversation(conversation_id)
        if conversation is None:
            return False
        prior_messages = conversation.get("messages", [])[:assistant_index]
        prior_assistants = [
            msg for msg in prior_messages if msg.get("role") == "assistant"
        ]
        prior_users = [msg for msg in prior_messages if msg.get("role") == "user"]
        return len(prior_assistants) == 0 and len(prior_users) == 1

    def _start_state_locked(self, state: ConsultationState) -> None:
        self._consultations[state.conversation_id] = state
        future = self._executor.submit(self._run_consultation, state)
        self._futures[state.conversation_id] = future

    def _create_started_state(
        self,
        consultation_id: str,
        conversation_id: str,
        content: str,
        negative_prompt: Optional[str],
        resume_from_storage: bool,
        assistant_index: Optional[int] = None,
        created_at: Optional[str] = None,
        status: Optional[str] = None,
    ) -> ConsultationState:
        if assistant_index is None:
            assistant_index = self._ensure_assistant_placeholder(
                consultation_id,
                conversation_id,
                content,
                negative_prompt,
            )
        else:
            existing_message = self._load_assistant_message(
                conversation_id, assistant_index
            )
            if existing_message is None:
                assistant_index = self._ensure_assistant_placeholder(
                    consultation_id,
                    conversation_id,
                    content,
                    negative_prompt,
                )
            else:
                metadata = {
                    **(existing_message.get("metadata") or {}),
                    **self._build_resume_metadata(
                        consultation_id,
                        content,
                        negative_prompt,
                        existing={
                            **(existing_message.get("metadata") or {}),
                            "status": status
                            or (existing_message.get("metadata") or {}).get("status"),
                            "resume": (existing_message.get("metadata") or {}).get(
                                "resume"
                            ),
                        },
                    ),
                }
                storage.update_assistant_message(
                    conversation_id,
                    assistant_index,
                    {"metadata": metadata},
                )
        consultation_journal.update_job(
            consultation_id,
            {
                "assistant_index": assistant_index,
                "status": status or ConsultationStatus.PENDING.value,
            },
        )

        assistant_message = self._load_assistant_message(
            conversation_id, assistant_index
        )
        metadata = dict((assistant_message or {}).get("metadata") or {})
        state = ConsultationState(
            id=consultation_id,
            conversation_id=conversation_id,
            message_content=content,
            negative_prompt=negative_prompt,
            status=self._coerce_status(metadata.get("status") or status),
            assistant_index=assistant_index,
            stage1_results=(assistant_message or {}).get("stage1"),
            negative_context=(assistant_message or {}).get("negative"),
            stage2_results=(assistant_message or {}).get("stage2"),
            stage3_result=(assistant_message or {}).get("stage3"),
            metadata=metadata,
            created_at=self._parse_datetime(created_at),
            updated_at=datetime.utcnow(),
            resume_from_storage=resume_from_storage,
        )
        return state

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
            active = self._consultations.get(conversation_id)
            if active and active.status not in (
                ConsultationStatus.COMPLETE,
                ConsultationStatus.FAILED,
            ):
                consultation_id = str(uuid.uuid4())
                queued = QueuedMessage(
                    id=consultation_id,
                    content=content,
                    negative_prompt=negative_prompt,
                )
                consultation_journal.create_job(
                    consultation_id,
                    conversation_id,
                    content,
                    negative_prompt,
                    status="queued",
                )
                self._queues.setdefault(conversation_id, []).append(queued)
                return {
                    "consultation_id": consultation_id,
                    "status": "queued",
                    "queue_position": len(self._queues[conversation_id]),
                }

            consultation_id = str(uuid.uuid4())
            consultation_journal.create_job(
                consultation_id,
                conversation_id,
                content,
                negative_prompt,
                status=ConsultationStatus.PENDING.value,
            )
            state = self._create_started_state(
                consultation_id,
                conversation_id,
                content,
                negative_prompt,
                resume_from_storage=False,
            )
            self._start_state_locked(state)
            return {
                "consultation_id": consultation_id,
                "status": "started",
            }

    def submit_interactive_response(
        self, session_id: str, prompt_response: Any
    ) -> None:
        """Submit a response to an interactive prompt in the worker thread's event loop."""

        with self._lock:
            state = self._interactive_sessions.get(session_id)
            if not state or not state.loop or not state.interactive_queue:
                raise ValueError("Interactive session not found or inactive")

            state.loop.call_soon_threadsafe(
                state.interactive_queue.put_nowait,
                prompt_response,
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
                if state.status not in (
                    ConsultationStatus.COMPLETE,
                    ConsultationStatus.FAILED,
                ):
                    active.append(
                        {
                            "conversation_id": conv_id,
                            "consultation_id": state.id,
                            "status": state.status.value,
                            "started_at": state.created_at.isoformat(),
                        }
                    )

            active_count = len(active)
            total_queued = sum(len(q) for q in self._queues.values())

            return {
                "active": active,
                "pool_size": CONSULTATION_POOL_SIZE,
                "pool_available": max(0, CONSULTATION_POOL_SIZE - active_count),
                "consultations": {
                    "active": active,
                    "active_count": active_count,
                    "queued_count": total_queued,
                },
                "pool": {
                    "size": CONSULTATION_POOL_SIZE,
                    "available": max(0, CONSULTATION_POOL_SIZE - active_count),
                    "utilization": round(active_count / CONSULTATION_POOL_SIZE, 2)
                    if CONSULTATION_POOL_SIZE > 0
                    else 0,
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
            queue = self._queues.pop(conversation_id, [])
            for queued in queue:
                consultation_journal.delete_job(queued.id)

            state = self._consultations.get(conversation_id)
            if state and state.status not in (
                ConsultationStatus.COMPLETE,
                ConsultationStatus.FAILED,
            ):
                state.cancel_event.set()
                state.status = ConsultationStatus.FAILED
                state.error = "Cancelled"
                state.updated_at = datetime.utcnow()
                consultation_journal.update_job(
                    state.id,
                    {"status": ConsultationStatus.FAILED.value},
                )
                if state.assistant_index is not None:
                    try:
                        storage.update_assistant_message(
                            conversation_id,
                            state.assistant_index,
                            {
                                "metadata": {
                                    "status": ConsultationStatus.FAILED.value,
                                    "error": "Cancelled",
                                    "interactive_prompt": None,
                                }
                            },
                        )
                    except Exception as exc:
                        print(
                            f"Warning: Failed to persist consultation cancellation: {exc}"
                        )
                return True
            return False

    def cancel_all_consultations(self) -> int:
        """Cancel all active and queued consultations and clear durable jobs."""

        cancelled = 0
        with self._lock:
            queued_ids = [
                queued.id for queue in self._queues.values() for queued in queue
            ]
            cancelled += len(queued_ids)
            self._queues.clear()

            for state in self._consultations.values():
                if state.status in (
                    ConsultationStatus.COMPLETE,
                    ConsultationStatus.FAILED,
                ):
                    continue
                state.cancel_event.set()
                state.status = ConsultationStatus.FAILED
                state.error = "Cancelled"
                state.updated_at = datetime.utcnow()
                cancelled += 1

        consultation_journal.clear_jobs()
        return cancelled

    def _update_status(
        self, state: ConsultationState, status: ConsultationStatus
    ) -> None:
        """Update consultation status and durable journal state."""

        with self._lock:
            if state.cancel_event.is_set() and status not in (
                ConsultationStatus.COMPLETE,
                ConsultationStatus.FAILED,
            ):
                return
            state.status = status
            state.updated_at = datetime.utcnow()

        consultation_journal.update_job(
            state.id,
            {
                "status": status.value,
                "assistant_index": state.assistant_index,
            },
        )

    def _run_consultation(self, state: ConsultationState) -> None:
        """Run consultation in a worker thread."""

        from .langgraph_flow import (
            CouncilState,
            FlowCallbacks,
            FlowContext,
            run_council_graph,
        )
        from .trace import TraceCollector

        conversation_id = state.conversation_id
        trace: Optional[TraceCollector] = None

        def persist_failure(error_message: str) -> None:
            if state.assistant_index is None:
                return

            metadata = {
                **(state.metadata or {}),
                "status": ConsultationStatus.FAILED.value,
                "error": error_message,
                "interactive_prompt": None,
            }
            updates: Dict[str, Any] = {"metadata": metadata}

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
                    state.assistant_index,
                    updates,
                )
            except Exception as exc:
                print(f"Warning: Failed to persist consultation failure: {exc}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            if state.assistant_index is None:
                state.assistant_index = self._ensure_assistant_placeholder(
                    state.id,
                    state.conversation_id,
                    state.message_content,
                    state.negative_prompt,
                )
                consultation_journal.update_job(
                    state.id,
                    {
                        "assistant_index": state.assistant_index,
                        "status": state.status.value,
                    },
                )

            conversation = storage.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")

            assistant_message = self._load_assistant_message(
                conversation_id, state.assistant_index
            )
            if assistant_message is None:
                raise ValueError(
                    f"Assistant placeholder {state.assistant_index} not found for {conversation_id}",
                )

            state.metadata = dict(assistant_message.get("metadata") or {})
            state.stage1_results = assistant_message.get("stage1")
            state.negative_context = assistant_message.get("negative")
            state.stage2_results = assistant_message.get("stage2")
            state.stage3_result = assistant_message.get("stage3")

            trace = TraceCollector()
            if state.resume_from_storage:
                trace.add_event(
                    "flow_resume",
                    conversation_id=conversation_id,
                    consultation_id=state.id,
                    assistant_index=state.assistant_index,
                    status=(state.metadata or {}).get("status"),
                )
            else:
                trace.add_event(
                    "flow_start",
                    conversation_id=conversation_id,
                    consultation_id=state.id,
                    user_message_length=len(state.message_content or ""),
                    negative_prompt_length=len(state.negative_prompt or ""),
                    async_mode=True,
                )

            status_map = {
                "interactive": ConsultationStatus.INTERACTIVE,
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

                metadata_update: Dict[str, Any] = {"status": stage}
                if stage != ConsultationStatus.INTERACTIVE.value:
                    metadata_update["interactive_prompt"] = None

                if state.assistant_index is None:
                    return
                try:
                    storage.update_assistant_message(
                        conversation_id,
                        state.assistant_index,
                        {"metadata": metadata_update},
                    )
                except Exception as exc:
                    print(f"Warning: Failed to persist consultation status: {exc}")

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

                if state.assistant_index is None:
                    return
                try:
                    storage.update_assistant_message(
                        conversation_id,
                        state.assistant_index,
                        dict(updates),
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

            resume_payload = dict((state.metadata or {}).get("resume") or {})
            restored_responses = deserialize_prompt_responses(
                resume_payload.get("collected_responses"),
            )
            context = FlowContext(
                trace=trace,
                callbacks=callbacks,
                negative_enabled=NEGATIVE_MODE_ENABLED,
                deliberation_enabled=DELIBERATION_ENABLED,
                interactive_enabled=INTERACTIVE_MODE_ENABLED,
                interactive_queue=state.interactive_queue
                if INTERACTIVE_MODE_ENABLED
                else None,
                session_id=state.session_id if INTERACTIVE_MODE_ENABLED else None,
                interactive_timeout_seconds=INTERACTIVE_TIMEOUT_SECONDS,
                prompt_refinement_enabled=PROMPT_REFINEMENT_ENABLED,
                collected_responses=restored_responses,
                conversation_id=conversation_id,
                conversation_title=conversation.get("title", "New Conversation"),
                is_first_message=self._is_first_message(
                    conversation_id, state.assistant_index
                ),
                is_resume=state.resume_from_storage,
                cancel_requested=state.cancel_event.is_set,
                resume_metadata=resume_payload,
            )
            if restored_responses:
                context.refresh_interactive_config()

            initial_state = self._build_initial_state(
                state.message_content,
                state.negative_prompt,
                assistant_message,
            )
            final_state = loop.run_until_complete(
                run_council_graph(
                    state.message_content,
                    state.negative_prompt,
                    context,
                    initial_state=cast(CouncilState, initial_state),
                )
            )

            if state.cancel_event.is_set():
                with self._lock:
                    state.error = "Cancelled"
                    state.status = ConsultationStatus.FAILED
                    state.metadata = {
                        **(state.metadata or {}),
                        "status": ConsultationStatus.FAILED.value,
                        "error": "Cancelled",
                        "interactive_prompt": None,
                    }
                    state.updated_at = datetime.utcnow()
                persist_failure("Cancelled")
                consultation_journal.delete_job(state.id)
                return

            error_message = final_state.get("error")
            stage3_result = final_state.get("stage3")
            if error_message:
                with self._lock:
                    state.error = error_message
                    state.status = ConsultationStatus.FAILED
                    state.metadata = {
                        **(state.metadata or {}),
                        "status": ConsultationStatus.FAILED.value,
                        "error": error_message,
                        "interactive_prompt": None,
                    }
                    state.updated_at = datetime.utcnow()
                persist_failure(error_message)
                consultation_journal.delete_job(state.id)
                return

            stage1_results = final_state.get("stage1") or []
            negative_context = final_state.get("negative")
            stage2_results = final_state.get("stage2") or []
            stage3_result = stage3_result or {}
            final_metadata = {
                **(state.metadata or {}),
                **(final_state.get("metadata") or {}),
                "status": ConsultationStatus.COMPLETE.value,
                "consultation_id": state.id,
                "interactive_prompt": None,
            }

            trace.add_event("flow_complete", stage3_model=stage3_result.get("model"))
            trace.finish()
            trace_payload = trace.to_dict()

            storage.update_assistant_message(
                conversation_id,
                state.assistant_index,
                {
                    "stage1": stage1_results,
                    "negative": negative_context,
                    "stage2": stage2_results,
                    "stage3": stage3_result,
                    "metadata": final_metadata,
                    "trace": trace_payload,
                },
            )
            with self._lock:
                state.metadata = final_metadata
                state.stage1_results = stage1_results
                state.negative_context = negative_context
                state.stage2_results = stage2_results
                state.stage3_result = stage3_result

            self._update_status(state, ConsultationStatus.COMPLETE)
            consultation_journal.delete_job(state.id)

        except Exception as exc:
            import traceback

            traceback.print_exc()
            with self._lock:
                state.error = str(exc)
                state.status = ConsultationStatus.FAILED
                state.metadata = {
                    **(state.metadata or {}),
                    "status": ConsultationStatus.FAILED.value,
                    "error": str(exc),
                    "interactive_prompt": None,
                }
                state.updated_at = datetime.utcnow()
            persist_failure(str(exc))
            consultation_journal.delete_job(state.id)

        finally:
            with self._lock:
                if state.session_id:
                    self._interactive_sessions.pop(state.session_id, None)
                state.loop = None
                state.interactive_queue = None
                state.session_id = None

            loop.close()
            self._on_consultation_complete(state)

    def _job_is_already_terminal(self, job: Dict[str, Any]) -> bool:
        assistant_index = job.get("assistant_index")
        if assistant_index is None:
            return job.get("status") in {
                ConsultationStatus.COMPLETE.value,
                ConsultationStatus.FAILED.value,
            }

        message = self._load_assistant_message(
            job["conversation_id"], int(assistant_index)
        )
        if message is None:
            return False
        metadata = message.get("metadata") or {}
        if message.get("stage3") is not None:
            return True
        return metadata.get("status") in {
            ConsultationStatus.COMPLETE.value,
            ConsultationStatus.FAILED.value,
        }

    def restore_incomplete_consultations(self) -> None:
        """Restore queued and in-flight consultations from durable storage."""

        with self._lock:
            if self._restored:
                return
            self._restored = True

        jobs = sorted(
            consultation_journal.list_jobs(),
            key=lambda job: (
                job.get("conversation_id", ""),
                job.get("created_at", ""),
                job.get("consultation_id", ""),
            ),
        )

        to_start: List[ConsultationState] = []
        recovered_conversations: set[str] = set()

        with self._lock:
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for job in jobs:
                grouped.setdefault(job.get("conversation_id", ""), []).append(job)

            for conversation_id, group in grouped.items():
                if storage.get_conversation(conversation_id) is None:
                    # Missing and unreadable files share this result. Keep the journal
                    # for repair, but do not let an unavailable conversation block startup.
                    print(
                        f"Warning: Skipping recovery for unavailable conversation "
                        f"{conversation_id}; consultation journals retained"
                    )
                    continue
                active_job = None
                queued_jobs: List[Dict[str, Any]] = []
                for job in group:
                    if self._job_is_already_terminal(job):
                        consultation_journal.delete_job(job["consultation_id"])
                        continue
                    if job.get("status") == "queued":
                        queued_jobs.append(job)
                    elif active_job is None:
                        active_job = job
                    else:
                        queued_jobs.append(job)

                if active_job is not None:
                    state = self._create_started_state(
                        active_job["consultation_id"],
                        conversation_id,
                        active_job.get("content", ""),
                        active_job.get("negative_prompt"),
                        resume_from_storage=True,
                        assistant_index=active_job.get("assistant_index"),
                        created_at=active_job.get("created_at"),
                        status=active_job.get("status"),
                    )
                    self._consultations[conversation_id] = state
                    to_start.append(state)
                    recovered_conversations.add(conversation_id)

                if queued_jobs:
                    self._queues[conversation_id] = [
                        QueuedMessage(
                            id=job["consultation_id"],
                            content=job.get("content", ""),
                            negative_prompt=job.get("negative_prompt"),
                            created_at=self._parse_datetime(job.get("created_at")),
                        )
                        for job in queued_jobs
                    ]

                if active_job is None and queued_jobs:
                    next_msg = self._queues[conversation_id].pop(0)
                    if not self._queues[conversation_id]:
                        del self._queues[conversation_id]
                    next_state = self._create_started_state(
                        next_msg.id,
                        conversation_id,
                        next_msg.content,
                        next_msg.negative_prompt,
                        resume_from_storage=True,
                    )
                    self._consultations[conversation_id] = next_state
                    to_start.append(next_state)
                    recovered_conversations.add(conversation_id)

        for candidate in storage.list_incomplete_assistant_messages():
            conversation_id = candidate["conversation_id"]
            if conversation_id in recovered_conversations:
                continue

            consultation_id = candidate.get("consultation_id") or str(uuid.uuid4())
            consultation_journal.create_job(
                consultation_id,
                conversation_id,
                candidate["content"],
                candidate.get("negative_prompt"),
                status=candidate.get("status") or ConsultationStatus.PENDING.value,
                assistant_index=candidate["assistant_index"],
            )
            state = self._create_started_state(
                consultation_id,
                conversation_id,
                candidate["content"],
                candidate.get("negative_prompt"),
                resume_from_storage=True,
                assistant_index=candidate["assistant_index"],
                status=candidate.get("status"),
            )
            with self._lock:
                self._consultations[conversation_id] = state
            to_start.append(state)

        with self._lock:
            for state in to_start:
                if state.conversation_id in self._futures:
                    continue
                future = self._executor.submit(self._run_consultation, state)
                self._futures[state.conversation_id] = future

    def _on_consultation_complete(self, state: ConsultationState) -> None:
        """Handle consultation completion and process the next queued message."""

        conversation_id = state.conversation_id
        with self._lock:
            current_state = self._consultations.get(conversation_id)
            if current_state is not state:
                return

            self._futures.pop(conversation_id, None)

            queue = self._queues.get(conversation_id, [])
            if not queue:
                return

            next_msg = queue.pop(0)
            if not queue:
                del self._queues[conversation_id]

            state = self._create_started_state(
                next_msg.id,
                conversation_id,
                next_msg.content,
                next_msg.negative_prompt,
                resume_from_storage=False,
            )
            self._consultations[conversation_id] = state
            future = self._executor.submit(self._run_consultation, state)
            self._futures[conversation_id] = future

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the thread pool."""

        self._executor.shutdown(wait=wait)


consultation_manager = ConsultationManager()
