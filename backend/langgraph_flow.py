"""LangGraph-based orchestration for the council workflow."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import StateGraph, START, END

from .config import (
    CHAIRMAN_MODEL,
    COUNCIL_MODELS,
    DELIBERATION_ENABLED,
    INTERACTIVE_MODE_ENABLED,
    INTERACTIVE_TIMEOUT_SECONDS,
    NEGATIVE_MODE_ENABLED,
    PAIRWISE_RANKING_ENABLED,
    PROMPT_REFINEMENT_ENABLED,
)
from . import storage
from .council import (
    build_label_mapping,
    build_responses_for_synthesis,
    calculate_aggregate_gap_scores,
    calculate_aggregate_rankings,
    calculate_pairwise_rankings,
    generate_prompt_refinement,
    generate_conversation_title,
    generate_negative_prompt,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    stage_deliberation,
    stage_negative_evaluations,
)
from .interactive import (
    get_pre_query_prompt,
    get_prompt_refinement_prompt,
    get_response_review_prompt,
    get_synthesis_prefs_prompt,
    InteractiveConfig,
    PromptResponse,
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


class CouncilState(TypedDict, total=False):
    """Mutable state for the LangGraph council flow."""

    user_query: str
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
        self.interactive_config = InteractiveConfig.from_responses(self.collected_responses)

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

        stage_state.update({
            "counts": counts,
            "total": total,
            "completed": completed,
            "failed": counts.get("failed", 0),
            "updated_at": now,
        })

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
        await self.emit_event({
            "type": "stage_progress",
            "stage": stage,
            "model": model,
            "status": status,
            "progress": self.stage_progress.get(stage),
        })


def _combine_parts(parts: List[str]) -> Optional[str]:
    filtered = [part.strip() for part in parts if part and part.strip()]
    return " ".join(filtered) if filtered else None


def _text_length(value: Optional[str]) -> int:
    return len(value or "")


def _interactive_modifier(context: FlowContext) -> Optional[str]:
    if not _prompt_refinements_active(context) or not context.interactive_config:
        return None
    modifier = context.interactive_config.get_system_prompt_modifier()
    return modifier or None


def _format_synthesis_prefs(config: InteractiveConfig) -> Optional[str]:
    parts = []
    if config.synthesis_mode:
        parts.append(f"Synthesis preference: {config.synthesis_mode}.")
    if config.focus_areas:
        parts.append(f"Focus areas: {config.focus_areas}.")
    return _combine_parts(parts)


def _stage1_system_prompt(context: FlowContext) -> Optional[str]:
    return _interactive_modifier(context)


def _stage2_prompt_modifier(context: FlowContext) -> Optional[str]:
    if not _prompt_refinements_active(context):
        return None
    return _combine_parts([
        _DECISION_SUPPORT_RANKING_GUIDANCE,
        _interactive_modifier(context) or "",
    ])


def _stage3_prompt_modifier(context: FlowContext) -> Optional[str]:
    if not _prompt_refinements_active(context):
        return None
    synthesis_prefs = (
        _format_synthesis_prefs(context.interactive_config)
        if context.interactive_config
        else None
    )
    return _combine_parts([
        _DECISION_SUPPORT_SYNTHESIS_GUIDANCE,
        _interactive_modifier(context) or "",
        synthesis_prefs or "",
    ])


def _prompt_refinements_active(context: FlowContext) -> bool:
    if not context.prompt_refinement_enabled:
        return False
    pre_query = context.collected_responses.get("pre_query_config")
    if pre_query is None or pre_query.skipped:
        return False
    return True


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
        return bool(context.conversation_title and context.conversation_title != "New Conversation")

    async def _persist_collected_responses(user_query: Optional[str] = None) -> None:
        updates: Dict[str, Any] = {
            "collected_responses": serialize_prompt_responses(context.collected_responses),
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

    async def _request_interactive_response(prompt: InteractivePrompt) -> PromptResponse:
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

        await context.emit_event({
            "type": "interactive_response_received",
            "prompt_id": prompt.prompt_id,
        })
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
            title = await generate_conversation_title(state["user_query"], trace=context.trace)
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

        await context.emit_event({
            "type": "title_complete",
            "data": {"title": unique_title},
        })
        return {}

    async def pre_query_node(state: CouncilState) -> Dict[str, Any]:
        if not context.interactive_enabled:
            return {}
        if not context.interactive_queue or not context.session_id:
            return {"error": "Interactive session not available"}
        if context.is_cancelled():
            return _cancel_state()

        await context.emit_event({"type": "session_start", "session_id": context.session_id})

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
                    response = context.collected_responses.get(f"prompt_refinement_{iteration}")
                    if response is None:
                        response = await _request_interactive_response(refinement_prompt)
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
                user_refined_prompt = str(response.responses.get("refined_prompt") or "").strip()
                refined_prompt = user_refined_prompt or suggested_prompt or refined_prompt

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
                    "clarifying_questions": last_suggestion.get("clarifying_questions") or [],
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
        await _persist_collected_responses(updated_state.get("user_query") or state["user_query"])
        context.refresh_interactive_config()
        return updated_state

    async def stage1_node(state: CouncilState) -> Dict[str, Any]:
        if context.is_cancelled():
            return _cancel_state()
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
        await context.update_message({
            "stage1": stage1_results,
            "metadata": {"label_to_model": label_to_model},
        })
        await context.emit_event({
            "type": "stage1_complete",
            "data": stage1_results,
            "labels": labels,
        })

        if not context.negative_enabled:
            await context.update_message({"negative": state.get("negative")})

        return {
            "stage1": stage1_results,
            "labels": labels,
            "label_to_model": label_to_model,
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
            metadata_update["excluded_models"] = context.interactive_config.excluded_models
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

        await context.emit_event({
            "type": "negative_start",
            "negative_prompt": resolved_negative_prompt,
        })

        stage1_results, labels, label_to_model = _active_stage1_inputs(state)
        if not stage1_results:
            error_message = "No successful responses remain after exclusions."
            context.trace.end_stage("negative", error=error_message)
            return {"error": error_message}
        stage_models = [result.get("model") for result in stage1_results if result.get("model")]
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

        await context.update_message({
            "negative": negative_context,
            "metadata": {
                "aggregate_gap_scores": aggregate_gap_scores,
                "label_to_model": label_to_model,
            },
        })
        await context.emit_event({
            "type": "negative_complete",
            "data": evaluations,
            "negative_prompt": resolved_negative_prompt,
            "aggregate_gap_scores": aggregate_gap_scores,
        })

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

        await context.update_message({
            "stage2": stage2_results,
            "metadata": metadata,
        })
        await context.emit_event({
            "type": "stage2_complete",
            "data": stage2_results,
            "metadata": metadata,
        })

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
        stage_models = [result.get("model") for result in stage1_results if result.get("model")]
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
        context.trace.end_stage("deliberation", revision_count=len(deliberation_results))

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
        await context.emit_event({
            "type": "deliberation_complete",
            "data": deliberation_results,
        })

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
        await context.set_status("stage3")
        context.trace.begin_stage("stage3", label="Stage 3")
        await context.emit_event({"type": "stage3_start"})
        await context.init_stage_progress("stage3", [CHAIRMAN_MODEL])

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

        prompt_modifier = _stage3_prompt_modifier(context)
        try:
            stage3_result = await stage3_synthesize_final(
                state["user_query"],
                responses_for_synthesis,
                state.get("stage2", []),
                state.get("label_to_model", {}),
                state.get("negative"),
                state.get("aggregate_gap_scores"),
                state.get("labels"),
                trace=context.trace,
                prompt_modifier=prompt_modifier,
                progress=context.progress_callback("stage3"),
            )
        except Exception as exc:
            context.trace.end_stage("stage3", error=str(exc))
            raise
        context.trace.end_stage("stage3", model=stage3_result.get("model"))

        if context.is_cancelled():
            return _cancel_state()

        # Include synthesis mode in metadata if it was set via interactive config
        synthesis_mode = context.interactive_config.synthesis_mode if context.interactive_config else None
        # Default to best_of_breed if no mode specified
        effective_mode = synthesis_mode or "best_of_breed"

        stage3_metadata: Dict[str, Any] = {}
        if synthesis_mode:
            stage3_metadata["synthesis_mode"] = synthesis_mode
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

        await context.update_message({"stage3": stage3_with_versions, "metadata": stage3_metadata})
        await context.emit_event({"type": "stage3_complete", "data": stage3_with_versions})

        return {"stage3": stage3_with_versions, "metadata": merged_metadata}

    def _route_from_start(state: CouncilState) -> str:
        if context.is_resume:
            if _has_stage3(state):
                return "end"
            if _has_deliberation_results(state):
                if context.interactive_enabled and "synthesis_prefs" not in context.collected_responses:
                    return "synthesis_prefs"
                return "stage3"
            if _has_stage2(state):
                if context.deliberation_enabled:
                    return "deliberation"
                if context.interactive_enabled and "synthesis_prefs" not in context.collected_responses:
                    return "synthesis_prefs"
                return "stage3"
            if _has_negative_results(state):
                return "stage2"
            if _has_stage1(state):
                if context.interactive_enabled and "response_review" not in context.collected_responses:
                    return "response_review"
                if context.negative_enabled:
                    return "negative"
                return "stage2"
            if context.is_first_message and not _title_already_generated():
                return "title"
            if context.interactive_enabled and "pre_query_config" not in context.collected_responses:
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
    return await graph.ainvoke(initial_state)
