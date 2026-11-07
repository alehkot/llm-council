"""Read-only next human-boundary preparation from actual saved Stage 1 evidence."""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_run_stage1_continuation_inputs as fixtures

from backend import (
    config,
    run_admission,
    run_cancellation,
    run_stage1_continuation_execution,
    storage,
)
from backend import run_response_review as review
from backend.consultation_contract import PromptSchema
from backend.council import build_label_mapping
from backend.interactive import get_response_review_prompt

STOP = {
    "content": "\nExact Пример ☃  ",
    "finish_reason": "stop",
    "usage": {"nested": [1]},
}


class ResponseReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FirstContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initial()
        self.run = self.fixture.run_id
        self.models = self.fixture.models

    def complete(self):
        for model in self.models:
            self.fixture.execute(model, STOP)

    def test_unstarted_and_partial_roster_have_no_review(self):
        self.assertIsNone(review.prepare(self.run))
        self.fixture.execute(self.models[0], STOP)
        self.assertIsNone(review.prepare(self.run))

    def test_exact_legacy_schema_roster_and_text_without_writes(self):
        self.complete()
        before = self.fixture.files()
        result = review.prepare(self.run.upper())
        responses = [
            {"model": model, "response": STOP["content"]} for model in self.models
        ]
        _, labels = build_label_mapping(responses)
        self.assertEqual(result.label_to_model, labels)
        self.assertEqual(
            result.prompt.model_dump(mode="json"),
            PromptSchema.model_validate(
                get_response_review_prompt(responses, labels).to_dict()
            ).model_dump(mode="json"),
        )
        self.assertEqual([r.model for r in result.results], self.models)
        self.assertTrue(all(r.content == STOP["content"] for r in result.results))
        self.assertEqual(result.source_revision, 3)
        self.assertEqual(result, review.prepare(self.run))
        self.assertEqual(before, self.fixture.files())
        self.assertNotIn(STOP["content"], repr(result))
        self.assertNotIn(STOP["content"], result.prompt.model_dump_json())

    def test_detached_nested_answers_and_schema(self):
        self.complete()
        result = review.prepare(self.run)
        result.results[0].chunks[0].response.usage["nested"].append(2)
        result.prompt.fields[0].options.clear()
        result.label_to_model.clear()
        fresh = review.prepare(self.run)
        self.assertEqual(fresh.results[0].chunks[0].response.usage, {"nested": [1]})
        self.assertEqual(len(fresh.prompt.fields[0].options), len(self.models))
        self.assertEqual(len(fresh.label_to_model), len(self.models))
        self.assertEqual(result.source_sha256, fresh.source_sha256)

    def test_continued_member_preserves_chunk_provenance(self):
        self.fixture.execute(self.models[0])
        self.fixture.bind(self.models[0])
        self.fixture.execute(self.models[1], STOP)
        self.assertIsNone(review.prepare(self.run))

        async def provider(**kwargs):
            return copy.deepcopy(STOP)

        asyncio.run(
            run_stage1_continuation_execution.execute(
                self.run,
                self.models[0],
                provider,
                turn=1,
                provider_key="synthetic-review",
                expected_revision=3,
            )
        )
        result = review.prepare(self.run)
        self.assertEqual(
            result.results[0].content, fixtures.PARTIAL["content"] + STOP["content"]
        )
        self.assertEqual([c.turn for c in result.results[0].chunks], [0, 1])
        self.assertEqual(result.results[1].content, STOP["content"])

    def test_unknown_deleted_and_corrupt_fail_closed(self):
        with self.assertRaises(run_admission.AdmissionUnavailable):
            review.prepare(str(uuid4()))
        self.complete()
        raw = json.loads(self.fixture.path.read_text())
        raw["stage1_execution"]["inputs_sha256"] = "0" * 64
        self.fixture.path.write_text(json.dumps(raw))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            review.prepare(self.run)

    def test_deletion_never_returns_historical_review(self):
        self.complete()
        storage.delete_conversation("cli-" + self.run)
        with self.assertRaises(run_admission.AdmissionDeleted):
            review.prepare(self.run)

    def test_config_changed_does_not_change_preparation(self):
        self.complete()
        expected = review.prepare(self.run)
        before = self.fixture.files()
        with (
            patch.object(config, "COUNCIL_MODELS", ["different/model"]),
            patch.object(config, "OPENROUTER_API_URL", "https://different.invalid"),
        ):
            actual = review.prepare(self.run)
        self.assertEqual(actual, expected)
        self.assertEqual(before, self.fixture.files())

    def test_digest_binds_exact_answer_and_attempt_identity(self):
        self.complete()
        first = review.prepare(self.run)
        path = self.fixture.path
        record = json.loads(path.read_text())
        op = record["stage1_execution"]["operations"][0]
        op["result"]["content"] += " "
        path.write_text(json.dumps(record))
        second = review.prepare(self.run)
        self.assertNotEqual(first.source_sha256, second.source_sha256)
        op["attempt"] = str(uuid4())
        path.write_text(json.dumps(record))
        third = review.prepare(self.run)
        self.assertNotEqual(second.source_sha256, third.source_sha256)

    def test_cancelled_run_cannot_prepare_a_new_review(self):
        self.complete()
        run_cancellation.request(self.run)
        before = self.fixture.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            review.prepare(self.run)
        self.assertEqual(before, self.fixture.files())

    def test_uncertain_member_never_becomes_reviewable(self):
        self.fixture.execute(self.models[1], STOP)
        from backend import run_stage1_execution

        async def fail(**kwargs):
            raise RuntimeError("synthetic uncertain")

        with self.assertRaises(run_stage1_execution.ProviderFailure):
            asyncio.run(
                run_stage1_execution.execute(
                    self.run,
                    self.models[0],
                    fail,
                    provider_key="synthetic-v1",
                    expected_revision=3,
                )
            )
        before = self.fixture.files()
        self.assertIsNone(review.prepare(self.run))
        self.assertEqual(before, self.fixture.files())
