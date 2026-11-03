"""Explicit provider failure/tool outcomes must not become council evidence."""

import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import council, openrouter
from backend.trace import TraceCollector


class TerminalOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.sleep = AsyncMock()
        for patcher in (
            patch.object(council, "SYNTHESIS_BACKEND", "openrouter"),
            patch.object(openrouter.httpx, "AsyncClient", return_value=self.client),
            patch.object(openrouter.asyncio, "sleep", self.sleep),
            patch.object(openrouter, "OPENROUTER_MAX_RETRIES", 2),
            patch.object(
                openrouter,
                "model_limits",
                AsyncMock(
                    return_value={"context_length": 8192, "max_completion_tokens": 2048}
                ),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(
        self,
        reason: str | None = "stop",
        *,
        text: str = "Partial private text",
        error_at: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> httpx.Response:
        choice = {"message": {"content": text}, "finish_reason": reason}
        if tool_calls is not None:
            choice["message"]["tool_calls"] = tool_calls
        body = {"choices": [choice], "usage": {"completion_tokens": 2}}
        if error_at:
            target = body if error_at == "body" else choice
            target["error"] = {"code": 500, "message": "Private provider error"}
        return httpx.Response(
            200,
            json=body,
            request=httpx.Request("POST", "https://provider.invalid/chat/completions"),
        )

    async def test_explicit_terminal_outcomes_never_retry_or_accept_partial_text(
        self,
    ) -> None:
        for reason in ("content_filter", "error", "tool_calls"):
            for text in ("Partial private text", ""):
                with self.subTest(reason=reason, text=bool(text)):
                    self.client.post.reset_mock()
                    self.sleep.reset_mock()
                    self.client.post.side_effect = [
                        self.response(reason, text=text),
                        self.response(text="Must not retry"),
                    ]
                    self.assertIsNone(
                        await openrouter.query_model("fake", [], trace_stage="stage1")
                    )
                    self.assertEqual(self.client.post.await_count, 1)
                    self.sleep.assert_not_awaited()

    async def test_embedded_errors_and_unfinished_tool_calls_are_terminal(self) -> None:
        responses = [
            self.response(error_at="body"),
            self.response(error_at="choice"),
            self.response(
                tool_calls=[
                    {
                        "id": "call",
                        "type": "function",
                        "function": {"name": "fake", "arguments": "{}"},
                    }
                ]
            ),
        ]
        for response in responses:
            with self.subTest(body=response.json()):
                self.client.post.reset_mock()
                self.client.post.side_effect = [
                    response,
                    self.response(text="Must not retry"),
                ]
                self.assertIsNone(await openrouter.query_model("fake", []))
                self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_explicit_failure_is_traced_without_content_or_error_text(
        self,
    ) -> None:
        trace = TraceCollector()
        self.client.post.side_effect = [self.response("error", error_at="choice")]
        self.assertIsNone(
            await openrouter.query_model("fake", [], trace=trace, trace_stage="stage3")
        )
        events = [
            event for event in trace.to_dict()["events"] if event["kind"] == "llm_call"
        ]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["error"])
        self.assertEqual(
            events[0]["response"]["body"]["choices"][0]["finish_reason"], "error"
        )
        self.assertNotIn("Partial private text", str(trace.to_dict()))
        self.assertNotIn("Private provider error", str(trace.to_dict()))

    async def test_stage1_marks_filtered_member_failed(self) -> None:
        self.client.post.side_effect = [self.response("content_filter")]
        progress = AsyncMock()
        with patch.object(council, "COUNCIL_MODELS", ["fake"]):
            result = await council.stage1_collect_responses(
                "Question", progress=progress
            )
        self.assertTrue(result[0]["failed"])
        self.assertEqual(result[0]["response"], "[Model failed to respond]")
        progress.assert_awaited_with("fake", "failed", None)
        self.assertEqual(self.client.post.await_count, 1)

    async def test_failure_after_length_continuation_does_not_return_prior_fragment(
        self,
    ) -> None:
        self.client.post.side_effect = [
            self.response("length", text="Earlier fragment"),
            self.response("content_filter"),
        ]
        self.assertIsNone(
            await openrouter.query_model("fake", [], trace_stage="stage3")
        )
        self.assertEqual(self.client.post.await_count, 2)
        self.sleep.assert_not_awaited()

    async def test_success_missing_and_provider_specific_reasons_remain_compatible(
        self,
    ) -> None:
        for reason in ("stop", None, "end_turn"):
            with self.subTest(reason=reason):
                self.client.post.reset_mock()
                self.client.post.side_effect = [
                    self.response(reason, text="  Complete answer\n", tool_calls=[])
                ]
                result = await openrouter.query_model("fake", [])
                self.assertEqual(result["content"], "  Complete answer\n")
                self.assertEqual(result["finish_reason"], reason)
                self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_length_continuation_still_completes_normally(self) -> None:
        self.client.post.side_effect = [
            self.response("length", text="Part one "),
            self.response("stop", text="part two"),
        ]
        result = await openrouter.query_model("fake", [], trace_stage="stage1")
        self.assertEqual(result["content"], "Part one part two")
        self.assertEqual(result["continuations"], 1)
        self.assertEqual(self.client.post.await_count, 2)
        self.sleep.assert_not_awaited()

    async def test_error_only_body_is_not_retried_as_a_malformed_success(self) -> None:
        self.client.post.side_effect = [
            httpx.Response(
                200,
                json={"error": {"code": 500, "message": "Private provider error"}},
                request=httpx.Request(
                    "POST", "https://provider.invalid/chat/completions"
                ),
            )
        ]
        self.assertIsNone(await openrouter.query_model("fake", []))
        self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_missing_finish_reason_remains_compatible(self) -> None:
        payload = self.response(text="Complete answer").json()
        del payload["choices"][0]["finish_reason"]
        self.client.post.side_effect = [
            httpx.Response(
                200,
                json=payload,
                request=httpx.Request(
                    "POST", "https://provider.invalid/chat/completions"
                ),
            )
        ]
        result = await openrouter.query_model("fake", [])
        self.assertEqual(result["content"], "Complete answer")
        self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_synthesis_does_not_publish_partial_error_text(self) -> None:
        self.client.post.side_effect = [self.response("error")]
        with self.assertRaisesRegex(RuntimeError, "failed to generate final synthesis"):
            await council.stage3_synthesize_final(
                "Question",
                [{"model": "fake", "response": "Original answer"}],
                [],
                {"Response A": "fake"},
            )
        self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_deliberation_retains_original_after_filtered_revision(self) -> None:
        self.client.post.side_effect = [self.response("content_filter")]
        progress = AsyncMock()
        result = await council.stage_deliberation(
            "Question",
            [{"model": "fake", "response": "Original answer"}],
            ["Response A"],
            {"Response A": "fake"},
            progress=progress,
        )
        self.assertEqual(result[0]["revised"], "Original answer")
        progress.assert_awaited_with("fake", "failed", None)
        self.assertEqual(self.client.post.await_count, 1)
        self.sleep.assert_not_awaited()
