"""Configured durable refinement tests with no provider network access."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    config,
    refinement_execution,
    run_admission,
    run_cancellation,
    storage,
)
from backend.consultation_contract import CreateRunRequest
from backend.refinement_provider import (
    ProviderFailure,
    resolve_configured_refinement,
    suggest_configured_refinement,
)


class ConfiguredRefinementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        directory = Path(self.directory.name)
        storage_patch = patch.object(
            storage, "DATA_DIR", str(directory / "conversations")
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        config_patch = patch.multiple(
            config,
            MODEL_BACKEND="openrouter",
            PROMPT_REFINEMENT_MODEL="fake/configured-model",
            OPENROUTER_API_URL="https://configured.invalid/completions",
            OPENROUTER_MAX_TOKENS=1234,
            OPENROUTER_API_KEY="unused-global-secret",
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.run_id = str(uuid4())
        run_admission.prepare(
            self.run_id,
            CreateRunRequest(
                content="Question — вопрос",
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        self.path = directory / "run_receipts" / f"{self.run_id}.json"
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "Clarify",
                                    "clarifying_questions": [],
                                    "refined_prompt": "Exact refined question",
                                }
                            )
                        },
                    }
                ]
            },
        )

    async def suggest(self, handler=None, **kwargs):
        return await suggest_configured_refinement(
            self.run_id,
            api_key=kwargs.pop("api_key", "ephemeral-secret"),
            expected_revision=kwargs.pop("expected_revision", 0),
            _transport=httpx.MockTransport(handler or self.complete),
            **kwargs,
        )

    async def test_settings_are_bound_and_consumed_without_global_credentials(self):
        resolved = resolve_configured_refinement()
        result = await self.suggest()
        saved = json.loads(self.path.read_text())["refinement_provider"]
        self.assertEqual(saved["configuration"], resolved.model_dump(mode="json"))
        self.assertEqual(saved["identity"], resolved.identity())
        request = self.calls[0]
        payload = json.loads(request.content)
        self.assertEqual(str(request.url), resolved.endpoint)
        self.assertEqual(payload["model"], resolved.model)
        self.assertEqual(payload["max_tokens"], resolved.max_tokens)
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(request.headers["authorization"], "Bearer ephemeral-secret")
        self.assertNotIn(b"secret", self.path.read_bytes())
        before = self.path.read_bytes()
        self.assertEqual(await self.suggest(api_key="rotated-secret"), result)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_unsupported_transport_never_binds_or_calls_provider(self):
        before = self.path.read_bytes()
        for backend in ("acp", "unknown", None):
            with (
                self.subTest(backend=backend),
                patch.object(config, "MODEL_BACKEND", backend),
            ):
                with self.assertRaises(refinement_execution.ExecutionConflict):
                    await self.suggest()
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    async def test_invalid_settings_fail_before_persistence(self):
        before = self.path.read_bytes()
        cases = {
            "OPENROUTER_API_URL": "https://user:private-secret@example.invalid",
            "PROMPT_REFINEMENT_MODEL": " invalid model ",
            "OPENROUTER_MAX_TOKENS": True,
        }
        for name, value in cases.items():
            with self.subTest(setting=name), patch.object(config, name, value):
                with self.assertRaises(ValueError) as raised:
                    await self.suggest()
                self.assertNotIn("private-secret", str(raised.exception))
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    async def test_changed_settings_conflict_even_for_completed_suggestion(self):
        await self.suggest()
        before = self.path.read_bytes()
        cases = {
            "OPENROUTER_API_URL": "https://other.invalid/completions",
            "PROMPT_REFINEMENT_MODEL": "different/model",
            "OPENROUTER_MAX_TOKENS": 5678,
            "MODEL_BACKEND": "acp",
        }
        for name, value in cases.items():
            with self.subTest(setting=name), patch.object(config, name, value):
                with self.assertRaises(refinement_execution.ExecutionConflict):
                    await self.suggest()
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_uncertain_retry_requires_same_settings_and_explicit_token(self):
        def fail(request):
            self.calls.append(request)
            raise httpx.ReadError("do-not-expose", request=request)

        with self.assertRaises(ProviderFailure):
            await self.suggest(fail)
        token = refinement_execution.inspect(self.run_id)["retry_token"]
        before = self.path.read_bytes()
        with patch.object(config, "PROMPT_REFINEMENT_MODEL", "changed/model"):
            with self.assertRaises(refinement_execution.ExecutionConflict):
                await self.suggest(retry_token=token)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(refinement_execution.RetryRequired):
            await self.suggest()
        self.assertEqual(len(self.calls), 1)
        await self.suggest(retry_token=token, api_key="rotated-secret")
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn(b"secret", self.path.read_bytes())

    async def test_no_config_reread_after_resolving(self):
        from backend import refinement_provider

        original = refinement_provider.suggest_openrouter

        async def mutate_then_call(run_id, resolved, **kwargs):
            with patch.multiple(
                config,
                MODEL_BACKEND="acp",
                PROMPT_REFINEMENT_MODEL="changed/model",
                OPENROUTER_MAX_TOKENS=999,
                OPENROUTER_API_URL="https://changed.invalid",
            ):
                return await original(run_id, resolved, **kwargs)

        with patch.object(refinement_provider, "suggest_openrouter", mutate_then_call):
            await self.suggest()
        payload = json.loads(self.calls[0].content)
        self.assertEqual(payload["model"], "fake/configured-model")
        self.assertEqual(payload["max_tokens"], 1234)
        self.assertEqual(
            str(self.calls[0].url), "https://configured.invalid/completions"
        )

    async def test_stale_revision_and_nonrefinement_do_not_bind(self):
        before = self.path.read_bytes()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.suggest(expected_revision=1)
        self.assertEqual(self.path.read_bytes(), before)
        self.run_id = str(uuid4())
        run_admission.prepare(
            self.run_id,
            CreateRunRequest(
                content="No refinement",
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        with self.assertRaises(refinement_execution.ExecutionConflict):
            await self.suggest()
        self.assertNotIn("refinement_provider", run_admission.get_prepared(self.run_id))
        self.assertEqual(self.calls, [])

    async def test_cancelled_run_does_not_dispatch(self):
        run_cancellation.request(self.run_id)
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    async def test_deleted_run_does_not_dispatch(self):
        receipt = run_admission.get_prepared(self.run_id)
        storage.delete_conversation(receipt["conversation_id"])
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.suggest()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])
