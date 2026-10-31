"""Offline empty-completion regressions through HTTPX and real council stages."""

import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, call, patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import council, openrouter  # noqa: E402
from backend.trace import TraceCollector  # noqa: E402


class EmptyContentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.sleep = AsyncMock()
        for patcher in (
            patch.object(council, "SYNTHESIS_BACKEND", "openrouter"),
            patch.object(
                openrouter,
                "model_limits",
                AsyncMock(
                    return_value={"context_length": 8192, "max_completion_tokens": 2048}
                ),
            ),
            patch.object(openrouter.httpx, "AsyncClient", return_value=self.client),
            patch.object(openrouter.asyncio, "sleep", self.sleep),
            patch.object(openrouter, "OPENROUTER_MAX_RETRIES", 2),
            patch.object(openrouter, "OPENROUTER_RETRY_BASE_SECONDS", 0.5),
            patch.object(openrouter, "OPENROUTER_RETRY_JITTER", 0.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, content: Any, finish_reason: str = "stop") -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": content,
                            "reasoning_details": [{"type": "reasoning.text"}],
                        },
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"completion_tokens": 10},
            },
            request=httpx.Request("POST", "https://provider.invalid/chat/completions"),
        )

    async def test_blank_strings_retry_then_recover(self) -> None:
        for blank in ("", " \t\r\n", "\u2003\u00a0"):
            with self.subTest(blank=repr(blank)):
                self.client.post.reset_mock()
                self.sleep.reset_mock()
                self.client.post.side_effect = [
                    self.response(blank),
                    self.response("Recovered"),
                ]
                result = await openrouter.query_model("fake", [])
                self.assertEqual(result["content"], "Recovered")
                self.assertEqual(self.client.post.await_count, 2)
                self.sleep.assert_awaited_once_with(0.5)

    async def test_exhausted_empty_completions_return_failure_with_bounded_retries(
        self,
    ) -> None:
        self.client.post.side_effect = [self.response("")] * 3
        self.assertIsNone(await openrouter.query_model("fake", []))
        self.assertEqual(self.client.post.await_count, 3)
        self.assertEqual(self.sleep.await_args_list, [call(0.5), call(1.0)])

    async def test_zero_retries_does_not_add_an_attempt(self) -> None:
        self.client.post.side_effect = [self.response(" \n")]
        with patch.object(openrouter, "OPENROUTER_MAX_RETRIES", 0):
            self.assertIsNone(await openrouter.query_model("fake", []))
        self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_nonblank_string_and_metadata_are_preserved_exactly(self) -> None:
        content = (
            "\n    indented code\n\nParagraph with a Markdown break  \nnext line\n"
        )
        self.client.post.side_effect = [self.response(content)]
        result = await openrouter.query_model("fake", [])
        self.assertEqual(
            result,
            {
                "content": content,
                "reasoning_details": [{"type": "reasoning.text"}],
                "finish_reason": "stop",
                "usage": {"completion_tokens": 10},
                "context_calls": 0,
                "continuations": 0,
            },
        )
        self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_existing_missing_and_blank_list_content_still_retry(self) -> None:
        for content in (None, [], [" ", {"type": "text", "text": "\n"}]):
            with self.subTest(content=content):
                self.client.post.reset_mock()
                self.sleep.reset_mock()
                self.client.post.side_effect = [
                    self.response(content),
                    self.response("OK"),
                ]
                self.assertEqual(
                    (await openrouter.query_model("fake", []))["content"], "OK"
                )
                self.assertEqual(self.client.post.await_count, 2)
                self.sleep.assert_awaited_once_with(0.5)

    async def test_nonempty_list_normalization_is_unchanged(self) -> None:
        self.client.post.side_effect = [
            self.response(["  Answer", {"text": " here\n"}])
        ]
        self.assertEqual(
            (await openrouter.query_model("fake", []))["content"], "Answer here"
        )
        self.sleep.assert_not_awaited()

    async def test_empty_reasoning_only_attempt_is_traced_as_failure(self) -> None:
        trace = TraceCollector()
        self.client.post.side_effect = [
            self.response("", "length"),
            self.response("Answer"),
        ]
        result = await openrouter.query_model(
            "fake", [], trace=trace, trace_stage="stage1"
        )
        self.assertEqual(result["content"], "Answer")
        events = [
            event for event in trace.to_dict()["events"] if event["kind"] == "llm_call"
        ]
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0]["error"], "No text content in API response from fake"
        )
        self.assertEqual(events[0]["context"]["attempt"], 1)
        self.assertEqual(
            events[0]["response"]["body"]["choices"][0]["finish_reason"], "length"
        )
        self.assertEqual(
            events[0]["response"]["body"]["choices"][0]["content_length"], 0
        )
        self.assertIsNone(events[1]["error"])
        self.assertEqual(events[1]["context"]["attempt"], 2)

    async def test_stage1_marks_exhausted_blank_answer_failed(self) -> None:
        self.client.post.side_effect = [self.response(" \n")] * 3
        progress = AsyncMock()
        with patch.object(council, "COUNCIL_MODELS", ["fake"]):
            results = await council.stage1_collect_responses("Q", progress=progress)
        self.assertEqual(
            results,
            [
                {
                    "model": "fake",
                    "response": "[Model failed to respond]",
                    "failed": True,
                }
            ],
        )
        self.assertEqual(progress.await_args_list[-1], call("fake", "failed", None))
        self.assertEqual(self.client.post.await_count, 3)

    async def test_deliberation_preserves_original_after_exhausted_blank_revisions(
        self,
    ) -> None:
        self.client.post.side_effect = [self.response("", "length")] * 3
        progress = AsyncMock()
        results = await council.stage_deliberation(
            "Q",
            [{"model": "fake", "response": "Useful original answer"}],
            ["Response A"],
            {"Response A": "fake"},
            progress=progress,
        )
        self.assertEqual(results[0]["revised"], "Useful original answer")
        self.assertEqual(progress.await_args_list[-1], call("fake", "failed", None))
        self.assertEqual(self.client.post.await_count, 3)

    async def test_synthesis_recovers_after_empty_attempt(self) -> None:
        self.client.post.side_effect = [
            self.response(""),
            self.response("Final answer"),
        ]
        result = await council.stage3_synthesize_final(
            "Q",
            [{"model": "fake", "response": "Original answer"}],
            [],
            {"Response A": "fake"},
        )
        self.assertEqual(result["response"], "Final answer")
        self.assertEqual(self.client.post.await_count, 2)


if __name__ == "__main__":
    unittest.main()
