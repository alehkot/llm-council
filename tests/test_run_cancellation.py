"""Run-specific internal cancellation with synthetic, non-network providers."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    durable_refinement,
    refinement_execution,
    run_admission,
    run_cancellation,
    run_interaction,
    storage,
)
from backend.consultation_contract import (
    AnswerRequest,
    CreateRunRequest,
    PromptInstance,
)
from backend.interactive import get_prompt_refinement_prompt
from backend.refinement import RefinementState

SUGGESTION = {
    "intent": "Intent",
    "clarifying_questions": [],
    "refined_prompt": "Refined",
}


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.patch = patch.object(
            storage, "DATA_DIR", str(self.directory / "conversations")
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.request = CreateRunRequest(
            content="Question",
            refine=True,
            interaction_policy="persistent",
            recovery_policy="explicit_retry",
        )
        self.run = self.prepare()

    def prepare(self):
        identity = str(uuid4())
        run_admission.prepare(identity, self.request)
        return identity

    async def suggest(self, provider, run=None, **kwargs):
        return await refinement_execution.suggest(
            run or self.run,
            provider,
            provider_key="fake-v1",
            expected_revision=0,
            **kwargs,
        )

    async def result(self, **kwargs):
        return dict(SUGGESTION)

    def test_prepared_cancellation_idempotent_and_admission_cannot_resurrect(self):
        first = run_cancellation.request(self.run)
        self.assertEqual(first["phase"], "cancelled")
        before = run_admission._path(self.run).read_bytes()
        self.assertEqual(first, run_cancellation.request(self.run))
        self.assertEqual(first, run_cancellation.inspect(self.run))
        self.assertEqual(run_admission._path(self.run).read_bytes(), before)
        self.assertEqual(
            run_admission.prepare(self.run, self.request)["cancellation"], first
        )
        with self.assertRaises(run_admission.AdmissionCancelled):
            run_admission.get_prepared(self.run)

    async def test_cancelled_run_never_dispatches(self):
        run_cancellation.request(self.run)
        calls = []

        async def provider(**kwargs):
            calls.append(kwargs)
            return SUGGESTION

        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.suggest(provider)
        self.assertEqual(calls, [])

    async def test_active_task_cancelled_sibling_unaffected_and_caller_survives(self):
        started, sibling_started, release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def provider(**kwargs):
            started.set()
            await asyncio.Event().wait()

        async def sibling(**kwargs):
            sibling_started.set()
            await release.wait()
            return SUGGESTION

        first = asyncio.create_task(self.suggest(provider))
        second = asyncio.create_task(self.suggest(sibling, run=self.prepare()))
        await started.wait()
        await sibling_started.wait()
        state = await asyncio.to_thread(run_cancellation.request, self.run)
        self.assertEqual(state["phase"], "cancelling")
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(second.done())
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")
        release.set()
        self.assertEqual((await second).phase, "waiting_answer")
        await asyncio.sleep(0)  # delayed cancellation cannot hit this caller

    async def test_suppressed_cancellation_stays_cancelling_and_late_result_rejected(
        self,
    ):
        started, caught, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def provider(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                caught.set()
                await release.wait()
            return SUGGESTION

        task = asyncio.create_task(self.suggest(provider))
        await started.wait()
        run_cancellation.request(self.run)
        await caught.wait()
        for _ in range(3):
            self.assertEqual(run_cancellation.request(self.run)["phase"], "cancelling")
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await task
        record = run_admission.get(self.run)
        self.assertEqual(record["cancellation"]["phase"], "cancelled")
        self.assertEqual(
            record["refinement_execution"]["operations"][0]["phase"], "in_flight"
        )
        self.assertNotIn("interaction", record)

    async def test_saved_partial_work_retained_answers_and_consumption_blocked(self):
        await self.suggest(self.result)
        prompt = run_interaction.get(self.run)["prompts"][0]
        before = run_admission.get(self.run)
        run_cancellation.request(self.run)
        after = run_admission.get(self.run)
        self.assertEqual(before["interaction"], after["interaction"])
        self.assertEqual(before["refinement_execution"], after["refinement_execution"])
        identity = prompt["instance"]["instance_id"]
        with self.assertRaises(run_admission.AdmissionCancelled):
            run_interaction.answer(
                self.run,
                identity,
                AnswerRequest(responses={"refinement_action": "use_original"}),
            )
        with self.assertRaises(run_admission.AdmissionCancelled):
            durable_refinement.resume_answer(self.run, identity)
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.suggest(self.result)

    async def test_uncertain_retry_blocked(self):
        async def failed(**kwargs):
            raise RuntimeError("fake failure")

        with self.assertRaises(RuntimeError):
            await self.suggest(failed)
        token = refinement_execution.inspect(self.run)["retry_token"]
        run_cancellation.request(self.run)
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.suggest(self.result, retry_token=token)

    def test_orphan_cancelling_finalized_without_provider(self):
        record = run_admission.get(self.run)
        record["cancellation"] = {
            "phase": "cancelling",
            "requested_at": "2026-11-02T00:00:00",
            "completed_at": None,
        }
        run_admission._write(record)
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")

    async def test_failed_write_before_replace_does_not_signal_and_repeat_can_cancel(
        self,
    ):
        started = asyncio.Event()

        async def provider(**kwargs):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.suggest(provider))
        await started.wait()
        with patch.object(run_admission, "_write", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                run_cancellation.request(self.run)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertNotIn("cancellation", run_admission.get(self.run))
        run_cancellation.request(self.run)
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_lost_write_ack_preserves_cancel_and_repeat_signals(self):
        started = asyncio.Event()

        async def provider(**kwargs):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.suggest(provider))
        await started.wait()
        real_write = run_admission._write

        def lost_ack(record):
            real_write(record)
            raise OSError("lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                run_cancellation.request(self.run)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.suggest(self.result)
        run_cancellation.request(self.run)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")

    def test_malformed_marker_fails_closed(self):
        path = run_admission._path(self.run)
        original = path.read_text()
        for marker in (
            None,
            {},
            {"phase": "cancelled", "requested_at": "bad", "completed_at": None},
        ):
            record = json.loads(original)
            record["cancellation"] = marker
            path.write_text(json.dumps(record))
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_admission.get_prepared(self.run)
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_cancellation.request(self.run)

    def test_deletion_erases_cancellation_and_prevents_recreation(self):
        run_cancellation.request(self.run)
        storage.delete_conversation(f"cli-{self.run}")
        record = run_admission.get(self.run)
        self.assertEqual(record["phase"], "deleted")
        self.assertNotIn("cancellation", record)
        with self.assertRaises(run_admission.AdmissionDeleted):
            run_cancellation.request(self.run)
        with self.assertRaises(run_admission.AdmissionDeleted):
            run_admission.prepare(self.run, self.request)

    async def test_cancel_before_provider_child_starts_never_enters_callback(self):
        calls = []

        async def provider(**kwargs):
            calls.append(kwargs)
            return SUGGESTION

        register = run_cancellation._register

        def cancel_at_registration(run_id, attempt, task):
            register(run_id, attempt, task)
            run_cancellation.request(run_id)

        with patch.object(
            run_cancellation, "_register", side_effect=cancel_at_registration
        ):
            with self.assertRaises(
                (run_admission.AdmissionCancelled, asyncio.CancelledError)
            ):
                await self.suggest(provider)
        self.assertEqual(calls, [])
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")

    def test_fresh_process_settles_cancellation_without_restoring_provider(self):
        record = run_admission.get(self.run)
        record["cancellation"] = {
            "phase": "cancelling",
            "requested_at": "2026-11-02T00:00:00",
            "completed_at": None,
        }
        run_admission._write(record)
        code = """
