"""Exercise one explicitly selected negative evaluator in the actual worker pool."""

import asyncio
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import test_run_stage2_inputs as fixtures

from backend import (
    consultation_journal,
    run_admission,
    run_cancellation,
    run_negative_execution,
    run_negative_inputs,
    run_stage1_execution,
)
from backend.consultation_manager import (
    ConsultationManager,
    RefinementStepHandle,
    Stage1StepHandle,
    Stage2StepHandle,
)


class NegativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        fixture = fixtures.Stage2InputTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        journal = tempfile.TemporaryDirectory()
        self.addCleanup(journal.cleanup)
        scope = patch.object(
            consultation_journal, "_CONSULTATION_DIR", Path(journal.name)
        )
        scope.start()
        self.addCleanup(scope.stop)
        fixture.complete(
            {"exclude_responses": ["provider/first"]},
            negative=True,
            requested="Check the evidence",
        )
        self.run_id = fixture.run
        self.excluded = fixture.models[0]
        self.inputs = run_negative_inputs.bind(self.run_id, expected_revision=6)
        self.model = self.inputs.requests[0].model
        self.manager = object.__new__(ConsultationManager)
        self.manager._initialized = False
        with patch("backend.consultation_manager.CONSULTATION_POOL_SIZE", 1):
            self.manager.__init__()
        self.addCleanup(self.manager.shutdown)
        self.calls: list[httpx.Request] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Raw critique"},
                    }
                ]
            },
        )

    def schedule(self, *, model=None, revision=6, key="synthetic-key", retry=None):
        return self.manager.schedule_cli_negative_evaluator(
            self.run_id,
            self.model if model is None else model,
            expected_revision=revision,
            api_key=key,
            retry_token=retry,
            _transport=httpx.MockTransport(self.respond),
        )

    async def block_pool(self) -> threading.Event:
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def block() -> None:
            entered.set()
            release.wait(6)

        self.manager._executor.submit(block)
        while not entered.is_set():
            await asyncio.sleep(0.005)
        return release

    async def test_queued_duplicate_detach_and_completed_replay(self) -> None:
        release = await asyncio.wait_for(self.block_pool(), 3)
        handle = self.schedule()
        self.assertIs(handle, self.schedule(key="different-synthetic-key"))
        for values in (
            {"revision": 7},
            {"retry": str(uuid4())},
            {"model": self.excluded},
        ):
            with self.assertRaises(run_negative_execution.ExecutionConflict):
                self.schedule(**values)
        with self.assertRaises(run_negative_execution.ExecutionConflict):
            self.manager.submit(f"cli-{self.run_id}", "Legacy overlap")
        with self.assertRaisesRegex(
            run_stage1_execution.ExecutionConflict, "other consultation work"
        ):
            self.manager.schedule_cli_stage1_member(
                self.run_id,
                self.model,
                expected_revision=6,
                api_key="stage-one-synthetic-key",
                _transport=httpx.MockTransport(self.respond),
            )
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(handle._future.cancelled())
        release.set()
        saved, separate = await asyncio.wait_for(
            asyncio.gather(handle.wait(), handle.wait()), 3
        )
        self.assertEqual(saved["internal_phase"], "negative_attempts_complete")
        self.assertEqual(saved["negative_execution"]["members"][0]["phase"], "complete")
        self.assertIsNone(saved["stage2_execution"])
        saved["request"]["content"] = "mutated"
        self.assertNotEqual(saved, separate)
        await asyncio.wait_for(self.schedule().wait(), 3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].headers["authorization"], "Bearer synthetic-key")
        self.assertNotIn("synthetic-key", repr(handle))
        self.assertNotIn("synthetic-key", run_admission._path(self.run_id).read_text())

    async def test_durable_cancellation_before_dispatch_is_not_waiter_detachment(
        self,
    ) -> None:
        release = await asyncio.wait_for(self.block_pool(), 3)
        handle = self.schedule()
        run_cancellation.request(self.run_id)
        release.set()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await asyncio.wait_for(handle.wait(), 3)
        self.assertEqual(self.calls, [])
        self.assertNotIn("negative_execution", run_admission.get(self.run_id))

    async def test_rejects_existing_legacy_owner_before_queueing(self) -> None:
        with patch.object(self.manager, "_has_legacy_owner", return_value=True):
            with self.assertRaises(run_negative_execution.ExecutionConflict):
                self.schedule()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.manager._cli_negative_steps, {})

    async def test_rejects_other_manager_owners_at_queue_admission(self) -> None:
        reports = (
            ("_cli_refinement_steps", self.run_id, RefinementStepHandle, ()),
            (
                "_cli_stage1_steps",
                (self.run_id, self.excluded),
                Stage1StepHandle,
                (self.excluded,),
            ),
            (
                "_cli_stage2_steps",
                (self.run_id, self.excluded),
                Stage2StepHandle,
                (self.excluded,),
            ),
        )
        for attribute, key, receipt, args in reports:
            with self.subTest(owner=attribute):
                with patch.object(
                    self.manager,
                    attribute,
                    {key: receipt(self.run_id, 6, None, Future(), *args)},
                ):
                    with self.assertRaisesRegex(
                        run_negative_execution.ExecutionConflict,
                        "other consultation work",
                    ):
                        self.schedule()
        self.assertEqual(self.calls, [])

    async def test_running_durable_cancel_releases_owner_without_result(self) -> None:
        entered = threading.Event()

        async def blocked(_: httpx.Request) -> httpx.Response:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("The provider should be cancelled")

        handle = self.manager.schedule_cli_negative_evaluator(
            self.run_id,
            self.model,
            expected_revision=6,
            api_key="synthetic-key",
            _transport=httpx.MockTransport(blocked),
        )

        async def wait_for_dispatch() -> None:
            while not entered.is_set():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait_for_dispatch(), 3)
        run_cancellation.request(self.run_id)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(handle.wait(), 3)
        await asyncio.wrap_future(self.manager._executor.submit(lambda: None))
        self.assertEqual(self.manager._cli_negative_steps, {})
        self.assertIsNone(run_negative_execution.load_result(self.run_id, self.model))
