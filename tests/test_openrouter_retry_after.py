"""Offline provider cooldown regressions using real HTTPX responses."""

import asyncio
import os
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import openrouter  # noqa: E402
from backend.trace import TraceCollector  # noqa: E402


class RetryAfterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patcher = patch.object(
            openrouter,
            "model_limits",
            AsyncMock(
                return_value={"context_length": 8192, "max_completion_tokens": 2048}
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (
            ("OPENROUTER_MAX_RETRIES", 2),
            ("OPENROUTER_RETRY_BASE_SECONDS", 0.5),
            ("OPENROUTER_RETRY_MAX_SECONDS", 8.0),
            ("OPENROUTER_RETRY_JITTER", 0.0),
        ):
            patcher = patch.object(openrouter, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, status: int, retry_after: str | None = None) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"Retry-After": retry_after} if retry_after is not None else {},
            json={"choices": [{"message": {"content": "Recovered answer"}}]}
            if status == 200
            else {"error": {"message": "Temporary failure"}},
            request=httpx.Request("POST", "https://provider.invalid/chat/completions"),
        )

    async def query(self, responses: list[Any]) -> tuple[Any, AsyncMock, AsyncMock]:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = responses
        with (
            patch.object(openrouter.httpx, "AsyncClient", return_value=client),
            patch.object(openrouter.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await openrouter.query_model(
                "fake", [{"role": "user", "content": "Q"}]
            )
        return result, sleep, client.post

    async def test_provider_cooldown_is_not_capped_by_exponential_max(self) -> None:
        for status in (429, 503):
            with self.subTest(status=status):
                result, sleep, post = await self.query(
                    [self.response(status, "60"), self.response(200)]
                )
                self.assertEqual(result["content"], "Recovered answer")
                sleep.assert_awaited_once_with(60.0)
                self.assertEqual(post.await_count, 2)

    async def test_http_date_cooldown(self) -> None:
        now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        date = format_datetime(
            datetime(2026, 10, 21, 12, 1, tzinfo=timezone.utc), usegmt=True
        )
        with patch.object(openrouter.time, "time", return_value=now.timestamp()):
            result, sleep, _ = await self.query(
                [self.response(503, date), self.response(200)]
            )
        self.assertIsNotNone(result)
        sleep.assert_awaited_once_with(60.0)

    async def test_obsolete_http_date_uses_utc(self) -> None:
        now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        with patch.object(openrouter.time, "time", return_value=now.timestamp()):
            _, sleep, _ = await self.query(
                [self.response(503, "Wed Oct 21 12:01:00 2026"), self.response(200)]
            )
        sleep.assert_awaited_once_with(60.0)

    async def test_jittered_backoff_remains_the_floor(self) -> None:
        with (
            patch.object(openrouter, "OPENROUTER_RETRY_JITTER", 0.2),
            patch.object(openrouter.random, "uniform", return_value=0.1),
        ):
            _, sleep, _ = await self.query(
                [self.response(429, "0"), self.response(200)]
            )
        sleep.assert_awaited_once_with(0.6)

    async def test_missing_invalid_and_expired_hints_keep_backoff(self) -> None:
        for hint in (
            None,
            "",
            "garbage",
            "-2",
            "1.5",
            "NaN",
            "inf",
            "1e3",
            "9" * 400,
            "Wed, 32 Oct 2026 12:00:00 GMT",
            "Wed, 21 Oct 2015 07:28:00 GMT",
            "0",
        ):
            with self.subTest(hint=hint):
                result, sleep, _ = await self.query(
                    [self.response(429, hint), self.response(200)]
                )
                self.assertIsNotNone(result)
                sleep.assert_awaited_once_with(0.5)

    async def test_short_hint_does_not_shorten_backoff(self) -> None:
        with patch.object(openrouter, "OPENROUTER_RETRY_BASE_SECONDS", 4.0):
            _, sleep, _ = await self.query(
                [self.response(429, "1"), self.response(200)]
            )
        sleep.assert_awaited_once_with(4.0)

    async def test_cooldown_applies_only_to_the_next_attempt(self) -> None:
        _, sleep, post = await self.query(
            [self.response(429, "60"), self.response(503), self.response(200)]
        )
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [60.0, 1.0])
        self.assertEqual(post.await_count, 3)

    async def test_new_hint_replaces_previous_hint(self) -> None:
        _, sleep, _ = await self.query(
            [self.response(429, "60"), self.response(503, "120"), self.response(200)]
        )
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list], [60.0, 120.0]
        )

    async def test_retry_budget_does_not_sleep_after_last_failure(self) -> None:
        result, sleep, post = await self.query([self.response(429, "60")] * 3)
        self.assertIsNone(result)
        self.assertEqual(post.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [60.0, 60.0])
        with patch.object(openrouter, "OPENROUTER_MAX_RETRIES", 0):
            result, sleep, post = await self.query([self.response(429, "60")])
        self.assertIsNone(result)
        sleep.assert_not_awaited()
        self.assertEqual(post.await_count, 1)

    async def test_nonretryable_errors_do_not_retry_despite_hint(self) -> None:
        for status in (400, 401, 402, 403):
            with self.subTest(status=status):
                result, sleep, post = await self.query([self.response(status, "60")])
                self.assertIsNone(result)
                sleep.assert_not_awaited()
                self.assertEqual(post.await_count, 1)

    async def test_transport_and_malformed_response_keep_existing_retries(self) -> None:
        for failure in (
            httpx.ConnectError("offline"),
            httpx.Response(
                200,
                json={},
                request=httpx.Request("POST", "https://provider.invalid/chat"),
            ),
        ):
            with self.subTest(failure=failure):
                result, sleep, post = await self.query([failure, self.response(200)])
                self.assertIsNotNone(result)
                sleep.assert_awaited_once_with(0.5)
                self.assertEqual(post.await_count, 2)

    async def test_cancellation_during_real_cooldown_stops_retries(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = self.response(429, "60")
        entered = asyncio.Event()
        real_sleep = asyncio.sleep
        delays = []

        async def observe_sleep(delay: float) -> None:
            delays.append(delay)
            entered.set()
            await real_sleep(delay)

        trace = TraceCollector()
        with (
            patch.object(openrouter.httpx, "AsyncClient", return_value=client),
            patch.object(openrouter.asyncio, "sleep", side_effect=observe_sleep),
        ):
            task = asyncio.create_task(openrouter.query_model("fake", [], trace=trace))
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
        self.assertEqual(delays, [60.0])
        self.assertEqual(client.post.await_count, 1)
        calls = [
            event for event in trace.to_dict()["events"] if event["kind"] == "llm_call"
        ]
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
