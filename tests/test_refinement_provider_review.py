"""Independent adversarial checks of the internal refinement provider boundary."""

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "synthetic-review-key")
os.environ.setdefault("API_AUTH_KEY", "synthetic-review-auth")

from backend import refinement_execution, run_admission, storage
from backend.consultation_contract import CreateRunRequest
from backend.refinement_prompt import build_refinement_messages
from backend.refinement_provider import OpenRouterRefinementConfig, suggest_openrouter


class ProviderBoundaryReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = str(Path(self.directory.name) / "conversations")
        self.patch = patch.object(storage, "DATA_DIR", self.data_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.run_id = str(uuid4())
        run_admission.prepare(
            self.run_id,
            CreateRunRequest(
                content="Review question — вопрос",
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        self.path = Path(self.directory.name) / "run_receipts" / f"{self.run_id}.json"
        self.config = OpenRouterRefinementConfig(
            endpoint="https://provider.invalid/chat/completions",
            model="review/model",
            max_tokens=1024,
        )
        self.requests = []

    def transport(self, request):
        self.requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "Clarify scope",
                                    "clarifying_questions": ["Which scope?"],
                                    "refined_prompt": "Review the specified scope.",
                                }
                            )
                        },
                    }
                ],
            },
        )

    async def suggest(self, **kwargs):
        return await suggest_openrouter(
            self.run_id,
            kwargs.pop("config", self.config),
            api_key="synthetic-review-secret",
            _transport=httpx.MockTransport(self.transport),
            **kwargs,
        )

    async def test_rejected_initial_requests_do_not_bind_provider(self):
        for kwargs in [
            {"expected_revision": 3},
            {"expected_revision": 0, "retry_token": str(uuid4())},
            {"expected_revision": True},
            {"expected_revision": 0, "retry_token": "invalid"},
        ]:
            with self.subTest(kwargs=kwargs):
                before = self.path.read_bytes()
                with self.assertRaises(
                    (ValueError, refinement_execution.ExecutionConflict)
                ):
                    await self.suggest(**kwargs)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.requests, [])
        changed = self.config.model_copy(update={"model": "review/another-model"})
        await self.suggest(config=changed, expected_revision=0)
        self.assertEqual(len(self.requests), 1)

    async def test_null_provider_checkpoint_is_not_silently_rebound(self):
        record = json.loads(self.path.read_text())
        record["refinement_provider"] = None
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest(expected_revision=0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.requests, [])

    async def test_completed_operation_does_not_hide_provenance_corruption(self):
        await self.suggest(expected_revision=0)
        record = json.loads(self.path.read_text())
        record["refinement_provider"]["identity"] = "incorrect"
        self.path.write_text(json.dumps(record))
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            await self.suggest(expected_revision=0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.requests), 1)

    async def test_provider_key_rotation_does_not_change_saved_request_identity(self):
        await self.suggest(expected_revision=0)
        before = self.path.read_bytes()
        repeated = await suggest_openrouter(
            self.run_id,
            self.config,
            api_key="new-synthetic-secret",
            expected_revision=0,
            _transport=httpx.MockTransport(self.transport),
        )
        self.assertEqual(repeated.phase, "waiting_answer")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("synthetic-review-secret", self.path.read_text())
        self.assertNotIn("new-synthetic-secret", self.path.read_text())

    async def test_cancellation_after_request_preserves_retry_without_background_work(
        self,
    ):
        entered, finished = asyncio.Event(), asyncio.Event()
        calls = []

        async def blocking(request):
            calls.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        task = asyncio.create_task(
            suggest_openrouter(
                self.run_id,
                self.config,
                api_key="synthetic-review-secret",
                expected_revision=0,
                _transport=httpx.MockTransport(blocking),
            )
        )
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            refinement_execution.inspect(self.run_id)["phase"], "needs_retry"
        )
        with self.assertRaises(refinement_execution.RetryRequired):
            await self.suggest(expected_revision=0)
        self.assertEqual(self.requests, [])


class SharedPromptReviewTests(unittest.TestCase):
    def test_shared_builder_matches_previous_legacy_request_text(self):
        # SHA-256 of the exact legacy request text at a902c10552fe34f419a2c21dff6f3109170a6a7f.
        # Fixtures are self-contained: shallow CI checkouts need no historical objects.
        expected = [
            "0a89ebc7ad6f1dabccf2f0547722c29b37ccb83854713bdb473340c64325715e",
            "7aaf5f062e546d468dc5953891229ffa8da355d865e20b3fd6d67a1bd7bad65b",
            "b5c2ba4d423ec0755ec0fbe42577dc6e5f3e248baf587c586a69449c0de54847",
            "32ae6cdab24c14b406a310d139a7fa43d05997dc164bafe09fd7585f8720896a",
            "cfc7441a1ce0c1a386da5bfb79d019ba2d2e405ff05b395fa0f442bfd0160898",
            "e7df052ba5350a373c95c8ba3622b81577532fbfd504790f3bb800357ed92483",
            "32ae6cdab24c14b406a310d139a7fa43d05997dc164bafe09fd7585f8720896a",
            "cfc7441a1ce0c1a386da5bfb79d019ba2d2e405ff05b395fa0f442bfd0160898",
            "e7df052ba5350a373c95c8ba3622b81577532fbfd504790f3bb800357ed92483",
        ]
        actual = []
        for iteration in (1, 2, 3):
            for query_text in (" English ", "Русский\n多语言\n", "a" * 15000):
                messages = build_refinement_messages(
                    user_query=query_text,
                    iteration=iteration,
                    clarifying_answers=[
                        {"question": " Which? ", "answer": " A "},
                        {"question": "", "answer": "B"},
                    ],
                    additional_context="  Context\n\nMore  ",
                    previous_refined_prompt=" Draft ",
                )
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0]["role"], "user")
                actual.append(
                    hashlib.sha256(messages[0]["content"].encode()).hexdigest()
                )
        self.assertEqual(actual, expected)
