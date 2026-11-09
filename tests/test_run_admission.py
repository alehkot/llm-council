"""Offline identity, initialization and deletion checks for internal CLI admission."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import consultation_journal, run_admission, storage
from backend.consultation_contract import CLI_API_VERSION, CreateRunRequest
from backend.consultation_manager import ConsultationManager

RUN_ID = "12345678-1234-4234-8234-123456789abc"


def request(**changes: object) -> CreateRunRequest:
    return CreateRunRequest.model_validate(
        {
            "content": "  A synthetic question — вопрос 🦫\n",
            "interaction_policy": "persistent",
            "recovery_policy": "explicit_retry",
        }
        | changes
    )


class RunAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.conversations = self.root / "conversations"
        self.journals = self.root / "consultations"
        for patcher in (
            patch.object(storage, "DATA_DIR", str(self.conversations)),
            patch.object(consultation_journal, "_CONSULTATION_DIR", self.journals),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = self.new_manager()

    def new_manager(self) -> ConsultationManager:
        manager = object.__new__(ConsultationManager)
        manager._initialized = False
        manager.__init__()
        self.addCleanup(manager.shutdown)
        patcher = patch.object(manager._executor, "submit")
        patcher.start()
        self.addCleanup(patcher.stop)
        return manager

    def prepare(self, run_id: str = RUN_ID, **changes: object) -> dict:
        return self.manager.prepare_cli_run(run_id, request(**changes))

    def receipt_path(self, run_id: str = RUN_ID) -> Path:
        return self.root / "run_receipts" / f"{run_id}.json"

    def conversation_path(self, run_id: str = RUN_ID) -> Path:
        return self.conversations / f"cli-{run_id}.json"

    def leave_initializing(self, *, conversation_written: bool = False) -> dict:
        original = storage._write_json_file

        def interrupted(path: str, payload: dict) -> None:
            if Path(path) == self.conversation_path() and not conversation_written:
                raise OSError("synthetic interruption before conversation")
            if payload.get("phase") == "prepared":
                raise OSError("synthetic interruption before final receipt")
            original(path, payload)

        with patch.object(storage, "_write_json_file", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.prepare()
        record = run_admission.get(RUN_ID)
        self.assertEqual(record["phase"], "initializing")
        self.assertEqual(self.conversation_path().exists(), conversation_written)
        return record

    def assert_tombstone(self, run_id: str = RUN_ID) -> None:
        tombstone = run_admission.get(run_id)
        self.assertEqual(
            set(tombstone),
            {"version", "run_id", "conversation_id", "phase", "deleted_at"},
        )
        self.assertEqual(tombstone["phase"], "deleted")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.prepare(run_id)
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.prepare(run_id, content="Different synthetic question")
        self.assertFalse(self.conversation_path(run_id).exists())

    def test_materializes_defaults_and_preserves_exact_unicode_text(self) -> None:
        record = self.prepare()
        self.assertEqual(record["request"], request().model_dump(mode="json"))
        self.assertEqual(record["phase"], "prepared")
        self.assertEqual(record["assistant_index"], 1)
        self.assertEqual(record["conversation_id"], f"cli-{RUN_ID}")
        conversation = storage.get_conversation(record["conversation_id"])
        self.assertEqual(conversation["messages"][0]["content"], request().content)
        self.assertEqual(len(conversation["messages"]), 2)
        self.assertEqual(
            conversation["messages"][1]["metadata"]["consultation_id"], RUN_ID
        )
        self.assertEqual(consultation_journal.list_jobs(), [])
        self.manager._executor.submit.assert_not_called()

    def test_canonical_uuid_and_explicit_defaults_have_same_receipt(self) -> None:
        initial = self.prepare(RUN_ID.upper())
        retried = self.prepare(
            UUID(RUN_ID).hex,
            negative_prompt=None,
            skill_ids=[],
            refine=False,
        )
        self.assertEqual(initial, retried)
        self.assertEqual(len(list(self.conversations.glob("*.json"))), 1)
        self.assertEqual(len(list((self.root / "run_receipts").glob("*.json"))), 1)

    def test_concurrent_duplicate_submission_creates_one_conversation(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as executor:
            records = list(executor.map(lambda _: self.prepare(), range(32)))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(len(list(self.conversations.glob("*.json"))), 1)
        self.assertEqual(len(storage.get_conversation(f"cli-{RUN_ID}")["messages"]), 2)
        self.manager._executor.submit.assert_not_called()

    def test_each_changed_payload_field_conflicts_without_mutation(self) -> None:
        self.prepare()
        before = self.receipt_path().read_bytes(), self.conversation_path().read_bytes()
        for changes in (
            {"content": "Other question"},
            {"negative_prompt": "Avoid speculation"},
            {"skill_ids": ["synthetic-skill"]},
            {"refine": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(run_admission.AdmissionConflict):
                    self.prepare(**changes)
                self.assertEqual(
                    (
                        self.receipt_path().read_bytes(),
                        self.conversation_path().read_bytes(),
                    ),
                    before,
                )

    def test_restart_repairs_receipt_before_conversation_without_new_identity(
        self,
    ) -> None:
        initial = self.leave_initializing()
        manager = self.new_manager()
        repaired = manager.prepare_cli_run(RUN_ID, request())
        self.assertEqual(repaired["created_at"], initial["created_at"])
        self.assertEqual(repaired["conversation_id"], initial["conversation_id"])
        self.assertEqual(repaired["phase"], "prepared")
        self.assertEqual(len(storage.get_conversation(f"cli-{RUN_ID}")["messages"]), 2)
        manager._executor.submit.assert_not_called()

    def test_restart_repairs_conversation_before_acknowledgment_without_duplicate(
        self,
    ) -> None:
        initial = self.leave_initializing(conversation_written=True)
        before = self.conversation_path().read_bytes()
        manager = self.new_manager()
        repaired = manager.prepare_cli_run(RUN_ID, request())
        self.assertEqual(repaired["created_at"], initial["created_at"])
        self.assertEqual(self.conversation_path().read_bytes(), before)
        self.assertEqual(repaired["phase"], "prepared")
        manager._executor.submit.assert_not_called()

    def test_same_identity_survives_fresh_python_processes(self) -> None:
        script = """
