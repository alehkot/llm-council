"""Strict saved critique validation with real durable evidence and fake providers."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import test_run_stage1_inputs as input_fixtures
import test_run_stage2_inputs as review_fixtures

from backend import (
    config,
    council,
    run_admission,
    run_cancellation,
    run_negative_execution,
    run_negative_inputs,
    run_negative_results,
    storage,
)
from backend import (
    run_negative_validation as validation,
)

ROSTER = ("provider/first", "provider/second", "provider/third", "provider/fourth")


def critique(peer_labels: tuple[str, ...]) -> str:
    # Use endpoints and reverse the JSON map order relative to the request.
    return json.dumps(
        {
            "self_score": 6.5,
            "self_summary": "Needs more context. Пример.",
            "peer_scores": dict(reversed(list(zip(peer_labels, (0, 10))))),
            "peer_summaries": {label: f"Check {label}." for label in peer_labels},
        }
    )


class ParserCases(unittest.TestCase):
    def parse(self, text: str) -> validation.ValidatedCritique:
        return validation._parse(
            run_negative_results.CritiqueResult(
                model=ROSTER[2],
                self_label="Response B",
                peer_labels=("Response A", "Response C"),
                request_sha256="1" * 64,
                attempt=uuid4(),
                retry_of=uuid4(),
                response=run_negative_execution.MemberResponse(
                    content=text, finish_reason="stop"
                ),
            )
        )

    def test_envelopes_and_order(self) -> None:
        raw = critique(("Response A", "Response C"))
        for value in (
            raw,
            f" ```json\n{raw}\n```\nNotes may mention {{other values}}.",
            f"```JSON\r\n{raw}\r\n```\r\nOptional notes.",
        ):
            with self.subTest(value=value):
                result = self.parse(value)
                self.assertEqual(result.own_answer.label, "Response B")
                self.assertEqual(result.own_answer.score, 6.5)
                self.assertEqual(
                    result.own_answer.summary, "Needs more context. Пример."
                )
                self.assertEqual(
                    [(a.label, a.score) for a in result.peers],
                    [("Response A", 0.0), ("Response C", 10.0)],
                )
                self.assertNotIn("Needs more context", repr(result))

    def test_malformed_inputs_never_fall_back_or_coerce(self) -> None:
        good = critique(("Response A", "Response C"))
        cases = (
            ("stale label", "peer_scope", good.replace("Response C", "Response D")),
            ("self as peer", "peer_scope", good.replace("Response C", "Response B")),
            (
                "missing summaries",
                "peer_scope",
                good.replace('"Response A": "Check Response A.", ', ""),
            ),
            (
                "duplicate",
                "duplicate_key",
                good.replace('"Response A": 0', '"Response A": 7, "Response A": 0'),
            ),
            (
                "escaped duplicate",
                "duplicate_key",
                good.replace(
                    '"Response A": 0', r'"Response \u0041": 7, "Response A": 0'
                ),
            ),
            (
                "duplicate root",
                "duplicate_key",
                good.replace('"self_score": 6.5', '"self_score": 2, "self_score": 6.5'),
            ),
            ("Boolean", "score", good.replace("6.5", "true")),
            ("null", "score", good.replace("6.5", "null")),
            ("numeric string", "score", good.replace("6.5", '"6.5"')),
            ("negative", "score", good.replace("6.5", "-0.5")),
            ("over 10", "score", good.replace('"Response C": 10', '"Response C": 11')),
            ("floating overflow", "score", good.replace("6.5", "1e999")),
            ("rounds to 10", "score", good.replace("6.5", "10.0000000000000001")),
            ("rounds to zero", "score", good.replace("6.5", "-1e-999")),
            ("nonstandard NaN", "json", good.replace("6.5", "NaN")),
            ("unclosed fence", "json", f"```json\n{good}"),
            ("prose-only", "json", "self_score: 6; Response A: 0; Response C: 10"),
            ("prose first", "json", f"Here it is: {good}"),
            (
                "extra root",
                "fields",
                good.replace('"self_score"', '"unexpected": 1, "self_score"'),
            ),
            ("nonobject", "fields", "[]"),
            ("blank summary", "summary", good.replace('"Check Response A."', '"   "')),
            (
                "lone surrogate",
                "summary",
                good.replace('"Check Response A."', r'"\ud800"'),
            ),
        )
        for label, reason, text in cases:
            with (
                self.subTest(label=label),
                self.assertRaises(validation.InvalidCritique) as failure,
            ):
                self.parse(text)
            self.assertEqual(failure.exception.reason, reason)
            self.assertNotIn(text, str(failure.exception))

    def test_legacy_parser_is_unchanged(self) -> None:
        raw = (
            critique(("Response A", "Response C"))
            .replace("6.5", "true")
            .replace("Response C", "Response D")
        )
        result = council.parse_negative_scores(raw, ["Response A", "Response C"])
        self.assertEqual(
            result["scores"],
            {"self": 1.0, "peers": {"Response D": 10.0, "Response A": 0.0}},
        )


class SavedFixture(unittest.IsolatedAsyncioTestCase):
    models = ROSTER
    exclude = ROSTER[1]

    def setUp(self) -> None:
        self.fixture = review_fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with patch.object(
            input_fixtures.Stage1InputTests, "fixture_models", self.models
        ):
            self.fixture.complete(
                {"exclude_responses": [self.exclude]},
                negative=True,
                requested="Private synthetic concern",
            )
        self.run_id = self.fixture.run
        self.inputs = run_negative_inputs.bind(
            self.run_id, expected_revision=self.fixture.revision
        )

    async def deliver(self, model: str, text: str, *, finish: str = "stop") -> None:
        async def provider(**_: Any) -> dict[str, Any]:
            return {"content": text, "finish_reason": finish}

        await run_negative_execution.execute(
            self.run_id,
            model,
            provider,
            provider_key="synthetic-critique-validation",
            expected_revision=self.fixture.revision,
        )

    def read(self) -> validation.ValidatedResults | None:
        before = self.fixture.files()
        try:
            with (
                patch.object(
                    run_admission, "_write", side_effect=AssertionError("read wrote")
                ),
                patch.object(
                    run_negative_execution,
                    "execute",
                    side_effect=AssertionError("read dispatched"),
                ),
                patch.object(
                    council,
                    "parse_negative_scores",
                    side_effect=AssertionError("used legacy fallback"),
                ),
            ):
                return validation.load_validated_results(self.run_id)
        finally:
            self.assertEqual(before, self.fixture.files())


class SavedCritiqueCases(SavedFixture):
    async def test_saved_relabeling_and_provenance(self) -> None:
        self.assertIsNone(self.read())
        first, relabeled, last = self.inputs.requests
        self.assertEqual(
            (first.model, relabeled.model, last.model),
            (ROSTER[0], ROSTER[2], ROSTER[3]),
        )
        self.assertEqual(relabeled.self_label, "Response B")
        self.assertEqual(relabeled.peer_labels, ("Response A", "Response C"))
        for request in reversed(self.inputs.requests):
            await self.deliver(request.model, critique(request.peer_labels))
        raw = run_negative_results.load_complete_results(self.run_id)
        result = self.read()
        assert raw is not None and result is not None
        for key in (
            "source_revision",
            "inputs_sha256",
            "stage1_source_sha256",
            "review_policy_sha256",
            "negative_prompt_source",
            "provider_key",
            "label_to_model",
        ):
            self.assertEqual(getattr(result, key), getattr(raw, key))
        self.assertEqual(
            result.label_to_model,
            {"Response A": ROSTER[0], "Response B": ROSTER[2], "Response C": ROSTER[3]},
        )
        for actual, original in zip(result.critiques, raw.critiques, strict=True):
            self.assertEqual(actual.model, original.model)
            self.assertEqual(actual.attempt, original.attempt)
            self.assertEqual(actual.retry_of, original.retry_of)
            self.assertEqual(actual.request_sha256, original.request_sha256)
        self.assertNotIn("Private synthetic concern", result.model_dump_json())
        self.assertNotIn("finish_reason", result.model_dump_json())
        result.label_to_model.clear()
        run_cancellation.request(self.run_id)
        with patch.object(config, "COUNCIL_MODELS", ["unrelated/current"]):
            fresh = self.read()
        assert fresh is not None
        self.assertEqual(fresh.label_to_model, raw.label_to_model)
        storage.delete_conversation(f"cli-{self.run_id}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.read()

    async def test_bad_completed_reviewer_blocks_the_entire_set(self) -> None:
        first, bad, last = self.inputs.requests
        for request in (first, bad):
            value = critique(request.peer_labels)
            if request.model == bad.model:
                value = value.replace("Response C", "Response D-PRIVATE")
            await self.deliver(request.model, value)
        self.assertIsNone(self.read(), "a partially available roster is not parsed")
        await self.deliver(last.model, critique(last.peer_labels))
        with self.assertRaises(validation.InvalidCritique) as failure:
            self.read()
        self.assertEqual(failure.exception.reviewer, "Response B")
        self.assertEqual(failure.exception.reason, "peer_scope")
        self.assertNotIn("PRIVATE", str(failure.exception))
        raw = run_negative_results.load_complete_results(self.run_id)
        assert raw is not None
        self.assertIn("Response D-PRIVATE", raw.critiques[1].response.content)

    async def test_length_limited_is_not_a_parsed_result(self) -> None:
        for request in self.inputs.requests:
            await self.deliver(
                request.model, critique(request.peer_labels), finish="length"
            )
        self.assertIsNone(self.read())


class SoloCritiqueCase(SavedFixture):
    models = ROSTER[:2]
    exclude = ROSTER[0]

    async def test_empty_peer_maps_are_valid_for_the_solo_saved_reviewer(self) -> None:
        (request,) = self.inputs.requests
        self.assertEqual(request.peer_labels, ())
        await self.deliver(request.model, critique(request.peer_labels))
        result = self.read()
        assert result is not None
        self.assertEqual(result.label_to_model, {"Response A": ROSTER[1]})
        self.assertEqual(result.critiques[0].peers, ())
