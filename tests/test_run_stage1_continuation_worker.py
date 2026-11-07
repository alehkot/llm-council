"""Offline actual-pool ownership for explicit first-continuation member steps."""

import asyncio
import json
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import httpx
import test_run_stage1_continuation_inputs as input_fixtures

from backend import config, run_admission, run_cancellation, run_inspection, storage
from backend import run_stage1_continuation_execution as execution
from backend import run_stage1_execution as initial_execution
from backend.consultation_manager import ConsultationManager


class ContinuationWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = input_fixtures.FirstContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run_id = self.fixture.run_id
        self.models = self.fixture.models
        for model in self.models:
            self.fixture.execute(model)
            self.fixture.bind(model)
        self.initial = json.loads(self.fixture.path.read_text())["stage1_execution"]
        pool = patch("backend.consultation_manager.CONSULTATION_POOL_SIZE", 1)
        pool.start()
        self.addCleanup(pool.stop)
        self.manager = object.__new__(ConsultationManager)
        self.manager._initialized = False
        self.manager.__init__()
        self.addCleanup(self.manager.shutdown)
        self.calls = []

    def complete(self, request, reason="stop"):
        self.calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": reason,
                        "message": {"role": "assistant", "content": "Exact ответ\n"},
                    }
                ]
            },
        )

    def schedule(self, model=None, handler=None, **kwargs):
        return self.manager.schedule_cli_stage1_continuation(
            kwargs.pop("run_id", self.run_id),
            model or self.models[0],
            turn=kwargs.pop("turn", 1),
            expected_revision=kwargs.pop("revision", 3),
            api_key=kwargs.pop("api_key", "synthetic-worker-key"),
            _transport=httpx.MockTransport(handler or self.complete),
            **kwargs,
        )

    async def wait(self, handle):
        return await asyncio.wait_for(handle.wait(), 3)

    async def event(self, event):
        async def poll():
            while not event.is_set():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(poll(), 3)

    def block_pool(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def block():
            started.set()
            release.wait(5)

        self.manager._executor.submit(block)
        return started, release

    async def test_complete_replay_preserves_initial_and_releases_slot(self):
        handle = self.schedule()
        self.assertEqual(handle.turn, 1)
        result = await self.wait(handle)
        self.assertEqual(result["stage1"]["members"][0]["phase"], "complete")
        await self.wait(self.schedule())
        self.assertEqual(len(self.calls), 1)
        saved = json.loads(self.fixture.path.read_text())
        self.assertEqual(saved["stage1_execution"], self.initial)
        self.assertEqual(self.manager._cli_stage1_steps, {})
        self.assertNotIn("synthetic-worker-key", self.fixture.path.read_text())

    async def test_duplicate_turn_identity_and_legacy_exclusion(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        self.assertIs(handle, self.schedule(api_key="rotated"))
        for kwargs in ({"revision": 4}, {"retry_token": str(uuid4())}):
            with self.assertRaises(initial_execution.ExecutionConflict):
                self.schedule(**kwargs)
        with self.assertRaises(initial_execution.ExecutionConflict):
            self.manager.schedule_cli_stage1_member(
                self.run_id, self.models[0], expected_revision=3, api_key="synthetic"
            )
        with self.assertRaises(initial_execution.ExecutionConflict):
            self.manager.submit(f"cli-{self.run_id}", "Legacy")
        release.set()
        a, b = await asyncio.gather(self.wait(handle), self.wait(handle))
        a["request"]["content"] = "changed"
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.calls), 1)

    async def test_initial_ownership_blocks_continuation(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.manager.schedule_cli_stage1_member(
            self.run_id, self.models[0], expected_revision=3, api_key="synthetic"
        )
        with self.assertRaises(initial_execution.ExecutionConflict):
            self.schedule()
        release.set()
        # Initial replay uses its original synthetic adapter identity, so the
        # real adapter fails closed rather than changing provenance.
        with self.assertRaises(initial_execution.ExecutionConflict):
            await self.wait(handle)
        self.assertEqual(self.calls, [])

    async def test_unsupported_turn_and_missing_input_never_queue(self):
        for turn in (0, 2, True, "1"):
            with self.assertRaises(ValueError):
                self.schedule(turn=turn)
        record = json.loads(self.fixture.path.read_text())
        del record["stage1_continuation_inputs"]
        self.fixture.path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            self.schedule()
        self.assertEqual(self.manager._cli_stage1_steps, {})
        self.assertEqual(self.calls, [])

    async def test_detached_queued_waiter_does_not_cancel(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(handle._future.cancelled())
        release.set()
        await self.wait(handle)
        self.assertEqual(len(self.calls), 1)

    async def test_cancel_and_delete_queued_never_dispatch(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        run_cancellation.request(self.run_id)
        release.set()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.wait(handle)
        self.assertEqual(self.calls, [])
        self.assertEqual(
            json.loads(self.fixture.path.read_text())["stage1_execution"], self.initial
        )

    async def test_delete_queued_never_resurrects(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        storage.delete_conversation(f"cli-{self.run_id}")
        release.set()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.wait(handle)
        self.assertEqual(self.calls, [])

    async def test_uncertainty_requires_own_retry_token(self):
        def failed(request):
            self.calls.append(request)
            return httpx.Response(503)

        with self.assertRaises(execution.ProviderFailure):
            await self.wait(self.schedule(handler=failed))
        token = run_inspection.inspect(self.run_id)["stage1"]["members"][0][
            "retry_token"
        ]
        with self.assertRaises(execution.RetryRequired):
            await self.wait(self.schedule())
        with self.assertRaises(execution.ExecutionConflict):
            await self.wait(self.schedule(retry_token=str(uuid4())))
        await self.wait(self.schedule(retry_token=token))
        await self.wait(self.schedule(retry_token=token))
        self.assertEqual(len(self.calls), 2)

    async def test_partial_never_schedules_another_turn(self):
        result = await self.wait(
            self.schedule(handler=lambda r: self.complete(r, "length"))
        )
        self.assertEqual(result["stage1"]["members"][0]["turn"], 2)
        self.assertEqual(result["stage1"]["members"][0]["phase"], "needs_continuation")
        await self.wait(self.schedule())
        self.assertEqual(len(self.calls), 1)

    async def test_two_explicit_members_run_concurrently(self):
        self.manager._executor.shutdown(wait=True)
        self.manager._executor = ThreadPoolExecutor(max_workers=2)
        starts = {m: threading.Event() for m in self.models}
        release = threading.Event()
        self.addCleanup(release.set)

        async def respond(request):
            starts[json.loads(request.content)["model"]].set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return self.complete(request)

        handles = [self.schedule(model=m, handler=respond) for m in self.models]
        for event in starts.values():
            await self.event(event)
        self.assertEqual(len(self.manager._cli_stage1_steps), 2)
        release.set()
        await asyncio.gather(*(self.wait(h) for h in handles))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "stage1_complete"
        )

    async def test_running_detach_then_cancel_releases_guard(self):
        started = threading.Event()

        async def respond(request):
            started.set()
            await asyncio.Event().wait()

        handle = self.schedule(handler=respond)
        await self.event(started)
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(handle._future.done())
        run_cancellation.request(self.run_id)
        with self.assertRaises(asyncio.CancelledError):
            await self.wait(handle)
        self.assertEqual(execution._active, set())
        self.assertEqual(run_cancellation._tasks, {})

    async def test_queued_configuration_drift_cannot_rebind(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        with patch.object(
            config, "OPENROUTER_API_URL", "https://changed.invalid/completions"
        ):
            release.set()
            with self.assertRaises(ValueError):
                await self.wait(handle)
        self.assertEqual(self.calls, [])

    async def test_legacy_queue_excludes_continuation(self):
        conversation = f"cli-{self.run_id}"
        self.manager._futures[conversation] = Future()
        with self.assertRaises(initial_execution.ExecutionConflict):
            self.schedule()
        self.manager._futures.pop(conversation)
        self.manager._queues[conversation] = [object()]
        try:
            with self.assertRaises(initial_execution.ExecutionConflict):
                self.schedule()
        finally:
            self.manager._queues.pop(conversation)

    async def test_queued_stale_revision_cannot_dispatch(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        record = json.loads(self.fixture.path.read_text())
        record["interaction"]["revision"] += 1
        self.fixture.path.write_text(json.dumps(record))
        release.set()
        with self.assertRaises((ValueError, run_admission.AdmissionUnavailable)):
            await self.wait(handle)
        self.assertEqual(self.calls, [])

    async def test_delete_running_rejects_late_result(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        async def respond(request):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return self.complete(request)

        handle = self.schedule(handler=respond)
        await self.event(started)
        storage.delete_conversation(f"cli-{self.run_id}")
        release.set()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.wait(handle)
        self.assertEqual(json.loads(self.fixture.path.read_text())["phase"], "deleted")
        self.assertEqual(execution._active, set())
        self.assertEqual(run_cancellation._tasks, {})
