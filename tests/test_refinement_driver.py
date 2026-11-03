"""Exercise the composed internal refinement loop with synthetic HTTP only."""

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

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

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


class RefinementDriverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = str(Path(self.directory.name) / "conversations")
        for p in (
            patch.object(storage, "DATA_DIR", self.data_dir),
            patch.multiple(
                config,
                MODEL_BACKEND="openrouter",
                PROMPT_REFINEMENT_MODEL="fake/refiner",
                OPENROUTER_API_URL="https://fixture.invalid/completions",
                OPENROUTER_MAX_TOKENS=1024,
            ),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.run_id = self.prepare()
        self.path = Path(self.directory.name) / "run_receipts" / f"{self.run_id}.json"
        self.calls = []

    def prepare(self, refine=True):
        run_id = str(uuid4())
        run_admission.prepare(
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

    async def step(self, revision, handler=None, **kwargs):
        return await refinement_driver.advance(
            kwargs.pop("run_id", self.run_id),
            expected_revision=revision,
            api_key=kwargs.pop("api_key", "ephemeral-secret"),
            _transport=httpx.MockTransport(handler or self.complete),
            **kwargs,
        )

    def answer(self, action="use_refined"):
        snapshot = run_inspection.inspect(self.run_id)
        instance = snapshot["current_prompt"]["instance_id"]
        run_interaction.answer(
            self.run_id,
            instance,
            AnswerRequest(
                responses={
                    "refinement_action": action,
                    "refined_prompt": "Edited — вопрос",
                    "clarifying_answer_0": "Scope answer"
                    if action == "refine_again"
                    else "",
                    "additional_insights": "Keep context"
                    if action == "refine_again"
                    else "",
                }
            ),
        )
        return instance

    async def test_two_rounds_return_at_each_boundary_without_implicit_dispatch(self):
        waiting = await self.step(0)
        self.assertEqual(waiting["internal_phase"], "waiting_input")
        self.assertEqual(waiting["interaction"]["revision"], 1)
        before = self.path.read_bytes()
        self.assertEqual(await self.step(1, api_key=None), waiting)
        self.assertEqual(self.path.read_bytes(), before)
        self.answer("refine_again")
        ready = await self.step(2, api_key=None)
        self.assertEqual(ready["internal_phase"], "refinement_ready")
        self.assertEqual(ready["interaction"]["revision"], 3)
        self.assertEqual(len(self.calls), 1)
        second = await self.step(3)
        self.assertEqual(second["interaction"]["revision"], 4)
        self.assertNotEqual(
            second["current_prompt"]["instance_id"],
            waiting["current_prompt"]["instance_id"],
        )
        self.assertIn("Scope answer", self.calls[1].content.decode())
        self.answer()
        done = await self.step(5, api_key=None)
        self.assertEqual(done["internal_phase"], "refinement_complete")
        self.assertEqual(done["refinement"]["refined_prompt"], "Edited — вопрос")
        before = self.path.read_bytes()
        with patch.object(config, "MODEL_BACKEND", "acp"):
            self.assertEqual(await self.step(6, api_key=None), done)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(any(key[0] == self.run_id for key in run_cancellation._tasks))
        self.assertFalse(
            any(key[0] == self.run_id for key in refinement_execution._active)
        )
        self.assertNotIn(b"ephemeral-secret", before)

    async def test_delayed_duplicate_never_consumes_or_starts_later_work(self):
        await self.step(0)
        self.answer("refine_again")
        await self.step(2)
        await self.step(3)
        self.answer()
        before = self.path.read_bytes()
        for stale in (0, 1, 2, 3, 4):
            with self.subTest(stale=stale):
                with self.assertRaises(refinement_execution.ExecutionConflict):
                    await self.step(stale)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 2)

    async def test_waiting_survives_new_process_without_network_or_credentials(self):
        expected = await self.step(0)
        before = self.path.read_bytes()
        code = """
import asyncio, json, socket, sys
from backend import refinement_driver, storage
storage.DATA_DIR = sys.argv[1]
def forbidden(*args, **kwargs): raise AssertionError("No network allowed")
socket.socket.connect = forbidden
print(json.dumps(asyncio.run(refinement_driver.advance(sys.argv[2], expected_revision=1))))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, self.data_dir, self.run_id],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_uncertain_work_requires_explicit_matching_retry(self):
        def fail(request):
            self.calls.append(request)
            raise httpx.ReadError("private failure", request=request)

        with self.assertRaises(refinement_provider.ProviderFailure):
            await self.step(0, fail)
        uncertain = run_inspection.inspect(self.run_id)
        token = uncertain["retry_token"]
        before = self.path.read_bytes()
        with self.assertRaises(refinement_execution.RetryRequired) as raised:
            await self.step(0, api_key=None)
        self.assertEqual(raised.exception.token, token)
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.step(0, retry_token=str(uuid4()))
        with patch.object(config, "PROMPT_REFINEMENT_MODEL", "changed/model"):
            with self.assertRaises(refinement_execution.ExecutionConflict):
                await self.step(0, retry_token=token)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        result = await self.step(0, retry_token=token)
        self.assertEqual(result["internal_phase"], "waiting_input")
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.step(0, retry_token=token)
        self.assertEqual(len(self.calls), 2)

    async def test_concurrent_suggestions_are_single_owned_operation(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(request):
            entered.set()
            await release.wait()
            return self.complete(request)

        first = asyncio.create_task(self.step(0, slow))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            with self.assertRaises(refinement_execution.ExecutionPending):
                await self.step(0)
        finally:
            release.set()
            await first
        self.assertEqual(len(self.calls), 1)

    async def test_concurrent_answer_consumers_do_not_start_next_round(self):
        await self.step(0)
        self.answer("refine_again")
        results = await asyncio.gather(
            self.step(2), self.step(2), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(
            sum(isinstance(r, refinement_execution.ExecutionConflict) for r in results),
            1,
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "refinement_ready"
        )

    async def test_cancellation_returns_evidence_and_never_advances_answer(self):
        await self.step(0)
        self.answer()
        run_cancellation.request(self.run_id)
        before = self.path.read_bytes()
        result = await self.step(2, api_key=None)
        self.assertEqual(result["internal_phase"], "cancelled")
        self.assertEqual(result["interaction"]["prompts"][-1]["phase"], "answered")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_between_inspection_and_dispatch_prevents_request(self):
        original = refinement_provider.suggest_configured_refinement

        async def cancel_first(run_id, **kwargs):
            run_cancellation.request(run_id)
            return await original(run_id, **kwargs)

        with patch.object(
            refinement_provider, "suggest_configured_refinement", cancel_first
        ):
            with self.assertRaises(run_admission.AdmissionCancelled):
                await self.step(0)
        self.assertEqual(self.calls, [])
        self.assertNotIn("refinement_provider", run_admission.get(self.run_id))

    async def test_cancelling_one_running_step_leaves_sibling_usable(self):
        entered = asyncio.Event()

        async def slow(request):
            entered.set()
            await asyncio.Event().wait()

        first = asyncio.create_task(self.step(0, slow))
        await asyncio.wait_for(entered.wait(), 2)
        run_cancellation.request(self.run_id)
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual(
            (await self.step(0, api_key=None))["internal_phase"], "cancelled"
        )
        sibling = self.prepare()
        self.assertEqual(
            (await self.step(0, run_id=sibling))["internal_phase"], "waiting_input"
        )

    async def test_invalid_stale_deleted_nonrefinement_fail_without_calls(self):
        before = self.path.read_bytes()
        for value in (-1, True, 1.0, "0"):
            with self.assertRaises(ValueError):
                await self.step(value)
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.step(1)
        with self.assertRaises(ValueError):
            await self.step(0, api_key=None)
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.step(0, retry_token=str(uuid4()))
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.step(0, run_id=self.prepare(refine=False))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.step(0, run_id=str(uuid4()))
        self.assertEqual(self.path.read_bytes(), before)
        storage.delete_conversation(run_admission.get(self.run_id)["conversation_id"])
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.step(0)
        self.assertEqual(self.calls, [])

    async def test_provider_config_drift_does_not_destroy_waiting_answer(self):
        waiting = await self.step(0)
        with patch.object(config, "MODEL_BACKEND", "acp"):
            self.assertEqual(await self.step(1, api_key=None), waiting)
            self.answer("refine_again")
            ready = await self.step(2, api_key=None)
            self.assertEqual(ready["internal_phase"], "refinement_ready")
            before = self.path.read_bytes()
            with self.assertRaises(refinement_execution.ExecutionConflict):
                await self.step(3)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_answer_write_failure_never_dispatches_and_can_resume_safely(self):
        await self.step(0)
        self.answer("refine_again")
        before = self.path.read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=OSError("disk unavailable")
        ):
            with self.assertRaises(OSError):
                await self.step(2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.step(2))["internal_phase"], "refinement_ready")

    async def test_lost_answer_consumption_ack_requires_reload_not_new_suggestion(self):
        await self.step(0)
        self.answer("refine_again")
        original = run_admission._write

        def save_then_fail(record):
            original(record)
            raise OSError("Lost write acknowledgement")

        with patch.object(run_admission, "_write", save_then_fail):
            with self.assertRaises(OSError):
                await self.step(2)
        saved = run_inspection.inspect(self.run_id)
        self.assertEqual(saved["internal_phase"], "refinement_ready")
        self.assertEqual(saved["interaction"]["revision"], 3)
        before = self.path.read_bytes()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.step(2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.step(3))["internal_phase"], "waiting_input")
        self.assertEqual(len(self.calls), 2)

    async def test_damaged_checkpoint_fails_closed_without_repair_or_dispatch(self):
        await self.step(0)
        saved = json.loads(self.path.read_text())
        saved["interaction"]["prompts"][0]["continuation"]["state"][
            "original_query"
        ] = "Changed"
        self.path.write_text(json.dumps(saved))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.step(1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
