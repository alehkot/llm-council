"""Offline API regressions for persisted prompt-refinement decisions."""

import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import main, storage  # noqa: E402


class ResynthesisPromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(storage, "DATA_DIR", self.tempdir.name)
        self.data_patch.start()
        self.original = "Help me choose a database."
        self.refined = "Compare PostgreSQL and SQLite for an offline-first desktop app."
        self.initial_version = {
            "model": "chair",
            "response": "Original synthesis",
            "mode": "best_of_breed",
            "timestamp": "2026-01-01T00:00:00",
        }

    def tearDown(self) -> None:
        self.data_patch.stop()
        self.tempdir.cleanup()

    def create_run(self, refinement: dict | None) -> dict:
        storage.create_conversation("test-run")
        storage.add_user_message("test-run", self.original)
        metadata = {"label_to_model": {"Response A": "model-a"}}
        if refinement is not None:
            metadata["prompt_refinement"] = refinement
        storage.add_assistant_message(
            "test-run",
            [{"model": "model-a", "response": "Evidence"}],
            None,
            [{"model": "model-a", "ranking": "Response A"}],
            {
                "current": deepcopy(self.initial_version),
                "versions": [deepcopy(self.initial_version)],
            },
            metadata=metadata,
        )
        return deepcopy(metadata)

    async def request_synthesis(self, endpoint: str, payload: dict) -> tuple:
        synthesis = AsyncMock(
            return_value={"model": "chair", "response": "New synthesis"}
        )
        with patch.object(main, "stage3_synthesize_final", synthesis):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/conversations/test-run/{endpoint}",
                    json={"message_index": 0, **payload},
                    headers={"X-API-Key": main.API_AUTH_KEY},
                )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), synthesis

    async def test_single_rerun_uses_accepted_refinement_and_preserves_history(
        self,
    ) -> None:
        metadata = self.create_run(
            {
                "decision": "use_refined",
                "original_prompt": self.original,
                "refined_prompt": self.refined,
            }
        )
        response, synthesis = await self.request_synthesis(
            "resynthesize",
            {"synthesis_mode": "consensus", "focus_areas": "Portability"},
        )
        self.assertEqual(synthesis.await_args.kwargs["user_query"], self.refined)
        self.assertIn(
            "Focus areas: Portability", synthesis.await_args.kwargs["prompt_modifier"]
        )
        persisted = storage.get_conversation("test-run")
        self.assertEqual(persisted["messages"][0]["content"], self.original)
        message = persisted["messages"][1]
        self.assertEqual(
            message["metadata"]["prompt_refinement"], metadata["prompt_refinement"]
        )
        self.assertEqual(message["stage3"]["versions"][0], self.initial_version)
        self.assertEqual(message["stage3"]["current"], response["version"])

    async def test_bulk_rerun_uses_accepted_refinement_for_every_mode(self) -> None:
        self.create_run({"decision": "use_refined", "refined_prompt": self.refined})
        response, synthesis = await self.request_synthesis("resynthesize-all", {})
        self.assertEqual(len(response["versions"]), 3)
        self.assertEqual(synthesis.await_count, 3)
        for call in synthesis.await_args_list:
            self.assertEqual(call.kwargs["user_query"], self.refined)
        message = storage.get_conversation("test-run")["messages"][1]
        self.assertEqual(message["stage3"]["current"], self.initial_version)
        self.assertEqual(len(message["stage3"]["versions"]), 4)

    async def test_rejected_refinement_keeps_original_prompt(self) -> None:
        self.create_run(
            {
                "decision": "use_original",
                "original_prompt": self.original,
                "refined_prompt": self.refined,
            }
        )
        _, synthesis = await self.request_synthesis(
            "resynthesize", {"synthesis_mode": "weighted"}
        )
        self.assertEqual(synthesis.await_args.kwargs["user_query"], self.original)

    async def test_legacy_run_without_refinement_keeps_original_prompt(self) -> None:
        self.create_run(None)
        _, synthesis = await self.request_synthesis(
            "resynthesize-all", {"modes": ["consensus"]}
        )
        self.assertEqual(synthesis.await_args.kwargs["user_query"], self.original)

    async def test_empty_accepted_refinement_falls_back_to_original(self) -> None:
        self.create_run({"decision": "use_refined", "refined_prompt": "  "})
        _, synthesis = await self.request_synthesis(
            "resynthesize", {"synthesis_mode": "comprehensive"}
        )
        self.assertEqual(synthesis.await_args.kwargs["user_query"], self.original)


if __name__ == "__main__":
    unittest.main()
