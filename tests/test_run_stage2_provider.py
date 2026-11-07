"""Offline integration of exact ranking inputs, durable guard and bounded wire I/O."""

import asyncio
import json
import os
import traceback
import unittest
from typing import Any
from unittest.mock import patch

import httpx
import test_run_stage1_provider as wire
import test_run_stage2_inputs as fixtures

from backend import (
    run_admission,
    run_cancellation,
    run_inspection,
    run_stage1_continuation_provider,
    run_stage1_provider,
)
from backend import run_stage2_execution as execution
from backend import run_stage2_provider as provider

RANKING = "Рецензия ☃\nFINAL RANKING:\n1. Response A\n"


class Stage2ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # Excluding one answer must not exclude its original author as reviewer.
        self.fixture.complete({"exclude_responses": ["provider/first"]})
        self.inputs = self.fixture.bind()
        self.run_id, self.models = self.fixture.run, self.fixture.models
        self.path = run_admission._path(self.run_id)
        self.calls: list[httpx.Request] = []
        self.before = self.saved()
        self.addCleanup(self.assert_released)

    def saved(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def assert_released(self) -> None:
        for owners in (
            execution._active,
            run_cancellation._tasks,
            run_cancellation._signalled,
        ):
            self.assertFalse(any(key[0] == self.run_id for key in owners))

    def member(self, index: int = 0) -> dict[str, Any]:
        return run_inspection.inspect(self.run_id)["stage2_execution"]["members"][index]

    def token(self, index: int = 0) -> str:
        value = self.member(index)
        self.assertEqual(value["phase"], "needs_retry")
        self.assertIsNotNone(value["retry_token"])
        return value["retry_token"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        record = self.saved()
        state = record.pop("stage2_execution")
        self.assertEqual(record, self.before)
        self.assertEqual(state["provider_key"], provider.PROVIDER_KEY)
        model = json.loads(request.content)["model"]
        op = next(op for op in state["operations"] if op["model"] == model)
        self.assertEqual(op["phase"], "in_flight")
        self.assertIsNone(op["result"])
        self.assertIn((self.run_id, op["attempt"]), execution._active)
        body = wire.completion()
        body["choices"][0]["message"]["content"] = RANKING
        return httpx.Response(200, json=body)

    async def execute(self, handler: Any = None, **kwargs: Any) -> Any:
        return await provider.execute_openrouter(
            self.run_id,
            kwargs.pop("model", self.models[0]),
            api_key=kwargs.pop("api_key", wire.SECRET),
            expected_revision=kwargs.pop("expected_revision", 6),
            _transport=kwargs.pop(
                "_transport", httpx.MockTransport(handler or self.handler)
            ),
            **kwargs,
        )

    async def test_exact_wire_all_reviewers_and_stage_identity(self) -> None:
        client = httpx.AsyncClient
        with (
            patch.object(
                run_stage1_provider.httpx, "AsyncClient", wraps=client
            ) as factory,
            patch.dict(
                os.environ,
                {
                    "OPENROUTER_API_KEY": "unused-key",
                    "OPENROUTER_MAX_RETRIES": "99",
                    "HTTPS_PROXY": "https://unused.invalid",
                },
            ),
        ):
            for model in self.models:
                result = await self.execute(model=model)
                self.assertEqual(result.content, RANKING)
        self.assertEqual(len(self.calls), len(self.models))
        self.assertEqual(self.inputs.active_models, (self.models[1],))
        for request, saved in zip(self.calls, self.inputs.requests):
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), self.inputs.endpoint)
            self.assertEqual(json.loads(request.content), saved.payload())
            self.assertEqual(request.headers["authorization"], "Bearer " + wire.SECRET)
            self.assertEqual(request.headers["accept-encoding"], "identity")
        for call in factory.call_args_list:
            self.assertFalse(call.kwargs["follow_redirects"])
            self.assertFalse(call.kwargs["trust_env"])
            self.assertEqual(call.kwargs["timeout"], self.inputs.timeout_seconds)
        self.assertNotIn(wire.SECRET, self.path.read_text())
        self.assertNotIn("unused-key", self.path.read_text())
        self.assertEqual(
            len(
                {
                    provider.PROVIDER_KEY,
                    run_stage1_provider.PROVIDER_KEY,
                    run_stage1_continuation_provider.PROVIDER_KEY,
                }
            ),
            3,
        )
        record = self.saved()
        record.pop("stage2_execution")
        self.assertEqual(record, self.before)

    async def test_default_transport_never_implicitly_retries(self) -> None:
        transport = httpx.AsyncHTTPTransport

        async def handle(client: Any, request: httpx.Request) -> httpx.Response:
            return await self.handler(request)

        with (
            patch("httpx._client.AsyncHTTPTransport", wraps=transport) as factory,
            patch.object(transport, "handle_async_request", new=handle),
        ):
            await self.execute(_transport=None)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.kwargs.get("retries", 0), 0)
        self.assertEqual(len(self.calls), 1)

    async def test_stopped_nonballot_and_length_are_retained_without_followup(
        self,
    ) -> None:
        for model, finish in zip(self.models, ("stop", "length")):
            body = wire.completion()
            body["choices"][0]["finish_reason"] = finish
            body["choices"][0]["message"]["content"] = (
                " unparsed partial or non-ballot "
            )
            result = await self.execute(
                lambda request: httpx.Response(200, json=body), model=model
            )
            self.assertEqual(result.content, " unparsed partial or non-ballot ")
            self.assertEqual(result.finish_reason, finish)
        self.assertEqual(self.member()["phase"], "complete")
        self.assertEqual(self.member(1)["phase"], "needs_continuation")
        with patch.object(
            httpx, "AsyncClient", side_effect=AssertionError("replay HTTP")
        ):
            for model in self.models:
                await self.execute(model=model)
        record = self.saved()
        state = record.pop("stage2_execution")
        self.assertEqual(len(state["operations"]), 2)
        self.assertEqual(record, self.before)

    async def test_replay_without_live_owner_is_read_only_with_rotated_credential(
        self,
    ) -> None:
        body = wire.completion(usage={"nested": [1, None]})
        body["choices"][0]["message"]["reasoning_details"] = [{"text": "private"}]
        result = await self.execute(lambda request: httpx.Response(200, json=body))
        expected = result.model_dump(mode="json")
        result.usage["nested"].append("mutation")
        before = self.fixture.files()
        with (
            patch.object(execution, "_active", set()),
            patch.object(run_admission, "_write", side_effect=AssertionError("write")),
            patch.object(httpx, "AsyncClient", side_effect=AssertionError("HTTP")),
        ):
            replay = await self.execute(api_key="rotated-key")
        self.assertEqual(replay.model_dump(mode="json"), expected)
        self.assertEqual(self.fixture.files(), before)
        self.assertNotIn("rotated-key", self.path.read_text())

    async def test_uncertainty_requires_exact_stage_and_member_consent(self) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            raise httpx.ReadError(wire.SECRET + " private payload", request=request)

        for model in self.models:
            with self.assertRaises(execution.ProviderFailure) as raised:
                await self.execute(fail, model=model)
            self.assertNotIn(
                wire.SECRET, "".join(traceback.format_exception(raised.exception))
            )
        first, second = self.token(), self.token(1)
        self.assertNotEqual(first, second)
        stage1_token = self.before["stage1_execution"]["operations"][0]["attempt"]
        before = self.fixture.files()
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        for wrong in (stage1_token, second):
            with self.assertRaises(execution.ExecutionConflict):
                await self.execute(retry_token=wrong)
        self.assertEqual(self.fixture.files(), before)
        result = await self.execute(retry_token=first, api_key="rotated-key")
        self.assertEqual(await self.execute(retry_token=first), result)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.token(1), second)
        self.assertEqual(self.calls[-1].headers["authorization"], "Bearer rotated-key")

    async def test_invalid_credential_stale_revision_and_unselected_adapter_do_not_dispatch(
        self,
    ) -> None:
        before = self.fixture.files()
        for key in (None, 123, "", " ", "bad\nkey", "не-ASCII"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.execute(api_key=key)
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(expected_revision=5)
        self.assertEqual(self.fixture.files(), before)

        async def callback(**kwargs: Any) -> dict[str, Any]:
            return {"content": RANKING, "finish_reason": "stop"}

        await execution.execute(
            self.run_id,
            self.models[0],
            callback,
            provider_key="synthetic-reviewer",
            expected_revision=6,
        )
        before = self.fixture.files()
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute()
        self.assertEqual(self.fixture.files(), before)
        self.assertEqual(self.calls, [])

    async def test_failed_attempt_write_prevents_http(self) -> None:
        before = self.fixture.files()
        with patch.object(run_admission, "_write", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.files(), before)

    async def test_redirect_and_retryable_status_never_follow_or_repeat(self) -> None:
        token = None
        for status in (307, 429, 503):

            def handler(request: httpx.Request) -> httpx.Response:
                self.calls.append(request)
                return httpx.Response(
                    status,
                    headers={"Location": "https://unused.invalid", "Retry-After": "0"},
                    text=wire.SECRET,
                )

            with (
                self.subTest(status=status),
                self.assertRaises(execution.ProviderFailure),
            ):
                await self.execute(handler, retry_token=token)
            token = self.token()
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(str(r.url) == self.inputs.endpoint for r in self.calls))
        self.assertNotIn(wire.SECRET, self.path.read_text())

    async def test_malformed_compressed_and_unsupported_output_remain_uncertain(
        self,
    ) -> None:
        tool = wire.completion()
        tool["choices"][0]["message"]["tool_calls"] = [{"id": "call"}]
        unsupported = wire.completion()
        unsupported["choices"][0]["finish_reason"] = "content_filter"
        cases = [
            (b"not JSON " + wire.SECRET.encode(), {}),
            (b'{"choices":[],"choices":[]}', {}),
            (json.dumps(tool).encode(), {}),
            (json.dumps(unsupported).encode(), {}),
            (json.dumps(wire.completion(error=None)).encode(), {}),
            (b"unused", {"Content-Encoding": "gzip"}),
        ]
        token = None
        for raw, headers in cases:

            def handler(request: httpx.Request) -> httpx.Response:
                self.calls.append(request)
                return httpx.Response(
                    200, stream=wire.ChunkStream([raw]), headers=headers
                )

            with self.assertRaises(execution.ProviderFailure) as raised:
                await self.execute(handler, retry_token=token)
            self.assertNotIn(
                wire.SECRET, "".join(traceback.format_exception(raised.exception))
            )
            token = self.token()
        self.assertEqual(len(self.calls), len(cases))

    async def test_response_size_bound_closes_stream(self) -> None:
        stream = wire.ChunkStream(
            [b"x" * run_stage1_provider.MAX_RESPONSE_BYTES, b"x"], stall=True
        )
        with self.assertRaises(execution.ProviderFailure):
            await asyncio.wait_for(
                self.execute(lambda request: httpx.Response(200, stream=stream)), 2
            )
        self.assertTrue(stream.closed)
        self.token()

    async def test_whole_request_deadline_includes_body(self) -> None:
        stream = wire.ChunkStream([b"{"], stall=True)
        wait_for = asyncio.wait_for
        timeouts = []

        async def short_wait(awaitable: Any, timeout: float) -> Any:
            timeouts.append(timeout)
            return await wait_for(awaitable, timeout=0.01)

        with patch.object(
            run_stage1_provider.asyncio, "wait_for", side_effect=short_wait
        ):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute(lambda request: httpx.Response(200, stream=stream))
        self.assertEqual(timeouts, [self.inputs.timeout_seconds])
        self.assertTrue(stream.closed)
        self.token()

    async def test_task_cancellation_releases_owner_but_does_not_authorize_retry(
        self,
    ) -> None:
        stream = wire.ChunkStream([b"{"], stall=True)
        task = asyncio.create_task(
            self.execute(lambda request: httpx.Response(200, stream=stream))
        )
        try:
            await asyncio.wait_for(stream.started.wait(), 2)
            with self.assertRaises(execution.ExecutionPending):
                await self.execute()
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stream.closed)
        token = self.token()
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        await self.execute(retry_token=token)

    async def test_durable_cancellation_retains_prior_review_and_stage1(self) -> None:
        retained = await self.execute()
        stream = wire.ChunkStream([b"{"], stall=True)
        task = asyncio.create_task(
            self.execute(
                lambda request: httpx.Response(200, stream=stream), model=self.models[1]
            )
        )
        try:
            await asyncio.wait_for(stream.started.wait(), 2)
            run_cancellation.request(self.run_id)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(stream.closed)
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "cancelled"
        )
        self.assertEqual(execution.load_result(self.run_id, self.models[0]), retained)
        self.assertEqual(
            self.saved()["stage1_execution"], self.before["stage1_execution"]
        )
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.execute(model=self.models[1])

    async def test_deletion_during_body_read_cannot_resurrect_run(self) -> None:
        release = asyncio.Event()

        class ReleasedStream(wire.ChunkStream):
            async def __aiter__(self):
                self.started.set()
                await release.wait()
                yield json.dumps(wire.completion()).encode()

        stream = ReleasedStream([])
        task = asyncio.create_task(
            self.execute(lambda request: httpx.Response(200, stream=stream))
        )
        try:
            await asyncio.wait_for(stream.started.wait(), 2)
            run_admission.tombstone_conversation(f"cli-{self.run_id}")
            tombstone = self.path.read_bytes()
        finally:
            release.set()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await asyncio.wait_for(task, 2)
        self.assertTrue(stream.closed)
        self.assertNotIn("stage2_execution", self.saved())
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.execute()
        self.assertEqual(self.path.read_bytes(), tombstone)
