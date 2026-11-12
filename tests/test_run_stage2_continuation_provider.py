"""Offline wire integration for explicitly selected Stage 2 continuations."""

import asyncio
import copy
import inspect
import json
import os
import traceback
import unittest
from typing import Any
from unittest.mock import patch

import httpx
import test_run_stage1_provider as wire
import test_run_stage2_continuation_inputs as continuation_fixtures

from backend import run_admission, run_cancellation, run_inspection, run_stage2_results
from backend import run_stage1_provider as shared
from backend import run_stage2_continuation_execution as execution
from backend import run_stage2_continuation_provider as provider


class FirstContinuationProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = continuation_fixtures.Stage2ContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run_id = self.fixture.run
        self.path = self.fixture.path
        self.models = self.fixture.models
        self.requests = []
        for model in self.models:
            self.fixture.execute(model)
            self.requests.append(self.fixture.bind(model))
        self.initial = copy.deepcopy(self.saved()["stage2_execution"])
        self.calls: list[httpx.Request] = []
        self.addCleanup(self.assert_released)

    def saved(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def assert_released(self) -> None:
        self.assertFalse(any(key[0] == self.run_id for key in execution._active))
        self.assertFalse(any(key[0] == self.run_id for key in run_cancellation._tasks))
        self.assertFalse(
            any(key[0] == self.run_id for key in run_cancellation._signalled)
        )

    def member(self, index: int = 0) -> dict[str, Any]:
        return run_inspection.inspect(self.run_id)["stage2_continuation_execution"][
            "members"
        ][index]

    def retry_token(self, index: int = 0) -> str:
        member = self.member(index)
        self.assertEqual(member["phase"], "needs_retry")
        self.assertIsNotNone(member["retry_token"])
        return member["retry_token"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        state = self.saved()["stage2_continuation_execution"]
        self.assertEqual(state["provider_key"], "openrouter-stage2-continuation-v1")
        model = json.loads(request.content)["model"]
        op = next(op for op in state["operations"] if op["model"] == model)
        self.assertEqual(op["turn"], 1)
        self.assertEqual(op["phase"], "in_flight")
        self.assertIsNone(op["result"])
        self.assertIn((self.run_id, op["attempt"]), execution._active)
        self.assertEqual(self.saved()["stage2_execution"], self.initial)
        return httpx.Response(200, json=wire.completion())

    async def execute(self, handler: Any = None, **kwargs: Any) -> Any:
        return await provider.execute_openrouter(
            self.run_id,
            kwargs.pop("model", self.models[0]),
            turn=kwargs.pop("turn", 1),
            api_key=kwargs.pop("api_key", wire.SECRET),
            expected_revision=kwargs.pop("expected_revision", 6),
            _transport=kwargs.pop(
                "_transport", httpx.MockTransport(handler or self.handler)
            ),
            **kwargs,
        )

    async def test_exact_saved_wire_and_durable_separate_identity(self) -> None:
        before = self.saved()
        client = httpx.AsyncClient
        with (
            patch.object(shared.httpx, "AsyncClient", wraps=client) as factory,
            patch.dict(
                os.environ,
                {
                    "OPENROUTER_API_KEY": "unused-secret",
                    "OPENROUTER_MAX_RETRIES": "99",
                    "HTTPS_PROXY": "http://unused.invalid",
                },
            ),
        ):
            result = await self.execute()
        self.assertIsInstance(result, execution.MemberResponse)
        self.assertEqual(result.content, wire.ANSWER)
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), self.fixture.inputs.endpoint)
        payload = json.loads(request.content)
        self.assertEqual(payload, self.requests[0].payload())
        self.assertEqual(
            payload["messages"][-2]["content"], continuation_fixtures.PARTIAL["content"]
        )
        self.assertEqual(
            payload["messages"][-2]["reasoning_details"],
            continuation_fixtures.PARTIAL["reasoning_details"],
        )
        self.assertEqual(request.headers["authorization"], "Bearer " + wire.SECRET)
        self.assertEqual(request.headers["accept-encoding"], "identity")
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(
            factory.call_args.kwargs["timeout"], self.fixture.inputs.timeout_seconds
        )
        self.assertIs(factory.call_args.kwargs["trust_env"], False)
        self.assertIs(factory.call_args.kwargs["follow_redirects"], False)
        after = self.saved()
        state = after.pop("stage2_continuation_execution")
        self.assertEqual(after, before)
        self.assertEqual(state["provider_key"], provider.PROVIDER_KEY)
        self.assertNotIn(wire.SECRET, self.path.read_text())
        self.assertNotIn("unused-secret", self.path.read_text())

    async def test_default_transport_has_no_automatic_retries(self) -> None:
        original_transport = httpx.AsyncHTTPTransport

        async def handle(transport: Any, request: httpx.Request) -> httpx.Response:
            return await self.handler(request)

        with (
            patch(
                "httpx._client.AsyncHTTPTransport", wraps=original_transport
            ) as factory,
            patch.object(original_transport, "handle_async_request", new=handle),
        ):
            await self.execute(_transport=None)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.kwargs.get("retries", 0), 0)
        self.assertEqual(len(self.calls), 1)

    async def test_stop_and_length_remain_independent_unaggregated_chunks(self) -> None:
        stopped = await self.execute()
        body = wire.completion()
        body["choices"][0]["finish_reason"] = "length"
        body["choices"][0]["message"]["content"] = " another partial "

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, json=body)

        partial = await self.execute(handler, model=self.models[1])
        self.assertEqual(partial.content, " another partial ")
        self.assertEqual(partial.finish_reason, "length")
        self.assertEqual(self.member(1)["phase"], "needs_continuation")
        self.assertIsNone(self.member(1)["retry_token"])
        self.assertEqual(await self.execute(), stopped)
        self.assertEqual(await self.execute(model=self.models[1]), partial)
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(run_stage2_results.load_complete_results(self.run_id))
        self.assertEqual(self.saved()["stage2_execution"], self.initial)
        self.assertTrue(
            all(
                op["turn"] == 1
                for op in self.saved()["stage2_continuation_execution"]["operations"]
            )
        )

    async def test_rotated_key_replay_is_detached_and_read_only(self) -> None:
        body = wire.completion(usage={"nested": [1, None]})
        body["choices"][0]["message"]["reasoning_details"] = [{"nested": ["private"]}]
        result = await self.execute(lambda request: httpx.Response(200, json=body))
        expected = result.model_dump(mode="json")
        result.usage["nested"].append("mutation")
        result.reasoning_details[0]["nested"].append("mutation")
        before = self.fixture.fixture.files()
        with (
            patch.object(run_admission, "_write", side_effect=AssertionError("write")),
            patch.object(
                shared.httpx, "AsyncClient", side_effect=AssertionError("HTTP replay")
            ),
        ):
            replay = await self.execute(api_key="rotated-synthetic-key")
        self.assertEqual(replay.model_dump(mode="json"), expected)
        self.assertEqual(self.fixture.fixture.files(), before)
        self.assertNotIn("rotated-synthetic-key", self.path.read_text())

    async def test_uncertain_retry_consent_is_isolated_to_member(self) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            raise httpx.ReadError(wire.SECRET + " private response", request=request)

        for model in self.models:
            with self.assertRaises(execution.ProviderFailure) as raised:
                await self.execute(fail, model=model)
            self.assertNotIn(
                wire.SECRET, "".join(traceback.format_exception(raised.exception))
            )
        first, second = self.retry_token(), self.retry_token(1)
        self.assertNotEqual(first, second)
        before = self.fixture.fixture.files()
        with self.assertRaises(execution.RetryRequired):
            await self.execute(api_key="rotated-synthetic-key")
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute(retry_token=second)
        self.assertEqual(self.fixture.fixture.files(), before)
        self.assertEqual(len(self.calls), 2)
        result = await self.execute(retry_token=first, api_key="rotated-synthetic-key")
        self.assertEqual(
            self.calls[-1].headers["authorization"], "Bearer rotated-synthetic-key"
        )
        self.assertEqual(await self.execute(retry_token=first), result)
        self.assertEqual(self.retry_token(1), second)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.saved()["stage2_execution"], self.initial)

    async def test_explicit_turn_and_valid_credentials_required_before_io(self) -> None:
        parameter = inspect.signature(provider.execute_openrouter).parameters["turn"]
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(parameter.default, inspect.Parameter.empty)
        before = self.fixture.fixture.files()
        with patch.object(
            shared.httpx, "AsyncClient", side_effect=AssertionError("HTTP")
        ):
            for turn in (None, False, True, 0, 2, "1", 1.0):
                with self.subTest(turn=turn), self.assertRaises(ValueError):
                    await self.execute(turn=turn)
            for key in (None, 123, "", " ", "bad\nkey", "nonascii-☃"):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    await self.execute(api_key=key)
        self.assertEqual(self.fixture.fixture.files(), before)
        self.assertEqual(self.calls, [])

    async def test_unselected_callback_identity_cannot_be_adopted(self) -> None:
        async def callback(**kwargs: Any) -> dict[str, Any]:
            return {"content": wire.ANSWER, "finish_reason": "stop"}

        await execution.execute(
            self.run_id,
            self.models[0],
            callback,
            turn=1,
            provider_key="synthetic-continuation-v1",
            expected_revision=6,
        )
        before = self.fixture.fixture.files()
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute()
        self.assertEqual(self.fixture.fixture.files(), before)
        self.assertEqual(self.calls, [])

    async def test_write_failure_prevents_dispatch(self) -> None:
        with patch.object(run_admission, "_write", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertEqual(self.calls, [])

    async def test_redirect_and_retryable_status_do_not_follow_or_repeat(self) -> None:
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
            token = self.retry_token()
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(
            all(
                str(request.url) == self.fixture.inputs.endpoint
                for request in self.calls
            )
        )
        self.assertNotIn(wire.SECRET, self.path.read_text())

    async def test_representative_malformed_and_protocol_responses_require_retry(
        self,
    ) -> None:
        tool = wire.completion()
        tool["choices"][0]["message"]["tool_calls"] = [{"id": "call"}]
        unsupported = wire.completion()
        unsupported["choices"][0]["finish_reason"] = "content_filter"
        cases = [
            b"not JSON " + wire.SECRET.encode(),
            b'{"choices":[],"choices":[]}',
            json.dumps(tool).encode(),
            json.dumps(unsupported).encode(),
            json.dumps(wire.completion(error=None)).encode(),
        ]
        token = None
        for raw in cases:

            def handler(request: httpx.Request) -> httpx.Response:
                self.calls.append(request)
                return httpx.Response(200, content=raw)

            with self.assertRaises(execution.ProviderFailure) as raised:
                await self.execute(handler, retry_token=token)
            self.assertNotIn(
                wire.SECRET, "".join(traceback.format_exception(raised.exception))
            )
            token = self.retry_token()
        self.assertEqual(len(self.calls), len(cases))
        self.assertEqual(self.saved()["stage2_execution"], self.initial)

    async def test_body_limit_closes_stream_without_consuming_stall(self) -> None:
        stream = wire.ChunkStream([b"x" * shared.MAX_RESPONSE_BYTES, b"x"], stall=True)
        with self.assertRaises(execution.ProviderFailure):
            await asyncio.wait_for(
                self.execute(lambda request: httpx.Response(200, stream=stream)), 2
            )
        self.assertTrue(stream.closed)
        self.retry_token()

    async def test_total_deadline_covers_body_read(self) -> None:
        stream = wire.ChunkStream([b"{"], stall=True)
        wait_for = asyncio.wait_for
        timeouts = []

        async def short_wait(awaitable: Any, timeout: float) -> Any:
            timeouts.append(timeout)
            return await wait_for(awaitable, timeout=0.01)

        with patch.object(shared.asyncio, "wait_for", side_effect=short_wait):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute(lambda request: httpx.Response(200, stream=stream))
        self.assertEqual(timeouts, [self.fixture.inputs.timeout_seconds])
        self.assertTrue(stream.closed)
        self.retry_token()

    async def test_task_cancellation_closes_stream_and_requires_consent(self) -> None:
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
        token = self.retry_token()
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        await self.execute(retry_token=token)
        self.assertEqual(self.saved()["stage2_execution"], self.initial)

    async def test_durable_cancellation_preserves_initial_and_prior_chunk(self) -> None:
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
        self.assertEqual(
            execution.load_result(self.run_id, self.models[0], turn=1), retained
        )
        self.assertEqual(self.saved()["stage2_execution"], self.initial)
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.execute(model=self.models[1])

    async def test_deletion_during_body_read_discards_late_chunk(self) -> None:
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
        self.assertNotIn("stage2_continuation_execution", self.saved())
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.execute()
        self.assertEqual(self.path.read_bytes(), tombstone)


class InitialToContinuationProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = continuation_fixtures.Stage2ContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def test_initial_adapter_length_to_explicit_continuation(self) -> None:
        from backend import run_stage2_provider as initial_provider

        fixture = self.fixture
        model = fixture.models[0]
        calls: list[httpx.Request] = []
        partial = copy.deepcopy(continuation_fixtures.PARTIAL)
        body = wire.completion(usage=partial["usage"])
        body["choices"][0].update(
            finish_reason="length",
            message={
                "role": "assistant",
                "content": partial["content"],
                "reasoning_details": partial["reasoning_details"],
            },
        )

        def initial_handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            saved = json.loads(fixture.path.read_text())
            state = saved["stage2_execution"]
            self.assertEqual(state["provider_key"], initial_provider.PROVIDER_KEY)
            self.assertEqual(state["operations"][0]["phase"], "in_flight")
            self.assertNotIn("stage2_continuation_execution", saved)
            return httpx.Response(200, json=body)

        first = await initial_provider.execute_openrouter(
            fixture.run,
            model,
            api_key=wire.SECRET,
            expected_revision=6,
            _transport=httpx.MockTransport(initial_handler),
        )
        self.assertEqual(first.model_dump(mode="json"), partial)
        initial_receipt = json.loads(fixture.path.read_text())["stage2_execution"]
        pinned = fixture.bind(model)

        def continuation_handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            saved = json.loads(fixture.path.read_text())
            state = saved["stage2_continuation_execution"]
            self.assertEqual(state["provider_key"], provider.PROVIDER_KEY)
            self.assertEqual(state["operations"][0]["phase"], "in_flight")
            self.assertEqual(saved["stage2_execution"], initial_receipt)
            return httpx.Response(200, json=wire.completion())

        result = await provider.execute_openrouter(
            fixture.run,
            model,
            turn=1,
            api_key="synthetic-continuation-key",
            expected_revision=6,
            _transport=httpx.MockTransport(continuation_handler),
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            json.loads(calls[0].content), fixture.inputs.requests[0].payload()
        )
        self.assertEqual(json.loads(calls[1].content), pinned.payload())
        self.assertNotEqual(initial_provider.PROVIDER_KEY, provider.PROVIDER_KEY)
        self.assertEqual(result.content, wire.ANSWER)
        self.assertNotEqual(result.content, partial["content"] + wire.ANSWER)
        self.assertIsNone(run_stage2_results.load_complete_results(fixture.run))
        self.assertEqual(
            json.loads(fixture.path.read_text())["stage2_execution"], initial_receipt
        )
        self.assertNotIn(wire.SECRET, fixture.path.read_text())
        self.assertNotIn("synthetic-continuation-key", fixture.path.read_text())
