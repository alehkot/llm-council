"""Offline exact first-reviewer continuation preparation and durable ancestry."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import test_run_stage2_inputs as fixtures

from backend import (
    config,
    model_context,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_stage1_inputs,
    run_stage2_execution,
    run_stage2_results,
    storage,
)
from backend import run_stage2_continuation_inputs as continuation

PARTIAL = {
    "content": "  Synthetic review Пример\nFINAL RANKING:\n1. Response A\n",
    "finish_reason": "length",
    "reasoning_details": [{"type": "synthetic", "nested": {"text": ["reason"]}}],
    "usage": {"completion_tokens": 7},
}


class Stage2ContinuationInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.complete()
        self.inputs = self.fixture.bind()
        self.run = self.fixture.run
        self.models = [r.model for r in self.inputs.requests]
        self.path = run_admission._path(self.run)

    def execute(
        self, model: str | None = None, finish: str = "length"
    ) -> run_stage2_execution.MemberResponse:
        async def provider(**kwargs: Any) -> dict[str, Any]:
            return {**copy.deepcopy(PARTIAL), "finish_reason": finish}

        return asyncio.run(
            run_stage2_execution.execute(
                self.run,
                model or self.models[0],
                provider,
                provider_key="synthetic-review-v1",
                expected_revision=6,
            )
        )

    def bind(
        self, model: str | None = None, revision: int = 6
    ) -> continuation.FirstContinuationInput:
        return continuation.bind(
            self.run, model or self.models[0], expected_revision=revision
        )

    def rewrite(self, change: Callable[[dict[str, Any]], None]) -> None:
        record = json.loads(self.path.read_text())
        change(record)
        self.path.write_text(json.dumps(record))

    def test_exact_payload_preserves_partial_reasoning_and_original_sources(
        self,
    ) -> None:
        self.execute()
        before = json.loads(self.path.read_text())
        saved = self.bind()
        after = json.loads(self.path.read_text())
        self.assertEqual(
            before,
            {k: v for k, v in after.items() if k != "stage2_continuation_inputs"},
        )
        request = self.inputs.requests[0]
        expected = request.payload()
        expected["messages"].extend(
            [
                {
                    "role": "assistant",
                    "content": PARTIAL["content"],
                    "reasoning_details": PARTIAL["reasoning_details"],
                },
                {"role": "user", "content": continuation.CONTINUATION_INSTRUCTION},
            ]
        )
        self.assertEqual(saved.payload(), expected)
        operation = run_stage2_execution.Operation.model_validate(
            before["stage2_execution"]["operations"][0]
        )
        self.assertEqual(saved.initial_attempt, operation.attempt)
        self.assertEqual(
            saved.initial_inputs_sha256, run_stage1_inputs._digest(self.inputs)
        )
        self.assertEqual(
            saved.initial_operation_sha256, run_stage1_inputs._digest(operation)
        )
        self.assertEqual(
            saved.initial_response_sha256, run_stage1_inputs._digest(operation.result)
        )
        self.assertEqual(saved.initial_provider_key, "synthetic-review-v1")
        self.assertEqual(
            saved.input_token_bound, model_context.token_bound(expected["messages"])
        )
        self.assertEqual(saved.input_budget, request.input_budget)
        self.assertEqual(saved.output_budget, request.output_budget)
        self.assertEqual(saved.source_revision, 6)
        self.assertEqual(saved.turn, 1)
        self.assertEqual(continuation.load(self.run, self.models[0]), saved)
        self.assertIsNone(run_stage2_results.load_complete_results(self.run))

    def test_inspection_exposes_identity_not_private_payload_and_does_not_advance(
        self,
    ) -> None:
        self.execute()
        before = run_inspection.inspect(self.run)
        saved = self.bind()
        after = run_inspection.inspect(self.run)
        self.assertEqual(after["internal_phase"], before["internal_phase"])
        self.assertEqual(after["interaction"], before["interaction"])
        self.assertEqual(after["stage2_execution"], before["stage2_execution"])
        self.assertNotEqual(after["cursor"], before["cursor"])
        summary = after["stage2_continuation_inputs"]
        self.assertEqual(
            summary["requests"][0]["sha256"], run_stage1_inputs._digest(saved)
        )
        self.assertNotIn(PARTIAL["content"], json.dumps(summary))
        self.assertNotIn(PARTIAL["content"], repr(saved))

    def test_repeat_and_concurrent_bind_are_idempotent(self) -> None:
        self.execute()
        saved = self.bind()
        before = self.path.read_bytes()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.bind(), range(16)))
        self.assertEqual(results, [saved] * 16)
        self.assertEqual(before, self.path.read_bytes())

    def test_two_reviewers_pin_in_original_order_without_losing_other_request(
        self,
    ) -> None:
        for model in reversed(self.models):
            self.execute(model)
            self.bind(model)
        raw = json.loads(self.path.read_text())["stage2_continuation_inputs"]
        self.assertEqual([r["model"] for r in raw["requests"]], self.models)

    def test_payload_mutation_does_not_mutate_saved_value(self) -> None:
        self.execute()
        saved = self.bind()
        modified = saved.payload()
        modified["messages"][-2]["reasoning_details"][0]["nested"]["text"].append(
            "changed"
        )
        self.assertEqual(saved, continuation.load(self.run, self.models[0]))
        self.assertNotIn("changed", saved.payload_json)

    def test_missing_and_stopped_results_cannot_be_continued(self) -> None:
        with self.assertRaises(continuation.ContinuationInputConflict):
            self.bind()
        self.execute(finish="stop")
        with self.assertRaises(continuation.ContinuationInputConflict):
            self.bind()
        with self.assertRaises(continuation.ContinuationInputConflict):
            self.bind("unknown/model")

    def test_uncertain_result_cannot_be_continued(self) -> None:
        async def fail(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("synthetic")

        with self.assertRaises(run_stage2_execution.ProviderFailure):
            asyncio.run(
                run_stage2_execution.execute(
                    self.run,
                    self.models[0],
                    fail,
                    provider_key="synthetic-review-v1",
                    expected_revision=6,
                )
            )
        with self.assertRaises(continuation.ContinuationInputConflict):
            self.bind()

    def test_stale_and_invalid_revision_rejected_without_write(self) -> None:
        self.execute()
        before = self.path.read_bytes()
        with self.assertRaises(run_interaction.PromptConflict):
            self.bind(revision=5)
        for revision in (True, -1, 6.0):
            with self.assertRaises(ValueError):
                self.bind(revision=revision)
        self.assertEqual(before, self.path.read_bytes())

    def test_budget_overflow_rejected_without_trimming_or_writing(self) -> None:
        self.execute()
        before = self.path.read_bytes()
        real = model_context.token_bound

        def bound(messages: list[dict[str, Any]]) -> int:
            if (
                len(messages) > 1
                and messages[-1].get("content") == continuation.CONTINUATION_INSTRUCTION
            ):
                return self.inputs.requests[0].input_budget + 1
            return real(messages)

        with patch.object(model_context, "token_bound", side_effect=bound):
            with self.assertRaises(continuation.ContinuationInputConflict):
                self.bind()
        self.assertEqual(before, self.path.read_bytes())

    def test_disabled_saved_continuation_policy_rejected(self) -> None:
        # Derivation must honor the immutable policy, not current-only settings.
        self.execute()
        from backend import council_request_policy

        original = council_request_policy.validate_saved

        def disabled(
            record: dict[str, Any], interaction: run_interaction.InteractionState
        ) -> Any:
            return original(record, interaction).model_copy(
                update={"max_continuations": 0}
            )

        record = json.loads(self.path.read_text())
        interaction = run_interaction._validate_record(record)
        execution = run_stage2_execution.validate_saved(record, self.inputs)
        with patch.object(
            council_request_policy, "validate_saved", side_effect=disabled
        ):
            with self.assertRaises(continuation.ContinuationInputConflict):
                continuation._derive(
                    record, interaction, self.inputs, execution, self.models[0]
                )

    def test_current_config_drift_rejects_bind_but_historical_load_survives(
        self,
    ) -> None:
        self.execute()
        saved = self.bind()
        with patch.object(config, "OPENROUTER_MAX_CONTINUATIONS", 0):
            self.assertEqual(continuation.load(self.run, self.models[0]), saved)
            with self.assertRaises(run_stage1_inputs.Stage1InputConflict):
                self.bind()

    def test_cancellation_rejects_bind_and_retains_historical_input(self) -> None:
        self.execute()
        saved = self.bind()
        run_cancellation.request(self.run)
        self.assertEqual(continuation.load(self.run, self.models[0]), saved)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()

    def test_deletion_removes_saved_input(self) -> None:
        self.execute()
        self.bind()
        storage.delete_conversation("cli-" + self.run)
        with self.assertRaises(
            (run_admission.AdmissionDeleted, continuation.ContinuationInputConflict)
        ):
            continuation.load(self.run, self.models[0])

    def test_corrupt_saved_input_or_source_fails_inspection_closed(self) -> None:
        self.execute()
        self.bind()
        original = self.path.read_bytes()
        for field, value in (
            ("turn", 2),
            ("initial_provider_key", "changed"),
            ("initial_inputs_sha256", "0" * 64),
            ("input_budget", 1),
            ("payload_json", "{}"),
        ):
            with self.subTest(field=field):
                self.path.write_bytes(original)
                self.rewrite(
                    lambda r: r["stage2_continuation_inputs"]["requests"][0].update(
                        {field: value}
                    )
                )
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    run_inspection.inspect(self.run)
        self.path.write_bytes(original)
        self.rewrite(
            lambda r: r["stage2_execution"]["operations"][0]["result"].update(
                content="changed"
            )
        )
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_restart_historical_read_avoids_current_config_and_library(self) -> None:
        self.execute()
        saved = self.bind()
        run_cancellation.request(self.run)
        before = self.path.read_bytes()
        code = """
