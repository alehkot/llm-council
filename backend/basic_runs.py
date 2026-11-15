"""Single-process durable three-stage CLI runs, independent of legacy recovery.

The basic capability deliberately rejects optional stages. Each paid operation
uses a saved request and explicit uncertain-attempt consent; reads never schedule.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import uuid4

from . import (
    config,
    council,
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_prequery,
    durable_response_review,
    model_context,
    prequery_policy,
    response_review_policy,
    run_admission,
    run_cancellation,
    run_interaction,
    run_skill_plan,
    run_skill_provenance,
    run_skill_snapshot,
    run_stage1_execution,
    run_stage1_inputs,
    run_stage1_provider,
    run_stage1_results,
    run_stage2_execution,
    run_stage2_inputs,
    run_stage2_results,
    storage,
)
from .consultation_contract import AnswerRequest, CreateRunRequest, RunSnapshot
from .export_markdown import build_markdown_export

_tasks: dict[str, asyncio.Task[None]] = {}
_wakeups: set[str] = set()
_capacity: asyncio.Semaphore | None = None


def requirements() -> list[str]:
    """Report unsupported loaded settings before admission or provider work."""
    required = {
        "MODEL_BACKEND": "openrouter",
        "SYNTHESIS_BACKEND": "openrouter",
        "NEGATIVE_MODE_ENABLED": False,
        "DELIBERATION_ENABLED": False,
        "SKILLS_ENABLED": True,
        "SKILLS_REFERENCE_SELECTION_ENABLED": False,
        "SKILLS_AUTO_SELECT_ENABLED": False,
    }
    return [
        f"{k}={str(v).lower()}" for k, v in required.items() if getattr(config, k) != v
    ]


def _record(run: str) -> dict[str, Any]:
    record = run_admission.get(run)
    if record is None or record["phase"] == "deleted":
        raise LookupError("Run not found")
    run_admission._validate_conversation(record)
    if "basic_run" not in record:
        raise ValueError("Not a basic CLI run")
    return record


def _update(run: str, **values: Any) -> None:
    with storage._storage_lock:
        record = run_admission.get_prepared(run)
        record["basic_run"].update(values)
        run_admission._write(record)


def _uncertain(record: dict[str, Any]) -> tuple[str, str] | None:
    for stage in ("stage1", "stage2"):
        for op in record.get(stage + "_execution", {}).get("operations", []):
            if op["result"] is None:
                return f"{stage}:{op['model']}", op["attempt"]
    synthesis = record["basic_run"].get("synthesis")
    if synthesis and synthesis.get("attempt") and synthesis.get("result") is None:
        return "stage3", synthesis["attempt"]
    return None


def snapshot(run: str, *, include_stages: bool = True) -> RunSnapshot:
    with storage._storage_lock:
        record = _record(run)
        state = record["basic_run"]
        interaction = run_interaction._validate_record(record)
        pending = next(
            (p.instance for p in interaction.prompts if p.phase == "waiting"), None
        )
        cancellation = run_cancellation.inspect(run)
        lifecycle = state["lifecycle"]
        retry = None
        if cancellation:
            lifecycle = cancellation["phase"]
        elif lifecycle != "complete":
            if pending:
                lifecycle = "waiting_input"
            elif run not in _tasks:
                uncertain = _uncertain(record)
                if uncertain:
                    lifecycle = "needs_retry"
                    retry = {
                        "operation": uncertain[0],
                        "revision": uncertain[1],
                        "explanation": "The previous provider call may have been billed. Retry only with explicit consent.",
                    }
                elif lifecycle == "running":
                    lifecycle = "queued"
        conversation = storage.get_conversation(record["conversation_id"])
        message = conversation["messages"][record["assistant_index"]]
        payload = dict(
            run_id=run,
            conversation_id=record["conversation_id"],
            assistant_index=1,
            lifecycle=lifecycle,
            cursor="",
            stage=state.get("stage"),
            pending_prompt=pending if lifecycle == "waiting_input" else None,
            retry=retry,
            result={
                "run_id": run,
                "conversation_id": record["conversation_id"],
                "assistant_index": 1,
            }
            if lifecycle == "complete"
            else None,
            stages={
                k: message[k]
                for k in ("stage1", "stage2", "stage3")
                if include_stages and message.get(k) is not None
            },
            provenance={"capability": "basic-v1", "created_at": record["created_at"]},
            error=state.get("error"),
        )
        result = RunSnapshot(**payload)
        result.cursor = hashlib.sha256(result.model_dump_json().encode()).hexdigest()
        return result


def create(run: str, request: CreateRunRequest) -> RunSnapshot:
    with storage._storage_lock:
        existing = run_admission.get(run)
        if existing is None:
            missing = requirements()
            if missing:
                raise ValueError("Basic CLI requires " + ", ".join(missing))
            if request.refine or request.negative_prompt is not None:
                raise ValueError(
                    "Basic CLI does not yet support refinement or negative mode"
                )
        record = run_admission.prepare(run, request)
        if "basic_run" not in record:
            record["basic_run"] = {
                "lifecycle": "queued",
                "stage": "pre_query",
                "error": None,
                "consent": {},
            }
            run_admission._write(record)
        schedule(run)
        return snapshot(run)


def schedule(run: str) -> None:
    if run in _tasks:
        _wakeups.add(run)
        return
    state = snapshot(run)
    if state.lifecycle.value not in ("queued", "running"):
        if (
            state.retry is None
            or _record(run)["basic_run"]["consent"].get(state.retry.operation)
            != state.retry.revision
        ):
            return
    _start(run)


def _start(run: str) -> None:
    global _capacity
    if not _tasks:
        _capacity = asyncio.Semaphore(config.CONSULTATION_POOL_SIZE)
    assert _capacity is not None
    task = asyncio.create_task(_bounded_drive(run, _capacity))
    _tasks[run] = task
    task.add_done_callback(lambda done: _finished(run, done))


def _finished(run: str, task: asyncio.Task[None]) -> None:
    if _tasks.get(run) is not task:
        return
    _tasks.pop(run)
    if run in _wakeups:
        _wakeups.discard(run)
        try:
            schedule(run)
        except (LookupError, ValueError, RuntimeError):
            # Cancellation, deletion or damaged state must not recreate work.
            pass


async def _bounded_drive(run: str, capacity: asyncio.Semaphore) -> None:
    async with capacity:
        await _drive(run)


def answer(run: str, instance: str, value: AnswerRequest) -> RunSnapshot:
    _record(run)
    run_interaction.answer(run, instance, value)
    schedule(run)
    return snapshot(run)


def retry(run: str, revision: str) -> RunSnapshot:
    with storage._storage_lock:
        state = snapshot(run)
        record = _record(run)
        consent = record["basic_run"]["consent"]
        if revision in consent.values():
            return state
        if state.retry is None or state.retry.revision != revision:
            raise ValueError("Retry revision is stale; reload the run")
        consent[state.retry.operation] = revision
        record["basic_run"].update(consent=consent, lifecycle="queued", error=None)
        run_admission._write(record)
        # Consent authorizes one saved attempt, never automatic future retries.
        _start(run)
    return snapshot(run)


def cancel(run: str, revision: str) -> RunSnapshot:
    with storage._storage_lock:
        current = snapshot(run)
        if current.lifecycle.value in ("complete", "cancelled", "cancelling"):
            return current
        if revision != current.cursor:
            raise ValueError("Cancellation revision is stale; reload the run")
        run_cancellation.request(run)
    return snapshot(run)


def history() -> list[dict[str, Any]]:
    return [
        snapshot(r["run_id"], include_stages=False).model_dump(mode="json")
        for r in run_admission.list_receipts(100)
        if "basic_run" in r
    ]


def export(run: str) -> str:
    record = _record(run)
    conversation = storage.get_conversation(record["conversation_id"])
    return build_markdown_export(
        conversation=conversation,
        message=conversation["messages"][1],
        user_query=record["request"]["content"],
        message_index=1,
        request_number=1,
    )


def _save_stage(run: str, stage: str, result: Any) -> None:
    with storage._storage_lock:
        record = run_admission.get_prepared(run)
        storage.update_assistant_message(record["conversation_id"], 1, {stage: result})


async def _synthesize(run: str) -> None:
    record = _record(run)
    basic = record["basic_run"]
    saved = basic.get("synthesis")
    if saved is None:
        ranks = run_stage2_results.load_complete_results(run)
        policy = record["pre_query_policy"]
        active = set(record["response_review_policy"]["active_models"])
        members = [
            {"model": r.model, "response": r.content}
            for r in run_stage1_results.load_complete_results(run)
            if r.model in active
        ]
        reviews = [
            {
                "model": r.model,
                "ranking": r.content,
                "parsed_ranking": list(r.parsed_ranking),
            }
            for r in ranks.reviewers
        ]
        messages = council.build_stage3_messages(
            policy["user_query"],
            members,
            reviews,
            ranks.label_to_model,
            aggregate_rankings=[
                r.model_dump(exclude_none=True) for r in ranks.aggregate
            ],
            prompt_modifier=policy["system_prompt_modifier"],
            skills_context=record["skill_plan"]["stage3"]["text"],
        )
        model = config.CHAIRMAN_MODEL
        limits = await model_context.model_limits(model)
        budget = council_model_budgets._budget(
            model, limits, config.OPENROUTER_MAX_TOKENS
        )
        if model_context.token_bound(messages) > budget.input_budget:
            raise ValueError(
                "Synthesis context exceeds the model budget; guarded reduction is not implemented"
            )
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": budget.output_budget,
        }
        if budget.reasoning:
            payload["reasoning"] = {"effort": "high"}
        saved = {
            "endpoint": config.OPENROUTER_API_URL,
            "timeout_seconds": 120.0,
            "payload": payload,
            "budget": budget.model_dump(),
            "attempt": None,
            "result": None,
        }
        _update(run, synthesis=saved)
    if saved["result"] is None:
        if saved["attempt"] and basic["consent"].get("stage3") != saved["attempt"]:
            raise ValueError("Synthesis requires explicit retry")
        if (
            saved["endpoint"] != config.OPENROUTER_API_URL
            or saved["payload"]["model"] != config.CHAIRMAN_MODEL
        ):
            raise ValueError("Synthesis configuration changed")
        saved["attempt"] = str(uuid4())
        _update(run, synthesis=saved)
        provider = run_stage1_provider._provider(config.OPENROUTER_API_KEY, None)
        with storage._storage_lock:
            run_admission.get_prepared(run)
            task = asyncio.create_task(
                provider(
                    endpoint=saved["endpoint"],
                    timeout_seconds=saved["timeout_seconds"],
                    payload=saved["payload"],
                )
            )
            run_cancellation._register(run, saved["attempt"], task)
        try:
            result = run_stage1_execution.MemberResponse.model_validate(await task)
            if result.finish_reason != "stop" or not result.content.strip():
                raise ValueError("Synthesis did not complete")
            saved["result"] = result.model_dump(mode="json")
            _update(run, synthesis=saved)
        finally:
            with storage._storage_lock:
                run_cancellation._release(run, saved["attempt"])
    result = saved["result"]
    _save_stage(
        run,
        "stage3",
        {
            "model": saved["payload"]["model"],
            "response": result["content"],
            "finish_reason": result["finish_reason"],
            "usage": result.get("usage"),
        },
    )
    _update(run, lifecycle="complete", stage="complete", error=None)


async def _roster(run: str, stage: str, revision: int) -> None:
    record = _record(run)
    provider = run_stage1_provider._provider(config.OPENROUTER_API_KEY, None)
    executor = run_stage1_execution if stage == "stage1" else run_stage2_execution
    requests = record[stage + "_inputs"]["requests"]
    outcomes = await asyncio.gather(
        *(
            executor.execute(
                run,
                request["model"],
                provider,
                provider_key=f"openrouter-{stage}-v1",
                expected_revision=revision,
                retry_token=record["basic_run"]["consent"].get(
                    stage + ":" + request["model"]
                ),
            )
            for request in requests
        ),
        return_exceptions=True,
    )
    # Save completed siblings even if another member failed or was truncated.
    field = "response" if stage == "stage1" else "ranking"
    evidence = [
        {
            "model": request["model"],
            field: result.content,
            "finish_reason": result.finish_reason,
            "usage": result.usage,
        }
        for request, result in zip(requests, outcomes)
        if isinstance(result, run_stage1_execution.MemberResponse)
    ]
    _save_stage(run, stage, evidence)
    for result in outcomes:
        if isinstance(result, BaseException):
            raise result
        if result.finish_reason != "stop":
            raise ValueError(
                "A member needs a continuation; basic runs retain partial evidence but do not continue automatically"
            )


async def _drive(run: str) -> None:
    try:
        if requirements():
            raise ValueError("Backend settings no longer support basic runs")
        _update(run, lifecycle="running", error=None)
        interaction = run_interaction._validate_record(_record(run))
        if interaction.revision < 3:
            if interaction.revision == 0:
                run_skill_provenance.bind_explicit_skills(run)
                run_skill_snapshot.persist(run, expected_revision=0)
            durable_prequery.advance(run, expected_revision=interaction.revision)
            interaction = run_interaction._validate_record(_record(run))
            if interaction.revision < 3:
                return
        record = _record(run)
        if "stage1_inputs" not in record:
            prequery_policy.bind(run, expected_revision=3)
            run_skill_plan.bind(run, expected_revision=3)
            council_selection.bind(run, expected_revision=3)
            council_request_policy.bind(run, expected_revision=3)
            await council_model_budgets.bind(run, expected_revision=3)
            run_stage1_inputs.bind(run, expected_revision=3)
        if interaction.revision == 3:
            _update(run, stage="stage1")
            await _roster(run, "stage1", 3)
            members = run_stage1_results.load_complete_results(run)
            _save_stage(
                run,
                "stage1",
                [{"model": r.model, "response": r.content} for r in members],
            )
            durable_response_review.advance(run, expected_revision=3)
            return
        if interaction.revision < 6:
            durable_response_review.advance(run, expected_revision=interaction.revision)
            interaction = run_interaction._validate_record(_record(run))
            if interaction.revision < 6:
                return
        record = _record(run)
        if "stage2_inputs" not in record:
            response_review_policy.bind(run, expected_revision=6)
            run_stage2_inputs.bind(run, expected_revision=6)
        _update(run, stage="stage2")
        await _roster(run, "stage2", 6)
        ranks = run_stage2_results.load_complete_results(run)
        if not ranks.aggregate:
            raise ValueError("No valid peer rankings; synthesis was not started")
        _save_stage(
            run,
            "stage2",
            [
                {
                    "model": r.model,
                    "ranking": r.content,
                    "parsed_ranking": list(r.parsed_ranking),
                }
                for r in ranks.reviewers
            ],
        )
        _update(run, stage="stage3")
        await _synthesize(run)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        # Never persist provider diagnostics, credentials, or arbitrary exception text.
        try:
            message = "Run stopped; inspect saved stages and retry only if a retry notice is present."
            if type(exc) is ValueError:
                message = str(exc)
            _update(run, lifecycle="failed", error=message)
        except (ValueError, RuntimeError):
            pass


async def shutdown() -> None:
    _wakeups.clear()
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
