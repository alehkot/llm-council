"""Offline first-continuation provenance, exact payloads, and durable guards."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import test_run_stage1_inputs as input_fixtures

from backend import (
    config,
    council_model_budgets,
    model_context,
    openrouter,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_stage1_inputs,
    skills,
    storage,
)
from backend import run_stage1_continuation_inputs as continuation
from backend import run_stage1_execution as execution

PARTIAL = {
    "content": "  Private synthetic partial Пример ☃\n",
    "finish_reason": "length",
    "reasoning_details": [
        {"type": "synthetic", "nested": {"text": ["private thought"]}}
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "private_usage": [True]},
}
INSTRUCTION = (
    "Continue exactly where the previous answer stopped. Do not repeat earlier "
    "text or add a new introduction. Complete the requested answer."
)


class FirstContinuationInputTests(unittest.TestCase):
    def setUp(self) -> None:
        # Compose the fixture so none of its test methods run a second time.
        self.fixture = input_fixtures.Stage1InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run_id = self.fixture.run
        self.path = run_admission._path(self.run_id)
        self.models = list(config.COUNCIL_MODELS)
        self.ready = False
        self.inputs = None
        self.calls: list[dict[str, Any]] = []

    def complete(self) -> None:
        if not self.ready:
            self.fixture.complete()
            self.ready = True

    def initial(self) -> None:
        self.complete()
        if self.inputs is None:
            self.inputs = self.fixture.bind()

    def execute(
        self, model: str | None = None, response: dict[str, Any] | None = None
    ) -> execution.MemberResponse:
        self.initial()

        async def provider(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(copy.deepcopy(kwargs))
            return copy.deepcopy(PARTIAL if response is None else response)

        return asyncio.run(
            execution.execute(
                self.run_id,
                model or self.models[0],
                provider,
                provider_key="synthetic-v1",
                expected_revision=3,
            )
        )

    def bind(self, model: str | None = None, revision: Any = 3) -> Any:
        return continuation.bind(
            self.run_id, model or self.models[0], expected_revision=revision
        )

    def load(self, model: str | None = None) -> Any:
        return continuation.load(self.run_id, model or self.models[0])

    def saved(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def rewrite(self, record: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(record))

    def files(self) -> dict[str, bytes]:
        return self.fixture.files()

    def expected_payload(self, index: int = 0, response: Any = None) -> dict[str, Any]:
        response = PARTIAL if response is None else response
        payload = self.inputs.requests[index].payload()
        assistant = {"role": "assistant", "content": response["content"]}
        if response.get("reasoning_details"):
            assistant["reasoning_details"] = copy.deepcopy(
                response["reasoning_details"]
            )
        payload["messages"].extend(
            [assistant, {"role": "user", "content": INSTRUCTION}]
        )
        return payload

    def set_budget(self, target: int, index: int = 0) -> None:
        """Choose valid resolver evidence before freezing the initial request."""
        raw = self.saved()
        for context in range(257, 65536):
            output = min(1234, 4096, context // 4)
            if context - output - max(256, context // 20) == target:
                limits = {
                    **input_fixtures.LIMITS,
                    "context_length": context,
                    "supported_parameters": ["reasoning"] if index == 0 else [],
                }
                raw["council_model_budgets"]["models"][index] = (
                    council_model_budgets._budget(
                        self.models[index], limits, 1234
                    ).model_dump(mode="json")
                )
                self.rewrite(raw)
                return
        self.fail("No exact synthetic budget found")

    def continuation_bound(self) -> int:
        self.complete()
        record = self.saved()
        initial = run_stage1_inputs._derive(
            record, run_interaction._validate_record(record)
        )
        messages = initial.requests[0].payload()["messages"]
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": PARTIAL["content"],
                    "reasoning_details": copy.deepcopy(PARTIAL["reasoning_details"]),
                },
                {"role": "user", "content": INSTRUCTION},
            ]
        )
        return model_context.token_bound(messages)

    def test_exact_payload_and_all_saved_causal_sources(self) -> None:
        result = self.execute()
        before = self.saved()
        request = self.bind()
        op = before["stage1_execution"]["operations"][0]
        self.assertIsInstance(request, continuation.FirstContinuationInput)
        self.assertEqual(request.version, 1)
        self.assertEqual(request.turn, 1)
        self.assertEqual(request.source_revision, 3)
        self.assertEqual(
            request.initial_inputs_sha256, run_stage1_inputs._digest(self.inputs)
        )
        self.assertEqual(request.initial_attempt, UUID(op["attempt"]))
        self.assertEqual(
            request.initial_operation_sha256,
            run_stage1_inputs._digest(execution.Operation.model_validate(op)),
        )
        self.assertEqual(
            request.initial_response_sha256, run_stage1_inputs._digest(result)
        )
        self.assertEqual(request.initial_provider_key, "synthetic-v1")
        self.assertEqual(request.model, self.models[0])
        self.assertEqual(request.endpoint, self.inputs.endpoint)
        self.assertEqual(request.timeout_seconds, self.inputs.timeout_seconds)
        self.assertEqual(request.input_budget, self.inputs.requests[0].input_budget)
        self.assertEqual(request.output_budget, self.inputs.requests[0].output_budget)
        self.assertEqual(request.payload(), self.expected_payload())
        self.assertEqual(
            request.payload_json, run_stage1_inputs._json(self.expected_payload())
        )
        self.assertEqual(
            request.input_token_bound,
            model_context.token_bound(request.payload()["messages"]),
        )
        after = self.saved()
        state = after.pop("stage1_continuation_inputs")
        self.assertEqual(after, before)
        self.assertEqual(
            state, {"version": 1, "requests": [request.model_dump(mode="json")]}
        )
        self.assertNotIn("usage", request.payload()["messages"][-2])
        self.assertEqual(len(self.calls), 1)

    def test_actual_legacy_continuation_messages_match_without_network(self) -> None:
        self.execute()
        request = self.bind()
        calls = []

        async def once(
            model: str, messages: Any, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            calls.append((model, copy.deepcopy(messages), copy.deepcopy(kwargs)))
            return (
                copy.deepcopy(PARTIAL)
                if len(calls) == 1
                else {"content": "done", "finish_reason": "stop"}
            )

        with (
            patch.object(
                openrouter,
                "model_limits",
                AsyncMock(return_value=input_fixtures.LIMITS),
            ),
            patch.object(openrouter, "OPENROUTER_MAX_TOKENS", 1234),
            patch.object(openrouter, "OPENROUTER_MAX_CONTINUATIONS", 1),
            patch.object(openrouter, "_query_model_once", side_effect=once),
        ):
            result = asyncio.run(
                openrouter.query_model(
                    self.models[0],
                    self.inputs.requests[0].payload()["messages"],
                    trace_stage="stage1",
                )
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1], request.payload()["messages"])
        self.assertEqual(calls[1][2]["max_tokens"], request.output_budget)
        self.assertTrue(calls[1][2]["reasoning"])
        self.assertEqual(result["content"], PARTIAL["content"] + "done")

    def test_absent_or_empty_reasoning_is_omitted_and_original_reasoning_flag_preserved(
        self,
    ) -> None:
        for index, value in enumerate((None, [])):
            response = {**copy.deepcopy(PARTIAL), "reasoning_details": value}
            self.execute(self.models[index], response)
            request = self.bind(self.models[index])
            self.assertEqual(request.payload(), self.expected_payload(index, response))
            self.assertNotIn("reasoning_details", request.payload()["messages"][-2])
            self.assertEqual("reasoning" in request.payload(), index == 0)

    def test_frozen_detached_values_and_private_repr(self) -> None:
        self.execute()
        request = self.bind()
        with self.assertRaises(ValueError):
            request.payload_json = "{}"
        payload = request.payload()
        payload["messages"][-2]["reasoning_details"][0]["nested"]["text"].append(
            "mutated"
        )
        payload["messages"][-2]["content"] = "mutated"
        dumped = request.model_dump(mode="json")
        dumped["payload_json"] = "{}"
        self.assertEqual(self.load(), request)
        self.assertEqual(request.payload(), self.expected_payload())
        self.assertNotIn(PARTIAL["content"], repr(request))
        self.assertNotIn("private thought", repr(request))
        self.assertNotIn("Original instructions", repr(request))

    def test_repeat_binds_and_historical_reads_are_byte_pure(self) -> None:
        self.execute()
        request = self.bind()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            self.assertEqual(self.bind(), request)
            self.assertEqual(self.load(), request)
            run_inspection.inspect(self.run_id)
        self.assertEqual(before, self.files())

    def test_bind_and_load_never_dispatch_fit_resolve_or_read_live_library(
        self,
    ) -> None:
        self.execute()
        skills.delete_skill("one")
        targets = (
            (model_context, "model_limits"),
            (model_context, "fit_messages"),
            (openrouter, "query_model"),
            (openrouter, "_query_model_once"),
            (openrouter, "fit_messages"),
            (execution, "execute"),
            (skills, "get_skill"),
            (council_model_budgets, "bind"),
            (run_stage1_inputs, "bind"),
        )
        with ExitStack() as stack:
            for module, name in targets:
                stack.enter_context(
                    patch.object(module, name, side_effect=AssertionError(name))
                )
            request = self.bind()
            self.assertEqual(self.bind(), request)
            self.assertEqual(self.load(), request)
        self.assertEqual(len(self.calls), 1)

    def test_current_configuration_drift_blocks_new_and_repeat_bind_not_history(
        self,
    ) -> None:
        self.execute()
        for already_saved in (False, True):
            if already_saved:
                request = self.bind()
            for key, value in (
                ("COUNCIL_MODELS", ["provider/other"]),
                ("MODEL_BACKEND", "acp"),
                ("NEGATIVE_MODE_ENABLED", True),
                ("OPENROUTER_API_URL", "https://changed.example/chat"),
                ("OPENROUTER_MAX_TOKENS", 17),
                ("OPENROUTER_MAX_CONTINUATIONS", 0),
                ("OPENROUTER_MAX_RETRIES", 99),
                ("SKILLS_ENABLED", False),
            ):
                with self.subTest(saved=already_saved, key=key):
                    before = self.files()
                    with patch.object(config, key, value):
                        with self.assertRaises(ValueError):
                            self.bind()
                        if already_saved:
                            self.assertEqual(self.load(), request)
                            run_inspection.inspect(self.run_id)
                    self.assertEqual(before, self.files())

    def test_live_imported_globals_and_credentials_do_not_change_pinned_inputs(
        self,
    ) -> None:
        self.execute()
        with (
            patch.object(openrouter, "OPENROUTER_API_URL", "https://ignored.example"),
            patch.object(openrouter, "OPENROUTER_MAX_TOKENS", 1),
            patch.object(openrouter, "OPENROUTER_MAX_CONTINUATIONS", 0),
            patch.object(
                model_context, "OPENROUTER_API_URL", "https://ignored.example"
            ),
            patch.object(config, "OPENROUTER_API_KEY", "SYNTHETIC-PRIVATE-CREDENTIAL"),
        ):
            request = self.bind()
            self.assertEqual(request.payload(), self.expected_payload())
            self.assertEqual(self.load(), request)
        self.assertNotIn("SYNTHETIC-PRIVATE-CREDENTIAL", self.path.read_text())

    def test_missing_prerequisites_are_never_created_implicitly(self) -> None:
        for checkpoint in ("admitted", "pre_query_complete", "initial_inputs"):
            with self.subTest(checkpoint=checkpoint):
                if checkpoint == "pre_query_complete":
                    self.complete()
                elif checkpoint == "initial_inputs":
                    self.initial()
                before = self.files()
                with self.assertRaises(
                    (ValueError, run_admission.AdmissionUnavailable)
                ):
                    self.bind()
                self.assertEqual(before, self.files())
                self.assertNotIn("stage1_continuation_inputs", self.saved())
        self.assertEqual(self.calls, [])

    def test_saved_zero_continuations_blocks_binding_even_after_length_result(
        self,
    ) -> None:
        with patch.object(config, "OPENROUTER_MAX_CONTINUATIONS", 0):
            self.execute()
            before = self.files()
            with self.assertRaises(ValueError):
                self.bind()
            self.assertEqual(before, self.files())
            self.assertNotIn("stage1_continuation_inputs", self.saved())

    def test_completed_stop_result_cannot_start_continuation(self) -> None:
        self.execute(response={**copy.deepcopy(PARTIAL), "finish_reason": "stop"})
        before = self.files()
        with self.assertRaises(ValueError):
            self.bind()
        self.assertEqual(before, self.files())

    def test_uncertain_or_live_initial_attempt_needs_completed_explicit_retry(
        self,
    ) -> None:
        self.initial()

        async def exercise() -> None:
            started, release = asyncio.Event(), asyncio.Event()

            async def provider(**kwargs: Any) -> dict[str, Any]:
                started.set()
                await release.wait()
                raise RuntimeError("synthetic failure")

            task = asyncio.create_task(
                execution.execute(
                    self.run_id,
                    self.models[0],
                    provider,
                    provider_key="synthetic-v1",
                    expected_revision=3,
                )
            )
            try:
                await asyncio.wait_for(started.wait(), 5)
                before = self.files()
                with self.assertRaises(ValueError):
                    self.bind()
                self.assertEqual(before, self.files())
            finally:
                release.set()
                with self.assertRaises(execution.ProviderFailure):
                    await task
            before = self.files()
            with self.assertRaises(ValueError):
                self.bind()
            self.assertEqual(before, self.files())

        asyncio.run(exercise())
        retry_token = self.saved()["stage1_execution"]["operations"][0]["attempt"]
        asyncio.run(
            execution.execute(
                self.run_id,
                self.models[0],
                AsyncMock(return_value=copy.deepcopy(PARTIAL)),
                provider_key="synthetic-v1",
                expected_revision=3,
                retry_token=retry_token,
            )
        )
        retained = self.bind()
        op = self.saved()["stage1_execution"]["operations"][0]
        self.assertEqual(op["retry_of"], retry_token)
        self.assertNotEqual(str(retained.initial_attempt), retry_token)
        self.assertEqual(
            retained.initial_operation_sha256,
            run_stage1_inputs._digest(execution.Operation.model_validate(op)),
        )

    def test_bad_revisions_and_unknown_or_unstarted_members_write_nothing(self) -> None:
        self.execute()
        for revision in (0, 2, 4, -1, True, "3", 3.0, None):
            with self.subTest(revision=revision):
                before = self.files()
                with self.assertRaises(ValueError):
                    self.bind(revision=revision)
                self.assertEqual(before, self.files())
        for model in ("provider/unknown", self.models[1]):
            with self.subTest(model=model):
                before = self.files()
                with self.assertRaises(ValueError):
                    self.bind(model)
                self.assertEqual(before, self.files())

    def test_unknown_runs_and_unsaved_loads_never_create_state(self) -> None:
        unknown = str(uuid4())
        before = self.files()
        for call in (
            lambda: continuation.bind(unknown, self.models[0], expected_revision=0),
            lambda: continuation.load(unknown, self.models[0]),
            self.load,
        ):
            with self.assertRaises((ValueError, run_admission.AdmissionUnavailable)):
                call()
        self.assertIsNone(run_inspection.inspect(unknown))
        self.assertEqual(before, self.files())

    def test_exact_full_message_budget_boundary_succeeds(self) -> None:
        bound = self.continuation_bound()
        self.set_budget(bound)
        self.execute()
        request = self.bind()
        self.assertEqual(request.input_token_bound, bound)
        self.assertEqual(request.input_budget, bound)
        self.assertEqual(request.payload(), self.expected_payload())
        self.assertEqual(request.output_budget, self.inputs.requests[0].output_budget)

    def test_one_token_overflow_rejects_without_write_truncation_or_fitting(
        self,
    ) -> None:
        bound = self.continuation_bound()
        self.set_budget(bound - 1)
        self.execute()
        before = self.files()
        with (
            patch.object(
                model_context, "fit_messages", side_effect=AssertionError("fit")
            ),
            patch.object(
                openrouter, "query_model", side_effect=AssertionError("dispatch")
            ),
        ):
            with self.assertRaises(ValueError):
                self.bind()
        self.assertEqual(before, self.files())
        self.assertEqual(
            execution.load_result(self.run_id, self.models[0]).content,
            PARTIAL["content"],
        )
        self.assertNotIn("stage1_continuation_inputs", self.saved())

    def test_inspection_adds_compact_summary_without_changing_initial_state(
        self,
    ) -> None:
        self.execute()
        before = run_inspection.inspect(self.run_id)
        self.assertIsNone(before["stage1_continuation_inputs"])
        request = self.bind()
        after = run_inspection.inspect(self.run_id)
        summary = after["stage1_continuation_inputs"]
        self.assertIsInstance(summary, dict)
        self.assertEqual(summary["version"], 1)
        self.assertIn(self.models[0], json.dumps(summary))
        self.assertNotEqual(before["cursor"], after["cursor"])
        for key in (
            "interaction",
            "stage1_inputs",
            "stage1_execution",
            "request",
            "internal_phase",
            "operation_owned",
        ):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after["internal_phase"], "stage1_needs_continuation")
        for secret in (
            PARTIAL["content"],
            "private thought",
            "private_usage",
            "Original instructions",
            INSTRUCTION,
            "payload_json",
        ):
            self.assertNotIn(secret, json.dumps(summary, ensure_ascii=False))
        summary.clear()
        self.assertEqual(self.load(), request)
        self.assertTrue(
            run_inspection.inspect(self.run_id)["stage1_continuation_inputs"]
        )

    def test_sibling_addition_is_ordered_and_preserves_existing_request(self) -> None:
        self.execute(self.models[1])
        second = self.bind(self.models[1])
        self.execute(self.models[0])
        first = self.bind(self.models[0])
        self.assertEqual(
            self.saved()["stage1_continuation_inputs"]["requests"],
            [first.model_dump(mode="json"), second.model_dump(mode="json")],
        )
        self.assertEqual(self.load(self.models[1]), second)
        self.assertEqual(len(self.calls), 2)

    def test_concurrent_duplicate_binders_write_exactly_once(self) -> None:
        self.execute()
        barrier = threading.Barrier(8)

        def bind() -> Any:
            barrier.wait(timeout=5)
            return self.bind()

        with patch.object(run_admission, "_write", wraps=run_admission._write) as write:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: bind(), range(8)))
        self.assertEqual(write.call_count, 1)
        self.assertTrue(all(value == results[0] for value in results))
        self.assertEqual(len(self.saved()["stage1_continuation_inputs"]["requests"]), 1)

    def test_concurrent_sibling_additions_retain_both_in_initial_order(self) -> None:
        for model in reversed(self.models):
            self.execute(model)
        barrier = threading.Barrier(2)

        def bind(model: str) -> Any:
            barrier.wait(timeout=5)
            return self.bind(model)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bind, reversed(self.models)))
        requests = self.saved()["stage1_continuation_inputs"]["requests"]
        self.assertEqual([request["model"] for request in requests], self.models)
        for result in results:
            self.assertEqual(self.load(result.model), result)

    def test_failed_persistence_does_not_create_or_overwrite_sibling_checkpoint(
        self,
    ) -> None:
        for model in self.models:
            self.execute(model)
        for index, model in enumerate(self.models):
            before = self.files()
            with patch.object(
                run_admission,
                "_write",
                side_effect=OSError("synthetic persistence failure"),
            ):
                with self.assertRaises(OSError):
                    self.bind(model)
            self.assertEqual(before, self.files())
            request = self.bind(model)
            self.assertEqual(self.load(model), request)
            self.assertEqual(
                len(self.saved()["stage1_continuation_inputs"]["requests"]), index + 1
            )

    def test_lost_write_ack_replays_committed_request_without_second_write(
        self,
    ) -> None:
        self.execute()
        original = run_admission._write

        def lost_ack(record: dict[str, Any]) -> None:
            original(record)
            raise OSError("synthetic lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                self.bind()
        retained = self.load()
        before = self.files()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("second write")
        ):
            self.assertEqual(self.bind(), retained)
        self.assertEqual(before, self.files())

    def test_cancellation_blocks_new_and_repeat_bind_but_keeps_historical_value(
        self,
    ) -> None:
        for model in self.models:
            self.execute(model)
        retained = self.bind()
        run_cancellation.request(self.run_id)
        before = self.files()
        for model in self.models:
            with self.assertRaises(run_admission.AdmissionCancelled):
                self.bind(model)
        self.assertEqual(self.load(), retained)
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "cancelled"
        )
        self.assertEqual(before, self.files())

    def test_deletion_erases_continuations_and_never_resurrects(self) -> None:
        self.execute()
        self.bind()
        storage.delete_conversation("cli-" + self.run_id)
        before = self.files()
        for call in (self.bind, self.load, lambda: run_inspection.inspect(self.run_id)):
            with self.assertRaises(run_admission.AdmissionDeleted):
                call()
        self.assertEqual(before, self.files())
        self.assertNotIn("stage1_continuation_inputs", self.path.read_text())
        self.assertNotIn("Private synthetic", self.path.read_text())

    def test_cancel_and_delete_serialize_with_binding_persistence(self) -> None:
        for action in ("cancel", "delete"):
            with self.subTest(action=action):
                fixture = FirstContinuationInputTests()
                fixture.setUp()
                try:
                    fixture.execute()
                    entered, release, mutating = (threading.Event() for _ in range(3))
                    original = run_admission._write

                    def blocked(record: dict[str, Any]) -> None:
                        if (
                            "stage1_continuation_inputs" in record
                            and "cancellation" not in record
                        ):
                            entered.set()
                            if not release.wait(5):
                                raise AssertionError("persistence release timed out")
                        original(record)

                    def mutate() -> Any:
                        mutating.set()
                        if action == "cancel":
                            return run_cancellation.request(fixture.run_id)
                        return storage.delete_conversation("cli-" + fixture.run_id)

                    with patch.object(run_admission, "_write", side_effect=blocked):
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            binding = pool.submit(fixture.bind)
                            try:
                                self.assertTrue(entered.wait(5))
                                mutation = pool.submit(mutate)
                                self.assertTrue(mutating.wait(5))
                                self.assertFalse(mutation.done())
                                with self.assertRaises(TimeoutError):
                                    mutation.result(timeout=0.05)
                            finally:
                                release.set()
                            retained = binding.result(timeout=5)
                            mutation.result(timeout=5)
                    if action == "cancel":
                        self.assertEqual(fixture.load(), retained)
                    else:
                        self.assertNotIn("stage1_continuation_inputs", fixture.saved())
                finally:
                    fixture.doCleanups()

    def test_malformed_saved_continuations_fail_closed_without_writes(self) -> None:
        for model in self.models:
            self.execute(model)
            self.bind(model)
        original = self.saved()
        mutations = [
            lambda r: r.update(stage1_continuation_inputs=None),
            lambda r: r["stage1_continuation_inputs"].update(version=True),
            lambda r: r["stage1_continuation_inputs"].update(version=2),
            lambda r: r["stage1_continuation_inputs"].update(requests=[]),
            lambda r: r["stage1_continuation_inputs"].update(extra="unexpected"),
            lambda r: r["stage1_continuation_inputs"]["requests"].reverse(),
            lambda r: r["stage1_continuation_inputs"]["requests"].append(
                copy.deepcopy(r["stage1_continuation_inputs"]["requests"][0])
            ),
        ]
        for key, value in (
            ("version", True),
            ("turn", 2),
            ("source_revision", 4),
            ("initial_inputs_sha256", "0" * 64),
            ("initial_response_sha256", "0" * 64),
            ("initial_operation_sha256", "0" * 64),
            ("initial_attempt", str(uuid4())),
            ("initial_provider_key", "other-v1"),
            ("model", self.models[1]),
            ("endpoint", "https://changed.example"),
            ("timeout_seconds", 1.0),
            ("timeout_seconds", 120),
            ("input_budget", 1),
            ("output_budget", True),
            ("input_token_bound", 1),
            ("payload_json", "{}"),
            ("extra", "unexpected"),
        ):
            mutations.append(
                lambda r, k=key, v=value: r["stage1_continuation_inputs"]["requests"][
                    0
                ].update({k: v})
            )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                raw = copy.deepcopy(original)
                mutate(raw)
                self.rewrite(raw)
                before = self.files()
                for call in (
                    self.bind,
                    self.load,
                    lambda: run_inspection.inspect(self.run_id),
                ):
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        call()
                self.assertEqual(before, self.files())
        self.rewrite(original)

    def test_changed_or_missing_causal_sources_fail_closed_including_usage(
        self,
    ) -> None:
        self.execute()
        self.bind()
        original = self.saved()
        mutations = [
            lambda r: r.pop("stage1_inputs"),
            lambda r: r.pop("stage1_execution"),
            lambda r: r.pop("council_request_policy"),
            lambda r: r.pop("council_model_budgets"),
            lambda r: r.pop("skill_plan"),
            lambda r: r["stage1_execution"].update(provider_key="changed-v1"),
            lambda r: r["stage1_execution"].update(inputs_sha256="0" * 64),
            lambda r: r["stage1_execution"]["operations"][0].update(
                attempt=str(uuid4())
            ),
            lambda r: r["stage1_execution"]["operations"][0].update(
                retry_of=str(uuid4())
            ),
            lambda r: r["stage1_execution"]["operations"][0].update(
                phase="in_flight", result=None
            ),
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                content="changed partial"
            ),
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                finish_reason="stop"
            ),
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                reasoning_details=[]
            ),
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                usage={"prompt_tokens": 999}
            ),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                raw = copy.deepcopy(original)
                mutate(raw)
                self.rewrite(raw)
                before = self.files()
                for call in (
                    self.bind,
                    self.load,
                    lambda: run_inspection.inspect(self.run_id),
                ):
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        call()
                self.assertEqual(before, self.files())
        self.rewrite(original)

    def test_initializing_receipt_cannot_hide_a_continuation_checkpoint(self) -> None:
        self.execute()
        self.bind()
        record = self.saved()
        retained = record["stage1_continuation_inputs"]
        # Remove every other execution checkpoint so this specifically tests the
        # initializing-record guard for the newly introduced key.
        keep = {
            "version",
            "run_id",
            "conversation_id",
            "phase",
            "created_at",
            "request",
            "assistant_index",
        }
        record = {key: value for key, value in record.items() if key in keep}
        record.update(phase="initializing", stage1_continuation_inputs=retained)
        self.rewrite(record)
        before = self.files()
        for call in (self.bind, self.load, lambda: run_inspection.inspect(self.run_id)):
            with self.assertRaises(run_admission.AdmissionUnavailable):
                call()
        self.assertEqual(before, self.files())

    def test_initial_execute_replays_original_partial_after_continuation_binding(
        self,
    ) -> None:
        partial = self.execute()
        self.bind()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            self.assertEqual(self.execute(), partial)
            self.assertEqual(
                execution.load_result(self.run_id, self.models[0]), partial
            )
        self.assertEqual(before, self.files())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(partial.content, PARTIAL["content"])

    def test_fresh_process_historical_read_needs_no_current_configuration_or_library(
        self,
    ) -> None:
        self.execute()
        retained = self.bind()
        run_cancellation.request(self.run_id)
        skills.delete_skill("one")
        before = self.files()
        code = """
import json, sys
from unittest.mock import patch
from backend import council_selection, council_request_policy, model_context, run_skill_plan, run_stage1_continuation_inputs, skills, storage
storage.DATA_DIR = sys.argv[1]
with patch.object(council_selection, '_current', side_effect=AssertionError('config')), patch.object(council_request_policy, '_current', side_effect=AssertionError('request')), patch.object(run_skill_plan, '_current', side_effect=AssertionError('skills config')), patch.object(model_context, 'model_limits', side_effect=AssertionError('catalog')), patch.object(skills, 'get_skill', side_effect=AssertionError('library')):
    value = run_stage1_continuation_inputs.load(sys.argv[2], sys.argv[3])
print(json.dumps(value.model_dump(mode='json')))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run_id, self.models[0]],
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        self.assertEqual(json.loads(result.stdout), retained.model_dump(mode="json"))
        self.assertEqual(before, self.files())


if __name__ == "__main__":
    unittest.main()
