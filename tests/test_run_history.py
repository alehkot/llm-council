"""Internal history discovery over real receipts; no providers or public routes."""

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    consultation_journal,
    durable_refinement,
    refinement_execution,
    run_admission,
    run_cancellation,
    run_history,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = str(self.root / "conversations")
        for obj, key, value in (
            (storage, "DATA_DIR", self.data),
            (consultation_journal, "_CONSULTATION_DIR", self.root / "consultations"),
        ):
            mock = patch.object(obj, key, value)
            mock.start()
            self.addCleanup(mock.stop)

    def prepare(self, *, identity=None, timestamp=None, content="Question"):
        identity = identity or str(uuid4())
        run_admission.prepare(
            identity,
            CreateRunRequest(
                content=content,
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        if timestamp is not None:
            record = run_admission.get(identity)
            record["created_at"] = timestamp
            run_admission._write(record)
        return identity

    def bytes(self):
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def history(self, **kwargs):
        before = self.bytes()
        with (
            patch.object(
                run_admission, "_write", side_effect=AssertionError("history wrote")
            ),
            patch.object(
                run_cancellation,
                "inspect",
                side_effect=AssertionError("history settled"),
            ),
            patch.object(
                refinement_execution,
                "suggest",
                side_effect=AssertionError("history dispatched"),
            ),
        ):
            result = run_history.list_runs(**kwargs)
        self.assertEqual(before, self.bytes())
        return result

    async def suggest(self, identity):
        async def synthetic(**kwargs):
            return {
                "intent": "Intent",
                "clarifying_questions": [],
                "refined_prompt": "Refined question",
            }

        return await refinement_execution.suggest(
            identity, synthetic, provider_key="history-test-v1", expected_revision=0
        )

    def test_empty_read_does_not_create_storage(self):
        self.assertEqual(self.history(), {"runs": [], "next_cursor": None})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_utc_order_and_uuid_ties_do_not_depend_on_file_order(self):
        low = self.prepare(identity=str(UUID(int=1)), timestamp="2026-01-01T12:00:00")
        older = self.prepare(timestamp="2026-01-01T12:30:00+01:00")
        high = self.prepare(
            identity=str(UUID(int=2)), timestamp="2026-01-01T07:00:00-05:00"
        )
        self.assertEqual(
            [r["run_id"] for r in self.history()["runs"]], [high, low, older]
        )

    def test_unicode_excerpt_is_bounded_and_summary_is_detached(self):
        identity = self.prepare(content="Ж🙂" * 150)
        page = self.history()
        summary = page["runs"][0]
        self.assertEqual(summary["question_excerpt"], "Ж🙂" * 80)
        self.assertEqual(summary["internal_phase"], "prepared")
        self.assertEqual(
            set(summary),
            {
                "run_id",
                "conversation_id",
                "created_at",
                "question_excerpt",
                "internal_phase",
                "cursor",
            },
        )
        summary["question_excerpt"] = "changed"
        self.assertEqual(self.history()["runs"][0]["question_excerpt"], "Ж🙂" * 80)
        self.assertEqual(run_admission.get(identity)["request"]["content"], "Ж🙂" * 150)

    def test_pages_cover_more_than_max_page_without_duplicates(self):
        identities = [
            self.prepare(identity=str(UUID(int=i + 1)), timestamp="2026-01-01T00:00:00")
            for i in range(103)
        ]
        first = self.history(limit=100)
        self.assertEqual(len(first["runs"]), 100)
        second = self.history(limit=100, before=first["next_cursor"])
        self.assertEqual(
            [r["run_id"] for r in first["runs"] + second["runs"]],
            list(reversed(identities)),
        )
        self.assertIsNone(second["next_cursor"])

    def test_deleted_cursor_anchor_and_newer_insert_do_not_break_pagination(self):
        oldest, middle, newest = [
            self.prepare(identity=str(UUID(int=i)), timestamp="2026-01-01T00:00:00")
            for i in (1, 2, 3)
        ]
        first = self.history(limit=1)
        self.assertEqual(first["runs"][0]["run_id"], newest)
        self.assertTrue(storage.delete_conversation(f"cli-{newest}"))
        inserted = self.prepare(timestamp="2026-02-01T00:00:00")
        self.assertEqual(
            [r["run_id"] for r in self.history(before=first["next_cursor"])["runs"]],
            [middle, oldest],
        )
        self.assertEqual(self.history()["runs"][0]["run_id"], inserted)

    def test_bad_limits_and_cursors_fail_without_storage_access(self):
        for limit in (True, False, 0, -1, 101, 1.0, "1", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.history(limit=limit)
        for cursor in (
            "",
            "?",
            "x" * 513,
            42,
            "e30",
            base64.urlsafe_b64encode(b"[]").decode(),
            base64.urlsafe_b64encode(b"\xff").decode(),
        ):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                self.history(before=cursor)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cursor_rejects_noncanonical_padding_and_wrong_version(self):
        for i in (1, 2):
            self.prepare(identity=str(UUID(int=i)), timestamp="2026-01-01T00:00:00")
        cursor = self.history(limit=1)["next_cursor"]
        with self.assertRaises(ValueError):
            self.history(before=cursor + "=")
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        value["version"] = True
        bad = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
        with self.assertRaises(ValueError):
            self.history(before=bad)

    async def test_real_prompt_answer_completion_and_cancellation_phases(self):
        identity = self.prepare()
        await self.suggest(identity)
        waiting = self.history()["runs"][0]
        self.assertEqual(waiting["internal_phase"], "waiting_input")
        instance = run_interaction.get(identity)["prompts"][-1]["instance"][
            "instance_id"
        ]
        run_interaction.answer(
            identity,
            instance,
            AnswerRequest(responses={"refinement_action": "use_refined"}),
        )
        accepted = self.history()["runs"][0]
        self.assertEqual(accepted["internal_phase"], "answer_accepted")
        durable_refinement.resume_answer(identity, instance)
        complete = self.history()["runs"][0]
        self.assertEqual(complete["internal_phase"], "refinement_complete")
        self.assertEqual(
            len({row["cursor"] for row in (waiting, accepted, complete)}), 3
        )
        run_cancellation.request(identity)
        self.assertEqual(self.history()["runs"][0]["internal_phase"], "cancelled")

    async def test_uncertain_work_survives_restart_and_transient_journal_removal(self):
        identity = self.prepare()

        async def fail(**kwargs):
            raise RuntimeError("uncertain synthetic outcome")

        with self.assertRaises(RuntimeError):
            await refinement_execution.suggest(
                identity, fail, provider_key="history-test-v1", expected_revision=0
            )
        consultation_journal.create_job(
            identity, f"cli-{identity}", "Question", None, "running", assistant_index=1
        )
        consultation_journal.delete_job(identity)
        expected = self.history()
        self.assertEqual(expected["runs"][0]["internal_phase"], "needs_retry")
        before = self.bytes()
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys; from backend import storage,run_history; storage.DATA_DIR=sys.argv[1]; print(json.dumps(run_history.list_runs()))",
                self.data,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        self.assertEqual(json.loads(child.stdout), expected)
        self.assertEqual(before, self.bytes())

    def test_corrupt_receipt_is_not_silently_omitted_even_outside_page(self):
        older = self.prepare(timestamp="2025-01-01T00:00:00")
        self.prepare(timestamp="2026-01-01T00:00:00")
        run_admission._path(older).write_text("{broken", encoding="utf-8")
        before = self.bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.history(limit=1)
        self.assertEqual(before, self.bytes())

    def test_invalid_timestamp_and_filename_fail_honestly(self):
        identity = self.prepare()
        record = run_admission.get(identity)
        record["created_at"] = "not a timestamp"
        run_admission._write(record)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.history()
        run_admission._path(identity).unlink()
        (run_admission._directory() / "unknown.json").write_text("{}")
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.history()

    def test_selected_corrupt_checkpoint_or_missing_conversation_fails(self):
        identity = self.prepare()
        saved = run_admission.get(identity)
        broken = dict(saved, refinement_execution={"version": 999})
        run_admission._write(broken)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.history()
        run_admission._write(saved)
        Path(storage.get_conversation_path(f"cli-{identity}")).unlink()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.history()

    def test_legacy_conversations_and_atomic_temp_files_are_not_runs(self):
        storage.create_conversation("legacy-conversation")
        identity = self.prepare()
        (run_admission._directory() / ".receipt.json.temporary.tmp").write_text(
            "partial"
        )
        self.assertEqual([r["run_id"] for r in self.history()["runs"]], [identity])
