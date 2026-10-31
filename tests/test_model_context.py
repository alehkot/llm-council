"""Model-specific budgets, bounded reduction, and continuation contracts."""

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import model_context, openrouter
from backend.trace import TraceCollector


class ModelContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.limits = {
            "context_length": 8192,
            "max_completion_tokens": 1024,
            "supported_parameters": [],
        }
        self.call = AsyncMock(
            return_value={
                "content": "answer",
                "finish_reason": "stop",
                "usage": {"total_tokens": 5},
            }
        )
        for attribute, value in (
            ("model_limits", AsyncMock(return_value=self.limits)),
            ("_query_model_once", self.call),
        ):
            patcher = patch.object(openrouter, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_budget_clamps_output_and_omits_unsupported_reasoning(self) -> None:
        await openrouter.query_model("small", [{"role": "user", "content": "Hello"}])
        self.assertEqual(self.call.call_args.kwargs["max_tokens"], 1024)
        self.assertFalse(self.call.call_args.kwargs["reasoning"])
        self.limits.update(
            context_length=1000000,
            max_completion_tokens=100000,
            supported_parameters=["reasoning"],
        )
        await openrouter.query_model("large", [{"role": "user", "content": "Hello"}])
        self.assertEqual(
            self.call.call_args.kwargs["max_tokens"], openrouter.OPENROUTER_MAX_TOKENS
        )
        self.assertTrue(self.call.call_args.kwargs["reasoning"])

    async def test_reduction_preserves_system_boundaries_and_usage(self) -> None:
        self.call.return_value = {
            "content": "Response A: important evidence; do not infer consent.",
            "finish_reason": "stop",
            "usage": {"total_tokens": 5},
        }
        original = [
            {"role": "system", "content": "MANDATORY SAFETY RULES"},
            {
                "role": "user",
                "content": "ORIGINAL QUESTION\n"
                + "Evidence " * 4000
                + "\nREQUIRED OUTPUT FORMAT",
            },
        ]
        trace = TraceCollector()
        result = await openrouter.query_model(
            "small", original, trace=trace, trace_stage="stage3"
        )
        self.assertIsNotNone(result)
        self.assertGreater(result["context_calls"], 0)
        self.assertEqual(result["usage"]["total_tokens"], self.call.await_count * 5)
        final = self.call.call_args.args[1]
        self.assertEqual(final[0], original[0])
        self.assertTrue(final[1]["content"].startswith("ORIGINAL QUESTION"))
        self.assertTrue(final[1]["content"].endswith("REQUIRED OUTPUT FORMAT"))
        self.assertIn("Response A", final[1]["content"])
        self.assertIn("Evidence " * 4000, original[1]["content"])
        for call in self.call.call_args_list:
            self.assertLess(
                model_context.token_bound(call.args[1]) + call.kwargs["max_tokens"],
                8192,
            )

    async def test_oversized_system_fails_without_summarizing_instructions(
        self,
    ) -> None:
        result = await openrouter.query_model(
            "small", [{"role": "system", "content": "RULE " * 3000}]
        )
        self.assertIsNone(result)
        self.call.assert_not_awaited()

    async def test_reduction_limit_prevents_extra_calls(self) -> None:
        with patch.object(openrouter, "OPENROUTER_MAX_CONTEXT_CALLS", 0):
            result = await openrouter.query_model(
                "small", [{"role": "user", "content": "data " * 4000}]
            )
        self.assertIsNone(result)
        self.call.assert_not_awaited()

    async def test_continuation_preserves_reasoning_and_accounts_usage(self) -> None:
        details = [{"type": "reasoning.encrypted", "data": "opaque"}]
        self.call.side_effect = [
            {
                "content": "First ",
                "finish_reason": "length",
                "reasoning_details": details,
                "usage": {"total_tokens": 10},
            },
            {
                "content": "second.",
                "finish_reason": "stop",
                "usage": {"total_tokens": 20},
            },
        ]
        result = await openrouter.query_model(
            "small", [{"role": "user", "content": "Question"}], trace_stage="stage3"
        )
        self.assertEqual(result["content"], "First second.")
        self.assertEqual(result["continuations"], 1)
        self.assertEqual(result["usage"]["total_tokens"], 30)
        self.assertEqual(self.call.call_args.args[1][-2]["reasoning_details"], details)

    async def test_structured_output_and_exhausted_prose_fail(self) -> None:
        self.call.return_value = {"content": "partial", "finish_reason": "length"}
        result = await openrouter.query_model(
            "small",
            [{"role": "user", "content": "Return JSON"}],
            trace_stage="skill_selection",
        )
        self.assertIsNone(result)
        self.assertEqual(self.call.await_count, 1)
        self.call.reset_mock()
        result = await openrouter.query_model(
            "small", [{"role": "user", "content": "Answer"}], trace_stage="stage1"
        )
        self.assertIsNone(result)
        self.assertEqual(
            self.call.await_count, openrouter.OPENROUTER_MAX_CONTINUATIONS + 1
        )

    async def test_unicode_chunking_is_lossless_and_bounded(self) -> None:
        original = '日本語🙂é\n\x00"' * 100
        chunks = model_context.split_text(original, 37)
        self.assertEqual("".join(chunks), original)
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 37 for chunk in chunks))

    async def test_verified_snapshot_and_unknown_model_fallback(self) -> None:
        with (
            patch.object(model_context, "_refresh_after", float("inf")),
            patch.object(model_context, "_catalog", {}),
        ):
            known = await model_context.model_limits("x-ai/grok-4.6")
            unknown = await model_context.model_limits("custom/unknown")
        self.assertEqual(known["context_length"], 500000)
        self.assertEqual(unknown["context_length"], 8192)
