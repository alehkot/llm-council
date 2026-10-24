"""Offline restart regressions for journals whose conversation is unavailable."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import consultation_journal, storage  # noqa: E402
from backend.consultation_manager import ConsultationManager  # noqa: E402


class UnavailableConversationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.conversations = Path(self.tempdir.name) / "conversations"
        self.journals = Path(self.tempdir.name) / "consultations"
        for patcher in (
            patch.object(storage, "DATA_DIR", str(self.conversations)),
            patch.object(consultation_journal, "_CONSULTATION_DIR", self.journals),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        # Isolate recovery from the application's singleton and never start model work.
        self.manager = object.__new__(ConsultationManager)
        self.manager._initialized = False
        self.manager.__init__()
        self.addCleanup(self.manager.shutdown)
        patcher = patch.object(self.manager._executor, "submit")
        self.submit = patcher.start()
        self.addCleanup(patcher.stop)

    def create_active(self, conversation_id: str, job_id: str) -> None:
        storage.create_conversation(conversation_id)
        consultation_journal.create_job(job_id, conversation_id, "Question", None, "pending")
        state = self.manager._create_started_state(
            job_id, conversation_id, "Question", None, resume_from_storage=False
        )
        self.manager._consultations[conversation_id] = state

    def simulate_restart(self) -> None:
        self.manager._consultations.clear()
        self.manager._queues.clear()
        self.manager._futures.clear()
        self.manager._restored = False
        self.submit.reset_mock()

    def test_cancel_delete_then_restart_does_not_recreate_or_run_conversation(self) -> None:
        self.create_active("deleted", "cancelled-job")
        self.assertTrue(self.manager.cancel_consultation("deleted"))
        self.assertTrue(storage.delete_conversation("deleted"))
        before = (self.journals / "cancelled-job.json").read_bytes()
        self.simulate_restart()

        self.manager.restore_incomplete_consultations()
        self.manager.restore_incomplete_consultations()

        self.submit.assert_not_called()
        self.assertIsNone(self.manager.get_status("deleted"))
        self.assertFalse((self.conversations / "deleted.json").exists())
        self.assertEqual((self.journals / "cancelled-job.json").read_bytes(), before)

    def test_missing_queued_conversation_does_not_block_healthy_active_and_queue(self) -> None:
        consultation_journal.create_job("orphan", "a-missing", "Gone", None, "queued")
        orphan = (self.journals / "orphan.json").read_bytes()
        self.create_active("healthy", "active")
        consultation_journal.create_job("queued", "healthy", "Next", None, "queued")
        self.simulate_restart()

        self.manager.restore_incomplete_consultations()
        self.manager.restore_incomplete_consultations()

        self.submit.assert_called_once()
        state = self.submit.call_args.args[1]
        self.assertEqual(state.id, "active")
        self.assertTrue(state.resume_from_storage)
        self.assertEqual([item["id"] for item in self.manager.get_queue("healthy")], ["queued"])
        self.assertEqual(self.manager.get_queue("a-missing"), [])
        self.assertEqual((self.journals / "orphan.json").read_bytes(), orphan)
        self.assertFalse((self.conversations / "a-missing.json").exists())

    def test_corrupt_conversation_and_journal_survive_for_later_repair(self) -> None:
        self.create_active("repairable", "repair-job")
        path = self.conversations / "repairable.json"
        original = path.read_bytes()
        path.write_text('{"unfinished":', encoding="utf-8")
        journal = (self.journals / "repair-job.json").read_bytes()
        self.simulate_restart()

        self.manager.restore_incomplete_consultations()

        self.submit.assert_not_called()
        self.assertEqual(path.read_text(encoding="utf-8"), '{"unfinished":')
        self.assertEqual((self.journals / "repair-job.json").read_bytes(), journal)
        path.write_bytes(original)
        self.simulate_restart()
        self.manager.restore_incomplete_consultations()
        self.submit.assert_called_once()
        self.assertEqual(self.submit.call_args.args[1].id, "repair-job")

    def test_healthy_queued_only_job_still_starts(self) -> None:
        storage.create_conversation("queued-conversation")
        consultation_journal.create_job("queued", "queued-conversation", "Next", None, "queued")

        self.manager.restore_incomplete_consultations()

        self.submit.assert_called_once()
        self.assertEqual(self.submit.call_args.args[1].id, "queued")
        self.assertEqual(self.manager.get_queue("queued-conversation"), [])

    def test_legacy_incomplete_conversation_without_journal_still_recovers(self) -> None:
        self.create_active("legacy", "legacy-job")
        consultation_journal.delete_job("legacy-job")
        self.simulate_restart()

        self.manager.restore_incomplete_consultations()

        self.submit.assert_called_once()
        self.assertEqual(self.submit.call_args.args[1].id, "legacy-job")
        self.assertIsNotNone(consultation_journal.get_job("legacy-job"))


if __name__ == "__main__":
    unittest.main()