import json
import socket
from unittest.mock import patch

def forbidden(*args, **kwargs):
    raise AssertionError("No networking or model scheduling in admission")

with patch.object(socket.socket, "connect", forbidden):
    from backend.consultation_contract import CreateRunRequest
    from backend.consultation_manager import consultation_manager
    from backend import storage
    request = CreateRunRequest(
        content="Fresh process synthetic question",
        interaction_policy="persistent",
        recovery_policy="explicit_retry",
    )
    with patch.object(consultation_manager._executor, "submit", forbidden):
        receipt = consultation_manager.prepare_cli_run(
            "12345678-1234-4234-8234-123456789abc", request
        )
        consultation_manager.restore_incomplete_consultations()
        conversation = storage.get_conversation(receipt["conversation_id"])
        print(json.dumps({"receipt": receipt, "messages": conversation["messages"]}))
    consultation_manager.shutdown()
"""
        environment = os.environ | {
            "DATA_DIR": str(self.conversations),
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "API_AUTH_KEY": "test-api-key",
        }
        results = []
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
                check=True,
            )
            results.append(json.loads(result.stdout))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[0]["messages"]), 2)
        self.assertEqual(len(list(self.conversations.glob("*.json"))), 1)
        self.assertEqual(consultation_journal.list_jobs(), [])

    def test_history_survives_lower_prompt_limit_in_fresh_process(self) -> None:
        original = self.prepare()
        paths = (self.receipt_path(), self.conversation_path())
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        script = """
import json
import socket
from unittest.mock import patch

def forbidden(*args, **kwargs):
    raise AssertionError("No networking during history lookup")

