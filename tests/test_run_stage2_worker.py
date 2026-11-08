"""Offline actual-pool ownership for explicit Stage 2 reviewer steps."""

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import test_run_stage2_inputs as input_fixtures

from backend import (
    config,
    consultation_journal,
    run_admission,
    run_cancellation,
    run_inspection,
    storage,
)
from backend import run_stage2_execution as execution
from backend.consultation_manager import ConsultationManager


class Stage2WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = input_fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        journals = tempfile.TemporaryDirectory()
        self.addCleanup(journals.cleanup)
        journal_patch = patch.object(
            consultation_journal, "_CONSULTATION_DIR", Path(journals.name)
        )
        journal_patch.start()
        self.addCleanup(journal_patch.stop)
        self.fixture.complete({"exclude_responses": ["provider/first"]})
        self.inputs = self.fixture.bind()
        self.run_id = self.fixture.run
        self.models = [r.model for r in self.inputs.requests]
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
        return self.manager.schedule_cli_stage2_reviewer(
            kwargs.pop("run_id", self.run_id),
            model or self.models[0],
            expected_revision=kwargs.pop("revision", 6),
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

    async def test_complete_releases_slot_and_saved_replay_does_not_call(self):
        first = await self.wait(self.schedule())
        self.assertEqual(first["stage2_execution"]["members"][0]["phase"], "complete")
        second = await self.wait(self.schedule(model=self.models[1]))
        self.assertEqual(second["internal_phase"], "stage2_attempts_complete")
        await self.wait(self.schedule())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.manager._cli_stage2_steps, {})
        self.assertEqual(self.manager._futures, {})
        self.assertEqual(run_cancellation._tasks, {})
        self.assertNotIn(
            "synthetic-worker-key", run_admission._path(self.run_id).read_text()
        )

    async def test_duplicate_identity_detached_results_and_conflicting_consent(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        self.assertIs(
            handle, self.schedule(run_id=self.run_id.upper(), api_key="rotated-key")
        )
        for kwargs in ({"revision": 7}, {"retry_token": str(uuid4())}):
            with self.assertRaises(execution.ExecutionConflict):
                self.schedule(**kwargs)
        self.assertNotIn("synthetic-worker-key", repr(handle))
        release.set()
        first, second = await asyncio.gather(self.wait(handle), self.wait(handle))
        first["request"]["content"] = "changed"
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0].headers["authorization"], "Bearer synthetic-worker-key"
        )

    async def test_queued_detach_does_not_cancel_owned_future(self):
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

    async def test_running_detach_keeps_provider_and_releases_worker(self):
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
            run_inspection.inspect(self.run_id)["stage2_execution"]["members"][0][
                "phase"
            ],
            "running",
        )
        release.set()
        await self.wait(handle)
        self.assertEqual(len(self.calls), 1)

    async def test_cancel_queued_blocks_dispatch_and_sibling_work_survives(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        sibling = self.manager._executor.submit(lambda: "unrelated")
        run_cancellation.request(self.run_id)
        release.set()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.wait(handle)
        self.assertEqual(await asyncio.wrap_future(sibling), "unrelated")
        self.assertEqual(self.calls, [])
        self.assertNotIn("stage2_execution", run_admission.get(self.run_id))

    async def test_delete_queued_blocks_dispatch_without_resurrection(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        storage.delete_conversation(f"cli-{self.run_id}")
        release.set()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.wait(handle)
        self.assertEqual(self.calls, [])

    async def test_queued_configuration_drift_cannot_change_saved_request(self):
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
        self.assertNotIn("stage2_execution", run_admission.get(self.run_id))

    async def test_active_cancel_prevents_late_result_and_releases_slot(self):
        started = threading.Event()

        async def blocked(request):
            started.set()
            await asyncio.Event().wait()

        handle = self.schedule(handler=blocked)
        await self.event(started)
        run_cancellation.request(self.run_id)
        with self.assertRaises(asyncio.CancelledError):
            await self.wait(handle)
        self.assertEqual(run_cancellation._tasks, {})
        self.assertEqual(execution._active, set())
        self.assertEqual(
            await asyncio.wrap_future(self.manager._executor.submit(lambda: 42)), 42
        )

    async def test_uncertain_attempt_requires_token_and_repeated_consent_calls_once(
        self,
    ):
        def fail(request):
            self.calls.append(request)
            return httpx.Response(503)

        with self.assertRaises(execution.ProviderFailure):
            await self.wait(self.schedule(handler=fail))
        token = run_inspection.inspect(self.run_id)["stage2_execution"]["members"][0][
            "retry_token"
        ]
        with self.assertRaises(execution.RetryRequired):
            await self.wait(self.schedule())
        with self.assertRaises(execution.ExecutionConflict):
            await self.wait(self.schedule(retry_token=str(uuid4())))
        await self.wait(self.schedule(retry_token=token))
        await self.wait(self.schedule(retry_token=token))
        self.assertEqual(len(self.calls), 2)

    async def test_partial_releases_slot_without_continuation_or_sibling_dispatch(self):
        snapshot = await self.wait(
            self.schedule(handler=lambda r: self.complete(r, "length"))
        )
        self.assertEqual(
            snapshot["stage2_execution"]["members"][0]["phase"], "needs_continuation"
        )
        self.assertEqual(
            snapshot["stage2_execution"]["members"][1]["phase"], "not_started"
        )
        await self.wait(self.schedule())
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(
            "stage2_continuation_execution", run_admission.get(self.run_id)
        )

    async def test_legacy_ownership_excluded_both_directions(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        with self.assertRaises(execution.ExecutionConflict):
            self.manager.submit(f"cli-{self.run_id}", "Legacy")
        release.set()
        await self.wait(handle)
        conversation = f"cli-{self.run_id}"
        self.manager._futures[conversation] = Future()
        try:
            with self.assertRaises(execution.ExecutionConflict):
                self.schedule()
        finally:
            self.manager._futures.pop(conversation)
        self.manager._queues[conversation] = [object()]
        try:
            with self.assertRaises(execution.ExecutionConflict):
                self.schedule()
        finally:
            self.manager._queues.pop(conversation)

    async def test_persisted_legacy_owner_blocks_before_recovery(self):
        conversation = f"cli-{self.run_id}"
        job_id = str(uuid4())
        consultation_journal.create_job(
            job_id, conversation, "Pending legacy work", None, status="queued"
        )
        with self.assertRaises(execution.ExecutionConflict):
            self.schedule()
        self.assertEqual(self.manager._cli_stage2_steps, {})
        self.assertEqual(self.calls, [])
        self.assertIsNotNone(consultation_journal.get_job(job_id))

    async def test_orphan_legacy_placeholder_blocks_before_recovery(self):
        conversation = f"cli-{self.run_id}"
        storage.add_user_message(conversation, "Unfinished legacy request")
        storage.add_assistant_placeholder(conversation, metadata={"status": "stage2"})
        self.assertEqual(consultation_journal.list_jobs(), [])
        with self.assertRaises(execution.ExecutionConflict):
            self.schedule()
        self.assertEqual(self.manager._cli_stage2_steps, {})
        self.assertEqual(self.calls, [])
        self.assertTrue(storage.list_incomplete_assistant_messages())

    async def test_terminal_and_unrelated_legacy_journals_do_not_block(self):
        consultation_journal.create_job(
            str(uuid4()), f"cli-{self.run_id}", "Finished", None, status="complete"
        )
        consultation_journal.create_job(
            str(uuid4()), str(uuid4()), "Other conversation", None, status="queued"
        )
        await self.wait(self.schedule())
        self.assertEqual(len(self.calls), 1)

    async def test_stale_unknown_member_and_invalid_key_do_not_queue(self):
        for kwargs in ({"revision": 2}, {"model": "unknown"}, {"api_key": ""}):
            with self.assertRaises(ValueError):
                self.schedule(**kwargs)
        self.assertEqual(self.manager._cli_stage2_steps, {})
        self.assertEqual(self.calls, [])

    async def test_two_members_run_concurrently_with_separate_ownership(self):
        self.manager._executor.shutdown(wait=True)
        self.manager._executor = ThreadPoolExecutor(max_workers=2)
        started = {model: threading.Event() for model in self.models}
        release = threading.Event()
        self.addCleanup(release.set)

        async def respond(request):
            model = json.loads(request.content)["model"]
            started[model].set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return self.complete(request)

        handles = [self.schedule(model=model, handler=respond) for model in self.models]
        for event in started.values():
            await self.event(event)
        self.assertEqual(len(self.manager._cli_stage2_steps), 2)
        self.assertEqual(len(execution._active), 2)
        self.assertTrue(
            all(
                m["phase"] == "running"
                for m in run_inspection.inspect(self.run_id)["stage2_execution"][
                    "members"
                ]
            )
        )
        release.set()
        await asyncio.gather(*(self.wait(handle) for handle in handles))
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"],
            "stage2_attempts_complete",
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.manager._cli_stage2_steps, {})

    async def test_queued_stale_revision_cannot_dispatch(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        path = run_admission._path(self.run_id)
        record = json.loads(path.read_text())
        record["interaction"]["revision"] += 1
        path.write_text(json.dumps(record))
        release.set()
        with self.assertRaises((ValueError, run_admission.AdmissionUnavailable)):
            await self.wait(handle)
        self.assertEqual(self.calls, [])
        self.assertNotIn("stage2_execution", json.loads(path.read_text()))

    async def test_detached_failed_worker_remains_observable(self):
        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule(handler=lambda request: httpx.Response(503))
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        with self.assertRaises(execution.ProviderFailure):
            await self.wait(handle)
        self.assertEqual(
            run_inspection.inspect(self.run_id)["stage2_execution"]["members"][0][
                "phase"
            ],
            "needs_retry",
        )

    async def test_missing_prepared_inputs_never_queues_or_prepares_them(self):
        path = run_admission._path(self.run_id)
        record = json.loads(path.read_text())
        record.pop("stage2_inputs")
        path.write_text(json.dumps(record))
        with self.assertRaises(execution.ExecutionConflict):
            self.schedule()
        self.assertEqual(self.manager._cli_stage2_steps, {})
        self.assertNotIn("stage2_inputs", run_admission.get(self.run_id))
        self.assertEqual(self.calls, [])

    async def test_stage1_and_refinement_ownership_exclude_reviewer(self):
        from backend.consultation_manager import RefinementStepHandle, Stage1StepHandle

        prior = Future()
        self.manager._cli_stage1_steps[(self.run_id, self.models[1])] = (
            Stage1StepHandle(self.run_id, 3, None, prior, self.models[1], 0)
        )
        try:
            with self.assertRaises(execution.ExecutionConflict):
                self.schedule()
        finally:
            self.manager._cli_stage1_steps.clear()
        self.manager._cli_refinement_steps[self.run_id] = RefinementStepHandle(
            self.run_id, 0, None, prior
        )
        try:
            with self.assertRaises(execution.ExecutionConflict):
                self.schedule()
        finally:
            self.manager._cli_refinement_steps.clear()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.manager._cli_stage2_steps, {})

    async def test_reviewer_ownership_blocks_stage1_admission(self):
        from backend import run_stage1_execution

        started, release = self.block_pool()
        await self.event(started)
        handle = self.schedule()
        with self.assertRaises(run_stage1_execution.ExecutionConflict):
            self.manager.schedule_cli_stage1_member(
                self.run_id,
                self.models[1],
                expected_revision=6,
                api_key="unused-synthetic-key",
                _transport=httpx.MockTransport(self.complete),
            )
        self.assertEqual(self.manager._cli_stage1_steps, {})
        release.set()
        await self.wait(handle)
        self.assertEqual(len(self.calls), 1)

    async def test_exact_saved_request_and_one_reviewer_only(self):
        before = run_admission.get(self.run_id)
        result = await self.wait(self.schedule())
        request = self.inputs.requests[0]
        self.assertEqual(json.loads(self.calls[0].content), request.payload())
        after = run_admission.get(self.run_id)
        after.pop("stage2_execution")
        self.assertEqual(before, after)
        self.assertEqual(
            result["stage2_execution"]["members"][1]["phase"], "not_started"
        )
        self.assertNotEqual(result["internal_phase"], "complete")

    async def test_shutdown_pool_rejects_without_durable_attempt(self):
        self.manager._executor.shutdown(wait=True)
        with self.assertRaises(RuntimeError):
            self.schedule()
        self.assertEqual(self.manager._cli_stage2_steps, {})
        self.assertNotIn("stage2_execution", run_admission.get(self.run_id))
        self.assertEqual(self.calls, [])
