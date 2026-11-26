"""Whole basic consultations with real persistence and fake, counted providers."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

# Set before backend.config loads. CI has no .env, and the CLI integration test's
# fixture server imports this module first.
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    basic_runs,
    config,
    model_context,
    run_admission,
    run_stage1_provider,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest


class BasicRunsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for obj, key, value in [
            (storage, "DATA_DIR", str(Path(self.temp.name) / "conversations")),
            *[
                (config, key, value)
                for key, value in {
                    "MODEL_BACKEND": "openrouter",
                    "SYNTHESIS_BACKEND": "openrouter",
                    "NEGATIVE_MODE_ENABLED": False,
                    "DELIBERATION_ENABLED": False,
                    "SKILLS_ENABLED": True,
                    "SKILLS_REFERENCE_SELECTION_ENABLED": False,
                    "SKILLS_AUTO_SELECT_ENABLED": False,
                    "COUNCIL_MODELS": ["test/a", "test/b"],
                    "CHAIRMAN_MODEL": "test/chair",
                    "OPENROUTER_API_KEY": "synthetic",
                    "OPENROUTER_API_URL": "https://openrouter.ai/api/v1/chat/completions",
                }.items()
            ],
        ]:
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []
        self.provider_fails = False
        self.hold = None

        async def provider(**kwargs):
            self.calls.append(kwargs)
            if self.hold is not None:
                await self.hold.wait()
            if self.provider_fails and kwargs["payload"]["model"] == "test/a":
                raise RuntimeError("sensitive provider diagnostics")
            model = kwargs["payload"]["model"]
            return {
                "content": "Final answer"
                if model == "test/chair"
                else "Answer\nFINAL RANKING:\n1. Response A\n2. Response B",
                "finish_reason": "stop",
                "usage": {},
            }

        p = patch.object(run_stage1_provider, "_provider", return_value=provider)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(
            model_context,
            "model_limits",
            AsyncMock(
                return_value={
                    "context_length": 128000,
                    "max_completion_tokens": 8192,
                    "supported_parameters": [],
                }
            ),
        )
        p.start()
        self.addCleanup(p.stop)
        self.run = str(uuid4())
        self.request = CreateRunRequest(
            content="Question",
            interaction_policy="persistent",
            recovery_policy="explicit_retry",
        )

    async def asyncTearDown(self) -> None:
        await basic_runs.shutdown()

    async def settle(self) -> None:
        tasks = list(basic_runs._tasks.values())
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    async def start(self) -> None:
        basic_runs.create(self.run, self.request)
        await self.settle()
        self.assertEqual(
            basic_runs.snapshot(self.run).lifecycle.value,
            "waiting_input",
            basic_runs.snapshot(self.run),
        )

    async def answer(self) -> None:
        current = basic_runs.snapshot(self.run)
        basic_runs.answer(
            self.run,
            str(current.pending_prompt.instance_id),
            AnswerRequest(skipped=True),
        )
        await self.settle()

    async def test_complete_reopen_export_and_idempotency(self) -> None:
        await self.start()
        await self.answer()
        self.assertEqual(
            basic_runs.snapshot(self.run).lifecycle.value,
            "waiting_input",
            basic_runs.snapshot(self.run),
        )
        await self.answer()
        state = basic_runs.snapshot(self.run)
        self.assertEqual(state.lifecycle.value, "complete", state)
        self.assertEqual(state.stages["stage3"]["current"]["response"], "Final answer")
        self.assertEqual(len(self.calls), 5)
        self.assertIn("Final answer", basic_runs.export(self.run))
        basic_runs.create(self.run, self.request)
        basic_runs.schedule(self.run)
        await self.settle()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(basic_runs.snapshot(self.run), state)
        self.assertEqual(len(basic_runs.history()), 1)

    async def test_uncertain_requires_exact_explicit_consent(self) -> None:
        await self.start()
        self.provider_fails = True
        await self.answer()
        state = basic_runs.snapshot(self.run)
        self.assertEqual(state.lifecycle.value, "needs_retry", state)
        self.assertNotIn("sensitive", state.model_dump_json())
        basic_runs.schedule(self.run)
        await self.settle()
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(ValueError):
            basic_runs.retry(self.run, str(uuid4()))
        self.provider_fails = False
        basic_runs.retry(self.run, state.retry.revision)
        await self.settle()
        self.assertEqual(len(self.calls), 3)
        basic_runs.retry(self.run, state.retry.revision)
        await self.settle()
        self.assertEqual(len(self.calls), 3)
        await self.answer()
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "complete")

    async def test_restart_wait_and_cancel_retains_no_new_work(self) -> None:
        await self.start()
        waiting = basic_runs.snapshot(self.run)
        await basic_runs.shutdown()
        self.assertEqual(basic_runs.snapshot(self.run), waiting)
        with self.assertRaises(ValueError):
            basic_runs.cancel(self.run, "stale")
        basic_runs.cancel(self.run, waiting.cursor)
        basic_runs.schedule(self.run)
        await self.settle()
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "cancelled")
        self.assertEqual(self.calls, [])

    async def test_reject_unsupported_before_admission(self) -> None:
        with patch.object(config, "DELIBERATION_ENABLED", True):
            with self.assertRaises(ValueError):
                basic_runs.create(self.run, self.request)
        self.assertIsNone(run_admission.get(self.run))

    async def test_delete_prevents_resurrection(self) -> None:
        await self.start()
        record = run_admission.get(self.run)
        storage.delete_conversation(record["conversation_id"])
        with self.assertRaises(ValueError):
            basic_runs.create(self.run, self.request)
        self.assertEqual(self.calls, [])

    async def test_old_answer_retry_cannot_reopen_complete_run(self) -> None:
        await self.start()
        instance = str(basic_runs.snapshot(self.run).pending_prompt.instance_id)
        await self.answer()
        await self.answer()
        before = basic_runs.snapshot(self.run)
        basic_runs.answer(self.run, instance, AnswerRequest(skipped=True))
        await self.settle()
        self.assertEqual(basic_runs.snapshot(self.run), before)
        self.assertEqual(len(self.calls), 5)

    async def test_cancel_active_only_targets_named_run(self) -> None:
        await self.start()
        self.hold = asyncio.Event()
        current = basic_runs.snapshot(self.run)
        basic_runs.answer(
            self.run,
            str(current.pending_prompt.instance_id),
            AnswerRequest(skipped=True),
        )
        for _ in range(100):
            if len(self.calls) == 2:
                break
            await asyncio.sleep(0.01)
        other = str(uuid4())
        basic_runs.create(other, self.request)
        await asyncio.sleep(0.01)
        basic_runs.cancel(self.run, basic_runs.snapshot(self.run).cursor)
        self.hold.set()
        await self.settle()
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "cancelled")
        self.assertEqual(basic_runs.snapshot(other).lifecycle.value, "waiting_input")
        self.assertNotIn("stage3", basic_runs.snapshot(self.run).stages)

    async def test_restart_in_flight_never_repeats_provider(self) -> None:
        await self.start()
        self.hold = asyncio.Event()
        current = basic_runs.snapshot(self.run)
        basic_runs.answer(
            self.run,
            str(current.pending_prompt.instance_id),
            AnswerRequest(skipped=True),
        )
        for _ in range(100):
            if len(self.calls) == 2:
                break
            await asyncio.sleep(0.01)
        await basic_runs.shutdown()
        basic_runs.schedule(self.run)
        await self.settle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "needs_retry")

    async def test_synthesis_retry_preserves_completed_stages(self) -> None:
        await self.start()
        await self.answer()
        real = run_stage1_provider._provider("synthetic", None)

        async def provider(**kwargs):
            if kwargs["payload"]["model"] == "test/chair":
                raise RuntimeError("synthetic uncertain synthesis")
            return await real(**kwargs)

        with patch.object(run_stage1_provider, "_provider", return_value=provider):
            await self.answer()
        state = basic_runs.snapshot(self.run)
        self.assertEqual(state.retry.operation, "stage3")
        self.assertEqual(len(self.calls), 4)
        basic_runs.retry(self.run, state.retry.revision)
        await self.settle()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "complete")

    async def _complete_with_length_limited_synthesis(self) -> None:
        await self.start()
        await self.answer()
        real = run_stage1_provider._provider("synthetic", None)

        async def provider(**kwargs):
            result = await real(**kwargs)
            if kwargs["payload"]["model"] == "test/chair":
                return {
                    **result,
                    "content": "Unfinished synthesis sentinel",
                    "finish_reason": "length",
                    "usage": {"completion_tokens": 8192},
                }
            return result

        with patch.object(run_stage1_provider, "_provider", return_value=provider):
            await self.answer()

    async def test_synthesis_length_is_saved_without_final_answer_or_retry(
        self,
    ) -> None:
        await self._complete_with_length_limited_synthesis()

        state = basic_runs.snapshot(self.run)
        self.assertEqual(state.lifecycle.value, "failed", state)
        self.assertIn("output limit", state.error)
        self.assertIsNone(state.retry)
        self.assertIsNone(state.result)
        self.assertEqual(set(state.stages), {"stage1", "stage2"})
        self.assertNotIn("Unfinished synthesis sentinel", state.model_dump_json())
        self.assertNotIn("Unfinished synthesis sentinel", basic_runs.export(self.run))
        saved = basic_runs._record(self.run)["basic_run"]["synthesis"]
        self.assertEqual(saved["result"]["finish_reason"], "length")
        self.assertEqual(saved["result"]["content"], "Unfinished synthesis sentinel")
        self.assertEqual(saved["result"]["usage"], {"completion_tokens": 8192})

        with self.assertRaisesRegex(ValueError, "Retry revision is stale"):
            basic_runs.retry(self.run, saved["attempt"])
        basic_runs.create(self.run, self.request)
        basic_runs.schedule(self.run)
        await self.settle()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(basic_runs.snapshot(self.run), state)

    async def test_restart_after_saving_synthesis_length_does_not_call_provider(
        self,
    ) -> None:
        await self._complete_with_length_limited_synthesis()
        before = basic_runs._record(self.run)["basic_run"]["synthesis"]
        self.assertIsNotNone(before["result"])
        # Simulate an interruption after the saved result, before the failure status.
        basic_runs._update(self.run, lifecycle="running", error=None)
        await basic_runs.shutdown()
        basic_runs.schedule(self.run)
        await self.settle()

        self.assertEqual(len(self.calls), 5)
        state = basic_runs.snapshot(self.run)
        self.assertEqual(state.lifecycle.value, "failed", state)
        self.assertIn("output limit", state.error)
        self.assertIsNone(state.retry)
        self.assertNotIn("stage3", state.stages)
        self.assertEqual(basic_runs._record(self.run)["basic_run"]["synthesis"], before)

    async def test_answer_before_owner_release_reschedules_once(self) -> None:
        ready, release = asyncio.Event(), asyncio.Event()
        drive = basic_runs._bounded_drive
        first = True

        async def delayed_release(run: str, capacity: asyncio.Semaphore) -> None:
            nonlocal first
            await drive(run, capacity)
            if first:
                first = False
                ready.set()
                await release.wait()

        with patch.object(basic_runs, "_bounded_drive", delayed_release):
            basic_runs.create(self.run, self.request)
            await asyncio.wait_for(ready.wait(), 2)
            owner = basic_runs._tasks[self.run]
            prompt = basic_runs.snapshot(self.run).pending_prompt
            basic_runs.answer(
                self.run, str(prompt.instance_id), AnswerRequest(skipped=True)
            )
            release.set()
            await owner
            await asyncio.sleep(0)
            await self.settle()
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "waiting_input")
        self.assertEqual(len(self.calls), 2)

    async def test_answer_replay_after_interrupted_handoff_resumes(self) -> None:
        from backend import run_interaction

        await self.start()
        prompt = basic_runs.snapshot(self.run).pending_prompt
        answer = AnswerRequest(skipped=True)
        run_interaction.answer(self.run, str(prompt.instance_id), answer)
        basic_runs.answer(self.run, str(prompt.instance_id), answer)
        await self.settle()
        self.assertEqual(basic_runs.snapshot(self.run).lifecycle.value, "waiting_input")
        self.assertEqual(len(self.calls), 2)

    async def test_history_omits_large_stage_bodies(self) -> None:
        import json

        await self.start()
        basic_runs._save_stage(
            self.run, "stage1", [{"model": "test/a", "response": "x" * 400_000}]
        )
        history = basic_runs.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["stages"], {})
        self.assertLess(len(json.dumps(history)), 20_000)
        self.assertEqual(
            len(basic_runs.snapshot(self.run).stages["stage1"][0]["response"]), 400_000
        )
