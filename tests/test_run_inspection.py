"""Read-only internal run snapshots with synthetic local provider callbacks."""

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
    refinement_provider,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest

SUGGESTION = {
    "intent": "Intent",
    "clarifying_questions": ["Which audience?"],
    "refined_prompt": "Refined question",
}


class InspectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = str(self.directory / "conversations")
        self.storage_patch = patch.object(storage, "DATA_DIR", self.data)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.run = str(uuid4())
        self.request = CreateRunRequest(
            content="Question",
            refine=True,
            interaction_policy="persistent",
            recovery_policy="explicit_retry",
        )
        run_admission.prepare(self.run, self.request)

    def bytes(self):
        return {
            str(p.relative_to(self.directory)): p.read_bytes()
            for p in self.directory.rglob("*")
            if p.is_file()
        }

    def inspect(self):
        before = self.bytes()
        with (
            patch.object(
                run_admission, "_write", side_effect=AssertionError("read wrote")
            ),
            patch.object(
                run_cancellation, "inspect", side_effect=AssertionError("read settled")
            ),
            patch.object(
                refinement_execution,
                "suggest",
                side_effect=AssertionError("read dispatched"),
            ),
        ):
            value = run_inspection.inspect(self.run)
        self.assertEqual(before, self.bytes())
        return value

    async def result(self, **kwargs):
        return dict(SUGGESTION)

    async def suggest(self, provider=None, *, revision=0, provider_key="fake-v1"):
        return await refinement_execution.suggest(
            self.run,
            provider or self.result,
            provider_key=provider_key,
            expected_revision=revision,
        )

    def answer(self, action="use_refined", *, clarify=True):
        prompt = run_interaction.get(self.run)["prompts"][-1]
        run_interaction.answer(
            self.run,
            prompt["instance"]["instance_id"],
            AnswerRequest(
                responses={
                    "refinement_action": action,
                    **({"clarifying_answer_0": "Parents"} if clarify else {}),
                },
            ),
        )
        return prompt["instance"]["instance_id"]

    def alter(self, change):
        record = run_admission.get(self.run)
        change(record)
        run_admission._write(record)

    def test_prepared_is_not_queued_and_reads_are_stable_detached(self):
        first = self.inspect()
        self.assertEqual(first["internal_phase"], "prepared")
        self.assertIsNone(first["current_prompt"])
        self.assertIsNone(first["retry_token"])
        self.assertEqual(first, self.inspect())
        first["request"]["content"] = "changed"
        first["interaction"]["prompts"].append("changed")
        again = self.inspect()
        self.assertEqual(again["request"]["content"], "Question")
        self.assertEqual(again["interaction"]["prompts"], [])

    def test_cursor_is_independent_of_receipt_json_key_order(self):
        expected = self.inspect()
        record = run_admission.get(self.run)
        run_admission._path(self.run).write_text(
            json.dumps(record, sort_keys=True, indent=7), encoding="utf-8"
        )
        self.assertEqual(expected, self.inspect())

    def test_unknown_deleted_and_initializing_are_distinct(self):
        self.assertIsNone(run_inspection.inspect(str(uuid4())))
        self.alter(lambda r: r.update(phase="initializing"))
        self.assertEqual(self.inspect()["internal_phase"], "initializing")
        run_admission.tombstone_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.inspect()

    def test_initializing_cannot_hide_execution_or_provider_checkpoint(self):
        saved = run_admission.get(self.run)
        for key in ("interaction", "refinement_execution", "refinement_provider"):
            with self.subTest(key=key):
                run_admission._write(saved)
                self.alter(lambda r: r.update(phase="initializing", **{key: {}}))
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.inspect()

    async def test_waiting_answer_accepted_and_refinement_complete(self):
        await self.suggest()
        waiting = self.inspect()
        self.assertEqual(waiting["internal_phase"], "waiting_input")
        self.assertEqual(
            waiting["current_prompt"], waiting["interaction"]["prompts"][-1]["instance"]
        )
        identity = self.answer(clarify=False)
        answered = self.inspect()
        self.assertEqual(answered["internal_phase"], "answer_accepted")
        self.assertIsNone(answered["current_prompt"])
        self.assertEqual(answered["interaction"]["prompts"][-1]["phase"], "answered")
        durable_refinement.resume_answer(self.run, identity)
        complete = self.inspect()
        self.assertEqual(complete["internal_phase"], "refinement_complete")
        self.assertEqual(complete["refinement"]["phase"], "complete")
        self.assertEqual(len({s["cursor"] for s in [waiting, answered, complete]}), 3)
        self.assertEqual(
            complete["interaction"]["prompts"][-1]["answer"]["responses"][
                "refinement_action"
            ],
            "use_refined",
        )

    async def test_consumed_refine_again_is_ready_not_running(self):
        await self.suggest()
        identity = self.answer("refine_again")
        durable_refinement.resume_answer(self.run, identity)
        first = self.inspect()
        self.assertEqual(first["internal_phase"], "refinement_ready")
        self.assertIsNone(first["retry_token"])
        await self.suggest(revision=3)
        self.assertEqual(self.inspect()["internal_phase"], "waiting_input")

    async def test_orphan_requires_retry_and_survives_fresh_process(self):
        async def fail(**kwargs):
            raise RuntimeError("uncertain synthetic outcome")

        with self.assertRaises(RuntimeError):
            await self.suggest(fail)
        expected = self.inspect()
        self.assertEqual(expected["internal_phase"], "needs_retry")
        self.assertEqual(
            expected["retry_token"], expected["execution"]["operations"][-1]["attempt"]
        )
        before = self.bytes()
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys; from backend import storage,run_inspection; "
                "storage.DATA_DIR=sys.argv[1]; print(json.dumps(run_inspection.inspect(sys.argv[2])))",
                self.data,
                self.run,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        self.assertEqual(json.loads(child.stdout), expected)
        self.assertEqual(before, self.bytes())

    async def test_current_ownership_changes_cursor_without_writes(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(**kwargs):
            started.set()
            await release.wait()
            return SUGGESTION

        task = asyncio.create_task(self.suggest(provider))
        try:
            await asyncio.wait_for(started.wait(), 2)
            running = self.inspect()
            self.assertEqual(running["internal_phase"], "running_refinement")
            self.assertTrue(running["operation_owned"])
            self.assertIsNone(running["retry_token"])
            before = self.bytes()
            # Simulate a restarted observer with no local invocation ownership.
            with patch.object(refinement_execution, "_active", set()):
                orphan = self.inspect()
            self.assertEqual(orphan["internal_phase"], "needs_retry")
            self.assertNotEqual(orphan["cursor"], running["cursor"])
            self.assertEqual(before, self.bytes())
        finally:
            release.set()
            await task

    async def test_cancelled_keeps_exact_answer_and_result_without_actions(self):
        await self.suggest()
        self.answer()
        before = self.inspect()
        run_cancellation.request(self.run)
        cancelled = self.inspect()
        self.assertEqual(cancelled["internal_phase"], "cancelled")
        self.assertEqual(cancelled["interaction"], before["interaction"])
        self.assertEqual(cancelled["execution"], before["execution"])
        self.assertIsNone(cancelled["current_prompt"])
        self.assertIsNone(cancelled["retry_token"])
        with self.assertRaises(run_admission.AdmissionCancelled):
            durable_refinement.resume_answer(
                self.run,
                before["interaction"]["prompts"][-1]["instance"]["instance_id"],
            )

    async def test_cancelled_completed_refinement_preserves_consumed_continuation(self):
        await self.suggest()
        identity = self.answer(clarify=False)
        durable_refinement.resume_answer(self.run, identity)
        complete = self.inspect()
        run_cancellation.request(self.run)
        cancelled = self.inspect()
        self.assertEqual(cancelled["internal_phase"], "cancelled")
        self.assertEqual(cancelled["refinement"], complete["refinement"])
        self.assertEqual(cancelled["interaction"], complete["interaction"])
        self.assertEqual(cancelled["interaction"]["prompts"][-1]["phase"], "consumed")

    async def test_cancelled_orphan_suppresses_retry_and_never_settles_marker(self):
        async def fail(**kwargs):
            raise RuntimeError("uncertain")

        with self.assertRaises(RuntimeError):
            await self.suggest(fail)
        token = self.inspect()["retry_token"]
        self.alter(
            lambda r: r.update(
                cancellation={
                    "phase": "cancelling",
                    "requested_at": "2026-11-03T00:00:00",
                    "completed_at": None,
                }
            )
        )
        snapshot = self.inspect()
        self.assertEqual(snapshot["internal_phase"], "cancelling")
        self.assertIsNone(snapshot["retry_token"])
        self.assertEqual(snapshot["execution"]["operations"][-1]["attempt"], token)
        self.assertIsNone(snapshot["cancellation"]["completed_at"])

    async def test_cancelled_waiting_prompt_is_evidence_not_action(self):
        await self.suggest()
        run_cancellation.request(self.run)
        snapshot = self.inspect()
        self.assertEqual(snapshot["interaction"]["prompts"][-1]["phase"], "waiting")
        self.assertIsNone(snapshot["current_prompt"])

    async def test_valid_provider_provenance_is_detached_and_retained(self):
        config = refinement_provider.OpenRouterRefinementConfig(
            endpoint="https://example.invalid/chat/completions",
            model="synthetic/model",
            max_tokens=256,
        )
        refinement_provider._bind(self.run, config, 0, None)
        await self.suggest(provider_key=config.identity())
        run_cancellation.request(self.run)
        snapshot = self.inspect()
        self.assertEqual(snapshot["refinement_provider"]["identity"], config.identity())
        snapshot["refinement_provider"]["configuration"]["model"] = "changed"
        self.assertEqual(
            self.inspect()["refinement_provider"]["configuration"]["model"],
            "synthetic/model",
        )

    async def test_corrupt_provider_digest_and_operation_identity_fail_closed(self):
        config = refinement_provider.OpenRouterRefinementConfig(
            endpoint="https://example.invalid/chat",
            model="synthetic/model",
            max_tokens=256,
        )
        refinement_provider._bind(self.run, config, 0, None)
        saved = run_admission.get(self.run)
        self.alter(lambda r: r["refinement_provider"].update(identity="bad"))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.inspect()
        run_admission._write(saved)
        await self.suggest(provider_key="unbound-fake")
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.inspect()

    async def test_corrupt_input_answer_or_unsupported_checkpoint_fails_closed(self):
        await self.suggest()
        identity = self.answer()
        durable_refinement.resume_answer(self.run, identity)
        saved = run_admission.get(self.run)
        changes = [
            lambda r: r["refinement_execution"]["operations"][0]["input"].update(
                original_query="Other"
            ),
            lambda r: r["interaction"]["prompts"][0]["next_continuation"][
                "state"
            ].update(refined_prompt="Other"),
            lambda r: r["interaction"]["prompts"][0]["continuation"].update(
                point="unsupported_future_workflow"
            ),
            lambda r: r["interaction"]["prompts"][0]["answer"]["responses"].update(
                refinement_action="unknown"
            ),
        ]
        for change in changes:
            with self.subTest(change=change):
                run_admission._write(saved)
                self.alter(change)
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.inspect()

    def test_missing_conversation_and_invalid_receipt_fail_without_repair(self):
        conversation = self.directory / "conversations" / f"cli-{self.run}.json"
        conversation.unlink()
        before = self.bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.inspect()
        self.assertEqual(before, self.bytes())
        run_admission._path(self.run).write_text("{broken", encoding="utf-8")
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
