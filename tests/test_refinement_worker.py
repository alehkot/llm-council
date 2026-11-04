"""Offline checks of actual manager-owned refinement worker boundaries."""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "synthetic-unused-key")
os.environ.setdefault("API_AUTH_KEY", "synthetic-api-key")

from backend import (
    config,
    refinement_driver,
    refinement_execution,
    refinement_provider,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest
from backend.consultation_manager import ConsultationManager


class RefinementWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = str(Path(self.directory.name) / "conversations")
        for patcher in (
            patch.object(storage, "DATA_DIR", self.data_dir),
            patch("backend.consultation_manager.CONSULTATION_POOL_SIZE", 1),
            patch.multiple(
                config,
                MODEL_BACKEND="openrouter",
                PROMPT_REFINEMENT_MODEL="fake/refiner",
                OPENROUTER_API_URL="https://fixture.invalid/completions",
                OPENROUTER_MAX_TOKENS=1024,
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = self.new_manager()
        self.run_id = self.prepare()
        self.calls = []

    def new_manager(self):
        manager = object.__new__(ConsultationManager)
        manager._initialized = False
        manager.__init__()
        self.addCleanup(manager.shutdown)
        return manager

    def prepare(self, refine=True):
        run_id = str(uuid4())
        self.manager.prepare_cli_run(
            run_id,
            CreateRunRequest(
                content="Original — вопрос",
                refine=refine,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        return run_id

    def complete(self, request):
        self.calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "Clarify",
                                    "clarifying_questions": ["Which scope?"],
                                    "refined_prompt": "Refined question",
                                }
                            )
                        },
                    }
                ]
            },
        )

    def schedule(self, revision=0, handler=None, **kwargs):
        return self.manager.schedule_cli_refinement_step(
            kwargs.pop("run_id", self.run_id),
            expected_revision=revision,
            api_key=kwargs.pop("api_key", "ephemeral-worker-key"),
            _transport=httpx.MockTransport(handler or self.complete),
            **kwargs,
        )

    async def wait(self, handle):
        return await asyncio.wait_for(handle.wait(), timeout=3)

    async def event(self, event):
        async def observe():
            while not event.is_set():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(observe(), timeout=3)

    def block_pool(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def block():
            started.set()
            release.wait(5)

        future = self.manager._executor.submit(block)
        return started, release, future

    def answer(self, action="use_refined"):
        snapshot = run_inspection.inspect(self.run_id)
        run_interaction.answer(
            self.run_id,
            snapshot["current_prompt"]["instance_id"],
            AnswerRequest(
                responses={
                    "refinement_action": action,
                    "refined_prompt": "Edited — вопрос",
                    "clarifying_answer_0": "Scope answer"
                    if action == "refine_again"
                    else "",
                    "additional_insights": "",
                }
            ),
        )

    async def test_waiting_releases_single_worker_for_unrelated_run(self):
        first = await self.wait(self.schedule())
        self.assertEqual(first["internal_phase"], "waiting_input")
        other = self.prepare()
        second = await self.wait(self.schedule(run_id=other))
        self.assertEqual(second["internal_phase"], "waiting_input")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.manager._cli_refinement_steps, {})
        self.assertEqual(self.manager._consultations, {})
        self.assertEqual(self.manager._futures, {})
        self.assertEqual(run_cancellation._tasks, {})

    async def test_exact_duplicates_share_ownership_and_results_are_detached(self):
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        duplicate = self.schedule(
            run_id=self.run_id.upper(), api_key="rotated-unused-key"
        )
        self.assertIs(handle, duplicate)
        for kwargs in ({"revision": 1}, {"retry_token": str(uuid4())}):
            with self.assertRaises(refinement_execution.ExecutionConflict):
                self.schedule(**kwargs)
        self.assertNotIn("ephemeral-worker-key", repr(handle))
        release.set()
        first, second = await asyncio.gather(self.wait(handle), self.wait(duplicate))
        first["request"]["content"] = "caller mutation"
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0].headers["authorization"], "Bearer ephemeral-worker-key"
        )

    async def test_detach_while_queued_does_not_cancel_owned_work(self):
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(handle._future.cancelled())
        release.set()
        result = await self.wait(handle)
        self.assertEqual(result["internal_phase"], "waiting_input")
        self.assertEqual(len(self.calls), 1)

    async def test_detach_while_running_does_not_cancel_provider(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        async def respond(request):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return self.complete(request)

        handle = self.schedule(handler=respond)
        await self.event(started)
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "running_refinement"
        )
        release.set()
        self.assertEqual((await self.wait(handle))["internal_phase"], "waiting_input")

    async def test_active_cancellation_releases_worker_without_cancelling_sibling(self):
        started = threading.Event()

        async def blocked(request):
            started.set()
            await asyncio.Event().wait()
            return self.complete(request)

        handle = self.schedule(handler=blocked)
        await self.event(started)
        sibling = self.schedule(run_id=self.prepare())
        run_cancellation.request(self.run_id)
        with self.assertRaises(asyncio.CancelledError):
            await self.wait(handle)
        self.assertEqual((await self.wait(sibling))["internal_phase"], "waiting_input")
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "cancelled"
        )
        self.assertEqual(self.manager._cli_refinement_steps, {})
        self.assertEqual(run_cancellation._tasks, {})
        self.assertEqual(len(self.calls), 1)

    async def test_queued_cancel_never_dispatches(self):
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        run_cancellation.request(self.run_id)
        release.set()
        self.assertEqual((await self.wait(handle))["internal_phase"], "cancelled")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.manager._cli_refinement_steps, {})

    async def test_queued_deletion_never_dispatches(self):
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        storage.delete_conversation(f"cli-{self.run_id}")
        release.set()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.wait(handle)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.manager._cli_refinement_steps, {})

    async def test_queued_revision_is_rechecked_before_dispatch(self):
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        await refinement_driver.advance(
            self.run_id,
            expected_revision=0,
            api_key="direct-synthetic-key",
            _transport=httpx.MockTransport(self.complete),
        )
        release.set()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.wait(handle)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.manager._cli_refinement_steps, {})

    async def test_waiting_and_answer_boundaries_ignore_configuration_and_credentials(
        self,
    ):
        waiting = await self.wait(self.schedule())
        with patch.object(config, "MODEL_BACKEND", "acp"):
            self.assertEqual(await self.wait(self.schedule(1, api_key=None)), waiting)
            self.answer("refine_again")
            ready = await self.wait(self.schedule(2, api_key=None))
        self.assertEqual(ready["internal_phase"], "refinement_ready")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(refinement_execution.ExecutionConflict):
            self.schedule(2)
        await self.wait(self.schedule(3))
        self.assertEqual(len(self.calls), 2)
        self.answer()
        complete = await self.wait(self.schedule(5, api_key=None))
        self.assertEqual(complete["internal_phase"], "refinement_complete")
        self.assertEqual(len(self.calls), 2)

    async def test_failure_releases_worker_and_explicit_retry_remains_required(self):
        handle = self.schedule(handler=lambda request: httpx.Response(503))
        with self.assertRaises(refinement_provider.ProviderFailure):
            await self.wait(handle)
        self.assertEqual(self.manager._cli_refinement_steps, {})
        snapshot = run_inspection.inspect(self.run_id)
        self.assertEqual(snapshot["internal_phase"], "needs_retry")
        with self.assertRaises(refinement_execution.RetryRequired):
            await self.wait(self.schedule())
        result = await self.wait(self.schedule(retry_token=snapshot["retry_token"]))
        self.assertEqual(result["internal_phase"], "waiting_input")
        self.assertEqual(len(self.calls), 1)

    async def test_settings_are_captured_before_queue_wait(self):
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        with patch.multiple(
            config, MODEL_BACKEND="acp", PROMPT_REFINEMENT_MODEL="other/model"
        ):
            release.set()
            result = await self.wait(handle)
        self.assertEqual(json.loads(self.calls[0].content)["model"], "fake/refiner")
        self.assertEqual(
            result["refinement_provider"]["configuration"]["model"], "fake/refiner"
        )
        receipt = Path(self.directory.name) / "run_receipts" / f"{self.run_id}.json"
        self.assertNotIn("ephemeral-worker-key", receipt.read_text())

    async def test_admission_validation_does_not_enqueue(self):
        with patch.object(self.manager._executor, "submit") as submit:
            for revision in (True, -1, 1, "0"):
                with self.assertRaises(ValueError):
                    self.schedule(revision)
            with self.assertRaises(refinement_execution.ExecutionConflict):
                self.schedule(run_id=self.prepare(refine=False))
            with patch.object(config, "MODEL_BACKEND", "acp"):
                with self.assertRaises(refinement_execution.ExecutionConflict):
                    self.schedule()
            submit.assert_not_called()

    async def test_immediately_completed_future_callback_does_not_deadlock(self):
        done = Future()
        done.set_result(run_inspection.inspect(self.run_id))
        # Run schedule outside the test loop so a lock regression has a bounded wait.
        with patch.object(self.manager._executor, "submit", return_value=done):
            handle = await asyncio.wait_for(asyncio.to_thread(self.schedule), timeout=2)
        self.assertEqual((await self.wait(handle))["internal_phase"], "prepared")
        self.assertEqual(self.manager._cli_refinement_steps, {})

    async def test_legacy_and_internal_ownership_do_not_overlap(self):
        conversation = f"cli-{self.run_id}"
        self.manager._futures[conversation] = Future()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            self.schedule()
        self.manager._futures.clear()
        started, release, _ = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            self.manager.submit(conversation, "legacy collision")
        release.set()
        await self.wait(handle)

    async def test_new_manager_does_not_redispatch_saved_waiting_work(self):
        saved = await self.wait(self.schedule())
        self.manager.shutdown()
        self.manager = self.new_manager()
        self.assertEqual(run_inspection.inspect(self.run_id), saved)
        with patch.object(config, "MODEL_BACKEND", "acp"):
            self.assertEqual(await self.wait(self.schedule(1, api_key=None)), saved)
        self.assertEqual(len(self.calls), 1)
