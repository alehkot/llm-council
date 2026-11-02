"""Offline HTTP-boundary tests: synthetic credentials, no live provider calls."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    durable_refinement,
    refinement_execution,
    run_admission,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest
from backend.refinement_prompt import build_refinement_messages
from backend.refinement_provider import (
    OpenRouterRefinementConfig,
    ProviderFailure,
    suggest_openrouter,
)

SUGGESTION = {
    "intent": "Choose a scope",
    "clarifying_questions": ["Which scope?"],
    "refined_prompt": "  Exact refined question — вопрос  ",
}


def completion(suggestion=None, **changes):
    body = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        SUGGESTION if suggestion is None else suggestion,
                        ensure_ascii=False,
                    )
                },
            }
        ]
    }
    body.update(changes)
    return body


class RefinementProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = str(Path(self.directory.name) / "conversations")
        self.patch = patch.object(storage, "DATA_DIR", self.data)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.run_id = str(uuid4())
        run_admission.prepare(
            self.run_id,
            CreateRunRequest(
                content="Original — вопрос",
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        self.path = Path(self.directory.name) / "run_receipts" / f"{self.run_id}.json"
        self.config = OpenRouterRefinementConfig(
            endpoint="https://provider.invalid/completions",
            model="fake/model",
            max_tokens=1200,
        )
        self.calls = []

    async def handler(self, request):
        self.calls.append(request)
        record = json.loads(self.path.read_text())
        self.assertEqual(
            record["refinement_provider"]["configuration"],
            self.config.model_dump(mode="json"),
        )
        self.assertEqual(
            record["refinement_provider"]["identity"], self.config.identity()
        )
        self.assertEqual(
            record["refinement_execution"]["operations"][-1]["phase"], "in_flight"
        )
        return httpx.Response(200, json=completion())

    async def suggest(self, handler=None, **kwargs):
        return await suggest_openrouter(
            self.run_id,
            kwargs.pop("config", self.config),
            api_key=kwargs.pop("api_key", "synthetic-secret-DO-NOT-PERSIST"),
            expected_revision=kwargs.pop("expected_revision", 0),
            _transport=httpx.MockTransport(handler or self.handler),
            **kwargs,
        )

    async def test_exact_saved_config_request_and_duplicate_read(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_MAX_RETRIES": "99",
                "HTTPS_PROXY": "http://must-not-use.invalid",
                "PROMPT_REFINEMENT_MODEL": "changed",
            },
        ):
            result = await self.suggest()
        self.assertEqual(result.last_suggestion.model_dump(), SUGGESTION)
        request = self.calls[0]
        self.assertEqual(str(request.url), self.config.endpoint)
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            json.loads(request.content),
            {
                "model": "fake/model",
                "max_tokens": 1200,
                "stream": False,
                "reasoning": {"effort": "high"},
                "messages": build_refinement_messages("Original — вопрос"),
            },
        )
        self.assertEqual(
            request.headers["authorization"], "Bearer synthetic-secret-DO-NOT-PERSIST"
        )
        before = self.path.read_bytes()
        self.assertEqual(await self.suggest(), result)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn(b"synthetic-secret", before)

    async def test_http_errors_and_redirect_are_one_request_and_uncertain(self):
        for status in (301, 302, 307, 400, 401, 408, 429, 500, 503):
            with self.subTest(status=status):
                run_id = str(uuid4())
                run_admission.prepare(
                    run_id,
                    CreateRunRequest(
                        content="Question",
                        refine=True,
                        interaction_policy="persistent",
                        recovery_policy="explicit_retry",
                    ),
                )
                seen = []

                def handler(request):
                    seen.append(str(request.url))
                    return httpx.Response(
                        status,
                        headers={
                            "Location": "https://must-not-follow.invalid",
                            "Retry-After": "0",
                        },
                        text="sensitive provider error",
                    )

                with self.assertRaises(ProviderFailure) as error:
                    await suggest_openrouter(
                        run_id,
                        self.config,
                        api_key="synthetic",
                        expected_revision=0,
                        _transport=httpx.MockTransport(handler),
                    )
                self.assertNotIn("sensitive", str(error.exception))
                self.assertEqual(seen, [self.config.endpoint])
                self.assertEqual(
                    refinement_execution.inspect(run_id)["phase"], "needs_retry"
                )

    async def test_transport_failure_does_not_replay_and_retry_requires_consent(self):
        def fail(request):
            self.calls.append(request)
            raise httpx.ReadError("synthetic-secret-DO-NOT-PERSIST", request=request)

        with self.assertRaises(ProviderFailure) as error:
            await self.suggest(fail)
        self.assertNotIn("synthetic-secret", str(error.exception))
        token = refinement_execution.inspect(self.run_id)["retry_token"]
        with self.assertRaises(refinement_execution.RetryRequired):
            await self.suggest()
        self.assertEqual(len(self.calls), 1)
        result = await self.suggest(retry_token=token)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(await self.suggest(retry_token=token), result)
        self.assertEqual(len(self.calls), 2)

    async def test_malformed_or_incomplete_success_is_not_original_prompt_fallback(
        self,
    ):
        cases = [
            None,
            {},
            [],
            {"choices": []},
            completion(error={"message": "sensitive"}),
            completion(
                {"intent": "", "clarifying_questions": [], "refined_prompt": ""}
            ),
            completion(
                {"intent": "", "clarifying_questions": [], "refined_prompt": "cut…"}
            ),
            completion(
                {"intent": 9, "clarifying_questions": [], "refined_prompt": "text"}
            ),
            completion(
                {
                    "intent": "",
                    "clarifying_questions": ["1", "2", "3", "4"],
                    "refined_prompt": "text",
                }
            ),
        ]
        for reason in ("length", "error", "content_filter", "tool_calls", None):
            data = completion()
            data["choices"][0]["finish_reason"] = reason
            cases.append(data)
        data = completion()
        data["choices"][0]["message"]["content"] = "not json"
        cases.append(data)
        for body in cases:
            with self.subTest(body=body):
                run_id = str(uuid4())
                seen = []
                run_admission.prepare(
                    run_id,
                    CreateRunRequest(
                        content="Question",
                        refine=True,
                        interaction_policy="persistent",
                        recovery_policy="explicit_retry",
                    ),
                )

                def handler(request):
                    seen.append(request)
                    return httpx.Response(200, content=json.dumps(body))

                with self.assertRaises(ProviderFailure):
                    await suggest_openrouter(
                        run_id,
                        self.config,
                        api_key="synthetic",
                        expected_revision=0,
                        _transport=httpx.MockTransport(handler),
                    )
                self.assertEqual(len(seen), 1)
                self.assertEqual(
                    refinement_execution.inspect(run_id)["phase"], "needs_retry"
                )
                self.assertEqual(run_interaction.get(run_id)["prompts"], [])

    async def test_provenance_write_failures_never_dispatch(self):
        with patch.object(run_admission, "_write", side_effect=OSError("before write")):
            with self.assertRaises(OSError):
                await self.suggest()
        self.assertEqual(self.calls, [])
        real_write = run_admission._write

        def lost_ack(record):
            real_write(record)
            raise OSError("after write")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                await self.suggest()
        self.assertEqual(self.calls, [])
        self.assertEqual(refinement_execution.inspect(self.run_id), None)
        await self.suggest()
        self.assertEqual(len(self.calls), 1)

    async def test_configuration_drift_after_success_and_between_rounds_conflicts(self):
        await self.suggest()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.suggest(
                config=self.config.model_copy(update={"model": "different"})
            )
        prompt = run_interaction.get(self.run_id)["prompts"][0]
        instance = prompt["instance"]["instance_id"]
        run_interaction.answer(
            self.run_id,
            instance,
            AnswerRequest(
                responses={
                    "refinement_action": "refine_again",
                    "refined_prompt": "edited",
                    "additional_insights": "extra",
                    "clarifying_answer_0": "chosen",
                }
            ),
        )
        durable_refinement.resume_answer(self.run_id, instance)
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.suggest(
                config=self.config.model_copy(update={"max_tokens": 999}),
                expected_revision=3,
            )
        await self.suggest(expected_revision=3)
        self.assertEqual(len(self.calls), 2)
        message = json.loads(self.calls[-1].content)["messages"][0]["content"]
        self.assertIn("chosen", message)
        self.assertIn("extra", message)
        self.assertIn("Follow-up round", message)

    async def test_corrupt_provenance_fails_closed(self):
        await self.suggest()
        record = json.loads(self.path.read_text())
        record["refinement_provider"]["identity"] = "wrong"
        self.path.write_text(json.dumps(record))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest()
        self.assertEqual(len(self.calls), 1)

    async def test_concurrent_duplicate_does_not_send_twice(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            self.calls.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json=completion())

        task = asyncio.create_task(self.suggest(handler))
        await started.wait()
        try:
            with self.assertRaises(refinement_execution.ExecutionPending):
                await self.suggest()
        finally:
            release.set()
        await task
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_preserves_uncertainty(self):
        started = asyncio.Event()

        async def handler(request):
            self.calls.append(request)
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.suggest(handler))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            refinement_execution.inspect(self.run_id)["phase"], "needs_retry"
        )

    async def test_total_timeout(self):
        async def slow(request):
            self.calls.append(request)
            await asyncio.sleep(1)

        with self.assertRaises(ProviderFailure):
            await self.suggest(
                slow, config=self.config.model_copy(update={"timeout_seconds": 0.01})
            )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            refinement_execution.inspect(self.run_id)["phase"], "needs_retry"
        )

    async def test_reasoning_disabled_is_not_sent(self):
        config = self.config.model_copy(update={"reasoning": False})

        async def handler(request):
            self.assertNotIn("reasoning", json.loads(request.content))
            return httpx.Response(200, json=completion())

        await self.suggest(handler, config=config)

    async def test_saved_result_reopens_in_fresh_process_without_http(self):
        await self.suggest()
        code = """import asyncio, json, sys
