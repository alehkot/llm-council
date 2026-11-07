"""Offline OpenRouter wire contract and durable initial Stage 1 boundaries."""

import asyncio
import copy
import gzip
import json
import os
import traceback
import unittest
from typing import Any
from unittest.mock import patch

import httpx
import test_run_stage1_inputs as input_fixtures

from backend import run_admission, run_cancellation, run_inspection
from backend import run_stage1_execution as execution
from backend import run_stage1_provider as provider

SECRET = "synthetic-stage1-secret-DO-NOT-PERSIST"
ANSWER = "  Exact synthetic answer Пример ☃\n"


def completion(**changes: Any) -> dict[str, Any]:
    body = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": ANSWER},
            }
        ]
    }
    body.update(changes)
    return body


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, stall: bool = False) -> None:
        self.chunks = chunks
        self.stall = stall
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class Stage1ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Composition avoids rediscovering all tests belonging to the fixture.
        self.fixture = input_fixtures.Stage1InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.complete()
        self.inputs = self.fixture.bind()
        self.run_id = self.fixture.run
        self.path = run_admission._path(self.run_id)
        self.models = [request.model for request in self.inputs.requests]
        self.calls: list[httpx.Request] = []
        self.addCleanup(self.assert_released)

    def assert_released(self) -> None:
        self.assertFalse(any(key[0] == self.run_id for key in execution._active))
        self.assertFalse(any(key[0] == self.run_id for key in run_cancellation._tasks))
        self.assertFalse(
            any(key[0] == self.run_id for key in run_cancellation._signalled)
        )

    def saved(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def member(self) -> dict[str, Any]:
        return run_inspection.inspect(self.run_id)["stage1_execution"]["members"][0]

    def retry_token(self) -> str:
        member = self.member()
        self.assertEqual(member["phase"], "needs_retry")
        self.assertIsNotNone(member["retry_token"])
        return member["retry_token"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        state = self.saved()["stage1_execution"]
        self.assertEqual(state["provider_key"], "openrouter-stage1-v1")
        operation = state["operations"][0]
        self.assertEqual(operation["phase"], "in_flight")
        self.assertIsNone(operation["result"])
        self.assertIn((self.run_id, operation["attempt"]), execution._active)
        return httpx.Response(200, json=completion())

    async def execute(self, handler: Any = None, **kwargs: Any) -> Any:
        return await provider.execute_openrouter(
            self.run_id,
            kwargs.pop("model", self.models[0]),
            api_key=kwargs.pop("api_key", SECRET),
            expected_revision=kwargs.pop("expected_revision", 3),
            _transport=kwargs.pop(
                "_transport", httpx.MockTransport(handler or self.handler)
            ),
            **kwargs,
        )

    async def test_exact_saved_wire_receipt_before_call_and_ephemeral_credential(
        self,
    ) -> None:
        before = self.saved()
        client = httpx.AsyncClient
        with (
            patch.object(provider.httpx, "AsyncClient", wraps=client) as factory,
            patch.dict(
                os.environ,
                {
                    "OPENROUTER_API_KEY": "must-not-use-environment-secret",
                    "OPENROUTER_MAX_RETRIES": "99",
                    "HTTPS_PROXY": "http://must-not-use.invalid",
                },
            ),
        ):
            result = await self.execute()
        self.assertIsInstance(result, execution.MemberResponse)
        self.assertEqual(result.content, ANSWER)
        self.assertEqual(result.finish_reason, "stop")
        self.assertIsNone(result.usage)
        self.assertIsNone(result.reasoning_details)
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), self.inputs.endpoint)
        self.assertEqual(json.loads(request.content), self.inputs.requests[0].payload())
        self.assertEqual(request.headers["authorization"], "Bearer " + SECRET)
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(request.headers["accept-encoding"], "identity")
        self.assertEqual(factory.call_args.kwargs["timeout"], 120.0)
        self.assertIs(factory.call_args.kwargs["trust_env"], False)
        self.assertIs(factory.call_args.kwargs["follow_redirects"], False)
        after = self.saved()
        state = after.pop("stage1_execution")
        self.assertEqual(after, before)
        self.assertEqual(state["provider_key"], provider.PROVIDER_KEY)
        self.assertEqual(state["operations"][0]["phase"], "complete")
        self.assertNotIn(SECRET, self.path.read_text())
        self.assertNotIn("must-not-use-environment-secret", self.path.read_text())

    async def test_default_transport_has_zero_retries(self) -> None:
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

    async def test_replay_with_rotated_credential_is_read_only_and_detached(
        self,
    ) -> None:
        body = completion(usage={"prompt_tokens": 7, "nested": [1, None]})
        body["choices"][0]["message"]["reasoning_details"] = [
            {"type": "synthetic", "nested": {"items": ["private"]}}
        ]
        body["unknown_metadata"] = "ignored"
        body["choices"][0]["unknown_metadata"] = {"ignored": True}

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, json=body)

        result = await self.execute(handler)
        self.assertEqual(result.usage, body["usage"])
        self.assertEqual(
            result.reasoning_details, body["choices"][0]["message"]["reasoning_details"]
        )
        expected = result.model_dump(mode="json")
        result.usage["nested"].append("mutated")
        result.reasoning_details[0]["nested"]["items"].append("mutated")
        before = self.fixture.files()
        with (
            patch.object(run_admission, "_write", side_effect=AssertionError("write")),
            patch.object(
                provider.httpx, "AsyncClient", side_effect=AssertionError("HTTP replay")
            ),
        ):
            repeated = await self.execute(api_key="rotated-synthetic-key")
        self.assertEqual(repeated.model_dump(mode="json"), expected)
        self.assertEqual(self.fixture.files(), before)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("unknown_metadata", self.path.read_text())
        self.assertNotIn("rotated-synthetic-key", self.path.read_text())

    async def test_length_is_saved_without_continuation_or_repeat(self) -> None:
        body = completion()
        body["choices"][0]["finish_reason"] = "length"

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, json=body)

        result = await self.execute(handler)
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(self.member()["phase"], "needs_continuation")
        self.assertIsNone(self.member()["retry_token"])
        self.assertEqual(await self.execute(), result)
        self.assertEqual(len(self.calls), 1)

    async def test_uncertain_transport_error_needs_explicit_retry_with_rotated_key(
        self,
    ) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            raise httpx.ReadError(SECRET + " private response", request=request)

        with self.assertRaises(execution.ProviderFailure) as error:
            await self.execute(fail)
        rendered = "".join(traceback.format_exception(error.exception))
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn("private response", rendered)
        token = self.retry_token()
        before = self.path.read_bytes()
        with self.assertRaises(execution.RetryRequired):
            await self.execute(api_key="rotated-synthetic-key")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        result = await self.execute(api_key="rotated-synthetic-key", retry_token=token)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            self.calls[-1].headers["authorization"], "Bearer rotated-synthetic-key"
        )
        self.assertEqual(await self.execute(retry_token=token), result)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn(SECRET, self.path.read_text())
        self.assertNotIn("rotated-synthetic-key", self.path.read_text())

    async def test_http_errors_and_redirects_never_follow_or_retry(self) -> None:
        token = None
        for status in (201, 204, 301, 302, 307, 308, 400, 401, 408, 429, 500, 503):
            with self.subTest(status=status):

                def handler(request: httpx.Request) -> httpx.Response:
                    self.calls.append(request)
                    return httpx.Response(
                        status,
                        headers={
                            "Location": "https://must-not-follow.invalid",
                            "Retry-After": "0",
                        },
                        text=SECRET + " provider error body",
                    )

                count = len(self.calls)
                with self.assertRaises(execution.ProviderFailure) as error:
                    await self.execute(handler, retry_token=token)
                self.assertEqual(len(self.calls), count + 1)
                self.assertEqual(str(self.calls[-1].url), self.inputs.endpoint)
                rendered = "".join(traceback.format_exception(error.exception))
                self.assertNotIn(SECRET, rendered)
                self.assertNotIn("provider error body", rendered)
                self.assertNotIn(SECRET, self.path.read_text())
                token = self.retry_token()

    async def test_malformed_and_unsupported_responses_remain_uncertain(self) -> None:
        cases: list[Any] = [
            None,
            [],
            {},
            {"choices": []},
            {"choices": [None]},
            completion(error=None),
            completion(error={"message": SECRET}),
        ]
        doubled = completion()
        doubled["choices"].append(copy.deepcopy(doubled["choices"][0]))
        cases.append(doubled)
        for reason in (None, "tool_calls", "content_filter", "error", "", 1):
            body = completion()
            body["choices"][0]["finish_reason"] = reason
            cases.append(body)
        for message in (
            None,
            [],
            {},
            {"role": "user", "content": ANSWER},
            {"content": ANSWER},
        ):
            body = completion()
            body["choices"][0]["message"] = message
            cases.append(body)
        for content in (None, "", " \t\n", 1, ["text"], {"text": "text"}):
            body = completion()
            body["choices"][0]["message"]["content"] = content
            cases.append(body)
        for field, value in (
            ("tool_calls", [{"id": "call"}]),
            ("function_call", {"name": "tool"}),
            ("function_call", {}),
            ("refusal", "refused"),
            ("reasoning_details", "reasoning"),
            ("reasoning_details", ["not an object"]),
        ):
            body = completion()
            body["choices"][0]["message"][field] = value
            cases.append(body)
        for target in ("choice", "message"):
            body = completion()
            node = body["choices"][0]
            if target == "message":
                node = node["message"]
            node["error"] = None
            cases.append(body)
        cases.extend(completion(usage=value) for value in ([], "usage", 3))
        raw_cases = [json.dumps(body).encode() for body in cases]
        raw_cases.extend(
            [
                b"not JSON " + SECRET.encode(),
                b"{",
                b"\xff",
                b'{"choices": [], "choices": []}',
                json.dumps(completion(ignored="OVERFLOW"))
                .replace('"OVERFLOW"', "1e400")
                .encode(),
                json.dumps(completion(usage={"tokens": float("nan")})).encode(),
                json.dumps(completion(usage={"tokens": float("inf")})).encode(),
            ]
        )
        token = None
        for index, raw in enumerate(raw_cases):
            with self.subTest(case=index):

                def handler(request: httpx.Request) -> httpx.Response:
                    self.calls.append(request)
                    return httpx.Response(200, content=raw)

                count = len(self.calls)
                with self.assertRaises(execution.ProviderFailure) as error:
                    await self.execute(handler, retry_token=token)
                self.assertEqual(len(self.calls), count + 1)
                self.assertNotIn(
                    SECRET, "".join(traceback.format_exception(error.exception))
                )
                self.assertNotIn(SECRET, self.path.read_text())
                token = self.retry_token()

    async def test_empty_tool_markers_and_optional_metadata_are_allowed(self) -> None:
        body = completion(usage={})
        body["choices"][0]["message"].update(
            tool_calls=[], function_call=None, refusal="", reasoning_details=[]
        )
        result = await self.execute(lambda request: httpx.Response(200, json=body))
        self.assertEqual(result.content, ANSWER)
        self.assertEqual(result.usage, {})
        self.assertEqual(result.reasoning_details, [])

    async def test_invalid_credentials_do_not_mutate_or_dispatch(self) -> None:
        before = self.fixture.files()
        for key in (
            None,
            123,
            "",
            " ",
            "has space",
            "bad\nkey",
            "bad\rkey",
            "nonascii-☃",
            "bad\x7fkey",
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    await self.execute(api_key=key)
                self.assertEqual(self.fixture.files(), before)
        self.assertEqual(self.calls, [])

    async def test_other_callback_identity_cannot_be_adopted(self) -> None:
        async def callback(**kwargs: Any) -> dict[str, Any]:
            return {"content": ANSWER, "finish_reason": "stop"}

        await execution.execute(
            self.run_id,
            self.models[0],
            callback,
            provider_key="injected-test-v1",
            expected_revision=3,
        )
        before = self.fixture.files()
        with self.assertRaises(execution.ExecutionConflict):
            await self.execute()
        self.assertEqual(self.fixture.files(), before)
        self.assertEqual(self.calls, [])

    async def test_durable_write_failure_prevents_http(self) -> None:
        with patch.object(run_admission, "_write", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                await self.execute()
        self.assertEqual(self.calls, [])

    async def test_compressed_response_is_rejected_before_decoding_and_closed(
        self,
    ) -> None:
        raw = json.dumps(completion(ignored="x" * (2 * 1024 * 1024))).encode()
        compressed = gzip.compress(raw)
        self.assertLess(len(compressed), 2 * 1024 * 1024)
        stream = ChunkStream([compressed])

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=stream
            )

        with self.assertRaises(execution.ProviderFailure):
            await self.execute(handler)
        self.assertTrue(stream.closed)
        self.assertFalse(stream.started.is_set())
        self.assertEqual(len(self.calls), 1)
        self.retry_token()

    async def test_exact_size_limit_and_identity_encoding_are_accepted(self) -> None:
        raw = json.dumps(completion()).encode()
        raw += b" " * (2 * 1024 * 1024 - len(raw))
        stream = ChunkStream([raw[: 1024 * 1024], raw[1024 * 1024 :]])
        result = await self.execute(
            lambda request: httpx.Response(
                200, headers={"Content-Encoding": " Identity "}, stream=stream
            )
        )
        self.assertEqual(result.content, ANSWER)
        self.assertTrue(stream.closed)

    async def test_chunked_limit_stops_before_consuming_remaining_body(self) -> None:
        stream = ChunkStream(
            [b"x" * (1024 * 1024), b"x" * (1024 * 1024), b"x"], stall=True
        )
        with self.assertRaises(execution.ProviderFailure):
            await asyncio.wait_for(
                self.execute(lambda request: httpx.Response(200, stream=stream)), 2
            )
        self.assertTrue(stream.closed)
        self.retry_token()

    async def test_total_timeout_covers_body_read_and_preserves_uncertainty(
        self,
    ) -> None:
        stream = ChunkStream([b"{"], stall=True)
        wait_for = asyncio.wait_for
        timeouts = []

        async def short_wait(awaitable: Any, timeout: float) -> Any:
            timeouts.append(timeout)
            return await wait_for(awaitable, timeout=0.01)

        with patch.object(provider.asyncio, "wait_for", side_effect=short_wait):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute(lambda request: httpx.Response(200, stream=stream))
        self.assertEqual(timeouts, [self.inputs.timeout_seconds])
        self.assertTrue(stream.closed)
        self.retry_token()

    async def test_cancellation_closes_stream_and_requires_explicit_retry(self) -> None:
        stream = ChunkStream([b"{"], stall=True)

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, stream=stream)

        task = asyncio.create_task(self.execute(handler))
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
        self.assertEqual(len(self.calls), 1)
        await self.execute(retry_token=token)
        self.assertEqual(len(self.calls), 2)

    async def test_durable_cancellation_waits_for_http_cleanup_and_preserves_prior_result(
        self,
    ) -> None:
        retained = await self.execute()
        cleanup, release = asyncio.Event(), asyncio.Event()

        class SlowCloseStream(ChunkStream):
            async def aclose(self) -> None:
                cleanup.set()
                await release.wait()
                await super().aclose()

        stream = SlowCloseStream([b"{"], stall=True)

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, stream=stream)

        task = asyncio.create_task(self.execute(handler, model=self.models[1]))
        try:
            await asyncio.wait_for(stream.started.wait(), 2)
            run_cancellation.request(self.run_id)
            await asyncio.wait_for(cleanup.wait(), 2)
            snapshot = run_inspection.inspect(self.run_id)
            self.assertEqual(snapshot["internal_phase"], "cancelling")
            self.assertTrue(snapshot["operation_owned"])
            self.assertFalse(task.done())
            self.assertEqual(
                execution.load_result(self.run_id, self.models[0]), retained
            )
            run_cancellation.request(self.run_id)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
            if not cleanup.is_set():
                task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertTrue(stream.closed)
        snapshot = run_inspection.inspect(self.run_id)
        self.assertEqual(snapshot["internal_phase"], "cancelled")
        self.assertFalse(snapshot["operation_owned"])
        self.assertEqual(execution.load_result(self.run_id, self.models[0]), retained)
        self.assertIsNone(execution.load_result(self.run_id, self.models[1]))
        before = self.fixture.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.execute(model=self.models[1])
        self.assertEqual(before, self.fixture.files())
        self.assertEqual(len(self.calls), 2)

    async def test_deletion_while_http_body_waits_discards_late_response(self) -> None:
        release = asyncio.Event()

        class ReleasedStream(ChunkStream):
            async def __aiter__(self):
                self.started.set()
                await release.wait()
                yield json.dumps(completion()).encode()

        stream = ReleasedStream([])

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, stream=stream)

        task = asyncio.create_task(self.execute(handler))
        try:
            await asyncio.wait_for(stream.started.wait(), 2)
            run_admission.tombstone_conversation(f"cli-{self.run_id}")
            tombstone = self.path.read_bytes()
            self.assertEqual(self.saved()["phase"], "deleted")
            self.assertNotIn("stage1_execution", self.saved())
        finally:
            release.set()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await asyncio.wait_for(task, 2)
        self.assertTrue(stream.closed)
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.execute()
        with self.assertRaises(run_admission.AdmissionDeleted):
            execution.load_result(self.run_id, self.models[0])
        self.assertEqual(self.path.read_bytes(), tombstone)
        self.assertEqual(len(self.calls), 1)

    async def test_durable_cancellation_before_dispatch_never_constructs_http_client(
        self,
    ) -> None:
        register = run_cancellation._register

        def cancel_before_dispatch(
            run_id: str, attempt: str, task: asyncio.Task
        ) -> None:
            register(run_id, attempt, task)
            run_cancellation.request(run_id)

        with (
            patch.object(
                run_cancellation, "_register", side_effect=cancel_before_dispatch
            ),
            patch.object(provider.httpx, "AsyncClient") as client,
        ):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute()
        client.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(
            run_inspection.inspect(self.run_id)["internal_phase"], "cancelled"
        )
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))

    async def test_deletion_before_dispatch_never_constructs_http_client(self) -> None:
        register = run_cancellation._register

        def delete_before_dispatch(
            run_id: str, attempt: str, task: asyncio.Task
        ) -> None:
            register(run_id, attempt, task)
            run_admission.tombstone_conversation(f"cli-{run_id}")

        with (
            patch.object(
                run_cancellation, "_register", side_effect=delete_before_dispatch
            ),
            patch.object(provider.httpx, "AsyncClient") as client,
        ):
            with self.assertRaises(execution.ProviderFailure):
                await self.execute()
        client.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.saved()["phase"], "deleted")
        self.assertNotIn("stage1_execution", self.saved())

    async def test_completion_write_failure_leaves_uncertain_attempt_without_http_retry(
        self,
    ) -> None:
        write = run_admission._write
        stream = ChunkStream([json.dumps(completion()).encode()])

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, stream=stream)

        def fail_completion(record: dict[str, Any]) -> None:
            if any(
                operation["phase"] == "complete"
                for operation in record.get("stage1_execution", {}).get(
                    "operations", []
                )
            ):
                raise OSError("completion write failed")
            write(record)

        with patch.object(run_admission, "_write", side_effect=fail_completion):
            with self.assertRaises(OSError):
                await self.execute(handler)
        self.assertTrue(stream.closed)
        self.assertIsNone(execution.load_result(self.run_id, self.models[0]))
        self.retry_token()
        before = self.fixture.files()
        with self.assertRaises(execution.RetryRequired):
            await self.execute()
        self.assertEqual(self.fixture.files(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_lost_completion_ack_replays_saved_response_without_http(
        self,
    ) -> None:
        write = run_admission._write
        stream = ChunkStream([json.dumps(completion()).encode()])

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, stream=stream)

        def lose_completion_ack(record: dict[str, Any]) -> None:
            write(record)
            if any(
                operation["phase"] == "complete"
                for operation in record.get("stage1_execution", {}).get(
                    "operations", []
                )
            ):
                raise OSError("completion acknowledgment lost")

        with patch.object(run_admission, "_write", side_effect=lose_completion_ack):
            with self.assertRaises(OSError):
                await self.execute(handler)
        self.assertTrue(stream.closed)
        before = self.fixture.files()
        with patch.object(provider.httpx, "AsyncClient") as client:
            result = await self.execute()
        client.assert_not_called()
        self.assertEqual(result.content, ANSWER)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(self.fixture.files(), before)
        self.assertEqual(len(self.calls), 1)
