"""Offline read-only complete Stage 1 member projections, not stage execution."""

import asyncio
import copy
import unittest
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import test_run_stage1_continuation_inputs as fixtures

from backend import (
    config,
    run_admission,
    run_cancellation,
    run_inspection,
    run_stage1_continuation_execution,
    run_stage1_execution,
    run_stage1_results,
    storage,
)

STOP = {
    "content": "\nFinished Пример ☃  ",
    "finish_reason": "stop",
    "reasoning_details": [{"type": "synthetic", "nested": [1, {"x": True}]}],
    "usage": {"completion_tokens": 7, "nested": [False, None]},
}


class Stage1ResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.FirstContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initial()
        self.run_id = self.fixture.run_id
        self.model = self.fixture.models[0]
        self.path = self.fixture.path
        self.calls = 0

    def load(self, model: str | None = None) -> Any:
        return run_stage1_results.load_complete_result(
            self.run_id, self.model if model is None else model
        )

    def continue_once(self, response: Any = None, **kwargs: Any) -> Any:
        async def provider(**unused: Any) -> Any:
            self.calls += 1
            if isinstance(response, BaseException):
                raise response
            return copy.deepcopy(STOP if response is None else response)

        return asyncio.run(
            run_stage1_continuation_execution.execute(
                self.run_id,
                self.model,
                provider,
                turn=1,
                provider_key="synthetic-continuation-v1",
                expected_revision=3,
                **kwargs,
            )
        )

    def partial(self) -> None:
        self.fixture.execute()
        self.fixture.bind()

    def test_unstarted_has_no_complete_result(self) -> None:
        self.assertIsNone(self.load())

    def test_initial_stop_preserves_exact_text_and_provenance(self) -> None:
        self.fixture.execute(response=STOP)
        before = self.fixture.files()
        result = self.load()
        self.assertEqual(result.content, STOP["content"])
        self.assertEqual(result.model, self.model)
        self.assertEqual(result.source_revision, 3)
        self.assertEqual(len(result.chunks), 1)
        chunk = result.chunks[0]
        self.assertEqual(chunk.turn, 0)
        self.assertEqual(chunk.provider_key, "synthetic-v1")
        self.assertEqual(chunk.response.model_dump(), STOP)
        saved = self.fixture.saved()["stage1_execution"]
        self.assertEqual(str(chunk.attempt), saved["operations"][0]["attempt"])
        self.assertEqual(chunk.inputs_sha256, saved["inputs_sha256"])
        self.assertEqual(before, self.fixture.files())

    def test_length_without_preparation_is_not_complete(self) -> None:
        self.fixture.execute()
        self.assertIsNone(self.load())

    def test_prepared_continuation_is_not_complete(self) -> None:
        self.partial()
        self.assertIsNone(self.load())

    def test_complete_concatenates_exactly_without_merging_metadata(self) -> None:
        self.partial()
        self.continue_once()
        before = self.fixture.files()
        snapshot = run_inspection.inspect(self.run_id)
        result = self.load()
        self.assertEqual(result.content, fixtures.PARTIAL["content"] + STOP["content"])
        self.assertEqual([c.turn for c in result.chunks], [0, 1])
        self.assertEqual(result.chunks[0].response.model_dump(), fixtures.PARTIAL)
        self.assertEqual(result.chunks[1].response.model_dump(), STOP)
        self.assertEqual(result.chunks[1].provider_key, "synthetic-continuation-v1")
        op = self.fixture.saved()["stage1_continuation_execution"]["operations"][0]
        self.assertEqual(str(result.chunks[1].attempt), op["attempt"])
        self.assertEqual(result.chunks[1].inputs_sha256, op["inputs_sha256"])
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(snapshot, run_inspection.inspect(self.run_id))
        self.assertEqual(self.calls, 1)

    def test_length_continuation_remains_incomplete(self) -> None:
        self.partial()
        self.continue_once({**STOP, "finish_reason": "length"})
        self.assertIsNone(self.load())
        self.assertEqual(self.calls, 1)

    def test_uncertain_continuation_is_not_complete(self) -> None:
        self.partial()
        with self.assertRaises(run_stage1_continuation_execution.ProviderFailure):
            self.continue_once(RuntimeError("synthetic interruption"))
        self.assertIsNone(self.load())
        self.assertEqual(self.calls, 1)

    def test_uncertain_initial_is_not_complete(self) -> None:
        self.fixture.execute(response=STOP)
        record = self.fixture.saved()
        record["stage1_execution"]["operations"][0].update(
            phase="in_flight", result=None
        )
        self.fixture.rewrite(record)
        self.assertIsNone(self.load())

    def test_retry_provenance_is_preserved(self) -> None:
        self.partial()
        with self.assertRaises(run_stage1_continuation_execution.ProviderFailure):
            self.continue_once(RuntimeError("synthetic interruption"))
        token = self.fixture.saved()["stage1_continuation_execution"]["operations"][0][
            "attempt"
        ]
        self.continue_once(retry_token=token)
        self.assertEqual(str(self.load().chunks[1].retry_of), token)
        self.assertEqual(self.calls, 2)

    def test_detached_metadata_cannot_mutate_future_reads(self) -> None:
        self.partial()
        self.continue_once()
        before = self.fixture.files()
        result = self.load()
        result.chunks[0].response.reasoning_details[0]["nested"]["text"].append(
            "changed"
        )
        result.chunks[1].response.usage["nested"].append("changed")
        self.assertEqual(self.load().chunks[0].response.model_dump(), fixtures.PARTIAL)
        self.assertEqual(self.load().chunks[1].response.model_dump(), STOP)
        self.assertEqual(before, self.fixture.files())

    def test_historical_reads_ignore_live_configuration(self) -> None:
        self.partial()
        self.continue_once()
        expected = self.load()
        with patch.object(config, "COUNCIL_MODELS", ["different/model"]):
            self.assertEqual(self.load(), expected)

    def test_cancellation_preserves_historical_result_not_run_success(self) -> None:
        self.partial()
        self.continue_once()
        run_cancellation.request(self.run_id)
        before = self.fixture.files()
        self.assertIsNotNone(self.load())
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "cancelled"
        )
        self.assertEqual(before, self.fixture.files())

    def test_deleted_run_does_not_return_retained_result(self) -> None:
        self.partial()
        self.continue_once()
        record = self.fixture.saved()
        storage.delete_conversation(record["conversation_id"])
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.load()

    def test_unknown_member_is_rejected(self) -> None:
        with self.assertRaises(run_stage1_execution.ExecutionConflict):
            self.load("not/member")

    def test_unknown_run_is_rejected(self) -> None:
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_stage1_results.load_complete_result(str(uuid4()), self.model)

    def test_siblings_do_not_supply_missing_member_chunk(self) -> None:
        self.partial()
        self.continue_once()
        self.fixture.execute(self.fixture.models[1])
        self.assertIsNone(self.load(self.fixture.models[1]))
        self.assertIsNotNone(self.load())

    def test_changed_initial_invalidates_continuation_causality(self) -> None:
        self.partial()
        self.continue_once()
        record = self.fixture.saved()
        record["stage1_execution"]["operations"][0]["result"]["content"] += "changed"
        self.fixture.rewrite(record)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.load()

    def test_changed_continuation_request_digest_fails_closed(self) -> None:
        self.partial()
        self.continue_once()
        record = self.fixture.saved()
        record["stage1_continuation_execution"]["operations"][0]["inputs_sha256"] = (
            "0" * 64
        )
        self.fixture.rewrite(record)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.load()

    def test_result_repr_does_not_expose_content_or_metadata(self) -> None:
        self.partial()
        self.continue_once()
        result = self.load()
        self.assertNotIn("Пример", repr(result))
        self.assertNotIn("private thought", repr(result))
        self.assertNotIn("Пример", repr(result.chunks[0]))

    def test_read_does_not_write_dispatch_or_settle_cancellation(self) -> None:
        self.partial()
        self.continue_once()
        record = self.fixture.saved()
        record["cancellation"] = {
            "phase": "cancelling",
            "requested_at": "2026-11-06T20:00:00",
            "completed_at": None,
        }
        self.fixture.rewrite(record)
        before = self.fixture.files()
        with (
            patch.object(run_admission, "_write", side_effect=AssertionError("write")),
            patch.object(
                run_stage1_execution, "execute", side_effect=AssertionError("dispatch")
            ),
            patch.object(
                run_stage1_continuation_execution,
                "execute",
                side_effect=AssertionError("dispatch"),
            ),
        ):
            self.assertIsNotNone(self.load())
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "cancelling"
        )

    def test_corrupt_sibling_receipt_fails_closed(self) -> None:
        self.fixture.execute(response=STOP)
        self.fixture.execute(self.fixture.models[1], response=STOP)
        record = self.fixture.saved()
        record["stage1_execution"]["operations"][1]["result"]["finish_reason"] = (
            "unknown"
        )
        self.fixture.rewrite(record)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.load()

    def test_aggregate_may_exceed_single_chunk_cap(self) -> None:
        alternate = fixtures.FirstContinuationInputTests()
        alternate.setUp()
        self.addCleanup(alternate.doCleanups)
        with patch.dict(fixtures.input_fixtures.LIMITS, context_length=5000000):
            alternate.initial()
        self.fixture = alternate
        self.run_id, self.path = alternate.run_id, alternate.path
        first = {**fixtures.PARTIAL, "content": "a" * 1100000}
        second = {**STOP, "content": "b" * 1100000}
        alternate.execute(response=first)
        alternate.bind()
        self.continue_once(second)
        result = self.load()
        self.assertEqual(result.content, first["content"] + second["content"])
        self.assertGreater(len(result.content), run_stage1_execution.MAX_RESULT_BYTES)
        self.assertEqual(len(result.chunks), 2)

    def test_reloaded_result_needs_no_live_ownership(self) -> None:
        self.partial()
        self.continue_once()
        before = self.fixture.files()
        with (
            patch.object(run_stage1_execution, "_active", set()),
            patch.object(run_stage1_continuation_execution, "_active", set()),
        ):
            self.assertEqual(
                self.load().content, fixtures.PARTIAL["content"] + STOP["content"]
            )
        self.assertEqual(before, self.fixture.files())


if __name__ == "__main__":
    unittest.main()
