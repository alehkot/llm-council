import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from fastapi.testclient import TestClient  # noqa: E402

from backend import consultation_journal, storage  # noqa: E402
from backend.consultation_manager import (  # noqa: E402
    ConsultationManager,
    ConsultationStatus,
)
from backend.main import app  # noqa: E402

AUTH_HEADERS = {"X-API-Key": "test-api-key"}


class SnapshotTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_consultation_dir = consultation_journal._CONSULTATION_DIR
        storage.DATA_DIR = str(Path(self.tempdir.name) / "conversations")
        consultation_journal._CONSULTATION_DIR = (
            Path(self.tempdir.name) / "consultations"
        )
        self.manager = ConsultationManager()
        self._reset_manager_state()

    def tearDown(self) -> None:
        self._reset_manager_state()
        storage.DATA_DIR = self.old_data_dir
        consultation_journal._CONSULTATION_DIR = self.old_consultation_dir
        self.tempdir.cleanup()

    def _reset_manager_state(self) -> None:
        with self.manager._lock:
            self.manager._consultations.clear()
            self.manager._queues.clear()
            self.manager._futures.clear()
            self.manager._interactive_sessions.clear()

    def _install_state(self, conversation_id: str, consultation_id: str):
        if storage.get_conversation(conversation_id) is None:
            storage.create_conversation(conversation_id)
        state = self.manager._create_started_state(
            consultation_id,
            conversation_id,
            "test question",
            None,
            resume_from_storage=False,
        )
        with self.manager._lock:
            self.manager._consultations[conversation_id] = state
        return state


class SyncSnapshotTests(SnapshotTestBase):
    def test_none_snapshot_includes_queue_and_cursor(self) -> None:
        snapshot = self.manager.get_sync_snapshot("missing-conversation")

        self.assertEqual(snapshot["status"], "none")
        self.assertEqual(snapshot["queue"], [])
        self.assertTrue(snapshot["cursor"])

    def test_cursor_stable_while_state_unchanged(self) -> None:
        self._install_state("conv-stable", "job-stable")

        first = self.manager.get_sync_snapshot("conv-stable")
        second = self.manager.get_sync_snapshot("conv-stable")

        self.assertEqual(first["cursor"], second["cursor"])

    def test_cursor_changes_on_status_update(self) -> None:
        state = self._install_state("conv-change", "job-change")
        before = self.manager.get_sync_snapshot("conv-change")["cursor"]

        with self.manager._lock:
            state.status = ConsultationStatus.STAGE1

        after = self.manager.get_sync_snapshot("conv-change")["cursor"]
        self.assertNotEqual(before, after)

    def test_cursor_changes_on_partial_results(self) -> None:
        state = self._install_state("conv-partial", "job-partial")
        before = self.manager.get_sync_snapshot("conv-partial")["cursor"]

        with self.manager._lock:
            state.stage1_results = [{"model": "model-a", "response": "draft"}]

        after = self.manager.get_sync_snapshot("conv-partial")["cursor"]
        self.assertNotEqual(before, after)

    def test_global_snapshot_has_cursor(self) -> None:
        snapshot = self.manager.get_global_snapshot()

        self.assertIn("active", snapshot)
        self.assertTrue(snapshot["cursor"])


class LongPollEndpointTests(SnapshotTestBase):
    def setUp(self) -> None:
        super().setUp()
        # TestClient is used without its context manager on purpose: the app
        # lifespan would shut down the shared consultation executor.
        self.client = TestClient(app)
        self.interval_patch = patch(
            "backend.main.STATUS_LONGPOLL_CHECK_INTERVAL_SECONDS", 0.05
        )
        self.interval_patch.start()

    def tearDown(self) -> None:
        self.interval_patch.stop()
        super().tearDown()

    def test_immediate_response_without_cursor(self) -> None:
        storage.create_conversation("conv-imm")

        response = self.client.get(
            "/api/conversations/conv-imm/consultation/status",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "none")
        self.assertEqual(body["queue"], [])
        self.assertTrue(body["cursor"])

    def test_unknown_conversation_is_404(self) -> None:
        response = self.client.get(
            "/api/conversations/nope/consultation/status",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 404)

    def test_longpoll_times_out_with_unchanged_cursor(self) -> None:
        storage.create_conversation("conv-timeout")
        cursor = self.client.get(
            "/api/conversations/conv-timeout/consultation/status",
            headers=AUTH_HEADERS,
        ).json()["cursor"]

        started = time.monotonic()
        response = self.client.get(
            "/api/conversations/conv-timeout/consultation/status",
            params={"wait": 0.4, "cursor": cursor},
            headers=AUTH_HEADERS,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cursor"], cursor)
        self.assertGreaterEqual(elapsed, 0.3)

    def test_longpoll_wakes_on_change(self) -> None:
        storage.create_conversation("conv-wake")
        state = self._install_state("conv-wake", "job-wake")
        cursor = self.client.get(
            "/api/conversations/conv-wake/consultation/status",
            headers=AUTH_HEADERS,
        ).json()["cursor"]

        def mutate_later() -> None:
            time.sleep(0.2)
            with self.manager._lock:
                state.status = ConsultationStatus.STAGE1

        mutator = threading.Thread(target=mutate_later)
        mutator.start()
        try:
            started = time.monotonic()
            response = self.client.get(
                "/api/conversations/conv-wake/consultation/status",
                params={"wait": 5, "cursor": cursor},
                headers=AUTH_HEADERS,
            )
            elapsed = time.monotonic() - started
        finally:
            mutator.join()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "stage1")
        self.assertNotEqual(body["cursor"], cursor)
        # Returned on the change, well before the 5s wait window lapsed.
        self.assertLess(elapsed, 2.0)

    def test_global_longpoll_times_out_with_unchanged_cursor(self) -> None:
        cursor = self.client.get(
            "/api/consultations/status", headers=AUTH_HEADERS
        ).json()["cursor"]

        response = self.client.get(
            "/api/consultations/status",
            params={"wait": 0.3, "cursor": cursor},
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cursor"], cursor)


if __name__ == "__main__":
    unittest.main()
