"""Offline durable initial Stage 1 attempts, explicit retries and safe reads."""

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

import test_run_stage1_inputs as input_fixtures

from backend import (
    config,
    model_context,
    openrouter,
    run_admission,
    run_cancellation,
    run_inspection,
    run_stage1_inputs,
    skills,
    storage,
)
from backend import run_stage1_execution as execution

RESPONSE = {
    "content": "Private synthetic answer Пример ☃",
    "finish_reason": "stop",
    "reasoning_details": [{"type": "synthetic", "nested": {"text": ["private"]}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "nested": [None, True]},
}


class Stage1ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Fixture preparation calls asyncio.run and must precede the async test.
        self.fixture = input_fixtures.Stage1InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.complete()
        self.inputs = self.fixture.bind()
        self.run_id = self.fixture.run
        self.path = run_admission._path(self.run_id)
        self.models = [request.model for request in self.inputs.requests]
        self.calls: list[dict[str, Any]] = []
        self.addCleanup(self.assert_released)

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
        return self.snapshot()["stage1_execution"]["members"]

    async def provider(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(kwargs))
        model = kwargs["payload"]["model"]
        operation = next(
            op
            for op in self.saved()["stage1_execution"]["operations"]
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
            provider_key=kwargs.pop("provider_key", "synthetic-v1"),
            expected_revision=kwargs.pop("expected_revision", 3),
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

    async def test_exact_saved_arguments_and_durable_attempt_precede_one_callback(
        self,
    ) -> None:
        before = self.saved()
        result = await self.execute()
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)
        self.assertEqual(
            self.calls,
            [
                {
                    "endpoint": self.inputs.endpoint,
                    "timeout_seconds": self.inputs.timeout_seconds,
                    "payload": self.inputs.requests[0].payload(),
                }
            ],
        )
        after = self.saved()
        state = after.pop("stage1_execution")
        self.assertEqual(after, before)
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["source_revision"], 3)
        self.assertEqual(state["inputs_sha256"], run_stage1_inputs._digest(self.inputs))
        self.assertEqual(state["provider_key"], "synthetic-v1")
        self.assertEqual(state["operations"][0]["result"], RESPONSE)
        self.assertEqual(state["operations"][0]["phase"], "complete")
        self.assertIsNone(state["operations"][0]["retry_of"])
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_partial")

    async def test_saved_result_replay_and_historical_read_are_detached_and_read_only(
        self,
    ) -> None:
        result = await self.execute()
        original = copy.deepcopy(RESPONSE)
        result.usage["nested"].append("caller mutation")
        result.reasoning_details[0]["nested"]["text"].append("caller mutation")
        before = self.fixture.files()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            repeated = await self.execute()
            loaded = execution.load_result(self.run_id, self.models[0])
            self.assertEqual(repeated.model_dump(mode="json"), original)
            self.assertEqual(loaded.model_dump(mode="json"), original)
            self.assertIsNone(execution.load_result(self.run_id, self.models[1]))
            loaded.usage["nested"].append("read mutation")
            self.assertEqual(
                execution.load_result(self.run_id, self.models[0]), repeated
            )
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_callback_mutation_cannot_change_saved_request_or_response(
        self,
    ) -> None:
        response = copy.deepcopy(RESPONSE)
        before = self.saved()["stage1_inputs"]

        async def mutate(**kwargs: Any) -> dict[str, Any]:
            kwargs["payload"]["messages"][0]["content"] = "modified by adapter"
            kwargs["payload"]["max_tokens"] = 1
            return response

        result = await self.execute(provider=mutate)
        response["usage"]["nested"].append("adapter mutation")
        self.assertEqual(self.saved()["stage1_inputs"], before)
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)

    async def test_length_stop_retains_response_without_automatic_continuation(
        self,
    ) -> None:
        async def length(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {**copy.deepcopy(RESPONSE), "finish_reason": "length"}

        result = await self.execute(provider=length)
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_needs_continuation")
        self.assertEqual(self.members()[0]["phase"], "needs_continuation")
        self.assertIsNone(self.members()[0]["retry_token"])
        self.assertEqual(await self.execute(), result)
        await self.execute(self.models[1])
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_needs_continuation")
        self.assertEqual(len(self.calls), 2)

    async def test_all_members_complete_and_optional_metadata_can_be_absent(
        self,
    ) -> None:
        async def simple(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"content": "  exact text\n", "finish_reason": "stop"}

        for model in self.models:
            result = await self.execute(model, provider=simple)
            self.assertEqual(result.content, "  exact text\n")
            self.assertIsNone(result.reasoning_details)
            self.assertIsNone(result.usage)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["internal_phase"], "stage1_complete")
        self.assertFalse(snapshot["operation_owned"])
        self.assertIsNone(snapshot["retry_token"])
        self.assertEqual([m["phase"] for m in self.members()], ["complete"] * 2)

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
            self.assertEqual(snapshot["internal_phase"], "running_stage1")
            self.assertTrue(snapshot["operation_owned"])
            self.assertEqual([m["phase"] for m in self.members()], ["running"] * 2)
            for op in self.saved()["stage1_execution"]["operations"]:
                for token in (None, op["attempt"]):
                    with self.assertRaises(execution.ExecutionPending):
                        await self.execute(op["model"], retry_token=token)
            self.assertEqual(len(self.calls), 2)
        finally:
            release.set()
            await asyncio.gather(*tasks)
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_complete")

    async def test_retry_requires_current_per_member_consent_and_never_replays_automatically(
        self,
    ) -> None:
        first = await self.uncertain()
        second = await self.uncertain(self.models[1])
        self.assertNotEqual(first, second)
        before = self.fixture.files()
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
        self.assertEqual(before, self.fixture.files())
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
                self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
                self.assertNotIn(secret, self.path.read_text())
                self.assert_released()
        self.assertEqual(len(self.calls), len(responses))

    async def test_initial_write_failure_never_calls_provider(self) -> None:
        before = self.fixture.files()
        with patch.object(
            run_admission, "_write", side_effect=OSError("attempt write failed")
        ):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.snapshot()["stage1_execution"])

    async def test_lost_attempt_write_ack_never_calls_and_requires_retry(self) -> None:
        original = run_admission._write

        def lose_ack(record: dict[str, Any]) -> None:
            original(record)
            raise OSError("attempt acknowledgment lost")

        with patch.object(run_admission, "_write", side_effect=lose_ack):
            with self.assertRaises(OSError):
                await self.execute()
        before = self.fixture.files()
        token = self.members()[0]["retry_token"]
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(before, self.fixture.files())
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
                for op in record.get("stage1_execution", {}).get("operations", [])
            ):
                raise OSError("completion write failed")
            original(record)

        with patch.object(run_admission, "_write", side_effect=fail_complete):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
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
                for op in record.get("stage1_execution", {}).get("operations", [])
            ):
                raise OSError("completion acknowledgment lost")

        with patch.object(run_admission, "_write", side_effect=lose_complete):
            with self.assertRaises(OSError):
                await self.execute()
        before = self.fixture.files()
        result = await self.execute()
        self.assertEqual(result.model_dump(mode="json"), RESPONSE)
        self.assertEqual(before, self.fixture.files())
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
            self.assertEqual(
                execution.load_result(self.run_id, self.models[0]), retained
            )
            run_cancellation.request(self.run_id)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertFalse(self.snapshot()["operation_owned"])
        self.assertEqual(execution.load_result(self.run_id, self.models[0]), retained)
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
        self.assertEqual(execution.load_result(self.run_id, self.models[0]), retained)
        self.assertIsNone(execution.load_result(self.run_id, self.models[1]))

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
        self.assertNotIn("stage1_execution", self.saved())
        for read in (
            self.snapshot,
            lambda: execution.load_result(self.run_id, self.models[0]),
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
        before = self.fixture.files()
        for name, value in (
            ("COUNCIL_MODELS", ["changed/model"]),
            ("OPENROUTER_API_URL", "https://invalid.example/api"),
            ("SKILLS_ENABLED", False),
        ):
            with self.subTest(setting=name), patch.object(config, name, value):
                self.assertEqual(
                    execution.load_result(self.run_id, self.models[0]), retained
                )
                with self.assertRaises(run_stage1_inputs.Stage1InputConflict):
                    await self.execute(self.models[1])
        self.assertEqual(before, self.fixture.files())
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
            self.assertEqual(execution.load_result(self.run_id, self.models[0]), result)
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
        before = self.fixture.files()
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
            execution.load_result(self.run_id, "unknown/model")
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(self.calls, [])

    async def test_adapter_identity_change_blocks_all_members_and_saved_replays(
        self,
    ) -> None:
        await self.execute()
        before = self.fixture.files()
        for model in self.models:
            with self.assertRaises(execution.ExecutionConflict):
                await self.execute(model, provider_key="synthetic-v2")
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_missing_inputs_or_unknown_run_does_not_create_work(self) -> None:
        record = self.saved()
        record.pop("stage1_inputs")
        self.rewrite(record)
        before = self.fixture.files()
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute()
        with self.assertRaises(execution.ExecutionConflict):
            execution.load_result(self.run_id, self.models[0])
        unknown = str(uuid4())
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await execution.execute(
                unknown,
                self.models[0],
                self.provider,
                provider_key="synthetic-v1",
                expected_revision=3,
            )
        with self.assertRaises(run_admission.AdmissionUnavailable):
            execution.load_result(unknown, self.models[0])
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(self.calls, [])

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
            self.saved()["stage1_execution"]["operations"][0]["phase"], "in_flight"
        )
        self.assertEqual(len(self.calls), 1)

    async def test_superseded_attempt_cannot_commit_a_late_response(self) -> None:
        replacement = str(uuid4())

        async def supersede(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage1_execution"]["operations"][0]["attempt"] = replacement
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=supersede)
        self.assertEqual(self.members()[0]["retry_token"], replacement)
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))

    async def test_polling_is_compact_private_detached_and_does_not_resolve_or_write(
        self,
    ) -> None:
        await self.execute()
        before = self.fixture.files()
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
                repr(execution.load_result(self.run_id, self.models[0])),
            )
            snapshot["stage1_execution"]["members"][0]["phase"] = "tampered"
            self.assertEqual(self.members()[0]["phase"], "complete")
        self.assertEqual(before, self.fixture.files())
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
            ("inputs_sha256", "0" * 64),
            ("provider_key", "bad key"),
            ("operations", []),
            ("extra", "private"),
        ):
            state = copy.deepcopy(baseline["stage1_execution"])
            state[key] = value
            variants.append((key, state))
        for key, value in (
            ("model", "other/model"),
            ("attempt", "bad"),
            ("phase", "in_flight"),
            ("result", None),
            ("retry_of", baseline["stage1_execution"]["operations"][0]["attempt"]),
        ):
            state = copy.deepcopy(baseline["stage1_execution"])
            state["operations"][0][key] = value
            variants.append((key, state))
        duplicate = copy.deepcopy(baseline["stage1_execution"])
        duplicate["operations"].append(copy.deepcopy(duplicate["operations"][0]))
        variants.append(("duplicate member", duplicate))
        for label, state in variants:
            with self.subTest(change=label):
                record = copy.deepcopy(baseline)
                record["stage1_execution"] = state
                self.rewrite(record)
                before = self.fixture.files()
                for read in (
                    self.snapshot,
                    lambda: execution.load_result(self.run_id, self.models[0]),
                ):
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        read()
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    await self.execute()
                self.assertEqual(before, self.fixture.files())
        self.assertEqual(len(self.calls), 1)

    async def test_oversized_content_or_metadata_is_rejected_without_leaking_or_retrying(
        self,
    ) -> None:
        baseline = self.path.read_bytes()
        for field in ("content", "usage", "reasoning_details"):
            with self.subTest(field=field):
                self.path.write_bytes(baseline)
                response = copy.deepcopy(RESPONSE)
                large = "☃" * (execution.MAX_RESULT_BYTES // 3 + 1)
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
                self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 3)

    async def test_normalized_response_size_limit_counts_utf8_bytes_and_metadata(
        self,
    ) -> None:
        raw = {"content": "☃ exact", "finish_reason": "stop"}
        normalized = {**raw, "reasoning_details": None, "usage": None}
        size = len(
            json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        with patch.object(execution, "MAX_RESULT_BYTES", size):
            self.assertEqual(
                execution.MemberResponse.model_validate(raw).content, raw["content"]
            )
        with patch.object(execution, "MAX_RESULT_BYTES", size - 1):
            with self.assertRaises(ValueError):
                execution.MemberResponse.model_validate(raw)

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
            before = self.fixture.files()
            with patch.object(execution, "_active", set()):
                orphaned = self.snapshot()
                self.assertEqual(orphaned["internal_phase"], "needs_retry")
                self.assertFalse(orphaned["operation_owned"])
                self.assertIsNotNone(
                    orphaned["stage1_execution"]["members"][0]["retry_token"]
                )
                self.assertNotEqual(live["cursor"], orphaned["cursor"])
            self.assertEqual(before, self.fixture.files())
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
            self.assertEqual(self.snapshot()["internal_phase"], "running_stage1")
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
        self.assertNotIn("stage1_execution", self.saved())

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
        before = self.fixture.files()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("read mutation")
        ):
            self.assertEqual(self.snapshot()["internal_phase"], "cancelling")
            self.assertFalse(self.snapshot()["operation_owned"])
            self.assertEqual(
                execution.load_result(self.run_id, self.models[0]), retained
            )
        self.assertEqual(before, self.fixture.files())

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
                first, second = record["stage1_execution"]["operations"]
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

    async def test_initializing_receipt_cannot_hide_saved_stage1_execution(
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
        ):
            record.pop(key, None)
        self.rewrite(record)
        before = self.fixture.files()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.snapshot()
        self.assertEqual(before, self.fixture.files())

    async def test_second_member_can_start_first_and_summary_keeps_input_order(
        self,
    ) -> None:
        await self.execute(self.models[1])
        self.assertEqual(self.calls[0]["payload"], self.inputs.requests[1].payload())
        self.assertEqual([m["model"] for m in self.members()], self.models)
        self.assertEqual(
            [m["phase"] for m in self.members()], ["not_started", "complete"]
        )
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
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
            self.assertEqual(execution.load_result(self.run_id, self.models[1]), second)
            self.assertEqual(self.snapshot()["internal_phase"], "running_stage1")
        finally:
            for event in release.values():
                event.set()
            results = await asyncio.gather(*tasks)
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_complete")
        for model, result in zip(self.models, results):
            self.assertEqual(execution.load_result(self.run_id, model), result)
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
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 1)

    async def test_saved_adapter_identity_drift_during_callback_cannot_misattribute_result(
        self,
    ) -> None:
        async def drift(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            record = self.saved()
            record["stage1_execution"]["provider_key"] = "synthetic-v2"
            self.rewrite(record)
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=drift)
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))

    async def test_saved_adapter_identity_drift_before_dispatch_calls_no_provider(
        self,
    ) -> None:
        original = run_cancellation._register

        def drift_before_dispatch(
            run_id: str, attempt: str, task: asyncio.Task
        ) -> None:
            original(run_id, attempt, task)
            record = self.saved()
            record["stage1_execution"]["provider_key"] = "synthetic-v2"
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
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
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
                token = self.saved()["stage1_execution"]["operations"][0]["attempt"]
                ownership = (self.run_id, token)
                before = self.fixture.files()
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
                    self.assertIsNone(
                        execution.load_result(self.run_id, self.models[0])
                    )
                    with self.assertRaises(execution.ExecutionPending):
                        await self.execute(retry_token=token)
                self.assertEqual(signal.call_count, 1)
                self.assertEqual(cancellations, ["initial"])
                self.assertEqual(before, self.fixture.files())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
        self.assert_released()
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
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
            record.pop("stage1_execution")
            self.rewrite(record)
            retained.append(self.path.read_bytes())
            return copy.deepcopy(RESPONSE)

        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(provider=remove_execution)
        self.assertEqual(self.path.read_bytes(), retained[0])
        self.assertNotIn("stage1_execution", self.saved())
        self.assertIsNone(self.snapshot()["stage1_execution"])
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
        self.assertEqual(len(self.calls), 1)

    async def test_restart_retains_results_and_member_retry_tokens_without_mutation(
        self,
    ) -> None:
        result = await self.execute()
        token = await self.uncertain(self.models[1])
        before = self.fixture.files()
        settings = {
            key: getattr(config, key)
            for key in (
                "MODEL_BACKEND",
                "COUNCIL_MODELS",
                "NEGATIVE_MODE_ENABLED",
                "DELIBERATION_ENABLED",
                "PAIRWISE_RANKING_ENABLED",
                *input_fixtures.request_fixtures.SETTINGS,
                *input_fixtures.skill_fixtures.SETTINGS,
            )
        }
        program = """
import asyncio, json, socket, sys
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend import config, storage, run_admission, run_inspection, run_stage1_execution as execution
storage.DATA_DIR = sys.argv[1]
for key, value in json.loads(sys.argv[6]).items():
    setattr(config, key, value)
run_admission._write = lambda *a, **k: (_ for _ in ()).throw(AssertionError('write'))
snapshot = run_inspection.inspect(sys.argv[2])
assert snapshot['internal_phase'] == 'needs_retry'
assert snapshot['stage1_execution']['members'][1]['retry_token'] == sys.argv[3]
assert not snapshot['operation_owned']
assert not execution._active
assert execution.load_result(sys.argv[2], sys.argv[5]) is None
result = execution.load_result(sys.argv[2], sys.argv[4])
async def forbidden(**kwargs):
    raise AssertionError('provider replay')
async def check():
    replay = await execution.execute(sys.argv[2], sys.argv[4], forbidden,
        provider_key='synthetic-v1', expected_revision=3)
    assert replay == result
    try:
        await execution.execute(sys.argv[2], sys.argv[5], forbidden,
            provider_key='synthetic-v1', expected_revision=3)
    except execution.RetryRequired as exc:
        assert exc.token == sys.argv[3]
    else:
        raise AssertionError('missing explicit retry guard')
asyncio.run(check())
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
                json.dumps(settings),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        self.assertEqual(json.loads(completed.stdout), result.model_dump(mode="json"))
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
