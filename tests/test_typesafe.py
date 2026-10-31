"""Jev's HTTP contract, failure handling, and real skill-context plumbing."""

import asyncio
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import council, langgraph_flow, skills, typesafe
from backend.langgraph_flow import FlowContext
from backend.trace import TraceCollector


class TypeSafeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.requests = []
        self.scores = {}
        self.failure = None
        real_client = httpx.AsyncClient
        key = patch.object(typesafe, "TYPESAFE_API_KEY", "test-key")
        factory = patch.object(
            typesafe.httpx,
            "AsyncClient",
            side_effect=lambda **kw: real_client(
                transport=httpx.MockTransport(self.respond), **kw
            ),
        )
        key.start()
        factory.start()
        self.addCleanup(key.stop)
        self.addCleanup(factory.stop)

    def respond(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        if self.failure == "timeout":
            raise httpx.ReadTimeout("private provider text")
        if self.failure == "cancel":
            raise asyncio.CancelledError()
        if self.failure == "http":
            return httpx.Response(429, text="private provider text")
        answers = {
            key: {"type": "score", "score": self.scores.get(int(key), 0)}
            for key in payload["questions"]
        }
        if self.failure == "missing":
            answers = {}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})

    async def test_rank_batches_cap_and_no_match(self) -> None:
        self.scores = {1: 1.6, 2: 2, 17: 1.9}
        result = await typesafe.select_relevant(
            "query", {i: {"description": str(i)} for i in range(1, 18)}, 2, "skills"
        )
        self.assertEqual(result["selected"], [2, 17])
        self.assertEqual([len(r["questions"]) for r in self.requests], [16, 1])
        self.assertTrue(result["parsed"])
        self.assertEqual(result["model"], "jev-test")

    async def test_no_match_is_success_and_empty_menu_skips_http(self) -> None:
        result = await typesafe.select_relevant("query", {1: {}}, 5, "skills")
        self.assertTrue(result["parsed"])
        self.assertEqual(result["selected"], [])
        self.requests.clear()
        result = await typesafe.select_relevant("query", {}, 5, "skills")
        self.assertTrue(result["parsed"])
        self.assertEqual(self.requests, [])

    async def test_empty_auto_selection_replays_without_routing(self) -> None:
        context = FlowContext(trace=TraceCollector())
        with (
            patch.object(langgraph_flow, "SKILLS_AUTO_SELECT_ENABLED", True),
            patch.object(
                langgraph_flow, "select_skills", new_callable=AsyncMock
            ) as router,
        ):
            await langgraph_flow._ensure_skills_runtime(
                context,
                {
                    "user_query": "query",
                    "metadata": {
                        "skills": {"resolved": [], "skill_selection": {"mode": "auto"}}
                    },
                },
            )
            router.assert_not_called()

    async def test_disabled_jev_keeps_openrouter_reference_contract(self) -> None:
        candidate = skills.ReferenceCandidate(
            1,
            "skill",
            "scope",
            "references/a.md",
            "1",
            "Heading",
            "summary",
            20,
            [],
            "summary",
        )
        with (
            patch.object(council, "SKILLS_TYPESAFE_ENABLED", False),
            patch.object(council, "query_model", new_callable=AsyncMock) as query,
        ):
            query.return_value = {"content": '{"selected": [1]}'}
            result = await council.select_skill_references("query", [candidate])
            self.assertTrue(result["parsed"])
            self.assertEqual(result["selected"], [1])
            self.assertEqual(len(query.call_args.args), 2)
            self.assertIsInstance(query.call_args.args[1], list)

    async def test_errors_return_fallback_contract_without_provider_text(self) -> None:
        self.failure = "missing"
        trace = TraceCollector()
        result = await typesafe.select_relevant("query", {1: {}}, 5, "skills", trace)
        self.assertFalse(result["parsed"])
        self.assertEqual(result["selected"], [])
        self.assertNotIn("test-key", json.dumps(trace.to_dict()))

    async def test_timeout_returns_fallback_contract(self) -> None:
        self.failure = "timeout"
        result = await typesafe.select_relevant("query", {1: {}}, 5, "skills")
        self.assertFalse(result["parsed"])
        self.assertNotIn("private", result["error"])

    async def test_invalid_score_returns_fallback_contract(self) -> None:
        self.scores = {1: True}
        result = await typesafe.select_relevant("query", {1: {}}, 5, "skills")
        self.assertFalse(result["parsed"])

    async def test_rate_limit_returns_fallback_contract(self) -> None:
        self.failure = "http"
        result = await typesafe.select_relevant("query", {1: {}}, 5, "skills")
        self.assertFalse(result["parsed"])

    async def test_cancellation_propagates(self) -> None:
        self.failure = "cancel"
        with self.assertRaises(asyncio.CancelledError):
            await typesafe.select_relevant("query", {1: {}}, 5, "skills")

    async def test_selected_skill_and_reference_reach_both_prompts_and_resume(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(skills, "_SKILLS_DIR", Path(directory)),
                patch.object(typesafe, "TYPESAFE_API_KEY", "test-key"),
                patch.object(council, "SKILLS_TYPESAFE_ENABLED", True),
                patch.object(langgraph_flow, "SKILLS_AUTO_SELECT_ENABLED", True),
                patch.object(
                    langgraph_flow, "SKILLS_REFERENCE_SELECTION_ENABLED", True
                ),
            ):
                archive = BytesIO()
                with ZipFile(archive, "w") as bundle:
                    bundle.writestr(
                        "SKILL.md",
                        "---\nname: gardening\ndescription: Rose care\n---\nGARDEN-INSTRUCTIONS\nSee references/roses.md for pruning.",
                    )
                    bundle.writestr(
                        "references/roses.md", "# Pruning\nPRUNING-EVIDENCE\n"
                    )
                skills.install_skill(archive.getvalue())
                self.scores = {1: 2}
                context = FlowContext(trace=TraceCollector())
                await langgraph_flow._ensure_skills_runtime(
                    context, {"user_query": "Prune roses"}
                )
                runtime = context.skills_runtime
                for block in (runtime.stage1_block, runtime.stage3_block):
                    self.assertIn("GARDEN-INSTRUCTIONS", block)
                    self.assertIn("PRUNING-EVIDENCE", block)
                self.assertEqual(len(self.requests), 2)
                self.assertEqual(
                    runtime.metadata["reference_selection"]["model"], "jev-test"
                )
                await langgraph_flow._ensure_skills_runtime(
                    FlowContext(trace=TraceCollector()),
                    {
                        "user_query": "Prune roses",
                        "metadata": {"skills": runtime.metadata},
                    },
                )
                self.assertEqual(len(self.requests), 2)