import json, sys
from backend import run_cancellation, run_admission, storage
storage.DATA_DIR = sys.argv[1]
print(json.dumps(run_cancellation.inspect(sys.argv[2])))
try:
    run_admission.get_prepared(sys.argv[2])
except run_admission.AdmissionCancelled:
    pass
else:
    raise AssertionError('cancelled run became runnable')
"""
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(json.loads(result.stdout)["phase"], "cancelled")
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")

    async def test_accepted_answer_retained_but_not_consumed_after_cancel(self):
        await self.suggest(self.result)
        identity = run_interaction.get(self.run)["prompts"][0]["instance"][
            "instance_id"
        ]
        run_interaction.answer(
            self.run,
            identity,
            AnswerRequest(responses={"refinement_action": "use_original"}),
        )
        saved = run_admission.get(self.run)["interaction"]
        run_cancellation.request(self.run)
        with self.assertRaises(run_admission.AdmissionCancelled):
            durable_refinement.resume_answer(self.run, identity)
        self.assertEqual(run_admission.get(self.run)["interaction"], saved)
        self.assertEqual(saved["prompts"][0]["phase"], "answered")

    def test_completion_marker_write_failure_is_repaired_by_inspection(self):
        real_write = run_admission._write

        def fail_completion(record):
            if record.get("cancellation", {}).get("phase") == "cancelled":
                raise OSError("completion write failed")
            real_write(record)

        with patch.object(run_admission, "_write", side_effect=fail_completion):
            with self.assertRaises(OSError):
                run_cancellation.request(self.run)
        self.assertEqual(
            run_admission.get(self.run)["cancellation"]["phase"], "cancelling"
        )
        with self.assertRaises(run_admission.AdmissionCancelled):
            run_admission.get_prepared(self.run)
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")

    def test_unknown_and_unprepared_run_refused_without_creation(self):
        unknown = str(uuid4())
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_cancellation.request(unknown)
        self.assertIsNone(run_admission.get(unknown))
        record = run_admission.get(self.run)
        record["phase"] = "initializing"
        run_admission._write(record)
        before = run_admission._path(self.run).read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_cancellation.request(self.run)
        self.assertEqual(run_admission._path(self.run).read_bytes(), before)

    async def test_delayed_signal_after_guard_exit_does_not_cancel_caller(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(**kwargs):
            started.set()
            await release.wait()
            return SUGGESTION

        task = asyncio.create_task(self.suggest(provider))
        await started.wait()
        callbacks = []
        with patch.object(
            asyncio.get_running_loop(),
            "call_soon_threadsafe",
            side_effect=lambda *args: callbacks.append(args),
        ):
            run_cancellation.request(self.run)
        self.assertEqual(len(callbacks), 1)
        release.set()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await task
        callback, *args = callbacks[0]
        callback(*args)
        await asyncio.sleep(0)
        self.assertEqual(run_cancellation.inspect(self.run)["phase"], "cancelled")
        self.assertFalse(asyncio.current_task().cancelled())

    async def test_changed_interaction_before_child_dispatch_never_enters_provider(
        self,
    ):
        calls = []

        async def provider(**kwargs):
            calls.append(kwargs)
            return SUGGESTION

        register = run_cancellation._register

        def change_at_registration(run_id, attempt, task):
            register(run_id, attempt, task)
            state = RefinementState.start("Question").accept_suggestion(SUGGESTION)
            instance = PromptInstance(
                run_id=run_id,
                instance_id=uuid4(),
                prompt=get_prompt_refinement_prompt(
                    SUGGESTION, iteration=state.iteration
                ).to_dict(),
            )
            run_interaction.put_prompt(
                run_id, instance, state.to_continuation(), expected_revision=0
            )

        with patch.object(
            run_cancellation, "_register", side_effect=change_at_registration
        ):
            with self.assertRaises(run_admission.AdmissionUnavailable):
                await self.suggest(provider)
        self.assertEqual(calls, [])
