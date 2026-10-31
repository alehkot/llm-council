"""Invalid peer votes remain diagnostics, not quantitative synthesis evidence."""

import os
import unittest
from importlib import import_module
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

council = import_module("backend.council")


LABELS = {"Response A": "model-a", "Response B": "model-b"}
MODES = ("consensus", "best_of_breed", "comprehensive", "weighted")


def invalid_review(parsed: list[str], error: str) -> dict:
    return {
        "model": "reviewer-invalid",
        "ranking": "Useful qualitative observation.\nFINAL RANKING:\n1. Response B",
        "parsed_ranking": parsed,
        "validation_status": "invalid",
        "validation_error": error,
    }


class RankingValidityContextTests(unittest.TestCase):
    def test_invalid_orders_are_diagnostic_in_every_synthesis_mode(self) -> None:
        cases = (
            (["Response B"], "missing labels: Response A"),
            (["Response B", "Response B"], "duplicate label Response B"),
            (["Response B", "Response Z"], "unknown label Response Z"),
            ([], "no labels parsed"),
        )
        for parsed, error in cases:
            for mode in MODES:
                with self.subTest(parsed=parsed, mode=mode):
                    text = council.build_synthesis_ranking_context(
                        [invalid_review(parsed, error)], LABELS, synthesis_mode=mode
                    )
                    self.assertIn("invalid", text.lower())
                    self.assertIn(error, text)
                    self.assertNotIn("- reviewer-invalid: Response", text)
                    if mode != "consensus":
                        rationale_lines = [
                            line
                            for line in text.splitlines()
                            if "Useful qualitative observation." in line
                        ]
                        self.assertTrue(rationale_lines)
                        self.assertTrue(
                            all("invalid" in line.lower() for line in rationale_lines)
                        )

    def test_explicit_invalid_status_without_error_is_not_a_valid_order(self) -> None:
        review = invalid_review(["Response B", "Response A"], "")
        text = council.build_synthesis_ranking_context([review], LABELS)
        self.assertIn("invalid", text.lower())
        self.assertNotIn("- reviewer-invalid: Response B > Response A", text)

    def test_validation_error_without_status_rejects_order(self) -> None:
        review = invalid_review(["Response B"], "missing labels: Response A")
        review.pop("validation_status")
        text = council.build_synthesis_ranking_context([review], LABELS)
        self.assertIn("missing labels: Response A", text)
        self.assertNotIn("- reviewer-invalid: Response B", text)
        feedback = council.build_peer_review_feedback(
            "model-b", "Response B", [review], [], expected_label_count=2
        )
        self.assertNotIn("Position distribution", feedback)

    def test_valid_and_legacy_valid_orders_are_preserved(self) -> None:
        for validated in (True, False):
            review = {
                "model": "reviewer-valid",
                "ranking": "Helpful explanation.\nFINAL RANKING:\n1. Response B\n2. Response A",
                "parsed_ranking": ["Response B", "Response A"],
            }
            if validated:
                review.update(validation_status="valid", validation_error=None)
            for mode in MODES:
                with self.subTest(validated=validated, mode=mode):
                    text = council.build_synthesis_ranking_context(
                        [review], LABELS, synthesis_mode=mode
                    )
                    self.assertIn("- reviewer-valid: Response B > Response A", text)
                    self.assertNotIn("invalid", text.lower())

    def test_invalid_votes_do_not_contribute_deliberation_positions(self) -> None:
        for parsed, error in (
            (["Response B"], "missing labels: Response A"),
            (["Response B", "Response B"], "duplicate label Response B"),
            (["Response B", "Response Z"], "unknown label Response Z"),
        ):
            with self.subTest(parsed=parsed):
                feedback = council.build_peer_review_feedback(
                    "model-b",
                    "Response B",
                    [invalid_review(parsed, error)],
                    [],
                    expected_label_count=2,
                )
                self.assertNotIn("Position distribution", feedback)

    def test_mixed_votes_only_count_valid_deliberation_positions(self) -> None:
        reviews = [
            invalid_review(["Response B"], "missing labels: Response A"),
            {
                "model": "reviewer-valid",
                "parsed_ranking": ["Response A", "Response B"],
                "validation_status": "valid",
                "validation_error": None,
            },
        ]
        feedback = council.build_peer_review_feedback(
            "model-b", "Response B", reviews, [], expected_label_count=2
        )
        self.assertIn("rank 2: 1", feedback)
        self.assertNotIn("rank 1: 1", feedback)


class RankingValidityProviderFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_review_stays_invalid_in_actual_chairman_prompt(
        self,
    ) -> None:
        stage1 = [
            {"model": "model-a", "response": "Answer A"},
            {"model": "model-b", "response": "Answer B"},
        ]

        async def reviewer(*_args: object, **_kwargs: object) -> dict:
            return {
                "content": "Useful qualitative observation.\nFINAL RANKING:\n1. Response B",
                "finish_reason": "length",
            }

        with (
            patch.object(council, "COUNCIL_MODELS", ["reviewer-invalid"]),
            patch.object(council, "query_model", reviewer),
        ):
            stage2, mapping = await council.stage2_collect_rankings("Question", stage1)
        self.assertEqual(stage2[0]["validation_status"], "invalid")
        self.assertEqual(stage2[0]["validation_error"], "missing labels: Response A")
        aggregate, invalid = council.calculate_aggregate_rankings(stage2, mapping)
        self.assertEqual(aggregate, [])
        self.assertEqual(len(invalid), 1)

        for mode in MODES:
            chairman = AsyncMock(return_value={"content": "Synthetic final answer"})

            with (
                self.subTest(mode=mode),
                patch.object(council, "query_model", chairman),
            ):
                result = await council.stage3_synthesize_final(
                    "Question", stage1, stage2, mapping, synthesis_mode=mode
                )
                self.assertEqual(result["response"], "Synthetic final answer")
                chairman.assert_awaited_once()
                prompt = chairman.call_args.args[1][0]["content"]
                self.assertIn("invalid", prompt.lower())
                self.assertIn("missing labels: Response A", prompt)
                self.assertNotIn("- reviewer-invalid: Response B", prompt)


if __name__ == "__main__":
    unittest.main()
