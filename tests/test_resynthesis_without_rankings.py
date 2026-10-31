"""Offline API coverage for completed consultations with no successful reviewers."""

import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import council, main, storage  # noqa: E402


class ResynthesisWithoutRankingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_patch = patch.object(storage, "DATA_DIR", self.tempdir.name)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.refined = "Compare PostgreSQL and SQLite for offline use."
        self.version = {
            "model": "chair",
            "response": "Original synthesis",
            "mode": "best_of_breed",
            "timestamp": "2026-01-01T00:00:00",
        }
        self.metadata = {
            "status": "complete",
            "label_to_model": {"Response A": "model-a"},
            "aggregate_rankings": [],
            "prompt_refinement": {
                "decision": "use_refined",
                "refined_prompt": self.refined,
            },
        }
        storage.create_conversation("no-reviews")
        storage.add_user_message("no-reviews", "Choose a database.")
        storage.add_assistant_message(
            "no-reviews",
            [{"model": "model-a", "response": "SQLite works offline."}],
            None,
            [],
            {"current": deepcopy(self.version), "versions": [deepcopy(self.version)]},
            metadata=deepcopy(self.metadata),
        )

    async def request(self, endpoint: str, payload: dict) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            return await client.post(
                f"/api/conversations/no-reviews/{endpoint}",
                json={"message_index": 0, **payload},
                headers={"X-API-Key": main.API_AUTH_KEY},
            )

    async def check_rerun(self, endpoint: str, payload: dict, count: int) -> None:
        # Keep real API, synthesis-context packing and file persistence. Only the
        # external provider is mocked, so no network requests or charges occur.
        provider = AsyncMock(return_value={"content": "New synthesis"})
        with patch.object(council, "query_model", provider):
            response = await self.request(endpoint, payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(provider.await_count, count)
        for call in provider.await_args_list:
            prompt = call.args[1][0]["content"]
            self.assertIn(self.refined, prompt)
            self.assertIn("SQLite works offline.", prompt)
            self.assertIn("No valid aggregate ranking was available.", prompt)
        message = storage.get_conversation("no-reviews")["messages"][1]
        self.assertEqual(message["stage2"], [])
        self.assertEqual(message["stage3"]["versions"][0], self.version)
        self.assertEqual(len(message["stage3"]["versions"]), count + 1)
        self.assertEqual(
            message["metadata"]["prompt_refinement"], self.metadata["prompt_refinement"]
        )
        if endpoint == "resynthesize":
            self.assertEqual(message["stage3"]["current"], response.json()["version"])
        else:
            self.assertEqual(message["stage3"]["current"], self.version)

    async def test_single_rerun_without_reviews(self) -> None:
        await self.check_rerun("resynthesize", {"synthesis_mode": "weighted"}, 1)

    async def test_all_modes_rerun_without_reviews(self) -> None:
        await self.check_rerun("resynthesize-all", {}, 3)

    async def test_incomplete_or_invalid_data_never_calls_provider(self) -> None:
        original = storage.get_conversation("no-reviews")
        cases = [
            ("stage2", None),
            ("stage2", {}),
            ("stage2", "invalid"),
            ("stage1", []),
            ("stage3", None),
        ]
        # Missing stage2 is distinct from the persisted empty-list outcome.
        cases.append(("stage2", "missing"))
        for field, value in cases:
            for endpoint in ("resynthesize", "resynthesize-all"):
                with self.subTest(field=field, value=value, endpoint=endpoint):
                    conversation = deepcopy(original)
                    if value == "missing":
                        del conversation["messages"][1][field]
                    else:
                        conversation["messages"][1][field] = value
                    storage.save_conversation(conversation)
                    provider = AsyncMock()
                    with patch.object(council, "query_model", provider):
                        response = await self.request(
                            endpoint, {"synthesis_mode": "weighted"}
                        )
                    self.assertEqual(response.status_code, 400, response.text)
                    provider.assert_not_awaited()
                    self.assertEqual(
                        storage.get_conversation("no-reviews"), conversation
                    )


if __name__ == "__main__":
    unittest.main()
