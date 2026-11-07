"""Archived rendering parity, source isolation and strict reference selection."""

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch
from uuid import uuid4

import test_run_skill_provenance as fixtures

from backend import (
    run_admission,
    run_cancellation,
    run_skill_provenance,
    run_skill_rendering,
    run_skill_snapshot,
    skills,
    storage,
)
from backend.consultation_contract import CreateRunRequest


class ArchivedSkillRenderingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExplicitSkillProvenanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.run = self.fixture.root, self.fixture.run
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                "one/SKILL.md",
                "---\nname: one\ndescription: Archive example.\n---\nUse references/alpha.md and references/beta.md.\n"
                + "Original instructions Пример.\n" * 30,
            )
            zipped.writestr(
                "one/references/alpha.md",
                "Introduction.\n# Alpha\n"
                + "Alpha body.\n" * 40
                + "## Beta\nUnicode ☃ beta text.\n",
            )
            zipped.writestr(
                "one/references/beta.md", "# Other\n" + "Other body.\n" * 20
            )
            zipped.writestr("one/scripts/unused.py", "NOT_PROMPT_INPUT")
        skills.install_skill(archive.getvalue(), replace=True)
        self.archive()

    def archive(self):
        self.skill = skills.get_skill("one")
        self.identity = run_skill_provenance.bind_explicit_skills(self.run)
        self.saved = run_skill_snapshot.persist(self.run, expected_revision=0)

    def render(self, selection=None, max_chars=10000, reasons=None):
        return run_skill_rendering.render(
            self.run,
            selection if selection is not None else {},
            max_chars=max_chars,
            reasons=reasons,
        )

    def files(self):
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def assert_parity(self, selection, max_chars=10000, reasons=None):
        block, report = skills.render_skills_context(
            [self.skill], selection, max_chars, reasons
        )
        for item in report["resolved"]:
            item.pop("bundled_unused")
        report.update(
            bundle_inventory="not_archived", renderer_version=1, source=self.identity
        )
        if block is not None and len(block) > max_chars:
            with self.assertRaises(run_skill_rendering.SkillRenderingConflict):
                self.render(selection, max_chars, reasons)
            return
        self.assertEqual(self.render(selection, max_chars, reasons), (block, report))

    def test_legacy_renderer_parity_for_bodies_whole_files_and_sections(self):
        for selection in (
            {},
            {"one": []},
            {"one": [skills.SectionSelection("references/alpha.md")]},
            {"one": [skills.SectionSelection("references/alpha.md", ("2",))]},
            {
                "one": [
                    skills.SectionSelection("references/alpha.md", ("3", "1")),
                    skills.SectionSelection("references/beta.md"),
                ]
            },
        ):
            with self.subTest(selection=selection):
                self.assert_parity(selection)

    def test_legacy_packing_parity_and_strict_budget_floor(self):
        selected = {
            "one": [
                skills.SectionSelection("references/alpha.md"),
                skills.SectionSelection("references/beta.md"),
            ]
        }
        for limit in (0, 1, 200, 500, 600, 800, 1000, 1400, 2000, 10000):
            with self.subTest(limit=limit):
                self.assert_parity(selected, limit)
        block, report = self.render(selected, 1000)
        self.assertLessEqual(len(block), 1000)
        self.assertTrue(report["dropped_references"])
        self.assertTrue(report["truncated_skills"])

    def test_archived_bytes_survive_library_replacement_disabling_and_deletion(self):
        selection = {"one": [skills.SectionSelection("references/alpha.md", ("3",))]}
        expected = self.render(selection)
        path = self.root / "skills" / "one" / "bundle" / "references/alpha.md"
        path.write_text("# Changed\nWRONG_NEW_REFERENCE")
        skills.set_skill_enabled("one", False)
        self.assertEqual(self.render(selection), expected)
        skills.delete_skill("one")
        before = self.files()
        with (
            patch.object(skills, "get_skill", side_effect=AssertionError("library")),
            patch.object(
                skills, "read_skill_markdown", side_effect=AssertionError("body")
            ),
            patch.object(
                skills, "read_reference", side_effect=AssertionError("reference")
            ),
            patch.object(
                skills,
                "read_reference_sections",
                side_effect=AssertionError("sections"),
            ),
        ):
            self.assertEqual(self.render(selection), expected)
        self.assertEqual(before, self.files())
        self.assertNotIn("WRONG_NEW_REFERENCE", expected[0])
        self.assertIn("Unicode ☃", expected[0])

    def test_rendering_is_byte_pure_deterministic_and_returns_detached_report(self):
        selected = {"one": [skills.SectionSelection("references/alpha.md", ("3", "1"))]}
        before = self.files()
        expected = self.render(selected)
        block, report = self.render(selected)
        report["source"]["skills"][0]["sha256"] = "changed"
        report["resolved"][0]["references"].clear()
        self.assertEqual(self.render(selected), expected)
        self.assertEqual(self.files(), before)
        self.assertEqual(block, expected[0])

    def test_reasons_are_retained_and_do_not_change_prompt_text(self):
        selected = {"one": [skills.SectionSelection("references/alpha.md", ("1", "3"))]}
        reasons = {
            "one": {
                "references/alpha.md::1": "Intro",
                "references/alpha.md::3": "Unicode",
            }
        }
        self.assert_parity(selected, reasons=reasons)
        block, report = self.render(selected, reasons=reasons)
        self.assertEqual(block, self.render(selected)[0])
        self.assertEqual(
            report["resolved"][0]["references"][0]["reason"], "Intro; Unicode"
        )
        reasons["one"]["references/alpha.md"] = "Whole reason wins"
        self.assert_parity(selected, reasons=reasons)

    def test_unknown_or_ambiguous_selections_fail_without_silent_broadening(self):
        invalid = [
            None,
            [],
            {"unknown": []},
            {"one": None},
            {"one": "reference"},
            {"one": [{}]},
            {"one": [skills.SectionSelection("references/missing.md")]},
            {"one": [skills.SectionSelection("references/alpha.md", ("unknown",))]},
            {"one": [skills.SectionSelection("references/alpha.md", ("1", "1"))]},
            {"one": [skills.SectionSelection("references/alpha.md", (1,))]},
            {"one": [skills.SectionSelection("references/alpha.md", ["1"])]},
            {
                "one": [
                    skills.SectionSelection("references/alpha.md"),
                    skills.SectionSelection("references/alpha.md", ("1",)),
                ]
            },
        ]
        before = self.files()
        for value in invalid:
            with (
                self.subTest(value=value),
                self.assertRaises(run_skill_rendering.SkillRenderingConflict),
            ):
                run_skill_rendering.render(self.run, value, max_chars=10000)
        self.assertEqual(before, self.files())

    def test_invalid_budgets_and_reason_inputs_fail_closed(self):
        for value in (True, False, -1, 1.5, "100", None):
            with self.assertRaises(ValueError):
                self.render(max_chars=value)
        selected = {"one": [skills.SectionSelection("references/alpha.md", ("1",))]}
        for reasons in (
            [],
            {"other": {}},
            {"one": []},
            {"one": {"references/missing.md": "x"}},
            {"one": {"references/alpha.md::2": "unselected"}},
            {"one": {"references/alpha.md": 4}},
        ):
            with self.assertRaises(run_skill_rendering.SkillRenderingConflict):
                self.render(selected, reasons=reasons)

    def test_archived_original_skill_order_and_missing_bundle_inventory(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                "two/SKILL.md",
                "---\nname: two\ndescription: Second skill.\n---\nOther instructions.",
            )
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
        run_skill_snapshot.persist(self.run, expected_revision=0)
        block, report = self.render()
        self.assertLess(block.index("## Skill: two"), block.index("## Skill: one"))
        self.assertEqual([entry["id"] for entry in report["resolved"]], ["two", "one"])
        self.assertEqual(report["bundle_inventory"], "not_archived")
        self.assertNotIn("NOT_PROMPT_INPUT", block)
        self.assertTrue(
            all("bundled_unused" not in entry for entry in report["resolved"])
        )

    def test_legacy_synthetic_sections_keep_whole_file_semantics(self):
        manifest = self.root / "skills" / "one" / "manifest.json"
        data = json.loads(manifest.read_text())
        for reference in data["references"]:
            reference.pop("sections", None)
        manifest.write_text(json.dumps(data))
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
        self.archive()
        selected = {"one": [skills.SectionSelection("references/alpha.md", ("1",))]}
        self.assert_parity(selected)
        block, _ = self.render(selected)
        self.assertIn("Introduction", block)
        self.assertIn("Alpha body", block)
        self.assertIn("Unicode ☃", block)
        with self.assertRaises(run_skill_rendering.SkillRenderingConflict):
            self.render(
                {"one": [skills.SectionSelection("references/alpha.md", ("2",))]}
            )

    def test_missing_archive_is_not_silently_captured_or_written(self):
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
        run_skill_provenance.bind_explicit_skills(self.run)
        before = self.files()
        with self.assertRaises(run_skill_provenance.SkillProvenanceConflict):
            self.render()
        self.assertEqual(self.files(), before)

    def test_cancellation_keeps_preview_but_deletion_blocks_new_reads(self):
        expected = self.render()
        run_cancellation.request(self.run)
        self.assertEqual(self.render(), expected)
        storage.delete_conversation("cli-" + self.run)
        before = self.files()
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.render()
        self.assertEqual(self.files(), before)

    def test_saved_corruption_is_rejected_without_library_fallback(self):
        path = run_admission._path(self.run)
        record = json.loads(path.read_text())
        record["explicit_skill_snapshot"]["skills"][0]["document"]["body"] = "changed"
        path.write_text(json.dumps(record))
        before = self.files()
        with patch.object(skills, "get_skill", side_effect=AssertionError("library")):
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.render()
        self.assertEqual(self.files(), before)

    def test_incoherent_archived_reference_metadata_fails_even_with_valid_identity(
        self,
    ):
        # A supported archive can retain a previously captured stale manifest.
        # Rendering must not attach the wrong heading or position to its text.
        for mutate in (
            lambda ref: ref.update(chars=1),
            lambda ref: ref.update(summary="stale"),
            lambda ref: ref.update(headings=["wrong"]),
            lambda ref: ref["sections"][1].update(heading="wrong"),
            lambda ref: ref["sections"][1].update(chars=1),
            lambda ref: ref.update(path="../outside.md"),
            lambda ref: ref.update(path="references/../../outside.md"),
            lambda ref: ref.update(path="references/unsafe\\path.md"),
            lambda ref: ref.update(path="references/unsafe\npath.md"),
        ):
            document = copy.deepcopy(self.saved[0].document())
            mutate(document["references"][0])
            encoded = run_skill_snapshot._encode(document)
            captured = run_skill_provenance.CapturedSkill(
                "one", hashlib.sha256(encoded).hexdigest(), encoded
            )
            with patch.object(run_skill_snapshot, "load", return_value=(captured,)):
                with self.assertRaises(run_skill_rendering.SkillRenderingConflict):
                    self.render()

    def test_fresh_process_after_library_deletion(self):
        expected = self.render(
            {"one": [skills.SectionSelection("references/alpha.md", ("3",))]}
        )
        skills.delete_skill("one")
        before = self.files()
        code = """
import json, sys
from unittest.mock import patch
from backend import run_skill_rendering, skills, storage
storage.DATA_DIR = sys.argv[1]
with patch.object(skills, 'read_skill_markdown', side_effect=AssertionError('library')):
    result = run_skill_rendering.render(sys.argv[2], {'one': [skills.SectionSelection('references/alpha.md', ('3',))]}, max_chars=10000)
print(json.dumps(result))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run],
            check=True,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        self.assertEqual(json.loads(result.stdout), list(expected))
        self.assertEqual(self.files(), before)

    def test_headingless_empty_and_capped_sections_match_current_renderer(self):
        for text in (
            "",
            "Unheaded Unicode ☃\nSecond line.\n",
            "\n".join(f"# Heading {index}\nBody {index}." for index in range(30)),
        ):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr(
                    "one/SKILL.md",
                    "---\nname: one\ndescription: Example.\n---\nInstructions.",
                )
                zipped.writestr("one/references/edge.md", text)
            skills.install_skill(archive.getvalue(), replace=True)
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
            self.archive()
            self.assert_parity({"one": [skills.SectionSelection("references/edge.md")]})
            reference = self.skill.references[0]
            final = skills.reference_sections(reference)[-1].section_id
            self.assert_parity(
                {"one": [skills.SectionSelection("references/edge.md", (final,))]}
            )

    def test_duplicate_archived_references_are_not_silently_collapsed(self):
        document = copy.deepcopy(self.saved[0].document())
        document["references"].append(copy.deepcopy(document["references"][0]))
        encoded = run_skill_snapshot._encode(document)
        captured = run_skill_provenance.CapturedSkill(
            "one", hashlib.sha256(encoded).hexdigest(), encoded
        )
        with patch.object(run_skill_snapshot, "load", return_value=(captured,)):
            with self.assertRaises(run_skill_rendering.SkillRenderingConflict):
                self.render()


if __name__ == "__main__":
    unittest.main()