import json, sys
from unittest.mock import patch
from backend import council_selection, council_request_policy, model_context, run_skill_plan, run_stage2_continuation_inputs, skills, storage
storage.DATA_DIR = sys.argv[1]
with patch.object(council_selection, '_current', side_effect=AssertionError('config')), patch.object(council_request_policy, '_current', side_effect=AssertionError('request')), patch.object(run_skill_plan, '_current', side_effect=AssertionError('skills config')), patch.object(model_context, 'model_limits', side_effect=AssertionError('catalog')), patch.object(skills, 'get_skill', side_effect=AssertionError('library')):
    value = run_stage2_continuation_inputs.load(sys.argv[2], sys.argv[3])
print(json.dumps(value.model_dump(mode='json')))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run, self.models[0]],
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        self.assertEqual(json.loads(result.stdout), saved.model_dump(mode="json"))
        self.assertEqual(before, self.path.read_bytes())

    def test_initializing_receipt_cannot_hide_continuation_checkpoint(self) -> None:
        self.execute()
        self.bind()
        record = json.loads(self.path.read_text())
        retained = record["stage2_continuation_inputs"]
        keep = {
            "version",
            "run_id",
            "conversation_id",
            "phase",
            "created_at",
            "request",
            "assistant_index",
        }
        record = {k: v for k, v in record.items() if k in keep}
        record.update(phase="initializing", stage2_continuation_inputs=retained)
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        for call in (
            self.bind,
            lambda: continuation.load(self.run, self.models[0]),
            lambda: run_inspection.inspect(self.run),
        ):
            with self.assertRaises(run_admission.AdmissionUnavailable):
                call()
        self.assertEqual(before, self.path.read_bytes())

    def test_failed_persistence_preserves_original_and_sibling_requests(self) -> None:
        for model in self.models:
            self.execute(model)
        for model in self.models:
            before = self.path.read_bytes()
            with patch.object(
                run_admission, "_write", side_effect=OSError("synthetic")
            ):
                with self.assertRaises(OSError):
                    self.bind(model)
            self.assertEqual(before, self.path.read_bytes())
            self.bind(model)

    def test_lost_ack_reuses_saved_value_without_second_write(self) -> None:
        self.execute()
        original = run_admission._write

        def lost_ack(record: dict[str, Any]) -> None:
            original(record)
            raise OSError("synthetic lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                self.bind()
        saved = continuation.load(self.run, self.models[0])
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("second write")
        ):
            self.assertEqual(self.bind(), saved)
