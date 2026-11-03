"""Completed snapshots must not overwrite persisted resynthesis results."""

import asyncio
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import main, storage  # noqa: E402
from backend.consultation_manager import (  # noqa: E402
    ConsultationManager,
    ConsultationState,
    ConsultationStatus,
)


class CompletedStatusRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        data_patch = patch.object(storage, "DATA_DIR", self.tempdir.name)
        data_patch.start()
        self.addCleanup(data_patch.stop)
        self.manager = ConsultationManager()
        # Isolate this singleton without submitting workers or changing its pool.
        states_patch = patch.object(self.manager, "_consultations", {})
        states_patch.start()
        self.addCleanup(states_patch.stop)
        queue_patch = patch.object(self.manager, "_queues", {})
        queue_patch.start()
        self.addCleanup(queue_patch.stop)
        self.version = {
            "model": "chair",
            "response": "Original synthesis",
            "mode": "best_of_breed",
            "timestamp": "2026-01-01T00:00:00",
        }
        self.stage3 = {"current": self.version, "versions": [self.version]}
        self.metadata = {
            "status": "complete",
            "consultation_id": "job-original",
            "synthesis_mode": "best_of_breed",
        }
        storage.create_conversation("completed")
        storage.add_user_message("completed", "A synthetic question")
        storage.add_assistant_message(
            "completed",
            [{"model": "member", "response": "A synthetic answer"}],
            None,
            [],
            deepcopy(self.stage3),
            metadata=deepcopy(self.metadata),
        )
        self.state = ConsultationState(
            id="job-original",
            conversation_id="completed",
            message_content="A synthetic question",
            negative_prompt=None,
            status=ConsultationStatus.COMPLETE,
            assistant_index=1,
            stage1_results=[{"model": "member", "response": "A synthetic answer"}],
            stage2_results=[],
            stage3_result=deepcopy(self.stage3),
            metadata=deepcopy(self.metadata),
        )
        self.manager._consultations["completed"] = self.state

    async def request(self, endpoint: str, payload: dict) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            return await client.post(
                f"/api/conversations/completed/{endpoint}",
                json={"message_index": 0, **payload},
                headers={"X-API-Key": main.API_AUTH_KEY},
            )

    def assert_current_snapshot(self, previous_cursor: str) -> dict:
        persisted = storage.get_conversation("completed")["messages"][1]
        status = self.manager.get_status("completed")
        snapshot = self.manager.get_sync_snapshot("completed")
        for payload in (status, snapshot):
            self.assertEqual(payload["stage3"], persisted["stage3"])
            self.assertEqual(payload["metadata"], persisted["metadata"])
            self.assertEqual(payload["assistant_index"], 1)
            self.assertEqual(payload["consultation_id"], "job-original")
        self.assertNotEqual(snapshot["cursor"], previous_cursor)
        self.assertEqual(
            snapshot["cursor"],
            self.manager.get_sync_snapshot("completed")["cursor"],
        )
        # Read-through must not mutate the original completion state or storage.
        self.assertEqual(self.state.stage3_result, self.stage3)
        self.assertEqual(self.state.metadata, self.metadata)
        return snapshot

    async def test_single_resynthesis_refreshes_status_and_cursor(self) -> None:
        before = self.manager.get_sync_snapshot("completed")["cursor"]
        new_version = {
            **self.version,
            "response": "Weighted answer",
            "mode": "weighted",
        }
        with patch.object(main, "_run_synthesis", AsyncMock(return_value=new_version)):
            response = await self.request(
                "resynthesize", {"synthesis_mode": "weighted"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.assert_current_snapshot(before)
        self.assertEqual(snapshot["stage3"]["current"], new_version)
        self.assertEqual(snapshot["metadata"]["synthesis_mode"], "weighted")

    async def test_all_resynthesis_refreshes_versions_without_changing_current(
        self,
    ) -> None:
        before = self.manager.get_sync_snapshot("completed")["cursor"]
        new_version = {
            **self.version,
            "response": "Weighted answer",
            "mode": "weighted",
        }
        with patch.object(main, "_run_synthesis", AsyncMock(return_value=new_version)):
            response = await self.request("resynthesize-all", {"modes": ["weighted"]})
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.assert_current_snapshot(before)
        self.assertEqual(snapshot["stage3"]["current"], self.version)
        self.assertEqual(snapshot["stage3"]["versions"], [self.version, new_version])

    async def test_set_current_version_refreshes_snapshot(self) -> None:
        new_version = {
            **self.version,
            "response": "Weighted answer",
            "mode": "weighted",
        }
        storage.append_stage3_version("completed", 1, new_version, set_as_current=False)
        before = self.manager.get_sync_snapshot("completed")["cursor"]
        response = await self.request("set-current-version", {"version_index": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.assert_current_snapshot(before)["stage3"]["current"], new_version
        )

    async def test_older_assistant_mutation_does_not_replace_latest_completion(
        self,
    ) -> None:
        storage.add_user_message("completed", "Second question")
        second_metadata = {**self.metadata, "consultation_id": "job-second"}
        second_stage3 = {"model": "chair", "response": "Second answer"}
        storage.add_assistant_message(
            "completed", [], None, [], second_stage3, metadata=second_metadata
        )
        self.state.id = "job-second"
        self.state.assistant_index = 3
        self.state.stage3_result = deepcopy(second_stage3)
        self.state.metadata = second_metadata
        before = self.manager.get_sync_snapshot("completed")
        with patch.object(main, "_run_synthesis", AsyncMock(return_value=self.version)):
            response = await self.request(
                "resynthesize", {"synthesis_mode": "weighted"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.manager.get_sync_snapshot("completed"), before)

    async def test_new_active_run_is_preserved_during_resynthesis(self) -> None:
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def delayed_synthesis(*args: object) -> dict:
            entered.set()
            await resume.wait()
            return self.version

        with patch.object(main, "_run_synthesis", delayed_synthesis):
            request = asyncio.create_task(
                self.request("resynthesize", {"synthesis_mode": "weighted"})
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            active = ConsultationState(
                id="job-active",
                conversation_id="completed",
                message_content="New",
                negative_prompt=None,
                status=ConsultationStatus.STAGE1,
                assistant_index=3,
                stage1_results=[{"response": "Partial answer"}],
            )
            self.manager._consultations["completed"] = active
            before = self.manager.get_sync_snapshot("completed")
            resume.set()
            response = await asyncio.wait_for(request, timeout=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.manager.get_sync_snapshot("completed"), before)

    async def test_missing_or_different_persisted_identity_is_not_adopted(self) -> None:
        for identity in (None, "job-other"):
            with self.subTest(identity=identity):
                conversation = storage.get_conversation("completed")
                conversation["messages"][1]["metadata"]["consultation_id"] = identity
                conversation["messages"][1]["stage3"]["current"]["response"] = (
                    "Unrelated"
                )
                storage.save_conversation(conversation)
                self.assertEqual(
                    self.manager.get_status("completed"), self.state.to_dict()
                )
        storage.delete_conversation("completed")
        self.assertEqual(self.manager.get_status("completed"), self.state.to_dict())

    async def test_missing_index_wrong_role_and_nonterminal_storage_are_not_adopted(
        self,
    ) -> None:
        original = storage.get_conversation("completed")
        for index in (None, -1, 0, 2):
            with self.subTest(index=index):
                self.state.assistant_index = index
                self.assertEqual(
                    self.manager.get_status("completed"), self.state.to_dict()
                )
        self.state.assistant_index = 1
        conversation = deepcopy(original)
        conversation["messages"][1]["metadata"]["status"] = "stage3"
        conversation["messages"][1]["stage3"]["current"]["response"] = "Partial"
        storage.save_conversation(conversation)
        self.assertEqual(self.manager.get_status("completed"), self.state.to_dict())

    async def test_long_poll_wakes_for_persisted_version_selection(self) -> None:
        new_version = {
            **self.version,
            "response": "Weighted answer",
            "mode": "weighted",
        }
        storage.append_stage3_version("completed", 1, new_version, set_as_current=False)
        before = self.manager.get_sync_snapshot("completed")["cursor"]
        # Make the real status route observe the old cursor once, then mutate via
        # the real version-selection route while the request is waiting.
        observed = asyncio.Event()
        original_snapshot = self.manager.get_sync_snapshot

        def snapshot(conversation_id: str) -> dict:
            result = original_snapshot(conversation_id)
            observed.set()
            return result

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            with (
                patch.object(self.manager, "get_sync_snapshot", snapshot),
                patch.object(main, "STATUS_LONGPOLL_CHECK_INTERVAL_SECONDS", 0.01),
            ):
                pending = asyncio.create_task(
                    client.get(
                        "/api/conversations/completed/consultation/status",
                        params={"cursor": before, "wait": 5},
                        headers={"X-API-Key": main.API_AUTH_KEY},
                    )
                )
                try:
                    await asyncio.wait_for(observed.wait(), timeout=1)
                    selected = await self.request(
                        "set-current-version", {"version_index": 1}
                    )
                    self.assertEqual(selected.status_code, 200)
                    response = await asyncio.wait_for(pending, timeout=1)
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["cursor"], before)
        self.assertEqual(response.json()["stage3"]["current"], new_version)

    async def test_active_and_failed_states_do_not_read_terminal_storage(self) -> None:
        for status in ConsultationStatus:
            if status == ConsultationStatus.COMPLETE:
                continue
            with self.subTest(status=status):
                self.state.status = status
                with patch.object(
                    storage,
                    "get_conversation",
                    side_effect=AssertionError("Unexpected read"),
                ):
                    self.assertEqual(
                        self.manager.get_status("completed"), self.state.to_dict()
                    )
                    self.assertEqual(
                        self.manager.get_sync_snapshot("completed")["status"],
                        status.value,
                    )

    async def test_completed_snapshots_do_not_alias_cached_state_or_each_other(
        self,
    ) -> None:
        first = self.manager.get_sync_snapshot("completed")
        first["stage3"]["current"]["response"] = "Mutated client payload"
        first["metadata"]["synthesis_mode"] = "mutated"
        first["stage1"][0]["response"] = "Mutated member"
        self.assertEqual(self.state.stage3_result, self.stage3)
        self.assertEqual(self.state.metadata, self.metadata)
        second = self.manager.get_sync_snapshot("completed")
        self.assertEqual(second["stage1"][0]["response"], "A synthetic answer")
        self.assertEqual(second["stage3"], self.stage3)


if __name__ == "__main__":
    unittest.main()