with patch.object(socket.socket, "connect", forbidden):
    from backend import config, run_admission
    assert config.MAX_PROMPT_CHARS == 1
    receipt = run_admission.get("12345678-1234-4234-8234-123456789abc")
    print(json.dumps({"receipt": receipt, "history": run_admission.list_receipts()}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ
            | {
                "DATA_DIR": str(self.conversations),
                "MAX_PROMPT_CHARS": "1",
                "OPENROUTER_API_KEY": "test-openrouter-key",
                "API_AUTH_KEY": "test-api-key",
            },
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout), {"receipt": original, "history": [original]}
        )
        self.assertEqual(
            [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths], before
        )

    def test_malformed_saved_request_schema_fails_closed_without_mutation(self) -> None:
        original = self.prepare()
        payload = original["request"]
        malformed = [
            None,
            [],
            {key: value for key, value in payload.items() if key != "refine"},
            payload | {"unexpected": True},
        ]
        for key, values in {
            "content": [None, 42, "  "],
            "negative_prompt": [False, []],
            "refine": [0, "false"],
            "interaction_policy": [None, "interactive"],
            "recovery_policy": [None, "automatic"],
            "skill_ids": [
                None,
                "example",
                ["example", "example"],
                [42],
                [[]],
                ["../escape"],
            ],
        }.items():
            malformed.extend(payload | {key: value} for value in values)
        for changed in malformed:
            with self.subTest(payload=changed):
                self.receipt_path().write_text(
                    json.dumps(original | {"request": changed}), encoding="utf-8"
                )
                paths = (self.receipt_path(), self.conversation_path())
                before = [
                    (path.read_bytes(), path.stat().st_mtime_ns) for path in paths
                ]
                for action in (
                    lambda: run_admission.get(RUN_ID),
                    run_admission.list_receipts,
                    self.prepare,
                ):
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        action()
                self.assertEqual(
                    [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths],
                    before,
                )

    def test_terminal_result_and_journal_cleanup_do_not_erase_request_identity(
        self,
    ) -> None:
        receipt = self.prepare()
        conversation = storage.get_conversation(receipt["conversation_id"])
        assistant = conversation["messages"][1]
        assistant["stage3"] = {"model": "fake", "response": "Saved answer"}
        assistant["metadata"]["status"] = "complete"
        storage.save_conversation(conversation)
        consultation_journal.create_job(
            RUN_ID, receipt["conversation_id"], request().content, None, "complete", 1
        )
        consultation_journal.delete_job(RUN_ID)
        consultation_journal.clear_jobs()
        before = self.conversation_path().read_bytes()
        manager = self.new_manager()
        self.assertEqual(manager.prepare_cli_run(RUN_ID, request()), receipt)
        self.assertEqual(self.conversation_path().read_bytes(), before)
        self.assertEqual(run_admission.get(RUN_ID), receipt)
        manager._executor.submit.assert_not_called()

    def test_restart_never_schedules_prepared_cli_work(self) -> None:
        self.prepare()
        manager = self.new_manager()
        manager.restore_incomplete_consultations()
        manager.restore_incomplete_consultations()
        manager._executor.submit.assert_not_called()
        self.assertEqual(storage.list_incomplete_assistant_messages(), [])
        self.assertEqual(consultation_journal.list_jobs(), [])
        self.assertEqual(CLI_API_VERSION, 0)

    def test_corrupt_receipt_fails_closed_and_preserves_both_files(self) -> None:
        self.prepare()
        self.receipt_path().write_text('{"interrupted":', encoding="utf-8")
        before = self.receipt_path().read_bytes(), self.conversation_path().read_bytes()
        for action in (lambda: self.prepare(), lambda: run_admission.get(RUN_ID)):
            with self.assertRaises(run_admission.AdmissionUnavailable):
                action()
        self.assertEqual(
            (self.receipt_path().read_bytes(), self.conversation_path().read_bytes()),
            before,
        )

    def test_invalid_receipt_identity_or_payload_fails_closed(self) -> None:
        initial = self.prepare()
        for changed in (
            initial | {"run_id": str(UUID(int=1))},
            initial | {"conversation_id": "unrelated"},
            initial | {"phase": "running"},
            initial | {"version": 2},
            initial | {"version": True},
            initial | {"assistant_index": 999},
            initial | {"assistant_index": True},
            initial | {"request": {"content": "Missing required policies"}},
            initial | {"created_at": None},
            initial | {"created_at": "not-a-date"},
            initial | {"phase": []},
        ):
            with self.subTest(changed=changed):
                self.receipt_path().write_text(json.dumps(changed), encoding="utf-8")
                before = self.receipt_path().read_bytes()
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.prepare()
                self.assertEqual(self.receipt_path().read_bytes(), before)

    def test_corrupt_or_changed_initializing_conversation_is_not_overwritten(
        self,
    ) -> None:
        self.leave_initializing(conversation_written=True)
        original = self.conversation_path().read_bytes()
        for content in ('{"broken":', '{"id": "another"}', "[]"):
            with self.subTest(content=content):
                self.conversation_path().write_text(content, encoding="utf-8")
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.prepare()
                self.assertEqual(self.conversation_path().read_text(), content)
        self.conversation_path().write_bytes(original)
        self.assertEqual(self.prepare()["phase"], "prepared")

    def test_missing_or_corrupt_prepared_conversation_is_not_recreated(self) -> None:
        self.prepare()
        self.conversation_path().unlink()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.prepare()
        self.assertFalse(self.conversation_path().exists())
        for malformed in ('{"broken":', "[]", "null", '{"messages": null}'):
            with self.subTest(malformed=malformed):
                self.conversation_path().write_text(malformed, encoding="utf-8")
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.prepare()
                self.assertEqual(self.conversation_path().read_text(), malformed)

    def test_prepared_conversation_requires_matching_assistant_reference(self) -> None:
        self.prepare()
        original = json.loads(self.conversation_path().read_text())
        variations = []
        missing = copy.deepcopy(original)
        missing["messages"] = missing["messages"][:1]
        variations.append(missing)
        wrong_role = copy.deepcopy(original)
        wrong_role["messages"][1]["role"] = "user"
        variations.append(wrong_role)
        wrong_id = copy.deepcopy(original)
        wrong_id["messages"][1]["metadata"]["consultation_id"] = str(UUID(int=2))
        variations.append(wrong_id)
        missing_id = copy.deepcopy(original)
        del missing_id["messages"][1]["metadata"]["consultation_id"]
        variations.append(missing_id)
        for changed in variations:
            with self.subTest(changed=changed):
                self.conversation_path().write_text(
                    json.dumps(changed), encoding="utf-8"
                )
                before = self.conversation_path().read_bytes()
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.prepare()
                self.assertEqual(self.conversation_path().read_bytes(), before)

    def test_existing_conversation_without_receipt_is_not_overwritten(self) -> None:
        storage.create_conversation(f"cli-{RUN_ID}")
        before = self.conversation_path().read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.prepare()
        self.assertEqual(self.conversation_path().read_bytes(), before)
        self.assertFalse(self.receipt_path().exists())

    def test_single_deletion_erases_payload_and_delayed_duplicate_cannot_resurrect(
        self,
    ) -> None:
        self.prepare(negative_prompt="Synthetic private text", skill_ids=["example"])
        self.assertTrue(storage.delete_conversation(f"cli-{RUN_ID}"))
        self.assert_tombstone()
        before = self.receipt_path().read_bytes()
        self.assertFalse(storage.delete_conversation(f"cli-{RUN_ID}"))
        self.assertEqual(self.receipt_path().read_bytes(), before)
        self.assertEqual(run_admission.list_receipts(), [])

    def _is_case_sensitive(self) -> bool:
        probe = self.root / ".case_sensitive_probe"
        probe.touch()
        try:
            return not (self.root / ".CASE_SENSITIVE_PROBE").exists()
        finally:
            probe.unlink(missing_ok=True)

    def test_deleting_distinct_uuid_spelling_does_not_tombstone_canonical_run(
        self,
    ) -> None:
        if not self._is_case_sensitive():
            self.skipTest("Requires a case-sensitive filesystem")
        self.prepare()
        alias = f"cli-{RUN_ID.upper()}"
        storage.create_conversation(alias)
        self.assertTrue(storage.delete_conversation(alias))
        self.assertEqual(run_admission.get(RUN_ID)["phase"], "prepared")
        self.assertTrue(self.conversation_path().exists())
        self.assertEqual(self.prepare()["phase"], "prepared")

    def test_single_deletion_during_partial_initialization_leaves_tombstone(
        self,
    ) -> None:
        self.leave_initializing()
        self.assertFalse(storage.delete_conversation(f"cli-{RUN_ID}"))
        self.assert_tombstone()

    def test_bulk_deletion_covers_partial_and_prepared_admissions(self) -> None:
        self.leave_initializing()
        second = str(UUID(int=2))
        self.prepare(second)
        storage.create_conversation("legacy")
        self.assertEqual(storage.clear_conversations(), 2)
        self.assert_tombstone()
        self.assert_tombstone(second)
        self.assertEqual(run_admission.list_receipts(), [])
        self.assertEqual(storage.clear_conversations(), 0)

    def test_bulk_deletion_covers_partial_admission_without_conversation_directory(
        self,
    ) -> None:
        self.leave_initializing()
        self.assertFalse(self.conversations.exists())
        self.assertEqual(storage.clear_conversations(), 0)
        self.assert_tombstone()

    def test_tombstone_write_failure_prevents_conversation_deletion(self) -> None:
        self.prepare()
        before = self.conversation_path().read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=OSError("synthetic disk failure")
        ):
            with self.assertRaises(OSError):
                storage.delete_conversation(f"cli-{RUN_ID}")
        self.assertEqual(self.conversation_path().read_bytes(), before)
        self.assertEqual(run_admission.get(RUN_ID)["phase"], "prepared")

    def test_concurrent_retry_and_deletion_cannot_resurrect_accepted_run(self) -> None:
        self.prepare()
        barrier = threading.Barrier(9)

        def retry() -> str:
            barrier.wait(timeout=5)
            try:
                self.prepare()
                return "prepared"
            except run_admission.AdmissionDeleted:
                return "deleted"

        def delete() -> bool:
            barrier.wait(timeout=5)
            return storage.delete_conversation(f"cli-{RUN_ID}")

        with ThreadPoolExecutor(max_workers=9) as executor:
            retries = [executor.submit(retry) for _ in range(8)]
            deletion = executor.submit(delete)
            self.assertTrue(deletion.result(timeout=5))
            self.assertTrue(
                all(
                    future.result(timeout=5) in {"prepared", "deleted"}
                    for future in retries
                )
            )
        self.assert_tombstone()
        self.manager._executor.submit.assert_not_called()

    def test_read_and_duplicate_are_detached_and_do_not_rewrite_files(self) -> None:
        self.prepare()
        paths = [self.receipt_path(), self.conversation_path()]
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        read = run_admission.get(RUN_ID)
        read["request"]["content"] = "Changed returned object"
        run_admission.list_receipts()[0]["request"]["skill_ids"].append("changed")
        self.prepare()
        self.assertEqual(
            [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths], before
        )
        self.assertEqual(
            run_admission.get(RUN_ID)["request"], request().model_dump(mode="json")
        )

    def test_history_is_bounded_newest_first_and_excludes_deleted(self) -> None:
        ids = [str(UUID(int=value)) for value in range(1, 5)]
        for index, run_id in enumerate(ids):
            record = self.prepare(run_id)
            record["created_at"] = f"2026-01-0{index + 1}T00:00:00"
            self.receipt_path(run_id).write_text(json.dumps(record), encoding="utf-8")
        storage.delete_conversation(f"cli-{ids[-1]}")
        self.assertEqual(
            [item["run_id"] for item in run_admission.list_receipts(2)], ids[2:0:-1]
        )
        for invalid in (0, 101, -1, True, "2", 1.5):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                run_admission.list_receipts(invalid)

    def test_invalid_ids_make_no_files_and_missing_read_makes_no_directory(
        self,
    ) -> None:
        self.assertIsNone(run_admission.get(RUN_ID))
        self.assertEqual(run_admission.list_receipts(), [])
        self.assertEqual(list(self.root.iterdir()), [])
        for invalid in ("../outside", "not-a-uuid", "", "1234/5678"):
            with self.subTest(run_id=invalid), self.assertRaises(ValueError):
                self.prepare(invalid)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
