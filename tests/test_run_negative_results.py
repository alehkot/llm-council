"""Projection of saved negative critiques never interprets or executes them."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Literal
from unittest.mock import patch
from uuid import uuid4

import test_run_stage2_inputs as fixtures

from backend import (
    config,
    run_admission,
    run_cancellation,
    run_inspection,
    run_negative_execution,
    run_negative_inputs,
    run_negative_results,
    run_stage1_inputs,
    storage,
)


class ProjectionFixture(unittest.IsolatedAsyncioTestCase):
    exclude = False
    enabled = True

    def setUp(self) -> None:
        self.fixture = fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # The fixture uses asyncio.run; complete it before the async test starts.
        self.fixture.complete(
            {"exclude_responses": ["provider/first"]} if self.exclude else None,
            negative=self.enabled,
            requested="Private synthetic concern" if self.enabled else None,
        )
        self.run_id = self.fixture.run
        self.path = run_admission._path(self.run_id)
        if self.enabled:
            self.inputs = run_negative_inputs.bind(
                self.run_id, expected_revision=self.fixture.revision
            )
            self.models = tuple(request.model for request in self.inputs.requests)

    def project(self) -> run_negative_results.NegativeResults | None:
        before = self.fixture.files()
        with (
            patch.object(
                run_admission, "_write", side_effect=AssertionError("read wrote")
            ),
            patch.object(
                run_negative_execution,
                "execute",
                side_effect=AssertionError("read dispatched"),
            ),
        ):
            result = run_negative_results.load_complete_results(self.run_id)
        self.assertEqual(before, self.fixture.files())
        return result

    async def execute(
        self,
        model: str,
        *,
        finish: Literal["stop", "length"] = "stop",
        retry: str | None = None,
    ) -> run_negative_execution.MemberResponse:
        async def provider(**_: Any) -> dict[str, Any]:
            return {
                "content": f"Private critique: {model} Пример\n",
                "finish_reason": finish,
                "reasoning_details": [{"note": ["synthetic private reasoning"]}],
                "usage": {"completion_tokens": 8, "other": [1, True]},
            }

        return await run_negative_execution.execute(
            self.run_id,
            model,
            provider,
            provider_key="synthetic-projection-v1",
            expected_revision=self.fixture.revision,
            retry_token=retry,
        )


class NegativeResultsTests(ProjectionFixture):
    async def test_exact_active_order_labels_sources_and_raw_response(self) -> None:
        self.assertIsNone(self.project())
        last, first = self.models[1], self.models[0]
        second_response = await self.execute(last)
        self.assertIsNone(self.project())
        first_response = await self.execute(first)
        projected = self.project()
        self.assertIsNotNone(projected)
        assert projected is not None
        self.assertEqual(tuple(c.model for c in projected.critiques), self.models)
        self.assertEqual(projected.label_to_model, self.inputs.label_to_model)
        self.assertEqual(projected.source_revision, self.inputs.source_revision)
        self.assertEqual(
            projected.stage1_source_sha256, self.inputs.stage1_source_sha256
        )
        self.assertEqual(
            projected.review_policy_sha256, self.inputs.review_policy_sha256
        )
        self.assertEqual(projected.negative_prompt_source, "request")
        self.assertEqual(
            projected.inputs_sha256, run_stage1_inputs._digest(self.inputs)
        )
        self.assertEqual(projected.provider_key, "synthetic-projection-v1")
        record = json.loads(self.path.read_text())
        operations = {
            op["model"]: op for op in record["negative_execution"]["operations"]
        }
        for critique, request, response in zip(
            projected.critiques,
            self.inputs.requests,
            (first_response, second_response),
            strict=True,
        ):
            self.assertEqual(critique.self_label, request.self_label)
            self.assertEqual(critique.peer_labels, request.peer_labels)
            self.assertEqual(
                critique.request_sha256, run_stage1_inputs._digest(request)
            )
            self.assertEqual(
                str(critique.attempt), operations[critique.model]["attempt"]
            )
            self.assertIsNone(critique.retry_of)
            self.assertEqual(critique.response, response)
        self.assertNotIn("Private critique:", repr(projected))
        self.assertNotIn("Private critique:", repr(projected.critiques[0]))
        self.assertNotIn("Private synthetic concern", projected.model_dump_json())
        self.assertNotIn("scores", projected.model_dump())
        self.assertNotIn(
            "Private critique:", json.dumps(run_inspection.inspect(self.run_id))
        )

        # Model freezes do not make nested containers immutable: the projection
        # must detach each result and copy labels even if a caller edits them.
        projected.label_to_model.clear()
        assert projected.critiques[0].response.usage is not None
        projected.critiques[0].response.usage["other"].append("caller change")
        fresh = self.project()
        assert fresh is not None
        self.assertEqual(fresh.label_to_model, self.inputs.label_to_model)
        self.assertEqual(fresh.critiques[0].response, first_response)

    async def test_length_is_never_complete_even_with_all_attempts_saved(self) -> None:
        await self.execute(self.models[0])
        partial = await self.execute(self.models[1], finish="length")
        self.assertIsNone(self.project())
        self.assertEqual(
            run_negative_execution.load_result(self.run_id, self.models[1]), partial
        )
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"],
            "negative_needs_continuation",
        )

    async def test_inflight_and_uncertain_require_explicit_retry_and_keep_ancestry(
        self,
    ) -> None:
        await self.execute(self.models[0])
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fail(**_: Any) -> dict[str, Any]:
            entered.set()
            await release.wait()
            raise RuntimeError("synthetic failure")

        task = asyncio.create_task(
            run_negative_execution.execute(
                self.run_id,
                self.models[1],
                fail,
                provider_key="synthetic-projection-v1",
                expected_revision=self.fixture.revision,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            self.assertIsNone(self.project())
        finally:
            release.set()
            with self.assertRaises(run_negative_execution.ProviderFailure):
                await asyncio.wait_for(task, timeout=5)
        self.assertIsNone(self.project())
        record = json.loads(self.path.read_text())
        predecessor = record["negative_execution"]["operations"][1]["attempt"]
        await self.execute(self.models[1], retry=predecessor)
        projected = self.project()
        assert projected is not None
        self.assertEqual(str(projected.critiques[1].retry_of), predecessor)
        self.assertNotEqual(
            projected.critiques[1].attempt, projected.critiques[1].retry_of
        )


class ExcludedOwnerProjectionTests(ProjectionFixture):
    exclude = True

    async def test_excluded_owner_is_not_required_or_projected(self) -> None:
        self.assertEqual(len(self.models), 1)
        await self.execute(self.models[0])
        projected = self.project()
        assert projected is not None
        self.assertEqual(projected.label_to_model, {"Response A": self.models[0]})
        self.assertEqual(projected.critiques[0].peer_labels, ())
        self.assertNotIn(self.fixture.models[0], projected.model_dump_json())

    async def test_historical_read_survives_cancellation_and_config_change_not_deletion(
        self,
    ) -> None:
        await self.execute(self.models[0])
        before = self.project()
        run_cancellation.request(self.run_id)
        with patch.object(config, "COUNCIL_MODELS", ["different/synthetic-model"]):
            self.assertEqual(self.project(), before)
        storage.delete_conversation(f"cli-{self.run_id}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.project()

    async def test_corrupted_receipt_and_source_do_not_yield_a_critique(self) -> None:
        await self.execute(self.models[0])
        baseline = self.path.read_text()
        for category in ("negative_execution", "negative_inputs"):
            with self.subTest(category=category):
                record = json.loads(baseline)
                if category == "negative_execution":
                    record[category]["inputs_sha256"] = "0" * 64
                else:
                    record[category]["requests"][0]["self_label"] = "Response Z"
                self.path.write_text(json.dumps(record))
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.project()
        self.path.write_text(baseline)
        self.assertIsNotNone(self.project())


class UnboundProjectionTests(ProjectionFixture):
    enabled = False

    async def test_unbound_and_missing_runs_fail_closed(self) -> None:
        with self.assertRaises(run_negative_execution.ExecutionConflict):
            self.project()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_negative_results.load_complete_results(str(uuid4()))
