"""Offline first-continuation execution, isolated consent and durable guards."""

import asyncio
import copy
import json
import subprocess
import sys
import threading
import traceback
import unittest
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import test_run_stage2_continuation_inputs as continuation_fixtures

from backend import (
    config,
    model_context,
    openrouter,
    run_admission,
    run_cancellation,
    run_inspection,
    run_stage1_execution,
    run_stage1_inputs,
    run_stage2_results,
    skills,
    storage,
)
from backend import run_stage2_continuation_execution as execution
from backend import run_stage2_continuation_inputs as continuation
from backend import run_stage2_execution as initial_execution

RESPONSE = {
    "content": "  Separate continuation chunk Пример ☃\n",
    "finish_reason": "stop",
    "reasoning_details": [{"type": "synthetic", "nested": {"text": ["private"]}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "nested": [None, True]},
}


class FirstContinuationExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Compose the fixture, never inherit its tests. Its asyncio.run setup
        # prepares initial partials and binds continuations before the test loop.
        self.fixture = continuation_fixtures.Stage2ContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.models = self.fixture.models
        self.run_id = self.fixture.run
        self.path = self.fixture.path
        self.requests = []
        for model in self.models:
            self.fixture.execute(model)
            self.requests.append(self.fixture.bind(model))
        self.inputs = self.fixture.inputs
        self.initial_state = copy.deepcopy(self.saved()["stage2_execution"])
        self.calls: list[dict[str, Any]] = []
        self.addCleanup(self.assert_released)

    def load_result(
        self, run_id: str, model: str
    ) -> initial_execution.MemberResponse | None:
        return execution.load_result(run_id, model, turn=1)

    def assert_released(self) -> None:
        self.assertFalse(any(key[0] == self.run_id for key in execution._active))
        self.assertFalse(any(key[0] == self.run_id for key in run_cancellation._tasks))
        self.assertFalse(
            any(key[0] == self.run_id for key in run_cancellation._signalled)
        )

    def saved(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def rewrite(self, record: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(record))

    def snapshot(self) -> dict[str, Any]:
        return run_inspection.inspect(self.run_id)

    def members(self) -> list[dict[str, Any]]:
        return self.snapshot()["stage2_continuation_execution"]["members"]

    async def provider(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(kwargs))
        model = kwargs["payload"]["model"]
        operation = next(
            op
            for op in self.saved()["stage2_continuation_execution"]["operations"]
            if op["model"] == model
        )
        self.assertEqual(operation["phase"], "in_flight")
        self.assertIsNone(operation["result"])
        self.assertIn((self.run_id, operation["attempt"]), execution._active)
        return copy.deepcopy(RESPONSE)

    async def execute(
        self, model: str | None = None, **kwargs: Any
    ) -> execution.MemberResponse:
        return await execution.execute(
            self.run_id,
            model or self.models[0],
            kwargs.pop("provider", self.provider),
            turn=kwargs.pop("turn", 1),
            provider_key=kwargs.pop("provider_key", "synthetic-v1"),
            expected_revision=kwargs.pop("expected_revision", 6),
            **kwargs,
        )

    async def uncertain(self, model: str | None = None, **kwargs: Any) -> str:
        async def fail(**request: Any) -> dict[str, Any]:
            self.calls.append(copy.deepcopy(request))
            raise RuntimeError("synthetic provider failed")

        with self.assertRaises(execution.ProviderFailure):
            await self.execute(model, provider=fail, **kwargs)
        member = next(
            m for m in self.members() if m["model"] == (model or self.models[0])
        )
        self.assertEqual(member["phase"], "needs_retry")
        self.assertIsNotNone(member["retry_token"])
        return member["retry_token"]

    async def test_saved_result_replay_and_historical_read_are_detached_and_read_only(
        self,
    ) -> None:
        result = await self.execute()
        original = copy.deepcopy(RESPONSE)
        result.usage["nested"].append("caller mutation")
        result.reasoning_details[0]["nested"]["text"].append("caller mutation")
        before = self.fixture.fixture.files()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            repeated = await self.execute()
            loaded = self.load_result(self.run_id, self.models[0])
            self.assertEqual(repeated.model_dump(mode="json"), original)
            self.assertEqual(loaded.model_dump(mode="json"), original)
            self.assertIsNone(self.load_result(self.run_id, self.models[1]))
            loaded.usage["nested"].append("read mutation")
            self.assertEqual(self.load_result(self.run_id, self.models[0]), repeated)
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_callback_mutation_cannot_change_saved_request_or_response(
        self,
    ) -> None:
        response = copy.deepcopy(RESPONSE)
        before = self.saved()["stage2_continuation_inputs"]

        async def mutate(**kwargs: Any) -> dict[str, Any]:
            kwargs["payload"]["messages"][0]["content"] = "modified by adapter"
            kwargs["payload"]["max_tokens"] = 1
            return response

        result = await self.execute(provider=mutate)
        response["usage"]["nested"].append("adapter mutation")
        self.assertEqual(self.saved()["stage2_continuation_inputs"], before)
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)

    async def test_parallel_members_have_independent_owners_and_duplicate_is_blocked(
        self,
    ) -> None:
        started = {model: asyncio.Event() for model in self.models}
        release = asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started[kwargs["payload"]["model"]].set()
            await release.wait()
            return copy.deepcopy(RESPONSE)

        tasks = [
            asyncio.create_task(self.execute(model, provider=block))
            for model in self.models
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())), 5
            )
            snapshot = self.snapshot()
            self.assertEqual(snapshot["internal_phase"], "running_stage2_continuation")
            self.assertTrue(snapshot["operation_owned"])
            self.assertEqual([m["phase"] for m in self.members()], ["running"] * 2)
            for op in self.saved()["stage2_continuation_execution"]["operations"]:
                for token in (None, op["attempt"]):
                    with self.assertRaises(execution.ExecutionPending):
                        await self.execute(op["model"], retry_token=token)
            self.assertEqual(len(self.calls), 2)
        finally:
            release.set()
            await asyncio.gather(*tasks)
        self.assertEqual(self.snapshot()["internal_phase"], "stage2_needs_continuation")

    async def test_retry_requires_current_per_member_consent_and_never_replays_automatically(
        self,
    ) -> None:
        first = await self.uncertain()
        second = await self.uncertain(self.models[1])
        self.assertNotEqual(first, second)
        before = self.fixture.fixture.files()
        for model, own, other in (
            (self.models[0], first, second),
            (self.models[1], second, first),
        ):
            with self.assertRaises(execution.RetryRequired) as raised:
                await self.execute(model)
            self.assertEqual(raised.exception.token, own)
            for token in (other, str(uuid4())):
                with self.assertRaises(execution.ExecutionConflict):
                    await self.execute(model, retry_token=token)
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 2)
        result = await self.execute(retry_token=first)
        self.assertEqual(await self.execute(retry_token=first), result)
        self.assertEqual(self.members()[1]["retry_token"], second)
        self.assertEqual(len(self.calls), 3)
        self.assertIsNone(self.snapshot()["retry_token"])

    async def test_old_retry_consent_cannot_authorize_another_uncertain_attempt(
        self,
    ) -> None:
        first = await self.uncertain()
        second = await self.uncertain(retry_token=first)
        self.assertNotEqual(first, second)
        with self.assertRaises(execution.RetryRequired) as raised:
            await self.execute(retry_token=first)
        self.assertEqual(raised.exception.token, second)
        result = await self.execute(retry_token=second)
        self.assertEqual(await self.execute(retry_token=second), result)
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(retry_token=first)
        self.assertEqual(len(self.calls), 3)

    async def test_concurrent_retry_consent_starts_one_replacement(self) -> None:
        token = await self.uncertain()
        started, release = asyncio.Event(), asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(provider=block, retry_token=token))
        try:
            await asyncio.wait_for(started.wait(), 5)
            with self.assertRaises(execution.ExecutionPending):
                await self.execute(retry_token=token)
        finally:
            release.set()
            result = await task
        self.assertEqual(await self.execute(retry_token=token), result)
        self.assertEqual(len(self.calls), 2)

    async def test_callback_exception_has_private_traceback_and_leaves_uncertainty(
        self,
    ) -> None:
        secret = "PRIVATE_CALLBACK_EXCEPTION_SENTINEL"

        async def fail(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            raise RuntimeError(secret)

        try:
            await self.execute(provider=fail)
        except execution.ProviderFailure as exc:
            self.assertNotIn(secret, "".join(traceback.format_exception(exc)))
            self.assertNotIn(secret, str(exc))
        else:
            self.fail("ProviderFailure was not raised")
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        self.assertNotIn(secret, self.path.read_text())
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_responses_fail_closed_without_text_or_metadata_leaks(
        self,
    ) -> None:
        secret = "PRIVATE_INVALID_RESPONSE_SENTINEL"
        responses = [
            None,
            [],
            "text",
            {},
            {"content": "", "finish_reason": "stop"},
            {"content": " \n\t", "finish_reason": "stop"},
            {"content": True, "finish_reason": "stop"},
            {"content": secret, "finish_reason": "tool_calls"},
            {"content": secret, "finish_reason": "stop", "extra": secret},
            {"content": secret, "finish_reason": "stop", "usage": {"n": float("nan")}},
            {"content": secret, "finish_reason": "stop", "usage": {"n": float("inf")}},
            {"content": secret, "finish_reason": "stop", "usage": {"n": object()}},
            {"content": secret, "finish_reason": "stop", "reasoning_details": [secret]},
        ]
        baseline = self.path.read_bytes()
        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                self.path.write_bytes(baseline)

                async def invalid(**kwargs: Any) -> Any:
                    self.calls.append(kwargs)
                    return response

                try:
                    await self.execute(provider=invalid)
                except execution.ProviderFailure as exc:
                    self.assertNotIn(secret, "".join(traceback.format_exception(exc)))
                else:
                    self.fail("Invalid response was accepted")
                self.assertEqual(self.members()[0]["phase"], "needs_retry")
                self.assertIsNone(self.load_result(self.run_id, self.models[0]))
                self.assertNotIn(secret, self.path.read_text())
                self.assert_released()
        self.assertEqual(len(self.calls), len(responses))

    async def test_initial_write_failure_never_calls_provider(self) -> None:
        before = self.fixture.fixture.files()
        with patch.object(
            run_admission, "_write", side_effect=OSError("attempt write failed")
        ):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.snapshot()["stage2_continuation_execution"])

    async def test_lost_attempt_write_ack_never_calls_and_requires_retry(self) -> None:
        original = run_admission._write

        def lose_ack(record: dict[str, Any]) -> None:
            original(record)
            raise OSError("attempt acknowledgment lost")

        with patch.object(run_admission, "_write", side_effect=lose_ack):
            with self.assertRaises(OSError):
                await self.execute()
        before = self.fixture.fixture.files()
        token = self.members()[0]["retry_token"]
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(before, self.fixture.fixture.files())
        await self.execute(retry_token=token)
        self.assertEqual(len(self.calls), 1)

    async def test_task_creation_failure_leaves_saved_attempt_without_owner_or_call(
        self,
    ) -> None:
        with patch.object(
            execution.asyncio,
            "create_task",
            side_effect=RuntimeError("task allocation"),
        ):
            with self.assertRaisesRegex(RuntimeError, "task allocation"):
                await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        with self.assertRaises(execution.RetryRequired):
            await self.execute()

    async def test_completion_write_failure_keeps_only_uncertain_attempt(self) -> None:
        original = run_admission._write

        def fail_complete(record: dict[str, Any]) -> None:
            if any(
                op["phase"] == "complete"
                for op in record.get("stage2_continuation_execution", {}).get(
                    "operations", []
                )
            ):
                raise OSError("completion write failed")
            original(record)

        with patch.object(run_admission, "_write", side_effect=fail_complete):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        self.assertEqual(len(self.calls), 1)

    async def test_lost_completion_ack_replays_saved_result_without_provider(
        self,
    ) -> None:
        original = run_admission._write

        def lose_complete(record: dict[str, Any]) -> None:
            original(record)
            if any(
                op["phase"] == "complete"
                for op in record.get("stage2_continuation_execution", {}).get(
                    "operations", []
                )
            ):
                raise OSError("completion acknowledgment lost")

        with patch.object(run_admission, "_write", side_effect=lose_complete):
            with self.assertRaises(OSError):
                await self.execute()
        before = self.fixture.fixture.files()
        result = await self.execute()
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_caller_cancellation_releases_owner_and_requires_explicit_retry(
        self,
    ) -> None:
        started = asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(self.execute(provider=block))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        self.assertIsNone(self.snapshot()["cancellation"])
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        self.assertEqual(len(self.calls), 1)

    async def test_provider_cancellation_is_not_wrapped_or_retried(self) -> None:
        async def cancel(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.execute(provider=cancel)
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        self.assertEqual(len(self.calls), 1)

    async def test_run_cancellation_preserves_prior_response_and_cancels_running_member(
        self,
    ) -> None:
        retained = await self.execute()
        started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleanup.set()
                await release.wait()

        task = asyncio.create_task(self.execute(self.models[1], provider=block))
        try:
            await asyncio.wait_for(started.wait(), 5)
            run_cancellation.request(self.run_id)
            await asyncio.wait_for(cleanup.wait(), 5)
            self.assertEqual(self.snapshot()["internal_phase"], "cancelling")
            self.assertTrue(self.snapshot()["operation_owned"])
            self.assertEqual(self.load_result(self.run_id, self.models[0]), retained)
            run_cancellation.request(self.run_id)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertFalse(self.snapshot()["operation_owned"])
        self.assertEqual(self.load_result(self.run_id, self.models[0]), retained)
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.execute(
                self.models[1], retry_token=self.members()[1]["retry_token"]
            )

    async def test_late_success_after_cancellation_cannot_commit_or_resurrect_work(
        self,
    ) -> None:
        retained = await self.execute()

        async def cancel_run(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            run_cancellation.request(self.run_id)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.execute(self.models[1], provider=cancel_run)
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertEqual(self.load_result(self.run_id, self.models[0]), retained)
        self.assertIsNone(self.load_result(self.run_id, self.models[1]))

    async def test_deletion_during_callback_cannot_save_or_recreate_receipt(
        self,
    ) -> None:
        async def delete(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            run_admission.tombstone_conversation(f"cli-{self.run_id}")
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.execute(provider=delete)
        tombstone = self.path.read_bytes()
        self.assertEqual(self.saved()["phase"], "deleted")
        self.assertNotIn("stage2_continuation_execution", self.saved())
        for read in (
            self.snapshot,
            lambda: self.load_result(self.run_id, self.models[0]),
        ):
            with self.assertRaises(run_admission.AdmissionDeleted):
                read()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.execute()
        self.assertEqual(self.path.read_bytes(), tombstone)
        self.assertEqual(len(self.calls), 1)

    async def test_current_configuration_drift_blocks_dispatch_but_historical_result_remains_readable(
        self,
    ) -> None:
        retained = await self.execute()
        before = self.fixture.fixture.files()
        for name, value in (
            ("COUNCIL_MODELS", ["changed/model"]),
            ("OPENROUTER_API_URL", "https://invalid.example/api"),
            ("SKILLS_ENABLED", False),
        ):
            with self.subTest(setting=name), patch.object(config, name, value):
                self.assertEqual(
                    self.load_result(self.run_id, self.models[0]), retained
                )
                with self.assertRaises(run_stage1_inputs.Stage1InputConflict):
                    await self.execute(self.models[1])
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_config_drift_during_callback_does_not_discard_completed_response(
        self,
    ) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(provider=block))
        await asyncio.wait_for(started.wait(), 5)
        with patch.object(config, "COUNCIL_MODELS", ["changed/model"]):
            release.set()
            result = await task
            self.assertEqual(self.load_result(self.run_id, self.models[0]), result)
        self.assertEqual(len(self.calls), 1)

    async def test_pre_dispatch_recheck_detects_new_configuration_without_callback(
        self,
    ) -> None:
        original = execution._save
        old_models = config.COUNCIL_MODELS

        def drift(record: dict[str, Any], state: execution.ExecutionState) -> None:
            original(record, state)
            config.COUNCIL_MODELS = ["changed/model"]

        try:
            with patch.object(execution, "_save", side_effect=drift):
                with self.assertRaises(execution.ProviderFailure):
                    await self.execute()
        finally:
            config.COUNCIL_MODELS = old_models
        self.assertEqual(self.calls, [])
        self.assertEqual(self.members()[0]["phase"], "needs_retry")

    async def test_invalid_arguments_unknown_members_and_unrequested_retries_write_nothing(
        self,
    ) -> None:
        before = self.fixture.fixture.files()
        for kwargs in (
            {"expected_revision": True},
            {"expected_revision": -1},
            {"expected_revision": 3.0},
            {"expected_revision": "3"},
            {"expected_revision": 2},
            {"expected_revision": 4},
            {"provider_key": ""},
            {"provider_key": "contains space"},
            {"provider_key": "\n"},
            {"provider_key": "é"},
            {"provider_key": "a" * 129},
            {"provider_key": None},
            {"retry_token": 1},
            {"retry_token": "not-uuid"},
            {"retry_token": str(uuid4())},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.execute(**kwargs)
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute("unknown/model")
        with self.assertRaises(execution.ExecutionConflict):
            self.load_result(self.run_id, "unknown/model")
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(self.calls, [])

    async def test_adapter_identity_change_blocks_all_members_and_saved_replays(
        self,
    ) -> None:
        await self.execute()
        before = self.fixture.fixture.files()
        for model in self.models:
            with self.assertRaises(execution.ExecutionConflict):
                await self.execute(model, provider_key="synthetic-v2")
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_source_drift_during_provider_cannot_commit_response(self) -> None:
        async def drift(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage1_inputs"]["requests"][0]["output_budget"] += 1
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.execute(provider=drift)
        self.assertEqual(
            self.saved()["stage2_continuation_execution"]["operations"][0]["phase"],
            "in_flight",
        )
        self.assertEqual(len(self.calls), 1)

    async def test_superseded_attempt_cannot_commit_a_late_response(self) -> None:
        replacement = str(uuid4())

        async def supersede(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage2_continuation_execution"]["operations"][0]["attempt"] = (
                replacement
            )
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=supersede)
        self.assertEqual(self.members()[0]["retry_token"], replacement)
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))

    async def test_polling_is_compact_private_detached_and_does_not_resolve_or_write(
        self,
    ) -> None:
        await self.execute()
        before = self.fixture.fixture.files()
        with (
            patch.object(run_admission, "_write", side_effect=AssertionError("write")),
            patch.object(
                model_context, "model_limits", side_effect=AssertionError("catalog")
            ),
            patch.object(
                openrouter, "query_model", side_effect=AssertionError("provider")
            ),
            patch.object(skills, "get_skill", side_effect=AssertionError("library")),
        ):
            snapshot = self.snapshot()
            self.assertEqual(snapshot, self.snapshot())
            serialized = json.dumps(snapshot)
            for private in (
                RESPONSE["content"],
                "reasoning_details",
                '"completion_tokens": 7',
                "Original instructions",
            ):
                self.assertNotIn(private, serialized)
            self.assertNotIn(
                RESPONSE["content"],
                repr(self.load_result(self.run_id, self.models[0])),
            )
            snapshot["stage2_continuation_execution"]["members"][0]["phase"] = (
                "tampered"
            )
            self.assertEqual(self.members()[0]["phase"], "complete")
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_malformed_saved_execution_is_rejected_without_repair_or_replay(
        self,
    ) -> None:
        await self.execute()
        baseline = self.saved()
        variants: list[tuple[str, Any]] = [
            ("null", None),
            ("list", []),
            ("empty", {}),
        ]
        for key, value in (
            ("version", 2),
            ("version", True),
            ("source_revision", 4),
            ("provider_key", "bad key"),
            ("operations", []),
            ("extra", "private"),
        ):
            state = copy.deepcopy(baseline["stage2_continuation_execution"])
            state[key] = value
            variants.append((key, state))
        for key, value in (
            ("model", "other/model"),
            ("turn", True),
            ("turn", 2),
            ("inputs_sha256", "0" * 64),
            ("attempt", "bad"),
            ("phase", "in_flight"),
            ("result", None),
            (
                "retry_of",
                baseline["stage2_continuation_execution"]["operations"][0]["attempt"],
            ),
        ):
            state = copy.deepcopy(baseline["stage2_continuation_execution"])
            state["operations"][0][key] = value
            variants.append((key, state))
        duplicate = copy.deepcopy(baseline["stage2_continuation_execution"])
        duplicate["operations"].append(copy.deepcopy(duplicate["operations"][0]))
        variants.append(("duplicate member", duplicate))
        for label, state in variants:
            with self.subTest(change=label):
                record = copy.deepcopy(baseline)
                record["stage2_continuation_execution"] = state
                self.rewrite(record)
                before = self.fixture.fixture.files()
                for read in (
                    self.snapshot,
                    lambda: self.load_result(self.run_id, self.models[0]),
                ):
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        read()
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    await self.execute()
                self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_oversized_content_or_metadata_is_rejected_without_leaking_or_retrying(
        self,
    ) -> None:
        baseline = self.path.read_bytes()
        for field in ("content", "usage", "reasoning_details"):
            with self.subTest(field=field):
                self.path.write_bytes(baseline)
                response = copy.deepcopy(RESPONSE)
                large = "☃" * (run_stage1_execution.MAX_RESULT_BYTES // 3 + 1)
                if field == "content":
                    response[field] = large
                elif field == "usage":
                    response[field] = {"private": large}
                else:
                    response[field] = [{"private": large}]

                async def oversized(**kwargs: Any) -> dict[str, Any]:
                    self.calls.append(kwargs)
                    return response

                with self.assertRaises(execution.ProviderFailure):
                    await self.execute(provider=oversized)
                self.assertEqual(self.members()[0]["phase"], "needs_retry")
                self.assertNotIn(large[:100], self.path.read_text())
                self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 3)

    async def test_ownership_changes_poll_cursor_without_changing_saved_bytes(
        self,
    ) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(provider=block))
        try:
            await asyncio.wait_for(started.wait(), 5)
            live = self.snapshot()
            before = self.fixture.fixture.files()
            with patch.object(execution, "_active", set()):
                orphaned = self.snapshot()
                self.assertEqual(orphaned["internal_phase"], "needs_retry")
                self.assertFalse(orphaned["operation_owned"])
                self.assertIsNotNone(
                    orphaned["stage2_continuation_execution"]["members"][0][
                        "retry_token"
                    ]
                )
                self.assertNotEqual(live["cursor"], orphaned["cursor"])
            self.assertEqual(before, self.fixture.fixture.files())
            self.assertEqual(self.snapshot()["cursor"], live["cursor"])
        finally:
            release.set()
            await task
        self.assertNotEqual(self.snapshot()["cursor"], live["cursor"])

    async def test_process_wide_retry_owner_blocks_duplicate_consent_from_another_event_loop(
        self,
    ) -> None:
        token = await self.uncertain()
        started, release = threading.Event(), threading.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await asyncio.to_thread(release.wait)
            return copy.deepcopy(RESPONSE)

        def threaded() -> execution.MemberResponse:
            return asyncio.run(self.execute(provider=block, retry_token=token))

        task = asyncio.create_task(asyncio.to_thread(threaded))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            with self.assertRaises(execution.ExecutionPending):
                await self.execute(retry_token=token)
            self.assertEqual(self.members()[0]["phase"], "running")
        finally:
            release.set()
            await task
        self.assertEqual(len(self.calls), 2)

    async def test_running_member_takes_status_precedence_without_consuming_other_retry(
        self,
    ) -> None:
        token = await self.uncertain()
        started, release = asyncio.Event(), asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(self.models[1], provider=block))
        try:
            await asyncio.wait_for(started.wait(), 5)
            self.assertEqual(
                self.snapshot()["internal_phase"], "running_stage2_continuation"
            )
            self.assertEqual(self.members()[0]["retry_token"], token)
        finally:
            release.set()
            await task
        self.assertEqual(self.snapshot()["internal_phase"], "needs_retry")
        self.assertEqual(self.members()[0]["retry_token"], token)
        self.assertEqual(self.members()[1]["phase"], "complete")

    async def test_cancellation_between_durable_attempt_and_dispatch_calls_no_provider(
        self,
    ) -> None:
        original = run_cancellation._register

        def cancel_before_dispatch(
            run_id: str, attempt: str, task: asyncio.Task
        ) -> None:
            original(run_id, attempt, task)
            run_cancellation.request(run_id)

        with patch.object(
            run_cancellation, "_register", side_effect=cancel_before_dispatch
        ):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")

    async def test_deletion_between_durable_attempt_and_dispatch_calls_no_provider(
        self,
    ) -> None:
        original = run_cancellation._register

        def delete_before_dispatch(
            run_id: str, attempt: str, task: asyncio.Task
        ) -> None:
            original(run_id, attempt, task)
            run_admission.tombstone_conversation(f"cli-{run_id}")

        with patch.object(
            run_cancellation, "_register", side_effect=delete_before_dispatch
        ):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.saved()["phase"], "deleted")
        self.assertNotIn("stage2_continuation_execution", self.saved())

    async def test_orphaned_cancellation_is_not_settled_by_snapshot_or_result_read(
        self,
    ) -> None:
        retained = await self.execute()
        record = self.saved()
        record["cancellation"] = {
            "phase": "cancelling",
            "requested_at": record["created_at"],
            "completed_at": None,
        }
        self.rewrite(record)
        before = self.fixture.fixture.files()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("read mutation")
        ):
            self.assertEqual(self.snapshot()["internal_phase"], "cancelling")
            self.assertFalse(self.snapshot()["operation_owned"])
            self.assertEqual(self.load_result(self.run_id, self.models[0]), retained)
        self.assertEqual(before, self.fixture.fixture.files())

    async def test_cross_member_attempt_identity_reuse_and_noncanonical_state_are_rejected(
        self,
    ) -> None:
        for model in self.models:
            await self.execute(model)
        baseline = self.saved()
        for change in (
            "same_attempt",
            "cross_retry",
            "uppercase_uuid",
            "missing_default",
            "private_invalid_result",
            "nan_metadata",
        ):
            with self.subTest(change=change):
                record = copy.deepcopy(baseline)
                first, second = record["stage2_continuation_execution"]["operations"]
                if change == "same_attempt":
                    second["attempt"] = first["attempt"]
                elif change == "cross_retry":
                    second["retry_of"] = first["attempt"]
                elif change == "uppercase_uuid":
                    first["attempt"] = first["attempt"].upper()
                elif change == "missing_default":
                    first["result"].pop("usage")
                elif change == "private_invalid_result":
                    first["result"]["finish_reason"] = "PRIVATE_SAVED_RESPONSE_SENTINEL"
                else:
                    first["result"]["usage"] = {"nan": float("nan")}
                self.rewrite(record)
                before = self.path.read_bytes()
                try:
                    self.snapshot()
                except run_admission.AdmissionUnavailable as exc:
                    self.assertNotIn(
                        "PRIVATE_SAVED_RESPONSE_SENTINEL",
                        "".join(traceback.format_exception(exc)),
                    )
                else:
                    self.fail("Noncanonical or reused identity was accepted")
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    await self.execute()
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 2)

    async def test_second_member_can_start_first_and_summary_keeps_input_order(
        self,
    ) -> None:
        await self.execute(self.models[1])
        self.assertEqual(self.calls[0]["payload"], self.requests[1].payload())
        self.assertEqual([m["model"] for m in self.members()], self.models)
        self.assertEqual(
            [m["phase"] for m in self.members()], ["not_started", "complete"]
        )
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 1)

    async def test_parallel_members_can_finish_out_of_order_without_overwriting_results(
        self,
    ) -> None:
        started = {model: asyncio.Event() for model in self.models}
        release = {model: asyncio.Event() for model in self.models}

        async def block(**kwargs: Any) -> dict[str, Any]:
            model = kwargs["payload"]["model"]
            self.calls.append(kwargs)
            started[model].set()
            await release[model].wait()
            return {"content": f"answer for {model}", "finish_reason": "stop"}

        tasks = [
            asyncio.create_task(self.execute(model, provider=block))
            for model in self.models
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())), 5
            )
            release[self.models[1]].set()
            second = await asyncio.wait_for(tasks[1], 5)
            self.assertEqual(
                [m["phase"] for m in self.members()], ["running", "complete"]
            )
            self.assertEqual(self.load_result(self.run_id, self.models[1]), second)
            self.assertEqual(
                self.snapshot()["internal_phase"], "running_stage2_continuation"
            )
        finally:
            for event in release.values():
                event.set()
            results = await asyncio.gather(*tasks)
        self.assertEqual(self.snapshot()["internal_phase"], "stage2_needs_continuation")
        for model, result in zip(self.models, results):
            self.assertEqual(self.load_result(self.run_id, model), result)
            self.assertEqual(result.content, f"answer for {model}")

    async def test_callback_suppressing_cancellation_cannot_commit_late_success(
        self,
    ) -> None:
        started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def suppress(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(provider=suppress))
        try:
            await asyncio.wait_for(started.wait(), 5)
            run_cancellation.request(self.run_id)
            await asyncio.wait_for(cancelled.wait(), 5)
            self.assertTrue(self.snapshot()["operation_owned"])
            self.assertEqual(self.snapshot()["internal_phase"], "cancelling")
            run_cancellation.request(self.run_id)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
            with self.assertRaises(run_admission.AdmissionCancelled):
                await task
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 1)

    async def test_saved_adapter_identity_drift_during_callback_cannot_misattribute_result(
        self,
    ) -> None:
        async def drift(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage2_continuation_execution"]["provider_key"] = "synthetic-v2"
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=drift)
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))

    async def test_saved_adapter_identity_drift_before_dispatch_calls_no_provider(
        self,
    ) -> None:
        original = run_cancellation._register

        def drift_before_dispatch(
            run_id: str, attempt: str, task: asyncio.Task
        ) -> None:
            original(run_id, attempt, task)
            record = self.saved()
            record["stage2_continuation_execution"]["provider_key"] = "synthetic-v2"
            self.rewrite(record)

        with patch.object(
            run_cancellation, "_register", side_effect=drift_before_dispatch
        ):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.members()[0]["phase"], "needs_retry")

    async def test_caller_cancellation_cannot_be_suppressed_into_a_saved_success(
        self,
    ) -> None:
        started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def suppress(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(provider=suppress))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.wait_for(cancelled.wait(), 5)
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        self.assertIsNone(self.snapshot()["cancellation"])
        self.assertEqual(len(self.calls), 1)

    async def test_repeated_caller_cancellation_signals_once_and_retains_owner_through_cleanup(
        self,
    ) -> None:
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancellations = []

        async def suppress(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancellations.append("initial")
                cleaning.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancellations.append("cleanup interrupted")
                return copy.deepcopy(RESPONSE)

        with patch.object(
            run_cancellation, "_signal", wraps=run_cancellation._signal
        ) as signal:
            task = asyncio.create_task(self.execute(provider=suppress))
            try:
                await asyncio.wait_for(started.wait(), 5)
                token = self.saved()["stage2_continuation_execution"]["operations"][0][
                    "attempt"
                ]
                ownership = (self.run_id, token)
                before = self.fixture.fixture.files()
                task.cancel()
                await asyncio.wait_for(cleaning.wait(), 5)
                for _ in range(3):
                    self.assertTrue(task.cancel())
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertIn(ownership, execution._active)
                    self.assertIn(ownership, run_cancellation._tasks)
                    self.assertIn(ownership, run_cancellation._signalled)
                    self.assertTrue(self.snapshot()["operation_owned"])
                    self.assertEqual(self.members()[0]["phase"], "running")
                    self.assertIsNone(self.load_result(self.run_id, self.models[0]))
                    with self.assertRaises(execution.ExecutionPending):
                        await self.execute(retry_token=token)
                self.assertEqual(signal.call_count, 1)
                self.assertEqual(cancellations, ["initial"])
                self.assertEqual(before, self.fixture.fixture.files())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
        self.assert_released()
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        self.assertEqual(self.members()[0]["retry_token"], token)
        self.assertFalse(self.snapshot()["operation_owned"])
        with self.assertRaises(execution.RetryRequired) as raised:
            await self.execute()
        self.assertEqual(raised.exception.token, token)
        self.assertEqual(len(self.calls), 1)

    async def test_execution_record_removed_during_callback_raises_conflict_without_recreation(
        self,
    ) -> None:
        retained = []

        async def remove_execution(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record.pop("stage2_continuation_execution")
            self.rewrite(record)
            retained.append(self.path.read_bytes())
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=remove_execution)
        self.assertEqual(self.path.read_bytes(), retained[0])
        self.assertNotIn("stage2_continuation_execution", self.saved())
        self.assertIsNone(self.snapshot()["stage2_continuation_execution"])
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 1)

    async def test_exact_saved_request_and_separate_durable_attempt_precede_callback(
        self,
    ) -> None:
        before = self.saved()
        result = await self.execute()
        self.assertIsInstance(result, initial_execution.MemberResponse)
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)
        request = self.requests[0]
        self.assertEqual(
            self.calls,
            [
                {
                    "endpoint": request.endpoint,
                    "timeout_seconds": request.timeout_seconds,
                    "payload": request.payload(),
                }
            ],
        )
        after = self.saved()
        state = after.pop("stage2_continuation_execution")
        self.assertEqual(after, before)
        self.assertEqual(
            set(state), {"version", "source_revision", "provider_key", "operations"}
        )
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["source_revision"], 6)
        self.assertEqual(state["provider_key"], "synthetic-v1")
        operation = state["operations"][0]
        self.assertEqual(operation["model"], self.models[0])
        self.assertEqual(operation["turn"], 1)
        self.assertEqual(operation["inputs_sha256"], run_stage1_inputs._digest(request))
        self.assertEqual(operation["phase"], "complete")
        self.assertEqual(operation["result"], RESPONSE)
        self.assertIsNone(operation["retry_of"])
        self.assertEqual(after["stage2_execution"], self.initial_state)
        self.assertEqual(self.snapshot()["internal_phase"], "stage2_needs_continuation")
        self.assertEqual(self.members()[0]["turn"], 1)
        self.assertNotEqual(
            result.content,
            continuation_fixtures.PARTIAL["content"] + RESPONSE["content"],
        )

    async def test_turn_is_required_exact_integer_one_for_execute_and_historical_read(
        self,
    ) -> None:
        before = self.fixture.fixture.files()
        with self.assertRaises(TypeError):
            await execution.execute(
                self.run_id,
                self.models[0],
                self.provider,
                provider_key="synthetic-v1",
                expected_revision=6,
            )
        with self.assertRaises(TypeError):
            execution.load_result(self.run_id, self.models[0])
        for turn in (None, True, False, 0, 2, -1, 1.0, "1", [], {}):
            with self.subTest(turn=turn):
                with self.assertRaises(ValueError):
                    await self.execute(turn=turn)
                with self.assertRaises(ValueError):
                    execution.load_result(self.run_id, self.models[0], turn=turn)
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(self.calls, [])

    async def test_length_chunk_retains_initial_and_continuation_without_next_turn(
        self,
    ) -> None:
        async def length(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {**copy.deepcopy(RESPONSE), "finish_reason": "length"}

        result = await self.execute(provider=length)
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(result.content, RESPONSE["content"])
        self.assertEqual(self.members()[0]["phase"], "needs_continuation")
        self.assertIsNone(self.members()[0]["retry_token"])
        self.assertEqual(self.snapshot()["internal_phase"], "stage2_needs_continuation")
        attempt = self.saved()["stage2_continuation_execution"]["operations"][0][
            "attempt"
        ]
        self.assertEqual(await self.execute(), result)
        self.assertEqual(await self.execute(retry_token=attempt), result)
        with self.assertRaises(ValueError):
            await self.execute(turn=2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.saved()["stage2_execution"], self.initial_state)
        self.assertEqual(
            initial_execution.load_result(self.run_id, self.models[0]).model_dump(
                mode="json"
            ),
            continuation_fixtures.PARTIAL,
        )

    async def test_all_continuation_chunks_complete_is_not_a_complete_council(
        self,
    ) -> None:
        async def simple(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"content": "  exact continuation\n", "finish_reason": "stop"}

        for model in self.models:
            result = await self.execute(model, provider=simple)
            self.assertIsNone(result.reasoning_details)
            self.assertIsNone(result.usage)
        self.assertEqual([m["phase"] for m in self.members()], ["complete"] * 2)
        self.assertEqual([m["turn"] for m in self.members()], [1, 1])
        self.assertEqual(self.snapshot()["internal_phase"], "stage2_needs_continuation")
        self.assertFalse(self.snapshot()["operation_owned"])
        self.assertEqual(self.saved()["stage2_execution"], self.initial_state)
        self.assertEqual(len(self.calls), 2)
        # Retaining stopped chunks is not permission to assemble a ballot.
        self.assertIsNone(run_stage2_results.load_complete_results(self.run_id))

    async def test_initial_attempt_tokens_never_authorize_continuation_retry(
        self,
    ) -> None:
        tokens = [op["attempt"] for op in self.initial_state["operations"]]
        before = self.fixture.fixture.files()
        for token in tokens:
            with self.assertRaises(execution.ExecutionConflict):
                await self.execute(retry_token=token)
        self.assertEqual(before, self.fixture.fixture.files())
        own = await self.uncertain()
        before = self.fixture.fixture.files()
        for token in tokens:
            with self.assertRaises(execution.ExecutionConflict):
                await self.execute(retry_token=token)
        self.assertEqual(before, self.fixture.fixture.files())
        result = await self.execute(retry_token=own)
        self.assertEqual(await self.execute(retry_token=own), result)
        self.assertEqual(len(self.calls), 2)

    async def test_missing_continuation_input_or_unknown_run_never_creates_work(
        self,
    ) -> None:
        record = self.saved()
        record.pop("stage2_continuation_inputs")
        self.rewrite(record)
        before = self.fixture.fixture.files()
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute()
        with self.assertRaises(execution.ExecutionConflict):
            self.load_result(self.run_id, self.models[0])
        unknown = str(uuid4())
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await execution.execute(
                unknown,
                self.models[0],
                self.provider,
                turn=1,
                provider_key="synthetic-v1",
                expected_revision=6,
            )
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.load_result(unknown, self.models[0])
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(self.calls, [])

    async def test_adding_sibling_prepared_inputs_during_dispatch_preserves_attempt(
        self,
    ) -> None:
        record = self.saved()
        record["stage2_continuation_inputs"]["requests"].pop()
        self.rewrite(record)
        saved_attempt = []

        async def prepare_sibling(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(copy.deepcopy(kwargs))
            saved_attempt.append(
                copy.deepcopy(
                    self.saved()["stage2_continuation_execution"]["operations"][0]
                )
            )
            prepared = continuation.bind(
                self.run_id, self.models[1], expected_revision=6
            )
            self.assertEqual(prepared, self.requests[1])
            self.assertEqual(
                self.saved()["stage2_continuation_execution"]["operations"][0],
                saved_attempt[0],
            )
            return copy.deepcopy(RESPONSE)

        result = await self.execute(provider=prepare_sibling)
        self.assertEqual(self.load_result(self.run_id, self.models[0]), result)
        self.assertEqual(len(self.saved()["stage2_continuation_inputs"]["requests"]), 2)
        self.assertEqual(
            saved_attempt[0]["inputs_sha256"],
            run_stage1_inputs._digest(self.requests[0]),
        )
        self.assertEqual(self.saved()["stage2_execution"], self.initial_state)
        self.assertEqual(len(self.calls), 1)

    async def test_adding_sibling_prepared_inputs_preserves_saved_result_and_retry(
        self,
    ) -> None:
        record = self.saved()
        record["stage2_continuation_inputs"]["requests"].pop()
        self.rewrite(record)
        token = await self.uncertain()
        operation = copy.deepcopy(
            self.saved()["stage2_continuation_execution"]["operations"][0]
        )
        continuation.bind(self.run_id, self.models[1], expected_revision=6)
        self.assertEqual(
            self.saved()["stage2_continuation_execution"]["operations"][0], operation
        )
        self.assertEqual(self.members()[0]["retry_token"], token)
        result = await self.execute(retry_token=token)
        self.assertEqual(await self.execute(retry_token=token), result)
        self.assertEqual(len(self.calls), 2)

    async def test_saved_initial_source_drift_during_callback_discards_chunk(
        self,
    ) -> None:
        async def drift(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage2_execution"]["operations"][0]["result"]["content"] += (
                " changed"
            )
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.execute(provider=drift)
        operation = self.saved()["stage2_continuation_execution"]["operations"][0]
        self.assertEqual(operation["phase"], "in_flight")
        self.assertIsNone(operation["result"])
        self.assertEqual(len(self.calls), 1)

    async def test_retry_ancestry_drift_during_callback_discards_chunk(
        self,
    ) -> None:
        token = await self.uncertain()
        replacement = str(uuid4())

        async def drift(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage2_continuation_execution"]["operations"][0]["retry_of"] = (
                replacement
            )
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=drift, retry_token=token)
        self.assertEqual(
            self.saved()["stage2_continuation_execution"]["operations"][0]["retry_of"],
            replacement,
        )
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 2)

    async def test_saved_request_drift_before_dispatch_never_calls_provider(
        self,
    ) -> None:
        original = run_cancellation._register

        def drift(run_id: str, attempt: str, task: asyncio.Task) -> None:
            original(run_id, attempt, task)
            record = self.saved()
            record["stage2_continuation_inputs"]["requests"][0]["payload_json"] += " "
            self.rewrite(record)

        with patch.object(run_cancellation, "_register", side_effect=drift):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute()
        self.assertEqual(self.calls, [])
        self.assertIsNone(
            self.saved()["stage2_continuation_execution"]["operations"][0]["result"]
        )

    async def test_running_initial_peer_takes_precedence_over_continuation_retry(
        self,
    ) -> None:
        record = self.saved()
        record["stage2_execution"]["operations"].pop()
        record["stage2_continuation_inputs"]["requests"].pop()
        self.rewrite(record)
        token = await self.uncertain()
        started, release = asyncio.Event(), asyncio.Event()
        initial_calls = []

        async def initial(**kwargs: Any) -> dict[str, Any]:
            initial_calls.append(kwargs)
            started.set()
            await release.wait()
            return {"content": "Independent initial answer", "finish_reason": "stop"}

        task = asyncio.create_task(
            initial_execution.execute(
                self.run_id,
                self.models[1],
                initial,
                provider_key="synthetic-review-v1",
                expected_revision=6,
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 5)
            self.assertEqual(self.snapshot()["internal_phase"], "running_stage2")
            self.assertTrue(self.snapshot()["operation_owned"])
            self.assertEqual(self.members()[0]["retry_token"], token)
            self.assertEqual(
                self.snapshot()["stage2_execution"]["members"][1]["phase"], "running"
            )
        finally:
            release.set()
            await task
        self.assertEqual(self.snapshot()["internal_phase"], "needs_retry")
        self.assertEqual(self.members()[0]["retry_token"], token)
        self.assertEqual(len(initial_calls), 1)
        self.assertEqual(len(self.calls), 1)

    async def test_running_continuation_takes_precedence_over_initial_peer_retry(
        self,
    ) -> None:
        record = self.saved()
        record["stage2_execution"]["operations"][1]["phase"] = "in_flight"
        record["stage2_execution"]["operations"][1]["result"] = None
        record["stage2_continuation_inputs"]["requests"].pop()
        self.rewrite(record)
        initial_token = record["stage2_execution"]["operations"][1]["attempt"]
        started, release = asyncio.Event(), asyncio.Event()

        async def block(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return copy.deepcopy(RESPONSE)

        task = asyncio.create_task(self.execute(provider=block))
        try:
            await asyncio.wait_for(started.wait(), 5)
            self.assertEqual(
                self.snapshot()["internal_phase"], "running_stage2_continuation"
            )
            self.assertTrue(self.snapshot()["operation_owned"])
            self.assertEqual(self.members()[0]["phase"], "running")
            self.assertEqual(
                self.snapshot()["stage2_execution"]["members"][1]["retry_token"],
                initial_token,
            )
        finally:
            release.set()
            await task
        self.assertEqual(self.snapshot()["internal_phase"], "needs_retry")
        self.assertEqual(len(self.calls), 1)

    async def test_initializing_receipt_cannot_hide_continuation_execution(
        self,
    ) -> None:
        await self.execute()
        record = self.saved()
        record["phase"] = "initializing"
        for key in (
            "interaction",
            "refinement_execution",
            "refinement_provider",
            "pre_query_policy",
            "council_selection",
            "council_request_policy",
            "council_model_budgets",
            "skill_plan",
            "stage1_inputs",
            "explicit_skill_provenance",
            "explicit_skill_snapshot",
            "stage1_execution",
            "stage1_continuation_inputs",
            "stage1_continuation_execution",
            "response_review_policy",
            "stage2_inputs",
            "stage2_execution",
            "stage2_continuation_inputs",
        ):
            record.pop(key, None)
        self.rewrite(record)
        before = self.fixture.fixture.files()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.snapshot()
        self.assertEqual(before, self.fixture.fixture.files())
        # The new field alone must be the reason initialization is rejected.
        record.pop("stage2_continuation_execution")
        self.rewrite(record)
        self.assertEqual(self.snapshot()["internal_phase"], "initializing")

    async def test_saved_continuation_tokens_cannot_reuse_initial_attempts(
        self,
    ) -> None:
        await self.execute()
        baseline = self.saved()
        for field in ("attempt", "retry_of"):
            for initial in self.initial_state["operations"]:
                with self.subTest(field=field, initial_model=initial["model"]):
                    record = copy.deepcopy(baseline)
                    record["stage2_continuation_execution"]["operations"][0][field] = (
                        initial["attempt"]
                    )
                    self.rewrite(record)
                    before = self.fixture.fixture.files()
                    for read in (
                        self.snapshot,
                        lambda: self.load_result(self.run_id, self.models[0]),
                    ):
                        with self.assertRaises(run_admission.AdmissionUnavailable):
                            read()
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        await self.execute()
                    self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_continuation_adapter_identity_is_independent_of_initial_adapter(
        self,
    ) -> None:
        result = await self.execute(provider_key="continuation-synthetic-v2")
        self.assertEqual(
            self.saved()["stage2_execution"]["provider_key"], "synthetic-review-v1"
        )
        self.assertEqual(
            self.saved()["stage2_continuation_execution"]["provider_key"],
            "continuation-synthetic-v2",
        )
        self.assertEqual(
            await self.execute(provider_key="continuation-synthetic-v2"), result
        )
        self.assertEqual(self.saved()["stage2_execution"], self.initial_state)
        self.assertEqual(len(self.calls), 1)

    async def test_adding_sibling_prepared_input_before_dispatch_preserves_target(
        self,
    ) -> None:
        record = self.saved()
        record["stage2_continuation_inputs"]["requests"].pop()
        self.rewrite(record)
        original = run_cancellation._register

        def prepare_sibling(run_id: str, attempt: str, task: asyncio.Task) -> None:
            original(run_id, attempt, task)
            continuation.bind(run_id, self.models[1], expected_revision=6)

        with patch.object(run_cancellation, "_register", side_effect=prepare_sibling):
            result = await self.execute()
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)
        self.assertEqual(self.calls[0]["payload"], self.requests[0].payload())
        self.assertEqual(len(self.saved()["stage2_continuation_inputs"]["requests"]), 2)
        self.assertEqual(len(self.calls), 1)

    async def test_retry_ancestry_drift_before_dispatch_calls_no_provider(self) -> None:
        token = await self.uncertain()
        original = run_cancellation._register

        def drift(run_id: str, attempt: str, task: asyncio.Task) -> None:
            original(run_id, attempt, task)
            record = self.saved()
            record["stage2_continuation_execution"]["operations"][0]["retry_of"] = str(
                uuid4()
            )
            self.rewrite(record)

        with patch.object(run_cancellation, "_register", side_effect=drift):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute(retry_token=token)
        self.assertIsNone(self.load_result(self.run_id, self.models[0]))
        self.assertEqual(self.members()[0]["phase"], "needs_retry")
        self.assertEqual(len(self.calls), 1)

    async def test_restart_historical_chunks_and_retry_tokens_are_byte_pure(
        self,
    ) -> None:
        result = await self.execute()
        token = await self.uncertain(self.models[1])
        before = self.fixture.fixture.files()
        program = """
import json, socket, sys
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend import config, storage, run_admission, run_inspection
from backend import run_stage2_continuation_execution as execution
from backend import run_stage2_execution as initial
storage.DATA_DIR = sys.argv[1]
config.COUNCIL_MODELS = ['changed/model']
config.OPENROUTER_API_URL = 'https://invalid.example/not-used'
config.SKILLS_ENABLED = False
run_admission._write = lambda *a, **k: (_ for _ in ()).throw(AssertionError('write'))
snapshot = run_inspection.inspect(sys.argv[2])
assert snapshot['internal_phase'] == 'needs_retry'
assert snapshot['stage2_continuation_execution']['members'][1]['retry_token'] == sys.argv[3]
assert not snapshot['operation_owned']
assert not execution._active
assert execution.load_result(sys.argv[2], sys.argv[5], turn=1) is None
result = execution.load_result(sys.argv[2], sys.argv[4], turn=1)
assert initial.load_result(sys.argv[2], sys.argv[4]).finish_reason == 'length'
assert result.finish_reason == 'stop'
print(result.model_dump_json())
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                storage.DATA_DIR,
                self.run_id,
                token,
                *self.models,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        self.assertEqual(json.loads(completed.stdout), result.model_dump(mode="json"))
        self.assertEqual(before, self.fixture.fixture.files())
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
