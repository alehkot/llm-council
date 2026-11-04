"""Offline regressions for JSON embedded in model-generated prose."""

import itertools
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import council
from backend.skills import ReferenceCandidate


class JsonExtractionTests(unittest.TestCase):
    def test_literal_braces_and_escaped_quotes_in_strings(self) -> None:
        for content in (
            "Explain the closing } character.",
            "Explain the opening { character.",
            'The string "} contains an escaped quote.',
            'A backslash before the quote: \\" followed by }.',
            "A trailing backslash: \\",
            "Русский текст с } и emoji 🦫",
        ):
            with self.subTest(content=content):
                expected = {"text": content, "nested": {"value": "plain"}}
                self.assertEqual(
                    council.extract_json_object(json.dumps(expected)), expected
                )

    def test_generated_quote_escape_and_brace_combinations(self) -> None:
        for characters in itertools.product('{}"\\a', repeat=3):
            text = "".join(characters)
            expected = {"text": text, "nested": {"items": [text, "next"]}}
            with self.subTest(text=text):
                self.assertEqual(
                    council.extract_json_object("Result: " + json.dumps(expected)),
                    expected,
                )

    def test_nested_objects_remain_with_their_outer_object(self) -> None:
        expected = {"selected": [{"id": 1, "reason": "Use } parsing guidance"}]}
        self.assertEqual(council.extract_json_object(json.dumps(expected)), expected)

    def test_prose_and_multiple_candidates_keep_last_valid_object(self) -> None:
        text = 'Consider {this} first. "quoted prose" {"old": 1} then {"new": "}"} end.'
        self.assertEqual(council.extract_json_object(text), {"new": "}"})

    def test_stray_closing_brace_in_prose_does_not_hide_object(self) -> None:
        self.assertEqual(
            council.extract_json_object(
                'The closing } is discussed here: {"answer": "yes"}'
            ),
            {"answer": "yes"},
        )

    def test_valid_fenced_object_retains_priority(self) -> None:
        text = '```json\n{"preferred": "}"}\n```\nThen {"later": 1}'
        self.assertEqual(council.extract_json_object(text), {"preferred": "}"})

    def test_embedded_markdown_fence_does_not_truncate_json_string(self) -> None:
        expected = {"refined_prompt": "Explain ``` and the } character."}
        text = "```json\n" + json.dumps(expected) + "\n```"
        self.assertEqual(council.extract_json_object(text), expected)

    def test_invalid_and_incomplete_objects_are_not_repaired(self) -> None:
        for text in (
            "No structured answer",
            '{"text": "unterminated }',
            '{"text": "value",}',
            '{"outer": {"inner": 1}',
            '{broken {"inner": 1}}',
            '{"draft":"unfinished} Final: {"answer":"ok"}',
        ):
            with self.subTest(text=text):
                self.assertIsNone(council.extract_json_object(text))

    def test_valid_candidate_survives_later_invalid_candidate(self) -> None:
        self.assertEqual(
            council.extract_json_object('{"valid": "{"} and {invalid}'),
            {"valid": "{"},
        )

    def test_negative_review_retains_summary_containing_brace(self) -> None:
        payload = {
            "self_score": 7,
            "self_summary": "Explain the } parser branch.",
            "peer_scores": {"Response B": 8},
            "peer_summaries": {"Response B": "Missing opening { handling."},
        }
        actual = council.parse_negative_scores(json.dumps(payload), ["Response B"])
        self.assertEqual(actual["scores"], {"self": 7.0, "peers": {"Response B": 8.0}})
        self.assertEqual(actual["summaries"]["self"], payload["self_summary"])
        self.assertEqual(actual["summaries"]["peers"], payload["peer_summaries"])


class JsonExtractionCallerTests(unittest.IsolatedAsyncioTestCase):
    async def test_refinement_preserves_valid_coding_prompt(self) -> None:
        payload = {
            "intent": "Debug parser",
            "clarifying_questions": ["Does } close the current scope?"],
            "refined_prompt": "Explain why the parser stops at the closing } character.",
        }
        provider = AsyncMock(return_value={"content": json.dumps(payload)})
        with patch.object(council, "query_model", provider):
            actual = await council.generate_prompt_refinement("Help debug my parser")
        self.assertEqual(actual, payload)
        provider.assert_awaited_once()

    async def test_reference_selection_retains_valid_choice_and_reason(self) -> None:
        candidate = ReferenceCandidate(
            candidate_id=1,
            skill_id="parser",
            skill_description="Parsing guidance",
            path="references/parser.md",
            section_id="1",
            heading="Strings",
            summary="Parsing JSON strings",
            chars=100,
            mentions=[],
            file_summary="Parser references",
        )
        payload = {"selected": [{"id": 1, "reason": "Handle literal } in strings."}]}
        provider = AsyncMock(return_value={"content": json.dumps(payload)})
        with (
            patch.object(council, "SKILLS_TYPESAFE_ENABLED", False),
            patch.object(council, "query_model", provider),
        ):
            actual = await council.select_skill_references("Fix parser", [candidate])
        self.assertTrue(actual["parsed"])
        self.assertIsNone(actual["error"])
        self.assertEqual(actual["selected"], [1])
        self.assertEqual(actual["reasons"], {1: "Handle literal } in strings."})
        provider.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