from backend import storage
from backend.refinement_provider import OpenRouterRefinementConfig, suggest_openrouter
storage.DATA_DIR = sys.argv[1]
config = OpenRouterRefinementConfig.model_validate_json(sys.argv[3])
result = asyncio.run(suggest_openrouter(sys.argv[2], config, api_key="rotated-synthetic", expected_revision=0))
print(result.model_dump_json())"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                self.data,
                self.run_id,
                self.config.model_dump_json(),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(json.loads(result.stdout)["last_suggestion"], SUGGESTION)
        self.assertEqual(len(self.calls), 1)

    async def test_response_size_limit_rejects_without_retry(self):
        def handler(request):
            self.calls.append(request)
            return httpx.Response(200, content=b"x" * 101)

        with patch("backend.refinement_provider.MAX_RESPONSE_BYTES", 100):
            with self.assertRaises(ProviderFailure):
                await self.suggest(handler)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            refinement_execution.inspect(self.run_id)["phase"], "needs_retry"
        )

    async def test_long_input_omission_guard_does_not_silently_fall_back(self):
        run_id = str(uuid4())
        run_admission.prepare(
            run_id,
            CreateRunRequest(
                content="Constraints " * 300,
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(
                200,
                json=completion(
                    {
                        "intent": "",
                        "clarifying_questions": [],
                        "refined_prompt": "short",
                    }
                ),
            )

        with self.assertRaises(ProviderFailure):
            await suggest_openrouter(
                run_id,
                self.config,
                api_key="synthetic",
                expected_revision=0,
                _transport=httpx.MockTransport(handler),
            )
        self.assertEqual(len(seen), 1)
        self.assertEqual(refinement_execution.inspect(run_id)["phase"], "needs_retry")

    async def test_invalid_configuration_and_credentials_never_change_receipt(self):
        changes = [
            {"endpoint": "http://provider.invalid/completions"},
            {"endpoint": "https://name:password@provider.invalid/completions"},
            {"endpoint": "https://provider.invalid/completions?api_key=secret"},
            {"endpoint": "https://provider.invalid/completions#fragment"},
            {"endpoint": "https:///missing-host"},
            {"endpoint": "https://provider.invalid:bad/completions"},
            {"endpoint": "https://provider.invalid/ white"},
            {"model": ""},
            {"model": " whitespace "},
            {"model": "bad\nmodel"},
            {"transport": "acp"},
            {"version": True},
            {"version": 2},
            {"max_tokens": True},
            {"max_tokens": 0},
            {"max_tokens": "100"},
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": float("inf")},
            {"timeout_seconds": -1.0},
            {"effort": "unsupported"},
            {"reasoning": "yes"},
        ]
        before = self.path.read_bytes()
        for changed in changes:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    await self.suggest(config=self.config.model_copy(update=changed))
                self.assertEqual(self.path.read_bytes(), before)
        for credential in ("", " ", "bad\nkey", "не-ascii", "with space"):
            with self.subTest(credential=credential):
                with self.assertRaises(ValueError):
                    await self.suggest(api_key=credential)
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    async def test_old_callback_provenance_is_not_backfilled(self):
        async def old_provider(**arguments):
            return SUGGESTION

        await refinement_execution.suggest(
            self.run_id,
            old_provider,
            provider_key=self.config.identity(),
            expected_revision=0,
        )
        before = self.path.read_bytes()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    async def test_missing_saved_config_field_is_not_defaulted(self):
        await self.suggest()
        record = json.loads(self.path.read_text())
        del record["refinement_provider"]["configuration"]["reasoning"]
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
