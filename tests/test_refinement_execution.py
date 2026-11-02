"""Offline suggestion execution: fake callbacks, durable prompts and no replay."""

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
    run_admission,
    run_interaction,
    storage,
)
from backend import (
    refinement_execution as execution,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest

SUGGESTION = {
    "intent": "Saved intent",
    "clarifying_questions": ["Which scope?"],
    "refined_prompt": "  Refined question  ",
}


class RefinementExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = str(Path(self.directory.name) / "conversations")
        self.patch = patch.object(storage, "DATA_DIR", self.data_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.run_id = str(uuid4())
        run_admission.prepare(
            self.run_id,
            CreateRunRequest(
                content="Original — вопрос",
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        self.path = Path(self.directory.name) / "run_receipts" / f"{self.run_id}.json"
        self.calls = []

    async def provider(self, **kwargs):
        self.calls.append(kwargs)
        saved = json.loads(self.path.read_text())["refinement_execution"]["operations"][
            -1
        ]
        self.assertEqual(saved["phase"], "in_flight")
        self.assertEqual(execution.inspect(self.run_id)["phase"], "in_flight")
        return SUGGESTION.copy()

    async def suggest(self, expected_revision=0, **kwargs):
        return await execution.suggest(
            self.run_id,
            self.provider,
            provider_key="fake-model-v1",
            expected_revision=expected_revision,
            **kwargs,
        )

    async def fail(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("synthetic provider failure")

    async def make_uncertain(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            await execution.suggest(
                self.run_id,
                self.fail,
                provider_key="fake-model-v1",
                expected_revision=0,
            )
        view = execution.inspect(self.run_id)
        self.assertEqual(view["phase"], "needs_retry")
        return view["retry_token"]

    async def test_result_and_exact_prompt_saved_atomically_and_duplicate_is_read(self):
        result = await self.suggest()
        self.assertEqual(result.phase, "waiting_answer")
        prompt = run_interaction.get(self.run_id)["prompts"][0]
        self.assertEqual(
            prompt["continuation"], result.to_continuation().model_dump(mode="json")
        )
        self.assertEqual(prompt["phase"], "waiting")
        before = self.path.read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("rewrite")
        ):
            repeated = await self.suggest()
        self.assertEqual(result, repeated)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_live_concurrent_duplicates_do_not_call_or_retry_provider(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def blocking(**kwargs):
            calls.append(kwargs)
            started.set()
            await release.wait()
            return SUGGESTION

        task = asyncio.create_task(
            execution.suggest(
                self.run_id, blocking, provider_key="fake-model-v1", expected_revision=0
            )
        )
        await started.wait()
        try:
            with self.assertRaises(execution.ExecutionPending):
                await self.suggest()
            token = json.loads(self.path.read_text())["refinement_execution"][
                "operations"
            ][0]["attempt"]
            with self.assertRaises(execution.ExecutionPending):
                await self.suggest(retry_token=token)
            self.assertIsNone(execution.inspect(self.run_id)["retry_token"])
        finally:
            release.set()
            await task
        self.assertEqual(len(calls), 1)

    async def test_uncertain_never_replays_without_exact_explicit_consent(self):
        token = await self.make_uncertain()
        before = self.path.read_bytes()
        with self.assertRaises(execution.RetryRequired) as raised:
            await self.suggest()
        self.assertEqual(raised.exception.token, token)
        with self.assertRaises(execution.ExecutionConflict):
            await self.suggest(retry_token=str(uuid4()))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        result = await self.suggest(retry_token=token)
        self.assertEqual(result.phase, "waiting_answer")
        self.assertEqual(await self.suggest(retry_token=token), result)
        self.assertEqual(len(self.calls), 2)

    async def test_prior_retry_token_cannot_authorize_a_second_uncertain_attempt(self):
        token = await self.make_uncertain()
        with self.assertRaises(RuntimeError):
            await execution.suggest(
                self.run_id,
                self.fail,
                provider_key="fake-model-v1",
                expected_revision=0,
                retry_token=token,
            )
        next_token = execution.inspect(self.run_id)["retry_token"]
        self.assertNotEqual(token, next_token)
        with self.assertRaises(execution.RetryRequired):
            await self.suggest(retry_token=token)
        self.assertEqual(len(self.calls), 2)
        await self.suggest(retry_token=next_token)
        self.assertEqual(len(self.calls), 3)

    async def test_initial_write_failure_prevents_provider_and_lost_ack_needs_retry(
        self,
    ):
        before = self.path.read_bytes()
        with patch.object(run_admission, "_write", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                await self.suggest()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), before)
        original = run_admission._write

        def lost_ack(record):
            original(record)
            raise OSError("ack lost")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                await self.suggest()
        self.assertEqual(self.calls, [])
        with self.assertRaises(execution.RetryRequired):
            await self.suggest()

    async def test_failed_completion_save_preserves_uncertainty_and_no_partial_prompt(
        self,
    ):
        original = run_admission._write

        def fail_complete(record):
            if (
                record.get("refinement_execution", {})
                .get("operations", [{}])[-1]
                .get("phase")
                == "complete"
            ):
                raise OSError("result write failed")
            original(record)

        with patch.object(run_admission, "_write", side_effect=fail_complete):
            with self.assertRaises(OSError):
                await self.suggest()
        self.assertEqual(run_interaction.get(self.run_id)["prompts"], [])
        self.assertEqual(execution.inspect(self.run_id)["phase"], "needs_retry")
        with self.assertRaises(execution.RetryRequired):
            await self.suggest()
        self.assertEqual(len(self.calls), 1)

    async def test_lost_completion_ack_replays_saved_result_not_provider(self):
        original = run_admission._write

        def lose_complete(record):
            original(record)
            if (
                record.get("refinement_execution", {})
                .get("operations", [{}])[-1]
                .get("phase")
                == "complete"
            ):
                raise OSError("result ack lost")

        with patch.object(run_admission, "_write", side_effect=lose_complete):
            with self.assertRaises(OSError):
                await self.suggest()
        before = self.path.read_bytes()
        result = await self.suggest()
        self.assertEqual(result.phase, "waiting_answer")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_leaves_uncertainty_not_a_live_owner(self):
        started = asyncio.Event()

        async def block(**kwargs):
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(
            execution.suggest(
                self.run_id, block, provider_key="fake-model-v1", expected_revision=0
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(execution.inspect(self.run_id)["phase"], "needs_retry")
        with self.assertRaises(execution.RetryRequired):
            await self.suggest()

    async def test_deletion_during_provider_cannot_save_result_or_resurrect(self):
        async def delete(**kwargs):
            run_admission.tombstone_conversation(f"cli-{self.run_id}")
            return SUGGESTION

        with self.assertRaises(run_admission.AdmissionDeleted):
            await execution.suggest(
                self.run_id, delete, provider_key="fake-model-v1", expected_revision=0
            )
        tombstone = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), tombstone)
        self.assertNotIn(b"Original", tombstone)
        self.assertNotIn(b"refinement_execution", tombstone)

    async def test_configuration_change_and_malformed_result_fail_closed(self):
        token = await self.make_uncertain()
        with self.assertRaises(execution.ExecutionConflict):
            await execution.suggest(
                self.run_id,
                self.provider,
                provider_key="different-model",
                expected_revision=0,
                retry_token=token,
            )
        self.assertEqual(len(self.calls), 1)

        async def invalid(**kwargs):
            return {"intent": True}

        with self.assertRaises(ValueError):
            await execution.suggest(
                self.run_id,
                invalid,
                provider_key="fake-model-v1",
                expected_revision=0,
                retry_token=token,
            )
        self.assertEqual(execution.inspect(self.run_id)["phase"], "needs_retry")
        self.assertEqual(run_interaction.get(self.run_id)["prompts"], [])

    async def test_second_round_uses_only_saved_first_answer_and_preserves_prior_result(
        self,
    ):
        first = await self.suggest()
        prompt = run_interaction.get(self.run_id)["prompts"][0]
        first_attempt = json.loads(self.path.read_text())["refinement_execution"][
            "operations"
        ][0]["attempt"]
        instance_id = prompt["instance"]["instance_id"]
        run_interaction.answer(
            self.run_id,
            instance_id,
            AnswerRequest(
                responses={
                    "refinement_action": "refine_again",
                    "additional_insights": "saved detail",
                }
            ),
        )
        durable_refinement.resume_answer(self.run_id, instance_id)
        # Delayed retry receipt must not dispatch the newly runnable round.
        self.assertEqual(await self.suggest(retry_token=first_attempt), first)
        self.assertEqual(await self.suggest(), first)
        self.assertEqual(len(self.calls), 1)
        second = await self.suggest(expected_revision=3)
        self.assertEqual(second.iteration, 2)
        self.assertEqual(self.calls[-1]["additional_context"], "saved detail")
        self.assertEqual(self.calls[-1]["iteration"], 2)
        saved = run_interaction.get(self.run_id)
        self.assertEqual(saved["revision"], 4)
        self.assertEqual(len(saved["prompts"]), 2)
        self.assertEqual(
            saved["prompts"][0]["continuation"],
            first.to_continuation().model_dump(mode="json"),
        )

    async def test_fresh_process_detects_uncertainty_without_provider_import_or_network(
        self,
    ):
        token = await self.make_uncertain()
        program = """
import asyncio, json, socket, sys
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend import storage, refinement_execution as execution
storage.DATA_DIR = sys.argv[1]
async def forbidden(**kwargs): raise AssertionError('provider replay')
async def check():
    view = execution.inspect(sys.argv[2])
    assert view['phase'] == 'needs_retry'
    try: await execution.suggest(sys.argv[2], forbidden, provider_key='fake-model-v1', expected_revision=0)
    except execution.RetryRequired as exc: assert exc.token == sys.argv[3]
    else: raise AssertionError('missing retry guard')
    assert 'backend.council' not in sys.modules
    print(json.dumps(view))
asyncio.run(check())
"""
        done = subprocess.run(
            [sys.executable, "-c", program, self.data_dir, self.run_id, token],
            timeout=10,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(json.loads(done.stdout)["retry_token"], token)

    async def test_interaction_mutation_during_callback_cannot_commit_stale_result(
        self,
    ):
        from backend.consultation_contract import PromptInstance
        from backend.interactive import get_prompt_refinement_prompt
        from backend.refinement import RefinementState

        async def mutate(**kwargs):
            state = RefinementState.start("Original — вопрос").accept_suggestion(
                SUGGESTION
            )
            run_interaction.put_prompt(
                self.run_id,
                PromptInstance(
                    run_id=self.run_id,
                    instance_id=uuid4(),
                    prompt=get_prompt_refinement_prompt(SUGGESTION).to_dict(),
                ),
                state.to_continuation(),
                expected_revision=0,
            )
            return SUGGESTION

        with self.assertRaises(run_admission.AdmissionUnavailable):
            await execution.suggest(
                self.run_id, mutate, provider_key="fake-model-v1", expected_revision=0
            )
        saved = json.loads(self.path.read_text())
        self.assertEqual(
            saved["refinement_execution"]["operations"][0]["phase"], "in_flight"
        )
        self.assertEqual(len(saved["interaction"]["prompts"]), 1)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            execution.inspect(self.run_id)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest(
                retry_token=saved["refinement_execution"]["operations"][0]["attempt"]
            )
        self.assertEqual(self.calls, [])

    async def test_concurrent_retry_approval_starts_one_replacement(self):
        token = await self.make_uncertain()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def blocking(**kwargs):
            calls.append(kwargs)
            started.set()
            await release.wait()
            return SUGGESTION

        task = asyncio.create_task(
            execution.suggest(
                self.run_id,
                blocking,
                provider_key="fake-model-v1",
                expected_revision=0,
                retry_token=token,
            )
        )
        await started.wait()
        try:
            with self.assertRaises(execution.ExecutionPending):
                await self.suggest(retry_token=token)
        finally:
            release.set()
            result = await task
        self.assertEqual(await self.suggest(retry_token=token), result)
        self.assertEqual(len(calls), 1)

    async def test_fresh_process_saved_result_returns_without_regenerating_prompt(self):
        result = await self.suggest()
        before = self.path.read_bytes()
        program = """
import asyncio, json, socket, sys
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend import storage, refinement_execution as execution
storage.DATA_DIR = sys.argv[1]
async def forbidden(**kwargs): raise AssertionError('provider replay')
async def check():
    result = await execution.suggest(sys.argv[2], forbidden, provider_key='fake-model-v1', expected_revision=0)
    print(result.model_dump_json())
asyncio.run(check())
"""
        done = subprocess.run(
            [sys.executable, "-c", program, self.data_dir, self.run_id],
            timeout=10,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(json.loads(done.stdout), result.model_dump(mode="json"))
        self.assertEqual(self.path.read_bytes(), before)

    async def consume_first(self):
        first = await self.suggest()
        prompt = run_interaction.get(self.run_id)["prompts"][0]
        instance = prompt["instance"]["instance_id"]
        run_interaction.answer(
            self.run_id,
            instance,
            AnswerRequest(responses={"refinement_action": "refine_again"}),
        )
        durable_refinement.resume_answer(self.run_id, instance)
        return first

    async def test_provider_configuration_cannot_change_between_rounds(self):
        await self.consume_first()
        before = self.path.read_bytes()
        with self.assertRaises(execution.ExecutionConflict):
            await execution.suggest(
                self.run_id,
                self.provider,
                provider_key="different-model",
                expected_revision=3,
            )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_typed_but_tampered_consumed_answer_continuation_cannot_dispatch(
        self,
    ):
        await self.consume_first()
        record = json.loads(self.path.read_text())
        record["interaction"]["prompts"][0]["next_continuation"]["state"][
            "refined_prompt"
        ] = "tampered prompt"
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest(expected_revision=3)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_delayed_old_normal_request_while_next_round_runs_only_reads_old_result(
        self,
    ):
        first = await self.consume_first()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def block(**kwargs):
            calls.append(kwargs)
            started.set()
            await release.wait()
            return SUGGESTION

        task = asyncio.create_task(
            execution.suggest(
                self.run_id, block, provider_key="fake-model-v1", expected_revision=3
            )
        )
        await started.wait()
        try:
            self.assertEqual(await self.suggest(), first)
            with self.assertRaises(execution.ExecutionPending):
                await self.suggest(expected_revision=3)
        finally:
            release.set()
            await task
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.calls), 1)

    async def test_future_and_invalid_revisions_cannot_dispatch(self):
        for revision in (True, -1, 1.0):
            with self.assertRaises(ValueError):
                await self.suggest(expected_revision=revision)
        with self.assertRaises(execution.ExecutionConflict):
            await self.suggest(expected_revision=3)
        self.assertEqual(self.calls, [])

    async def test_complete_operation_coherence_is_verified_before_replay(self):
        await self.suggest()
        original = self.path.read_text()
        for change in ("revision", "schema", "source"):
            with self.subTest(change=change):
                record = json.loads(original)
                operation = record["refinement_execution"]["operations"][0]
                prompt = record["interaction"]["prompts"][0]
                if change == "revision":
                    operation["revision"] = 3
                elif change == "schema":
                    prompt["instance"]["prompt"]["prompt_id"] = "other_prompt"
                else:
                    operation["input"]["refined_prompt"] = "different source"
                    operation["result"]["refined_prompt"] = "different source"
                    prompt["continuation"]["state"]["refined_prompt"] = (
                        "different source"
                    )
                self.path.write_text(json.dumps(record))
                before = self.path.read_bytes()
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    await self.suggest()
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_corrupt_in_flight_source_is_not_reported_retryable(self):
        await self.make_uncertain()
        record = json.loads(self.path.read_text())
        record["refinement_execution"]["operations"][0]["input"]["refined_prompt"] = (
            "different source"
        )
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            execution.inspect(self.run_id)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_abrupt_process_exit_leaves_attempt_uncertain_until_explicit_retry(
        self,
    ):
        marker = Path(self.directory.name) / "fake-provider-started"
        program = """
import asyncio, os, socket, sys
from pathlib import Path
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend import storage, refinement_execution as execution
storage.DATA_DIR = sys.argv[1]
async def crash(**kwargs):
    Path(sys.argv[3]).write_text('synthetic provider entered')
    os._exit(73)
asyncio.run(execution.suggest(sys.argv[2], crash, provider_key='fake-model-v1', expected_revision=0))
"""
        done = subprocess.run(
            [sys.executable, "-c", program, self.data_dir, self.run_id, str(marker)],
            timeout=10,
            text=True,
            capture_output=True,
        )
        self.assertEqual(done.returncode, 73, done.stderr)
        self.assertEqual(marker.read_text(), "synthetic provider entered")
        saved = json.loads(self.path.read_text())
        token = saved["refinement_execution"]["operations"][0]["attempt"]
        view = execution.inspect(self.run_id)
        self.assertEqual(view["phase"], "needs_retry")
        self.assertEqual(view["retry_token"], token)
        self.assertEqual(run_interaction.get(self.run_id)["prompts"], [])
        before = self.path.read_bytes()
        with self.assertRaises(execution.RetryRequired) as raised:
            await self.suggest()
        self.assertEqual(raised.exception.token, token)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), before)
        result = await self.suggest(retry_token=token)
        self.assertEqual(result.phase, "waiting_answer")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(run_interaction.get(self.run_id)["prompts"]), 1)
        self.assertEqual(await self.suggest(retry_token=token), result)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(run_interaction.get(self.run_id)["prompts"]), 1)

    async def test_corrupt_saved_result_cannot_be_returned_or_overwritten(self):
        await self.suggest()
        record = json.loads(self.path.read_text())
        record["refinement_execution"]["operations"][0]["result"]["original_query"] = (
            "tampered"
        )
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
