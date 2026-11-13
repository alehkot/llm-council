"""Offline complete ballot projection from genuine durable reviewer receipts."""

import copy
import json
import unittest
from typing import Any
from unittest.mock import patch

import test_run_stage1_inputs as initial_fixtures
import test_run_stage2_inputs as fixtures

from backend import config, council, run_admission, run_cancellation, storage
from backend import run_stage2_execution as execution
from backend import run_stage2_results as results


class Stage2ResultFixtureMixin:
    pairwise = False

    def setUp(self) -> None:
        original = initial_fixtures.Stage1InputTests.setUp

        def setup_with_method(fixture: Any) -> None:
            original(fixture)
            setting = patch.object(config, "PAIRWISE_RANKING_ENABLED", self.pairwise)
            setting.start()
            fixture.addCleanup(setting.stop)

        patched = patch.object(
            initial_fixtures.Stage1InputTests, "setUp", setup_with_method
        )
        patched.start()
        self.addCleanup(patched.stop)
        self.fixture = fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.complete()
        self.inputs = self.fixture.bind()
        self.run = self.fixture.run
        self.path = run_admission._path(self.run)
        self.models = [r.model for r in self.inputs.requests]
        self.labels = list(self.inputs.label_to_model)

    def ballot(self, labels: list[str] | None = None) -> str:
        return "FINAL RANKING:\n" + "\n".join(
            f"{i}. {label}" for i, label in enumerate(labels or self.labels, 1)
        )

    async def execute(self, model: str, content: str, finish: str = "stop") -> None:
        async def provider(**kwargs: Any) -> dict[str, Any]:
            return {
                "content": content,
                "finish_reason": finish,
                "usage": {"nested": [1]},
                "reasoning_details": [{"text": "synthetic"}],
            }

        await execution.execute(
            self.run,
            model,
            provider,
            provider_key="synthetic-v1",
            expected_revision=6,
        )

    async def complete(self, ballots: list[str] | None = None) -> None:
        for model, content in zip(
            self.models, ballots or [self.ballot()] * len(self.models), strict=True
        ):
            await self.execute(model, content)


class Stage2ResultFixture(Stage2ResultFixtureMixin, unittest.TestCase):
    """Synchronous setup/cleanup owner for tests composing async helpers."""


