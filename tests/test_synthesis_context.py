import os
import unittest

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend.council import (  # noqa: E402
    build_synthesis_ranking_context,
    build_synthesis_response_context,
)


def _response(prefix: str, paragraphs: int = 5) -> str:
    return "\n\n".join(
        [
            f"{prefix} recommendation and conclusion.",
            *[
                (
                    f"{prefix} detail {index}: evidence, risk, caveat, "
                    "trade-off, example, and implementation step."
                )
                for index in range(paragraphs)
            ],
            f"{prefix} final priority and reversal condition.",
        ]
    )


class SynthesisContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stage1 = [
            {"model": "model-a", "response": _response("A")},
            {"model": "model-b", "response": _response("B")},
            {"model": "model-c", "response": _response("C")},
        ]
        self.label_to_model = {
            "Response A": "model-a",
            "Response B": "model-b",
            "Response C": "model-c",
        }
        self.labels = list(self.label_to_model.keys())

    def test_weighted_mode_prioritizes_ranked_responses(self) -> None:
        aggregate_rankings = [
            {"model": "model-c", "win_percentage": 1.0, "rankings_count": 3},
            {"model": "model-a", "win_percentage": 0.5, "rankings_count": 3},
            {"model": "model-b", "win_percentage": 0.0, "rankings_count": 3},
        ]

        text, note = build_synthesis_response_context(
            self.stage1,
            self.labels,
            self.label_to_model,
            aggregate_rankings=aggregate_rankings,
            synthesis_mode="weighted",
            max_chars=20_000,
        )

        self.assertLess(text.index("Response C"), text.index("Response A"))
        self.assertLess(text.index("Response A"), text.index("Response B"))
        self.assertIn("Response C (model-c; rank #1", text)
        self.assertIn("Response B (model-b; rank #3", text)
        self.assertIn("compact excerpt", text)
        self.assertIn("Higher-ranked responses receive full-priority space", note)

    def test_weighted_mode_without_rankings_uses_equal_compact_evidence(self) -> None:
        text, note = build_synthesis_response_context(
            self.stage1,
            self.labels,
            self.label_to_model,
            aggregate_rankings=[],
            synthesis_mode="weighted",
            max_chars=20_000,
        )

        self.assertIn("Response A (model-a; not ranked; compact excerpt)", text)
        self.assertIn("Response B (model-b; not ranked; compact excerpt)", text)
        self.assertIn("Response C (model-c; not ranked; compact excerpt)", text)
        self.assertIn("No aggregate rankings were available", note)

    def test_consensus_ranking_context_omits_reviewer_rationale(self) -> None:
        stage2 = [
            {
                "model": "reviewer-1",
                "ranking": (
                    "Long rationale that should not be forwarded in consensus mode.\n"
                    "FINAL RANKING: Response B > Response A"
                ),
                "parsed_ranking": ["Response B", "Response A"],
            }
        ]
        aggregate_rankings = [
            {"model": "model-b", "average_rank": 1.0, "rankings_count": 1},
            {"model": "model-a", "average_rank": 2.0, "rankings_count": 1},
        ]

        text = build_synthesis_ranking_context(
            stage2,
            {"Response A": "model-a", "Response B": "model-b"},
            aggregate_rankings=aggregate_rankings,
            synthesis_mode="consensus",
        )

        self.assertIn("Aggregate ranking summary:", text)
        self.assertIn("- reviewer-1: Response B > Response A", text)
        self.assertNotIn("Long rationale", text)

    def test_global_budget_compression_keeps_each_response_represented(self) -> None:
        long_stage1 = [
            {
                "model": f"model-{index}",
                "response": _response(str(index), paragraphs=30),
            }
            for index in range(4)
        ]
        label_to_model = {
            f"Response {chr(65 + index)}": f"model-{index}" for index in range(4)
        }

        text, note = build_synthesis_response_context(
            long_stage1,
            list(label_to_model.keys()),
            label_to_model,
            synthesis_mode="comprehensive",
            max_chars=1_800,
        )

        self.assertLessEqual(len(text), 1_800)
        for label in label_to_model:
            self.assertIn(label, text)
        self.assertIn("global context budget", note)


if __name__ == "__main__":
    unittest.main()
