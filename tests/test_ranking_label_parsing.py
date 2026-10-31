"""Keep malformed peer ballots visible to validation instead of repairing them."""

import os
import unittest
from importlib import import_module
from itertools import permutations
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")
council = import_module("backend.council")


def ballot(labels: list[str]) -> str:
    return "FINAL RANKING:\n" + "\n".join(
        f"{index}. {label}" for index, label in enumerate(labels, 1)
    )


class RankingLabelParsingTests(unittest.TestCase):
    def test_unknown_labels_are_not_silently_filtered(self) -> None:
        expected = ["Response A", "Response B"]
        for labels in permutations([*expected, "Response C"]):
            with self.subTest(labels=labels):
                self.assertEqual(
                    council.parse_ranking_from_text(ballot(labels), expected),
                    list(labels),
                )

    def test_unknown_label_prefixes_are_not_known_labels(self) -> None:
        expected = ["Response A", "Response B"]
        for unknown in ("Response AA", "Response A1", "Response B2"):
            with self.subTest(unknown=unknown):
                labels = [unknown, "Response B"]
                self.assertEqual(
                    council.parse_ranking_from_text(ballot(labels), expected), labels
                )

    def test_complete_and_duplicate_labels_preserve_their_order(self) -> None:
        expected = ["Response A", "Response AA", "Response A1", "Response 1"]
        for labels in permutations(expected):
            with self.subTest(labels=labels):
                self.assertEqual(
                    council.parse_ranking_from_text(ballot(labels), expected),
                    list(labels),
                )
        labels = ["Response A", "Response A", "Response AA"]
        self.assertEqual(
            council.parse_ranking_from_text(ballot(labels), expected), labels
        )

    def test_custom_expected_labels_remain_supported(self) -> None:
        expected = [
            "Answer A",
            "Answer AA",
            "Response A",
            "Response A-variant",
            "Choice (B)",
        ]
        labels = [
            "Response A-variant",
            "Answer AA",
            "Response Z",
            "Choice (B)",
            "Answer A",
            "Response A",
        ]
        self.assertEqual(
            council.parse_ranking_from_text(ballot(labels), expected), labels
        )

    def test_existing_unstructured_fallbacks_remain_supported(self) -> None:
        for text in (
            "Response B is best, then Response A.",
            "FINAL RANKING:\nResponse B > Response A",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    council.parse_ranking_from_text(text), ["Response B", "Response A"]
                )
        self.assertEqual(council.parse_ranking_from_text(""), [])


class Stage2UnknownLabelTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_ballots_reach_validation_and_never_count_as_votes(
        self,
    ) -> None:
        stage1 = [
            {"model": "one", "response": "Answer one"},
            {"model": "two", "response": "Answer two"},
        ]
        for labels in (
            ["Response C", "Response A", "Response B"],
            ["Response A", "Response C", "Response B"],
            ["Response A", "Response B", "Response C"],
            ["Response A1", "Response B"],
            ["Response AA", "Response B"],
        ):
            unknown = next(
                label for label in labels if label not in ("Response A", "Response B")
            )
            with (
                self.subTest(labels=labels),
                patch.object(council, "COUNCIL_MODELS", ["reviewer"]),
                patch.object(
                    council,
                    "query_model",
                    AsyncMock(return_value={"content": ballot(labels)}),
                ),
            ):
                reviews, mapping = await council.stage2_collect_rankings(
                    "Question", stage1
                )
            self.assertEqual(reviews[0]["parsed_ranking"], labels)
            self.assertEqual(reviews[0]["validation_status"], "invalid")
            self.assertEqual(reviews[0]["validation_error"], f"unknown label {unknown}")
            for aggregate in (
                council.calculate_aggregate_rankings,
                council.calculate_pairwise_rankings,
            ):
                votes, invalid = aggregate(reviews, mapping)
                self.assertEqual(votes, [])
                self.assertEqual(
                    invalid,
                    [{"model": "reviewer", "reason": f"unknown label {unknown}"}],
                )
            for mode in ("consensus", "best_of_breed", "comprehensive", "weighted"):
                context = council.build_synthesis_ranking_context(
                    reviews, mapping, synthesis_mode=mode
                )
                self.assertIn(f"unknown label {unknown}", context)
                self.assertNotIn("- reviewer: Response", context)

    async def test_valid_and_duplicate_ballots_keep_validation_behavior(self) -> None:
        stage1 = [
            {"model": "one", "response": "Answer one"},
            {"model": "two", "response": "Answer two"},
        ]
        for labels, status in (
            (["Response B", "Response A"], "valid"),
            (["Response A", "Response A"], "invalid"),
        ):
            with (
                self.subTest(labels=labels),
                patch.object(council, "COUNCIL_MODELS", ["reviewer"]),
                patch.object(
                    council,
                    "query_model",
                    AsyncMock(return_value={"content": ballot(labels)}),
                ),
            ):
                reviews, mapping = await council.stage2_collect_rankings(
                    "Question", stage1
                )
            self.assertEqual(reviews[0]["parsed_ranking"], labels)
            self.assertEqual(reviews[0]["validation_status"], status)
            for aggregate in (
                council.calculate_aggregate_rankings,
                council.calculate_pairwise_rankings,
            ):
                votes, invalid = aggregate(reviews, mapping)
                if status == "valid":
                    self.assertEqual(votes[0]["model"], "two")
                    self.assertTrue(all(vote["rankings_count"] == 1 for vote in votes))
                    self.assertEqual(invalid, [])
                else:
                    self.assertEqual(votes, [])
                    self.assertEqual(invalid[0]["reason"], "duplicate label Response A")


if __name__ == "__main__":
    unittest.main()
