"""Complete ranking projection from exact stopped continuation chunks."""

import asyncio
import copy
import json
import unittest
from typing import Any
from unittest.mock import patch

import test_run_stage2_results as fixtures

from backend import config, run_admission, run_cancellation, run_inspection
from backend import run_stage2_continuation_execution as execution
from backend import run_stage2_continuation_inputs as inputs
from backend import run_stage2_results as results


class ContinuationRankingTests(unittest.IsolatedAsyncioTestCase):
    pairwise = False

    def setUp(self) -> None:
        self.fixture = fixtures.Stage2ResultFixture()
        self.fixture.pairwise = self.pairwise
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run = self.fixture.run
        self.path = self.fixture.path
        self.models = self.fixture.models
        self.labels = self.fixture.labels

    async def prepare(self, content: str = "FINAL RAN") -> None:
        await self.fixture.execute(self.models[0], content, "length")
        inputs.bind(self.run, self.models[0], expected_revision=6)
        for model in self.models[1:]:
            await self.fixture.execute(model, self.fixture.ballot())

    async def finish(self, content: str, **kwargs: Any) -> None:
        finish = kwargs.pop("finish", "stop")

        async def provider(**request: Any) -> dict[str, Any]:
            return {
                "content": content,
                "finish_reason": finish,
                "usage": {"nested": [2]},
                "reasoning_details": [{"text": "continuation reasoning"}],
            }

        await execution.execute(
            self.run,
            self.models[0],
            kwargs.pop("provider", provider),
            turn=1,
            provider_key="continuation-test-v1",
            expected_revision=6,
            **kwargs,
        )

    def remainder(self) -> str:
        return self.fixture.ballot()[len("FINAL RAN") :]

    async def test_split_ballot_exact_text_and_per_chunk_provenance(self) -> None:
        await self.prepare()
        initial = json.loads(self.path.read_text())["stage2_execution"]
        await self.finish(self.remainder())
        before = self.path.read_bytes()
        inspection = run_inspection.inspect(self.run)
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            result = results.load_complete_results(self.run)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(inspection, run_inspection.inspect(self.run))
        self.assertEqual(initial, json.loads(before)["stage2_execution"])
        self.assertEqual([r.model for r in result.reviewers], self.models)
        reviewer = result.reviewers[0]
        self.assertEqual(reviewer.content, self.fixture.ballot())
        self.assertEqual(reviewer.parsed_ranking, tuple(self.labels))
        self.assertEqual(reviewer.response.content, "FINAL RAN")
        self.assertEqual(reviewer.response.finish_reason, "length")
        self.assertEqual([c.turn for c in reviewer.chunks], [0, 1])
        self.assertEqual(
            [c.response.usage for c in reviewer.chunks],
            [{"nested": [1]}, {"nested": [2]}],
        )
        saved = json.loads(before)
        initial_op = saved["stage2_execution"]["operations"][0]
        follow_op = saved["stage2_continuation_execution"]["operations"][0]
        self.assertEqual(str(reviewer.attempt), initial_op["attempt"])
        self.assertEqual(reviewer.provider_key, "synthetic-v1")
        for chunk, op in zip(reviewer.chunks, [initial_op, follow_op], strict=True):
            self.assertEqual(str(chunk.attempt), op["attempt"])
            self.assertEqual(chunk.retry_of, None)
            self.assertEqual(chunk.response.model_dump(mode="json"), op["result"])
        self.assertEqual(
            reviewer.chunks[0].inputs_sha256, saved["stage2_execution"]["inputs_sha256"]
        )
        self.assertEqual(reviewer.chunks[1].inputs_sha256, follow_op["inputs_sha256"])
        self.assertEqual(reviewer.chunks[1].provider_key, "continuation-test-v1")
        self.assertEqual(len(result.reviewers[1].chunks), 1)
        self.assertTrue(
            all(r.rankings_count == len(self.models) for r in result.aggregate)
        )
        self.assertEqual(
            result.ranking_method, "pairwise" if self.pairwise else "average_rank"
        )

    async def test_prepared_or_still_length_continuation_cannot_form_quorum(
        self,
    ) -> None:
        await self.prepare()
        self.assertIsNone(results.load_complete_results(self.run))
        await self.finish(self.remainder(), finish="length")
        self.assertIsNone(results.load_complete_results(self.run))

    async def test_unstarted_sibling_cannot_form_quorum(self) -> None:
        await self.fixture.execute(self.models[0], "FINAL RAN", "length")
        inputs.bind(self.run, self.models[0], expected_revision=6)
        await self.finish(self.remainder())
        self.assertIsNone(results.load_complete_results(self.run))

    async def test_uncertain_then_explicit_retry_preserves_retry_provenance(
        self,
    ) -> None:
        await self.prepare()

        async def fail(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("synthetic uncertain")

        with self.assertRaises(execution.ProviderFailure):
            await self.finish("", provider=fail)
        self.assertIsNone(results.load_complete_results(self.run))
        token = run_inspection.inspect(self.run)["stage2_continuation_execution"][
            "members"
        ][0]["retry_token"]
        await self.finish(self.remainder(), retry_token=token)
        chunk = results.load_complete_results(self.run).reviewers[0].chunks[1]
        self.assertEqual(str(chunk.retry_of), token)
        self.assertNotEqual(str(chunk.attempt), token)

    async def test_live_continuation_does_not_form_quorum(self) -> None:
        await self.prepare()
        started, release = asyncio.Event(), asyncio.Event()

        async def wait(**kwargs: Any) -> dict[str, Any]:
            started.set()
            await release.wait()
            return {"content": self.remainder(), "finish_reason": "stop"}

        task = asyncio.create_task(self.finish("", provider=wait))
        try:
            await asyncio.wait_for(started.wait(), 3)
            self.assertIsNone(results.load_complete_results(self.run))
        finally:
            release.set()
            await task
        self.assertIsNotNone(results.load_complete_results(self.run))

    async def test_invalid_completed_ballot_keeps_chunks_but_does_not_vote(
        self,
    ) -> None:
        await self.prepare("no ")
        await self.finish("valid ranking")
        result = results.load_complete_results(self.run)
        self.assertEqual(result.reviewers[0].content, "no valid ranking")
        self.assertEqual(len(result.reviewers[0].chunks), 2)
        self.assertEqual(len(result.invalid_rankings), 1)
        self.assertTrue(
            all(r.rankings_count == len(self.models) - 1 for r in result.aggregate)
        )

    async def test_all_invalid_continued_ballots_have_no_winner(self) -> None:
        for model in self.models:
            await self.fixture.execute(model, "no ", "length")
            inputs.bind(self.run, model, expected_revision=6)

            async def provider(**kwargs: Any) -> dict[str, Any]:
                return {"content": "ballot", "finish_reason": "stop"}

            await execution.execute(
                self.run,
                model,
                provider,
                turn=1,
                provider_key="continuation-test-v1",
                expected_revision=6,
            )
        result = results.load_complete_results(self.run)
        self.assertEqual(result.aggregate, ())
        self.assertEqual(len(result.invalid_rankings), len(self.models))

    async def test_historical_detached_results_survive_cancellation_and_drift(
        self,
    ) -> None:
        await self.prepare()
        await self.finish(self.remainder())
        expected = results.load_complete_results(self.run)
        changed = results.load_complete_results(self.run)
        changed.reviewers[0].chunks[0].response.usage["nested"].append(77)
        changed.reviewers[0].chunks[1].response.reasoning_details[0]["text"] = "mutated"
        changed.reviewers[0].response.usage["nested"].clear()
        self.assertEqual(
            changed.reviewers[0].chunks[0].response.usage["nested"], [1, 77]
        )
        with patch.object(config, "PAIRWISE_RANKING_ENABLED", not self.pairwise):
            run_cancellation.request(self.run)
            self.assertEqual(results.load_complete_results(self.run), expected)

    async def test_corrupt_continuation_source_or_attempt_fails_closed(self) -> None:
        await self.prepare()
        await self.finish(self.remainder())
        original = json.loads(self.path.read_text())
        cases = [
            (
                "stage2_continuation_inputs",
                "requests",
                "initial_attempt",
                "00000000-0000-0000-0000-000000000000",
            ),
            (
                "stage2_continuation_inputs",
                "requests",
                "initial_response_sha256",
                "0" * 64,
            ),
            ("stage2_continuation_execution", "operations", "inputs_sha256", "0" * 64),
            ("stage2_continuation_execution", "operations", "model", self.models[1]),
        ]
        for section, rows, field, value in cases:
            with self.subTest(field=field):
                damaged = copy.deepcopy(original)
                damaged[section][rows][0][field] = value
                self.path.write_text(json.dumps(damaged))
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    results.load_complete_results(self.run)
        self.path.write_text(json.dumps(original))

    async def test_deleted_continued_result_is_unavailable(self) -> None:
        await self.prepare()
        await self.finish(self.remainder())
        run_admission.tombstone_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            results.load_complete_results(self.run)


class PairwiseContinuationRankingTests(ContinuationRankingTests):
    pairwise = True
