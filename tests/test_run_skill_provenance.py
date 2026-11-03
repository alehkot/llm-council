"""Offline identity checks for explicit uploaded skill content."""

import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    run_admission,
    run_inspection,
    run_interaction,
    run_skill_provenance,
    skills,
    storage,
)
from backend.consultation_contract import CreateRunRequest, PromptInstance


class ExplicitSkillProvenanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, name, value in (
            (storage, "DATA_DIR", str(self.root / "conversations")),
            (skills, "_SKILLS_DIR", self.root / "skills"),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(
                "one/SKILL.md",
                "---\nname: one\ndescription: An example.\n---\nPrivate instructions.\n",
            )
            z.writestr("one/references/a.md", "# Heading\nReference body.\n")
        skills.install_skill(archive.getvalue())
        self.run = str(uuid4())
        run_admission.prepare(
            self.run,
            CreateRunRequest(
                content="question",
                skill_ids=["one"],
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )

    def test_stable_idempotent_and_text_not_persisted(self):
        a = run_skill_provenance.bind_explicit_skills(self.run)
        b = run_skill_provenance.bind_explicit_skills(self.run)
        self.assertEqual(a, b)
        raw = (self.root / "run_receipts" / f"{self.run}.json").read_text()
        self.assertNotIn("Private instructions", raw)
        self.assertNotIn("Reference body", raw)
        self.assertNotIn(str(self.root), raw)
        self.assertEqual(len(a["skills"][0]["sha256"]), 64)

    def test_reference_and_body_drift_conflict(self):
        run_skill_provenance.bind_explicit_skills(self.run)
        for path in ("SKILL.md", "references/a.md"):
            p = self.root / "skills" / "one" / "bundle" / path
            original = p.read_text()
            p.write_text(original + " changed")
            with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
                run_skill_provenance.bind_explicit_skills(self.run)
            p.write_text(original)

    def test_disabled_deleted_and_unavailable_fail_closed(self):
        run_skill_provenance.bind_explicit_skills(self.run)
        skills.set_skill_enabled("one", False)
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)
        skills.set_skill_enabled("one", True)
        skills.delete_skill("one")
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)

    def test_multi_skill_order_and_digest_validation(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(
                "two/SKILL.md",
                "---\nname: two\ndescription: Another example.\n---\nOther instructions.\n",
            )
        skills.install_skill(archive.getvalue())
        run = str(uuid4())
        run_admission.prepare(
            run,
            CreateRunRequest(
                content="question",
                skill_ids=["two", "one"],
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        identity = run_skill_provenance.bind_explicit_skills(run)
        self.assertEqual([item["id"] for item in identity["skills"]], ["two", "one"])
        path = self.root / "run_receipts" / f"{run}.json"
        record = json.loads(path.read_text())
        record["explicit_skill_provenance"]["skills"].reverse()
        path.write_text(json.dumps(record))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(run)
        record["explicit_skill_provenance"]["skills"].reverse()
        record["explicit_skill_provenance"]["skills"][0]["sha256"] = "not-a-digest"
        path.write_text(json.dumps(record))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(run)

    def test_no_empty_automatic_or_missing_skill_snapshot(self):
        for ids in ([], ["missing"]):
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
            with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
                run_skill_provenance.bind_explicit_skills(run)
            self.assertNotIn("explicit_skill_provenance", run_admission.get(run))

    def test_inspection_detached_and_cursor_tracks_identity_without_library_reads(self):
        before = run_inspection.inspect(self.run)
        identity = run_skill_provenance.bind_explicit_skills(self.run)
        with patch.object(
            skills, "get_skill", side_effect=AssertionError("library read")
        ):
            observed = run_inspection.inspect(self.run)
        self.assertNotEqual(before["cursor"], observed["cursor"])
        self.assertEqual(identity, observed["explicit_skill_provenance"])
        observed["explicit_skill_provenance"]["skills"][0]["id"] = "changed"
        self.assertEqual(
            run_inspection.inspect(self.run)["explicit_skill_provenance"], identity
        )

    def test_initializing_checkpoint_fails_closed(self):
        record = run_admission.get(self.run)
        record["phase"] = "initializing"
        record["explicit_skill_provenance"] = {"version": 1, "skills": []}
        run_admission._write(record)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_legacy_reference_routing_metadata_drift_conflicts(self):
        manifest = self.root / "skills" / "one" / "manifest.json"
        record = json.loads(manifest.read_text())
        record["references"][0].pop("sections", None)
        manifest.write_text(json.dumps(record))
        run_skill_provenance.bind_explicit_skills(self.run)
        record["references"][0]["chars"] += 1
        manifest.write_text(json.dumps(record))
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)

    def test_saved_boolean_version_and_other_corruption_fail_closed(self):
        run_skill_provenance.bind_explicit_skills(self.run)
        path = self.root / "run_receipts" / f"{self.run}.json"
        record = json.loads(path.read_text())
        record["explicit_skill_provenance"]["version"] = True
        path.write_text(json.dumps(record))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_skill_provenance.bind_explicit_skills(self.run)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_provider_binding_forbids_retroactive_skill_identity(self):
        record = run_admission.get(self.run)
        record["refinement_provider"] = {
            "configuration": {},
            "identity": "already-bound",
        }
        run_admission._write(record)
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)
        self.assertNotIn("explicit_skill_provenance", run_admission.get(self.run))

    def test_existing_prompt_forbids_retroactive_binding(self):
        instance = PromptInstance(
            run_id=self.run,
            instance_id=uuid4(),
            prompt={
                "prompt_id": "refinement",
                "prompt_type": "prompt_refinement",
                "title": "Question",
                "description": "Choose",
                "skip_allowed": False,
                "fields": [
                    {"id": "text", "type": "text", "label": "Text", "required": True}
                ],
            },
        )
        run_interaction.put_prompt(
            self.run,
            instance,
            run_interaction.Continuation(
                point="refinement_answer", state={"iteration": 1}
            ),
            expected_revision=0,
        )
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)
        self.assertNotIn("explicit_skill_provenance", run_admission.get(self.run))

    def test_bind_serializes_replacement_and_deletion(self):
        original_read = skills.read_skill_markdown
        entered = threading.Event()
        release = threading.Event()
        mutation_started = threading.Event()

        def blocked_read(skill_id):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test release timed out")
            return original_read(skill_id)

        def replace():
            mutation_started.set()
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(
                    "one/SKILL.md",
                    "---\nname: one\ndescription: Replaced.\n---\nNew body.\n",
                )
            skills.install_skill(archive.getvalue(), replace=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(skills, "read_skill_markdown", side_effect=blocked_read):
                binding = pool.submit(
                    run_skill_provenance.bind_explicit_skills, self.run
                )
                self.assertTrue(entered.wait(3))
                replacing = pool.submit(replace)
                self.assertTrue(mutation_started.wait(3))
                self.assertFalse(replacing.done())
                release.set()
                old = binding.result(timeout=3)
                replacing.result(timeout=3)
        self.assertEqual(run_admission.get(self.run)["explicit_skill_provenance"], old)
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)

        # A later concurrent delete either waits for the snapshot or precedes it;
        # the first run demonstrates the deterministic lock serialization.
        second = str(uuid4())
        run_admission.prepare(
            second,
            CreateRunRequest(
                content="question",
                skill_ids=["one"],
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        entered.clear()
        release.clear()
        mutation_started.clear()

        def delete():
            mutation_started.set()
            return skills.delete_skill("one")

        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(skills, "read_skill_markdown", side_effect=blocked_read):
                binding = pool.submit(run_skill_provenance.bind_explicit_skills, second)
                self.assertTrue(entered.wait(3))
                deleting = pool.submit(delete)
                self.assertTrue(mutation_started.wait(3))
                self.assertFalse(deleting.done())
                release.set()
                captured = binding.result(timeout=3)
                self.assertTrue(deleting.result(timeout=3))
        self.assertEqual(
            run_admission.get(second)["explicit_skill_provenance"], captured
        )
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(second)

    def test_lost_acknowledgement_retries_existing_identity(self):
        write = run_admission._write

        def wrote_then_lost(record):
            write(record)
            raise OSError("synthetic lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=wrote_then_lost):
            with self.assertRaises(OSError):
                run_skill_provenance.bind_explicit_skills(self.run)
        saved = run_admission.get(self.run)["explicit_skill_provenance"]
        self.assertEqual(run_skill_provenance.bind_explicit_skills(self.run), saved)

    def test_failed_write_does_not_create_false_identity(self):
        with patch.object(run_admission, "_write", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                run_skill_provenance.bind_explicit_skills(self.run)
        self.assertNotIn("explicit_skill_provenance", run_admission.get(self.run))

    def test_concurrent_bind_retries_produce_one_identity(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            identities = list(
                pool.map(
                    lambda _: run_skill_provenance.bind_explicit_skills(self.run),
                    range(20),
                )
            )
        self.assertTrue(all(item == identities[0] for item in identities))

    def test_local_source_is_not_silently_claimed_as_immutable(self):
        manifest = self.root / "skills" / "one" / "manifest.json"
        record = json.loads(manifest.read_text())
        record["source"] = "codex"
        record["source_path"] = str(self.root / "remote-external")
        manifest.write_text(json.dumps(record))
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            run_skill_provenance.bind_explicit_skills(self.run)

    def test_tombstone_rejects_binding(self):
        storage.delete_conversation("cli-" + self.run)
        with self.assertRaises(run_admission.AdmissionDeleted):
            run_skill_provenance.bind_explicit_skills(self.run)

    def test_cancelled_run_cannot_bind(self):
        from backend import run_cancellation

        run_cancellation.request(self.run)
        with self.assertRaises(run_admission.AdmissionCancelled):
            run_skill_provenance.bind_explicit_skills(self.run)


if __name__ == "__main__":
    unittest.main()