class Stage2ResultTests(Stage2ResultFixtureMixin, unittest.IsolatedAsyncioTestCase):
    async def test_complete_aggregation_matches_saved_algorithm_and_provenance(
        self,
    ) -> None:
        await self.complete()
        before = self.path.read_bytes()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            result = results.load_complete_results(self.run)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result.ranking_method, self.inputs.ranking_method)
        self.assertEqual(
            result.ranking_method, "pairwise" if self.pairwise else "average_rank"
        )
        self.assertEqual(result.label_to_model, self.inputs.label_to_model)
        self.assertEqual([r.model for r in result.reviewers], self.models)
        saved = json.loads(before)["stage2_execution"]
        self.assertEqual(result.inputs_sha256, saved["inputs_sha256"])
        for reviewer, operation in zip(
            result.reviewers, saved["operations"], strict=True
        ):
            self.assertEqual(str(reviewer.attempt), operation["attempt"])
            self.assertEqual(reviewer.retry_of, None)
            self.assertEqual(reviewer.provider_key, "synthetic-v1")
            self.assertEqual(reviewer.parsed_ranking, tuple(self.labels))
            self.assertEqual(reviewer.response.content, self.ballot())
        algorithm = (
            council.calculate_pairwise_rankings
            if result.ranking_method == "pairwise"
            else council.calculate_aggregate_rankings
        )
        expected, invalid = algorithm(
            [{"model": m, "parsed_ranking": self.labels} for m in self.models],
            self.inputs.label_to_model,
        )
        self.assertEqual(
            [r.model_dump(exclude_none=True) for r in result.aggregate], expected
        )
        self.assertEqual(result.invalid_rankings, tuple(invalid))

    async def test_unstarted_partial_and_length_never_form_quorum(self) -> None:
        self.assertIsNone(results.load_complete_results(self.run))
        for model in self.models[:-1]:
            await self.execute(model, self.ballot())
            self.assertIsNone(results.load_complete_results(self.run))
        await self.execute(self.models[-1], self.ballot(), "length")
        self.assertIsNone(results.load_complete_results(self.run))

    async def test_uncertain_reviewer_does_not_form_quorum(self) -> None:
        for model in self.models[:-1]:
            await self.execute(model, self.ballot())

        async def fail(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("synthetic failure")

        with self.assertRaises(execution.ProviderFailure):
            await execution.execute(
                self.run,
                self.models[-1],
                fail,
                provider_key="synthetic-v1",
                expected_revision=6,
            )
        self.assertIsNone(results.load_complete_results(self.run))

    async def test_invalid_ballot_retained_but_excluded(self) -> None:
        bad = self.ballot([*self.labels, "Response UNKNOWN"])
        await self.complete([bad, *[self.ballot()] * (len(self.models) - 1)])
        result = results.load_complete_results(self.run)
        self.assertEqual(result.reviewers[0].response.content, bad)
        self.assertEqual(len(result.invalid_rankings), 1)
        self.assertEqual(result.invalid_rankings[0].model, self.models[0])
        self.assertIn("unknown label", result.invalid_rankings[0].reason)
        self.assertTrue(
            all(r.rankings_count == len(self.models) - 1 for r in result.aggregate)
        )

    async def test_duplicate_and_missing_labels_do_not_vote(self) -> None:
        await self.complete(
            [
                self.ballot([self.labels[0], self.labels[0]]),
                self.ballot([self.labels[0]]),
            ]
        )
        result = results.load_complete_results(self.run)
        self.assertEqual(result.aggregate, ())
        self.assertIn("duplicate", result.invalid_rankings[0].reason)
        self.assertIn("missing", result.invalid_rankings[1].reason)

    async def test_all_invalid_returns_no_winner(self) -> None:
        await self.complete(["no ballot"] * len(self.models))
        result = results.load_complete_results(self.run)
        self.assertEqual(result.aggregate, ())
        self.assertEqual(len(result.invalid_rankings), len(self.models))

    async def test_detached_results_survive_config_drift_and_cancellation(self) -> None:
        await self.complete()
        expected = results.load_complete_results(self.run)
        changed = results.load_complete_results(self.run)
        changed.label_to_model.clear()
        changed.reviewers[0].response.usage["nested"].append(42)
        with patch.object(
            config,
            "PAIRWISE_RANKING_ENABLED",
            not self.inputs.ranking_method == "pairwise",
        ):
            run_cancellation.request(self.run)
            self.assertEqual(results.load_complete_results(self.run), expected)

    async def test_corrupt_causal_receipts_fail_closed(self) -> None:
        await self.complete()
        original = json.loads(self.path.read_text())
        for kind in ("stage2_inputs", "stage2_execution"):
            with self.subTest(kind=kind):
                record = copy.deepcopy(original)
                record[kind]["source_revision"] += 1
                self.path.write_text(json.dumps(record))
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    results.load_complete_results(self.run)
        self.path.write_text(json.dumps(original))

    async def test_deleted_run_not_projected(self) -> None:
        await self.complete()
        run_admission.tombstone_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            results.load_complete_results(self.run)


class PairwiseStage2ResultTests(Stage2ResultTests):
    pairwise = True


class ExcludedCandidateTests(unittest.TestCase):
    def test_excluded_candidate_still_reviews_the_survivor(self) -> None:
        fixture = fixtures.Stage2InputTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.complete({"exclude_responses": ["provider/first"]})
        inputs = fixture.bind()
        self.assertEqual(inputs.label_to_model, {"Response A": "provider/second"})
        self.assertEqual(len(inputs.requests), 2)

        async def complete() -> None:
            async def provider(**kwargs: Any) -> dict[str, Any]:
                return {
                    "content": "FINAL RANKING:\n1. Response A",
                    "finish_reason": "stop",
                }

            for request in inputs.requests:
                await execution.execute(
                    fixture.run,
                    request.model,
                    provider,
                    provider_key="synthetic-v1",
                    expected_revision=6,
                )

        import asyncio

        asyncio.run(complete())
        result = results.load_complete_results(fixture.run)
        self.assertEqual(len(result.reviewers), 2)
        self.assertEqual(len(result.aggregate), 1)
        self.assertEqual(result.aggregate[0].model, "provider/second")
        self.assertEqual(result.aggregate[0].rankings_count, 2)


class RankingFixtureIsolationTests(unittest.TestCase):
    def test_pairwise_fixture_restores_original_setting(self) -> None:
        for original in (False, True):
            with (
                self.subTest(original=original),
                patch.object(config, "PAIRWISE_RANKING_ENABLED", original),
            ):
                original_data_dir = storage.DATA_DIR
                fixture = Stage2ResultFixture()
                fixture.pairwise = True
                try:
                    fixture.setUp()
                    self.assertEqual(fixture.inputs.ranking_method, "pairwise")
                finally:
                    self.assertTrue(fixture.doCleanups())
                self.assertEqual(config.PAIRWISE_RANKING_ENABLED, original)
                self.assertEqual(storage.DATA_DIR, original_data_dir)
                self.assertFalse(fixture.path.exists())
