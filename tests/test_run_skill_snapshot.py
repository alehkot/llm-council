"""Offline durable explicit-skill copies, recovery and read-only history."""

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from uuid import uuid4

import test_run_skill_provenance as fixtures

from backend import (
    config,
    durable_prequery,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_skill_provenance,
    run_skill_snapshot,
    skills,
    storage,
)
from backend.consultation_contract import CreateRunRequest


class SkillSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExplicitSkillProvenanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run = self.fixture.run
        self.root = self.fixture.root
        self.identity = run_skill_provenance.bind_explicit_skills(self.run)

    def persist(self, revision=0):
        return run_skill_snapshot.persist(self.run, expected_revision=revision)

    def files(self):
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def rewrite(self, modify):
        path = run_admission._path(self.run)
        data = json.loads(path.read_text())
        modify(data)
        path.write_text(json.dumps(data))

    def test_exact_accepted_content_is_persisted_once_and_inspection_is_compact(self):
        expected = run_skill_provenance.capture_explicit_skills(self.run)
        before = run_inspection.inspect(self.run)
        write = run_admission._write
        with patch.object(run_admission, "_write", wraps=write) as writes:
            saved = self.persist()
            self.assertEqual(saved, expected)
            self.assertEqual(self.persist(), expected)
            self.assertEqual(writes.call_count, 1)
        raw = run_admission._path(self.run).read_text()
        self.assertIn("Private instructions", raw)
        self.assertIn("Reference body", raw)
        self.assertNotIn(str(self.root), raw)
        after = run_inspection.inspect(self.run)
        self.assertEqual(after["explicit_skill_snapshot"], self.identity)
        self.assertNotIn("Private instructions", json.dumps(after))
        self.assertNotEqual(before["cursor"], after["cursor"])
        for key in ("request", "interaction", "execution", "internal_phase"):
            self.assertEqual(before[key], after[key])

    def test_values_are_frozen_detached_and_repr_omits_content(self):
        saved = self.persist()
        with self.assertRaises(FrozenInstanceError):
            saved[0].document_json = b"different"
        doc = saved[0].document()
        doc["body"] = "different"
        doc["references"][0]["sections"].clear()
        self.assertEqual(run_skill_snapshot.load(self.run), saved)
        self.assertNotIn("Private instructions", repr(saved))
        self.assertNotIn("Reference body", repr(saved))
        observed = run_inspection.inspect(self.run)
        observed["explicit_skill_snapshot"]["skills"][0]["id"] = "changed"
        self.assertEqual(run_skill_snapshot.load(self.run), saved)

    def test_ordered_multiple_skills_and_unicode_are_retained(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                "two/SKILL.md",
                "---\nname: two\ndescription: Another.\n---\nПример ☃\n",
            )
            zipped.writestr("two/scripts/helper.py", "PRIVATE_SCRIPT_NOT_PROMPT_INPUT")
        skills.install_skill(archive.getvalue())
        self.run = str(uuid4())
        run_admission.prepare(
            self.run,
            CreateRunRequest(
                content="question",
                skill_ids=["two", "one"],
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        run_skill_provenance.bind_explicit_skills(self.run)
        saved = self.persist()
        self.assertEqual([item.id for item in saved], ["two", "one"])
        self.assertIn("Пример ☃", saved[0].document()["body"])
        self.assertNotIn(
            "PRIVATE_SCRIPT_NOT_PROMPT_INPUT", run_admission._path(self.run).read_text()
        )
        self.rewrite(lambda r: r["explicit_skill_snapshot"]["skills"].reverse())
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_skill_snapshot.load(self.run)

    def test_saved_content_survives_replacement_disabling_and_deletion(self):
        saved = self.persist()
        path = self.root / "skills" / "one" / "bundle" / "SKILL.md"
        path.write_text(path.read_text() + " replacement")
        skills.set_skill_enabled("one", False)
        self.assertEqual(run_skill_snapshot.load(self.run), saved)
        self.assertEqual(self.persist(), saved)
        skills.delete_skill("one")
        before = self.files()
        with (
            patch.object(skills, "get_skill", side_effect=AssertionError("library")),
            patch.object(skills, "read_skill_markdown", side_effect=AssertionError),
            patch.object(skills, "read_reference", side_effect=AssertionError),
        ):
            self.assertEqual(run_skill_snapshot.load(self.run), saved)
            self.assertEqual(self.persist(), saved)
            run_inspection.inspect(self.run)
        self.assertEqual(before, self.files())

    def test_drift_before_first_persistence_cannot_be_laundered(self):
        for name in ("SKILL.md", "references/a.md"):
            path = self.root / "skills" / "one" / "bundle" / name
            original = path.read_text()
            path.write_text(original + " drift")
            before = self.files()
            with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
                self.persist()
            self.assertEqual(before, self.files())
            path.write_text(original)
        self.assertNotIn("explicit_skill_snapshot", run_admission.get(self.run))

    def test_no_missing_identity_empty_selection_or_implicit_binding(self):
        for ids in (["one"], []):
            run = str(uuid4())
            run_admission.prepare(
                run,
                CreateRunRequest(
                    content="question",
                    skill_ids=ids,
                    interaction_policy="persistent",
                    recovery_policy="explicit_retry",
                ),
            )
            before = self.files()
            with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
                run_skill_snapshot.persist(run, expected_revision=0)
            self.assertEqual(before, self.files())

    def test_revisions_are_strict_and_rechecked_for_repeat_calls(self):
        for revision in (-1, True, 0.0, "0", 1):
            before = self.files()
            with self.assertRaises(ValueError):
                self.persist(revision)
            self.assertEqual(before, self.files())
        saved = self.persist()
        durable_prequery.advance(self.run, expected_revision=0)
        with self.assertRaises(run_interaction.PromptConflict):
            self.persist()
        self.assertEqual(self.persist(revision=1), saved)

    def test_content_can_be_archived_after_prompt_without_retroactive_identity(self):
        durable_prequery.advance(self.run, expected_revision=0)
        saved = self.persist(revision=1)
        self.assertEqual(run_skill_snapshot.load(self.run), saved)
        self.assertEqual(
            run_inspection.inspect(self.run)["internal_phase"], "waiting_input"
        )

    def test_cancellation_preserves_history_but_blocks_first_and_repeat_persistence(
        self,
    ):
        for saved_before in (False, True):
            if saved_before:
                # A separate fixture avoids undoing an accepted cancellation.
                self.doCleanups()
                self.setUp()
                saved = self.persist()
            run_cancellation.request(self.run)
            before = self.files()
            with self.assertRaises(run_admission.AdmissionCancelled):
                self.persist()
            snapshot = run_inspection.inspect(self.run)
            self.assertEqual(snapshot["internal_phase"], "cancelled")
            if saved_before:
                self.assertEqual(run_skill_snapshot.load(self.run), saved)
            else:
                self.assertIsNone(snapshot["explicit_skill_snapshot"])
            self.assertEqual(before, self.files())

    def test_deletion_erases_all_saved_content_without_resurrection(self):
        self.persist()
        storage.delete_conversation("cli-" + self.run)
        before = self.files()
        for operation in (self.persist, lambda: run_skill_snapshot.load(self.run)):
            with self.assertRaises(run_admission.AdmissionDeleted):
                operation()
        receipt = run_admission._path(self.run).read_text()
        self.assertNotIn("Private instructions", receipt)
        self.assertNotIn("Reference body", receipt)
        self.assertNotIn("explicit_skill_snapshot", receipt)
        self.assertEqual(before, self.files())

    def test_fresh_process_reads_original_content_without_library_or_writes(self):
        saved = self.persist()
        skills.delete_skill("one")
        before = self.files()
        code = """
import json, sys
from unittest.mock import patch
from backend import run_skill_snapshot, skills, storage
storage.DATA_DIR = sys.argv[1]
with patch.object(skills, 'get_skill', side_effect=AssertionError('library read')):
    content = run_skill_snapshot.load(sys.argv[2])
    assert run_skill_snapshot.persist(sys.argv[2], expected_revision=0) == content
    print(json.dumps([item.document() for item in content]))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run],
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        self.assertEqual(
            json.loads(completed.stdout), [item.document() for item in saved]
        )
        self.assertEqual(before, self.files())

    def test_failed_and_lost_write_acknowledgements(self):
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.persist()
        self.assertEqual(before, self.files())
        write = run_admission._write

        def lost(record):
            write(record)
            raise OSError("lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=lost):
            with self.assertRaises(OSError):
                self.persist()
        skills.delete_skill("one")
        before = self.files()
        saved = run_skill_snapshot.load(self.run)
        self.assertEqual(self.persist(), saved)
        self.assertEqual(before, self.files())

    def test_concurrent_duplicates_capture_and_write_once(self):
        write = run_admission._write
        capture = run_skill_provenance.capture_explicit_skills
        with (
            patch.object(run_admission, "_write", wraps=write) as writes,
            patch.object(
                run_skill_provenance, "capture_explicit_skills", wraps=capture
            ) as reads,
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            values = list(pool.map(lambda _: self.persist(), range(24)))
            self.assertTrue(all(item == values[0] for item in values))
            self.assertEqual(writes.call_count, 1)
            self.assertEqual(reads.call_count, 1)

    def test_supported_library_delete_waits_for_exact_capture(self):
        entered, release, deleting = (threading.Event() for _ in range(3))
        original = skills.read_skill_markdown

        def blocked(skill_id):
            entered.set()
            if not release.wait(3):
                raise AssertionError("release timed out")
            return original(skill_id)

        def delete():
            deleting.set()
            return skills.delete_skill("one")

        with (
            patch.object(skills, "read_skill_markdown", side_effect=blocked),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            saving = pool.submit(self.persist)
            self.assertTrue(entered.wait(3))
            removal = pool.submit(delete)
            self.assertTrue(deleting.wait(3))
            self.assertFalse(removal.done())
            release.set()
            saved = saving.result(timeout=3)
            self.assertTrue(removal.result(timeout=3))
        self.assertEqual(run_skill_snapshot.load(self.run), saved)

    def test_run_cancellation_and_deletion_serialize_with_initial_persistence(self):
        for action in ("cancel", "delete"):
            self.doCleanups()
            self.setUp()
            entered, release, mutating = (threading.Event() for _ in range(3))
            original = skills.read_skill_markdown

            def blocked(skill_id):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("release timed out")
                return original(skill_id)

            def mutate():
                mutating.set()
                if action == "cancel":
                    return run_cancellation.request(self.run)
                return storage.delete_conversation("cli-" + self.run)

            with (
                patch.object(skills, "read_skill_markdown", side_effect=blocked),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                saving = pool.submit(self.persist)
                self.assertTrue(entered.wait(3))
                mutation = pool.submit(mutate)
                self.assertTrue(mutating.wait(3))
                self.assertFalse(mutation.done())
                release.set()
                saved = saving.result(timeout=3)
                mutation.result(timeout=3)
            if action == "cancel":
                self.assertEqual(run_skill_snapshot.load(self.run), saved)
                with self.assertRaises(run_admission.AdmissionCancelled):
                    self.persist()
            else:
                self.assertNotIn("explicit_skill_snapshot", run_admission.get(self.run))
                with self.assertRaises(run_admission.AdmissionDeleted):
                    self.persist()

    def test_mixed_content_and_later_external_changes_are_not_reread(self):
        original = skills.read_skill_markdown
        body = self.root / "skills" / "one" / "bundle" / "SKILL.md"
        reference = self.root / "skills" / "one" / "bundle" / "references/a.md"

        def mixed(skill_id):
            content = original(skill_id)
            reference.write_text(reference.read_text() + " mixed")
            return content

        old = reference.read_text()
        with patch.object(skills, "read_skill_markdown", side_effect=mixed):
            with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
                self.persist()
        reference.write_text(old)

        def changed_after_read(skill_id):
            content = original(skill_id)
            body.write_text(body.read_text() + " later change")
            return content

        with patch.object(
            skills, "read_skill_markdown", side_effect=changed_after_read
        ):
            saved = self.persist()
        self.assertNotIn("later change", saved[0].document()["body"])
        self.assertEqual(run_skill_snapshot.load(self.run), saved)

    def test_corrupt_snapshots_fail_closed_without_repair_or_library_reads(self):
        self.persist()
        path = run_admission._path(self.run)
        original = json.loads(path.read_text())
        mutations = [
            lambda r: r.pop("explicit_skill_provenance"),
            lambda r: r.update(phase="initializing"),
            lambda r: r.update(explicit_skill_snapshot=None),
            lambda r: r["explicit_skill_snapshot"].update(version=True),
            lambda r: r["explicit_skill_snapshot"].update(version=2),
            lambda r: r["explicit_skill_snapshot"].update(extra="unexpected"),
            lambda r: r["explicit_skill_snapshot"].update(skills=[]),
            lambda r: r["explicit_skill_snapshot"]["skills"][0].update(id="other"),
            lambda r: r["explicit_skill_snapshot"]["skills"][0].update(sha256="0" * 64),
            lambda r: r["explicit_skill_snapshot"]["skills"][0]["document"].update(
                body="changed"
            ),
            lambda r: r["explicit_skill_snapshot"]["skills"][0]["document"][
                "references"
            ][0].update(summary="changed"),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(original)
            mutate(bad)
            path.write_text(json.dumps(bad))
            before = self.files()
            with patch.object(skills, "get_skill", side_effect=AssertionError("read")):
                for operation in (
                    self.persist,
                    lambda: run_skill_snapshot.load(self.run),
                    lambda: run_inspection.inspect(self.run),
                ):
                    with self.assertRaises(run_admission.AdmissionUnavailable):
                        operation()
            self.assertEqual(before, self.files())

    def test_matching_digests_do_not_allow_malformed_document_shapes(self):
        self.persist()
        path = run_admission._path(self.run)
        original = json.loads(path.read_text())
        for update in (
            lambda d: d.update(body=None),
            lambda d: d.update(version=True),
            lambda d: d.update(extra="unexpected"),
            lambda d: d.update(id="wrong"),
            lambda d: d["references"][0].update(chars="1"),
            lambda d: d["references"][0].update(headings="heading"),
            lambda d: d["references"][0]["sections"][0].update(chars=-1),
        ):
            bad = copy.deepcopy(original)
            item = bad["explicit_skill_snapshot"]["skills"][0]
            update(item["document"])
            digest = hashlib.sha256(
                json.dumps(
                    item["document"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            item["sha256"] = digest
            bad["explicit_skill_provenance"]["skills"][0]["sha256"] = digest
            path.write_text(json.dumps(bad))
            before = self.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_skill_snapshot.load(self.run)
            self.assertEqual(before, self.files())

    def test_changed_current_limits_and_credentials_do_not_change_saved_content(self):
        saved = self.persist()
        before = self.files()
        with (
            patch.object(config, "SKILLS_MAX_SELECTED", 0),
            patch.object(config, "OPENROUTER_API_KEY", "synthetic-secret-never-stored"),
            patch.object(skills, "get_skill", side_effect=AssertionError("read")),
        ):
            self.assertEqual(self.persist(), saved)
            self.assertEqual(run_skill_snapshot.load(self.run), saved)
        self.assertEqual(before, self.files())
        self.assertNotIn(
            "synthetic-secret-never-stored", run_admission._path(self.run).read_text()
        )


if __name__ == "__main__":
    unittest.main()
