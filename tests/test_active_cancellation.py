"""Offline regressions for cancelling running and pool-waiting consultations."""

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import consultation_journal, storage  # noqa: E402
from backend.consultation_manager import (  # noqa: E402
    ConsultationManager,
    ConsultationState,
)
from backend.langgraph_flow import (  # noqa: E402
    FlowCallbacks,
    FlowContext,
    run_council_graph,
)
from backend.openrouter import query_model  # noqa: E402
from backend.trace import TraceCollector  # noqa: E402

RESULT = {"stage1": [], "stage2": [], "stage3": {"model": "fake", "response": "Done"}}


class ActiveCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        for patcher in (
            patch.object(
                storage, "DATA_DIR", str(Path(self.tempdir.name) / "conversations")
            ),
            patch.object(
                consultation_journal,
                "_CONSULTATION_DIR",
                Path(self.tempdir.name) / "consultations",
            ),
            patch("backend.consultation_manager.CONSULTATION_POOL_SIZE", 1),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = object.__new__(ConsultationManager)
        self.manager._initialized = False
        self.manager.__init__()
        self.addCleanup(self.manager.shutdown)

    def start(self, conversation_id: str, content: str) -> ConsultationState:
        storage.create_conversation(conversation_id)
        self.manager.submit(conversation_id, content)
        return self.manager._consultations[conversation_id]

    def assert_cancelled(self, state: ConsultationState) -> None:
        self.assertEqual(state.status.value, "failed")
        self.assertEqual(state.error, "Cancelled")
        message = storage.get_conversation(state.conversation_id)["messages"][
            state.assistant_index
        ]
        self.assertEqual(message["metadata"]["status"], "failed")
        self.assertEqual(message["metadata"]["error"], "Cancelled")
        self.assertIsNone(consultation_journal.get_job(state.id))
        self.assertIsNone(state.loop)
        self.assertIsNone(state.task)

    def test_cancel_interrupts_provider_await_and_immediate_retry_runs(self) -> None:
        entered = threading.Event()
        exited = threading.Event()
        second_entered = threading.Event()
        calls = []

        class FakeClient:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *_args: Any) -> None:
                exited.set()

            async def post(self, *_args: Any, **_kwargs: Any) -> None:
                calls.append("provider request")
                entered.set()
                # Bound the fake wait so a broken implementation cannot hang tests.
                await asyncio.sleep(5)
                raise AssertionError("Provider await was not cancelled")

        async def graph(
            query: str, _negative: Any, context: FlowContext, **_kwargs: Any
        ) -> Dict[str, Any]:
            if query == "first":
                await query_model("fake", [{"role": "user", "content": query}])
            else:
                second_entered.set()
            return RESULT

        with (
            patch("backend.langgraph_flow.run_council_graph", graph),
            patch("backend.openrouter.httpx.AsyncClient", FakeClient),
            patch(
                "backend.openrouter.model_limits",
                AsyncMock(
                    return_value={"context_length": 8192, "max_completion_tokens": 2048}
                ),
            ),
        ):
            old_state = self.start("same", "first")
            old_future = self.manager._futures["same"]
            self.assertTrue(entered.wait(2))
            self.manager.submit("same", "discard queued message")
            self.assertTrue(self.manager.cancel_consultation("same"))
            self.manager.submit("same", "second")
            new_state = self.manager._consultations["same"]
            self.assertTrue(
                second_entered.wait(2), "Cancelled await still occupies worker"
            )
            old_future.result(timeout=2)
            self.manager.shutdown()

        self.assertTrue(exited.is_set())
        self.assertEqual(len(calls), 1, "Cancelled provider request must not retry")
        self.assert_cancelled(old_state)
        self.assertIs(self.manager._consultations["same"], new_state)
        self.assertEqual(new_state.status.value, "complete")
        self.assertEqual(self.manager.get_queue("same"), [])
        self.assertEqual(consultation_journal.list_jobs(), [])
        self.assertEqual(len(storage.get_conversation("same")["messages"]), 4)

    def test_cancel_all_cancels_children_and_never_starts_pool_waiting_graph(
        self,
    ) -> None:
        entered = [threading.Event(), threading.Event()]
        cleaned = [threading.Event(), threading.Event()]
        graph_calls = []

        async def child(index: int) -> None:
            entered[index].set()
            try:
                await asyncio.sleep(5)
            finally:
                cleaned[index].set()

        async def graph(query: str, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
            graph_calls.append(query)
            if query == "running":
                await asyncio.gather(child(0), child(1))
            return RESULT

        with patch("backend.langgraph_flow.run_council_graph", graph):
            running = self.start("running", "running")
            running_future = self.manager._futures["running"]
            self.assertTrue(all(event.wait(2) for event in entered))
            pending = self.start("waiting", "must not run")
            pending_future = self.manager._futures["waiting"]
            self.manager.submit("running", "queued")
            self.assertEqual(self.manager.cancel_all_consultations(), 3)
            running_future.result(timeout=2)
            pending_future.result(timeout=2)

        self.assertEqual(graph_calls, ["running"])
        self.assertTrue(all(event.is_set() for event in cleaned))
        self.assert_cancelled(running)
        self.assert_cancelled(pending)
        self.assertEqual(consultation_journal.list_jobs(), [])

    def test_cancellation_after_graph_return_wins_over_completion(self) -> None:
        async def graph(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
            return RESULT

        storage.create_conversation("late")
        state = self.manager._create_started_state(
            "late-job", "late", "Question", None, resume_from_storage=False
        )
        self.manager._consultations["late"] = state
        with (
            patch("backend.langgraph_flow.run_council_graph", graph),
            patch(
                "backend.trace.TraceCollector.finish",
                side_effect=lambda: self.manager.cancel_consultation("late"),
            ),
        ):
            self.manager._run_consultation(state)

        self.assert_cancelled(state)
        self.assertIsNone(storage.get_conversation("late")["messages"][1]["stage3"])

    def test_real_graph_cancellation_interrupts_all_stage1_providers(self) -> None:
        all_entered = threading.Event()
        entered = []
        cleaned = []

        async def fake_query(model: str, _messages: Any, **kwargs: Any) -> Any:
            if kwargs.get("trace_stage") == "title":
                return {"content": "Preserved title"}
            self.assertEqual(kwargs.get("trace_stage"), "stage1")
            entered.append(model)
            if len(entered) == 2:
                all_entered.set()
            try:
                await asyncio.sleep(5)
            finally:
                cleaned.append(model)

        with (
            patch("backend.council.query_model", fake_query),
            patch("backend.council.COUNCIL_MODELS", ["a", "b"]),
            patch("backend.langgraph_flow.COUNCIL_MODELS", ["a", "b"]),
            patch("backend.langgraph_flow.SKILLS_AUTO_SELECT_ENABLED", False),
            patch("backend.consultation_manager.INTERACTIVE_MODE_ENABLED", False),
        ):
            state = self.start("real", "Keep this question")
            future = self.manager._futures["real"]
            self.assertTrue(all_entered.wait(2))
            self.assertTrue(self.manager.cancel_consultation("real"))
            future.result(timeout=2)

        self.assertCountEqual(cleaned, ["a", "b"])
        self.assert_cancelled(state)
        conversation = storage.get_conversation("real")
        self.assertEqual(conversation["title"], "Preserved title")
        self.assertEqual(conversation["messages"][0]["content"], "Keep this question")
        self.assertEqual(len(conversation["messages"]), 2)


class InteractiveCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_interactive_wait_cleans_queue_and_cancel_poll_tasks(
        self,
    ) -> None:
        prompted = asyncio.Event()

        async def update(updates: Dict[str, Any]) -> None:
            if (updates.get("metadata") or {}).get("interactive_prompt"):
                prompted.set()

        context = FlowContext(
            trace=TraceCollector(),
            callbacks=FlowCallbacks(update_message=update),
            interactive_enabled=True,
            prompt_refinement_enabled=False,
            interactive_queue=asyncio.Queue(),
            session_id="test-session",
            interactive_timeout_seconds=5,
            cancel_requested=lambda: False,
        )
        before = asyncio.all_tasks()
        with patch("backend.council.query_model") as provider:
            task = asyncio.create_task(run_council_graph("Question", None, context))
            await asyncio.wait_for(prompted.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            provider.assert_not_called()

        self.assertEqual(asyncio.all_tasks() - before, set())


if __name__ == "__main__":
    unittest.main()
