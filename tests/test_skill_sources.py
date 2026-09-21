"""Portable packs and opt-in local skills share the council's prompt pipeline."""

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import skill_sources, skills


def bundle(entries: dict[str, str]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return stream.getvalue()


def markdown(name: str, body: str = "Instructions") -> str:
    return f"---\nname: {name}\ndescription: Relevant guidance\n---\n{body}"


class SkillSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / "library"
        self.roots = {name: self.root / name for name in ("codex", "claude")}
        for root in self.roots.values():
            root.mkdir()
        for target, attribute, value in (
            (skills, "_SKILLS_DIR", self.library),
            (skill_sources, "ROOTS", self.roots),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_combined_layouts_preserve_references_and_exclude_evals(self) -> None:
        for prefix in ("skills/", "business-0.1/skills/", "plugins/business/skills/"):
            with self.subTest(prefix=prefix):
                installed, _ = skills.install_skill_pack(
                    bundle(
                        {
                            prefix + "one/SKILL.md": markdown("one"),
                            prefix + "one/references/guide.md": "# Guide\nREFERENCE",
                            prefix + "one/evals/answer.md": "SECRET ANSWER KEY",
                            prefix + "two/SKILL.md": markdown("two"),
                            "manifest.json": "{}",
                        }
                    ),
                    replace=True,
                )
                self.assertEqual([s.id for s in installed], ["one", "two"])
                self.assertIn(
                    "REFERENCE", skills.read_reference("one", "references/guide.md")
                )
                self.assertIsNone(skills.read_reference("one", "evals/answer.md"))

    def test_pack_conflict_and_invalid_skill_do_not_partially_install(self) -> None:
        skills.install_skill(bundle({"SKILL.md": markdown("two", "ORIGINAL")}))
        for second in (markdown("two", "REPLACEMENT"), "invalid"):
            with self.assertRaises(skills.SkillError):
                skills.install_skill_pack(
                    bundle(
                        {
                            "skills/one/SKILL.md": markdown("one"),
                            "skills/two/SKILL.md": second,
                        }
                    )
                )
            self.assertIsNone(skills.get_skill("one"))
            self.assertIn("ORIGINAL", skills.read_skill_markdown("two"))

    def test_source_toggles_symlinks_refresh_and_uploaded_precedence(self) -> None:
        original = self.root / "original"
        original.mkdir()
        (original / "SKILL.md").write_text(markdown("one", "LOCAL"))
        (self.roots["codex"] / "linked").symlink_to(original, target_is_directory=True)
        (self.roots["claude"] / "also-linked").symlink_to(
            original, target_is_directory=True
        )
        self.assertEqual(skills.list_skills(), [])
        skill_sources.configure(True, True)
        found = skills.list_skills()
        self.assertEqual([(s.id, s.source) for s in found], [("one", "codex")])
        version = found[0].version
        self.assertEqual(skills.list_skills()[0].version, version)
        (original / "SKILL.md").write_text(markdown("one", "UPDATED"))
        skills.list_skills()
        self.assertIn("UPDATED", skills.read_skill_markdown("one"))
        with self.assertRaises(skills.SkillError):
            skills.delete_skill("one")
        skill_sources.configure(False, True)
        self.assertEqual(skills.get_skill("one").source, "claude")
        skill_sources.configure(False, False)
        self.assertEqual(skills.list_skills(), [])
        self.assertIsNone(skills.read_skill_markdown("one"))
        self.assertTrue((original / "SKILL.md").is_file())
        skills.install_skill_pack(bundle({"SKILL.md": markdown("one", "UPLOADED")}))
        skill_sources.configure(True, True)
        self.assertEqual(skills.get_skill("one").source, "uploaded")
        self.assertIn("UPLOADED", skills.read_skill_markdown("one"))

    def test_pack_publish_failure_restores_previous_skills(self) -> None:
        skills.install_skill(bundle({"SKILL.md": markdown("two", "ORIGINAL")}))
        rename = os.rename

        def fail_second(source: Path, target: Path) -> None:
            if Path(source).parent.name == "new" and Path(source).name == "two":
                raise OSError("Simulated publication failure")
            rename(source, target)

        with patch.object(skills.os, "rename", side_effect=fail_second):
            with self.assertRaises(OSError):
                skills.install_skill_pack(
                    bundle(
                        {
                            "skills/one/SKILL.md": markdown("one"),
                            "skills/two/SKILL.md": markdown("two", "REPLACEMENT"),
                        }
                    ),
                    replace=True,
                )
        self.assertIsNone(skills.get_skill("one"))
        self.assertIn("ORIGINAL", skills.read_skill_markdown("two"))

    def test_nested_links_and_deleted_sources_are_not_read(self) -> None:
        root = self.roots["codex"] / "one"
        (root / "references").mkdir(parents=True)
        (root / "SKILL.md").write_text(markdown("one"))
        outside = self.root / "outside.md"
        outside.write_text("PRIVATE")
        (root / "references/leak.md").symlink_to(outside)
        skill_sources.configure(True, False)
        self.assertEqual(skills.list_skills(), [])
        (root / "references/leak.md").unlink()
        skills.list_skills()
        self.assertIsNotNone(skills.get_skill("one"))
        (root / "SKILL.md").unlink()
        self.assertIsNone(skills.get_skill("one"))
