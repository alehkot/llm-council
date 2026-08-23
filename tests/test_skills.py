"""Tests for the uploadable Agent Skills library.

Covers zip ingest hardening, the on-disk store, prompt-block rendering, the
reference-selection parser and its deterministic fallback, the ``skill_ids``
plumbing, and the raw-body upload route.
"""

import asyncio
import contextlib
import json
import os
import stat
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest.mock import patch

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import consultation_journal, council, langgraph_flow  # noqa: E402
from backend import skills, storage  # noqa: E402
from backend.config import API_AUTH_KEY, SKILL_DESCRIPTION_MAX_CHARS  # noqa: E402
from backend.consultation_manager import (  # noqa: E402
    ConsultationManager,
    QueuedMessage,
)
from backend.interactive import PromptResponse  # noqa: E402
from backend.langgraph_flow import FlowContext  # noqa: E402
from backend.skills import ReferenceCandidate, Skill, SkillError  # noqa: E402
from backend.trace import TraceCollector  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALPHA_BODY = "\n".join(
    [
        "# Drafting Guardrails",
        "",
        "ALPHA-REFERENCE-BODY covers what a guarded vendor reply must never state.",
        "",
        "## Default Non-Disclosure List",
        "",
        "Reservation value, fallback options, internal authority.",
    ]
)

BETA_BODY = "\n".join(
    [
        "# Escalation Ladder",
        "",
        "BETA-REFERENCE-BODY covers escalation sequencing.",
    ]
)

DEFAULT_SKILL_BODY = "\n".join(
    [
        "# My Skill",
        "",
        "Operating instructions for the council.",
        "",
        "Load [references/alpha.md](references/alpha.md) before any sendable reply.",
    ]
)


def _skill_md(
    name: str = "My-Skill",
    description: str = "Guarded drafting for vendor replies and price pushback.",
    body: str = DEFAULT_SKILL_BODY,
    extra_frontmatter: Sequence[str] = (),
) -> str:
    """Build a SKILL.md with YAML frontmatter."""
    lines = ["---", f"name: {name}", f"description: {description}"]
    lines.extend(extra_frontmatter)
    lines.extend(["---", "", body, ""])
    return "\n".join(lines)


def _zip(files: Dict[str, str], top: str = "my-skill/") -> bytes:
    """Build a skill archive in memory.

    ZIP_STORED keeps the compression-ratio guard out of the way of unrelated
    fixtures; the ratio-bomb test opts into ZIP_DEFLATED explicitly.
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for relpath, content in files.items():
            archive.writestr(f"{top}{relpath}", content)
    return buffer.getvalue()


def _entry(name: str, external_attr: int = 0o600 << 16) -> zipfile.ZipInfo:
    """A ZipInfo with an explicit Unix mode in ``external_attr``."""
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.external_attr = external_attr
    return info


def _zip_entries(entries: Sequence[Tuple[zipfile.ZipInfo, str]]) -> bytes:
    """Build an archive from explicit ZipInfo entries."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for info, content in entries:
            archive.writestr(info, content)
    return buffer.getvalue()


def _candidate(
    candidate_id: int,
    path: str,
    chars: int,
    headings: Optional[List[str]] = None,
    mentions: Optional[List[str]] = None,
    description: str = "A skill.",
) -> ReferenceCandidate:
    """Build one router candidate without touching disk."""
    return ReferenceCandidate(
        candidate_id=candidate_id,
        skill_id="my-skill",
        skill_description=description,
        path=path,
        chars=chars,
        headings=headings or [],
        mentions=mentions or [],
    )


class _SkillsDirTestCase(unittest.TestCase):
    """Rebinds ``skills._SKILLS_DIR`` at a TemporaryDirectory for each test."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_skills_dir = skills._SKILLS_DIR
        skills._SKILLS_DIR = Path(self.tempdir.name) / "skills"
        skills._SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        skills._SKILLS_DIR = self.old_skills_dir
        self.tempdir.cleanup()

    def _install(
        self,
        files: Optional[Dict[str, str]] = None,
        top: str = "my-skill/",
        **kwargs: Any,
    ) -> Skill:
        payload = (
            files
            if files is not None
            else {"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY}
        )
        skill, _replaced = skills.install_skill(_zip(payload, top=top), **kwargs)
        return skill

    def _assert_rejected(self, data: bytes, status_code: int = 400) -> SkillError:
        with self.assertRaises(SkillError) as caught:
            skills.install_skill(data)
        self.assertEqual(caught.exception.status_code, status_code)
        return caught.exception

    def _skill_dir(self, skill_id: str = "my-skill") -> Path:
        return skills._SKILLS_DIR / skill_id

    def _bundle_file(self, relpath: str, skill_id: str = "my-skill") -> Path:
        return self._skill_dir(skill_id) / "bundle" / relpath

    def _temp_entries(self) -> List[str]:
        return [
            name
            for name in os.listdir(str(skills._SKILLS_DIR))
            if name.startswith(".tmp-") or name.startswith(".trash-")
        ]


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class SkillIngestTests(_SkillsDirTestCase):
    def test_minimal_skill_installs_with_lowercased_id(self) -> None:
        skill, replaced = skills.install_skill(
            _zip({"SKILL.md": _skill_md(name="My-Skill")}),
            source_filename="My-Skill.zip",
        )

        self.assertFalse(replaced)
        self.assertEqual(skill.id, "my-skill")
        self.assertEqual(skill.name, "my-skill")
        self.assertEqual(skill.version, 1)
        self.assertTrue(skill.enabled)
        self.assertEqual(skill.source_filename, "My-Skill.zip")
        self.assertEqual(skill.counts.get("references"), 0)
        self.assertTrue(self._bundle_file("SKILL.md").is_file())

        manifest = json.loads(
            (self._skill_dir() / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["id"], "my-skill")
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["skill_md_chars"], skill.skill_md_chars)
        self.assertEqual(manifest["references"], [])

    def test_manifest_indexes_references_and_bundled_files(self) -> None:
        skill = self._install(
            {
                "SKILL.md": _skill_md(),
                "references/alpha.md": ALPHA_BODY,
                "references/logo.png": "OTHER-SENTINEL",
                "evals/answers.md": "EVALS-SENTINEL",
                "scripts/run.py": "SCRIPTS-SENTINEL",
                "assets/logo.txt": "ASSETS-SENTINEL",
            }
        )

        self.assertEqual(skill.reference_paths(), ["references/alpha.md"])
        reference = skill.references[0]
        self.assertEqual(reference.chars, len(ALPHA_BODY))
        self.assertIn("Drafting Guardrails", reference.headings)
        self.assertIn("Default Non-Disclosure List", reference.headings)
        self.assertIn("ALPHA-REFERENCE-BODY", reference.summary)
        self.assertTrue(
            any("references/alpha.md" in mention for mention in reference.mentions),
            reference.mentions,
        )

        categories = {bundled.path: bundled.category for bundled in skill.bundled_files}
        self.assertEqual(categories.get("evals/answers.md"), "evals")
        self.assertEqual(categories.get("scripts/run.py"), "scripts")
        self.assertEqual(categories.get("assets/logo.txt"), "assets")
        self.assertEqual(categories.get("references/logo.png"), "other")
        self.assertEqual(skill.counts.get("references"), 1)
        self.assertEqual(skill.counts.get("evals"), 1)
        self.assertEqual(skill.counts.get("scripts"), 1)
        self.assertEqual(skill.counts.get("assets"), 1)
        self.assertEqual(skill.counts.get("other"), 1)
        self.assertTrue(self._bundle_file("evals/answers.md").is_file())

    def test_allowed_tools_frontmatter_records_a_warning(self) -> None:
        skill = self._install(
            {
                "SKILL.md": _skill_md(
                    extra_frontmatter=["allowed-tools: [Bash, Read]"]
                ),
            }
        )

        self.assertIn("allowed-tools", skill.frontmatter_extra)
        self.assertTrue(skill.warnings)

    def test_root_level_and_single_top_dir_normalize_identically(self) -> None:
        files = {"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY}

        root_skill = self._install(files, top="")
        root_detail = root_skill.to_detail()
        self.assertTrue(skills.delete_skill("my-skill"))

        nested_skill = self._install(files, top="my-skill/")
        nested_detail = nested_skill.to_detail()

        for key in ("id", "description", "skill_md_chars", "counts", "references"):
            self.assertEqual(root_detail[key], nested_detail[key], key)

    def test_multiple_top_level_directories_are_rejected(self) -> None:
        data = _zip({"a/SKILL.md": _skill_md(), "b/notes.md": "hello"}, top="")
        self._assert_rejected(data)

    def test_finder_junk_is_ignored(self) -> None:
        data = _zip(
            {
                "my-skill/SKILL.md": _skill_md(),
                "my-skill/references/alpha.md": ALPHA_BODY,
                "my-skill/.DS_Store": "junk",
                "my-skill/._SKILL.md": "junk",
                "__MACOSX/my-skill/._SKILL.md": "junk",
                "__MACOSX/._my-skill": "junk",
            },
            top="",
        )

        skill, _replaced = skills.install_skill(data)

        self.assertEqual(skill.id, "my-skill")
        self.assertEqual(skill.reference_paths(), ["references/alpha.md"])
        self.assertEqual(skill.bundled_files, [])
        self.assertFalse(self._bundle_file(".DS_Store").exists())
        self.assertFalse(self._bundle_file("._SKILL.md").exists())

    def test_windows_style_zip_without_unix_modes_installs(self) -> None:
        data = _zip_entries(
            [
                (_entry("my-skill/SKILL.md", external_attr=0), _skill_md()),
                (_entry("my-skill/references/alpha.md", external_attr=0), ALPHA_BODY),
            ]
        )

        skill, _replaced = skills.install_skill(data)

        self.assertEqual(skill.id, "my-skill")
        self.assertEqual(skill.reference_paths(), ["references/alpha.md"])

    def test_zip_slip_entry_is_rejected(self) -> None:
        data = _zip({"SKILL.md": _skill_md(), "../evil.md": "pwned"})
        self._assert_rejected(data)

    def test_absolute_path_entry_is_rejected(self) -> None:
        data = _zip({"my-skill/SKILL.md": _skill_md(), "/etc/passwd": "pwned"}, top="")
        self._assert_rejected(data)

    def test_backslash_separator_entry_is_rejected(self) -> None:
        data = _zip({"my-skill\\SKILL.md": _skill_md()}, top="")
        self._assert_rejected(data)

    def test_symlink_entry_is_rejected(self) -> None:
        link = _entry("my-skill/link.md", external_attr=(stat.S_IFLNK | 0o777) << 16)
        data = _zip_entries(
            [
                (_entry("my-skill/SKILL.md"), _skill_md()),
                (link, "../../../etc/passwd"),
            ]
        )
        self._assert_rejected(data)

    def test_encrypted_entry_is_rejected(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            info = _entry("my-skill/SKILL.md")
            archive.writestr(info, _skill_md())
            # writestr resets flag_bits; set the encryption bit afterwards so the
            # central directory carries it.
            info.flag_bits |= 0x1
        self._assert_rejected(buffer.getvalue())

    def test_entry_count_cap_is_enforced(self) -> None:
        data = _zip(
            {
                "SKILL.md": _skill_md(),
                "references/alpha.md": ALPHA_BODY,
                "references/beta.md": BETA_BODY,
            }
        )
        with patch.object(skills, "SKILLS_MAX_ENTRIES", 2):
            self._assert_rejected(data)

    def test_uncompressed_total_cap_is_enforced(self) -> None:
        data = _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY})
        with patch.object(skills, "SKILLS_MAX_UNCOMPRESSED_BYTES", 64):
            self._assert_rejected(data, status_code=413)

    def test_compression_ratio_bomb_is_rejected(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("my-skill/SKILL.md", _skill_md())
            archive.writestr("my-skill/references/alpha.md", "A" * 200_000)
        self._assert_rejected(buffer.getvalue(), status_code=413)

    def test_member_larger_than_its_declared_size_is_rejected(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr(_entry("my-skill/SKILL.md"), _skill_md())
            info = _entry("my-skill/references/alpha.md")
            archive.writestr(info, "B" * 5000)
            # Lie in the central directory: headers are attacker-controlled, so the
            # bounded read must not trust file_size.
            info.file_size = 16
        with self.assertRaises(SkillError):
            skills.install_skill(buffer.getvalue())

    def test_not_a_zip_is_rejected(self) -> None:
        self._assert_rejected(b"this is not a zip archive")

    def test_missing_skill_md_is_rejected(self) -> None:
        self._assert_rejected(_zip({"references/alpha.md": ALPHA_BODY}))

    def test_missing_name_is_rejected(self) -> None:
        text = "---\ndescription: A skill without a name.\n---\n\nBody.\n"
        self._assert_rejected(_zip({"SKILL.md": text}))

    def test_blank_name_is_rejected(self) -> None:
        text = '---\nname: "   "\ndescription: A skill.\n---\n\nBody.\n'
        self._assert_rejected(_zip({"SKILL.md": text}))

    def test_missing_description_is_rejected(self) -> None:
        text = "---\nname: my-skill\n---\n\nBody.\n"
        self._assert_rejected(_zip({"SKILL.md": text}))

    def test_blank_description_is_rejected(self) -> None:
        text = '---\nname: my-skill\ndescription: ""\n---\n\nBody.\n'
        self._assert_rejected(_zip({"SKILL.md": text}))

    def test_overlong_description_is_rejected(self) -> None:
        text = _skill_md(description="d" * (SKILL_DESCRIPTION_MAX_CHARS + 1))
        self._assert_rejected(_zip({"SKILL.md": text}))

    def test_invalid_name_charset_is_rejected(self) -> None:
        self._assert_rejected(_zip({"SKILL.md": _skill_md(name="my skill!")}))

    def test_empty_body_is_rejected(self) -> None:
        text = "---\nname: my-skill\ndescription: A skill.\n---\n\n   \n"
        self._assert_rejected(_zip({"SKILL.md": text}))

    def test_folded_description_parses(self) -> None:
        text = "\n".join(
            [
                "---",
                "name: my-skill",
                "description: >-",
                "  Context-aware guarded drafting for vendor replies",
                "  and price negotiations.",
                "---",
                "",
                "Body of the skill.",
                "",
            ]
        )

        skill = self._install({"SKILL.md": text})

        self.assertEqual(
            skill.description,
            "Context-aware guarded drafting for vendor replies and price negotiations.",
        )

    def test_bom_prefixed_skill_md_parses(self) -> None:
        skill = self._install({"SKILL.md": "\ufeff" + _skill_md()})

        self.assertEqual(skill.id, "my-skill")
        self.assertTrue(skill.description)

    def test_parse_frontmatter_splits_mapping_and_body(self) -> None:
        frontmatter, body = skills.parse_frontmatter(_skill_md())

        self.assertEqual(frontmatter.get("name"), "My-Skill")
        self.assertNotIn("---", body.split("\n")[0])
        self.assertIn("Operating instructions", body)

    def test_inspect_archive_strips_the_single_top_directory(self) -> None:
        extracted = skills.inspect_archive(
            _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY})
        )

        self.assertEqual(
            sorted(extracted), ["SKILL.md", "references/alpha.md"]
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SkillStoreTests(_SkillsDirTestCase):
    def test_duplicate_upload_conflicts_without_replace(self) -> None:
        self._install()

        error = self._assert_rejected(
            _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY}),
            status_code=409,
        )

        self.assertIn("my-skill", str(error))
        existing = skills.get_skill("my-skill")
        assert existing is not None
        self.assertEqual(existing.version, 1)

    def test_replace_bumps_version_and_drops_removed_files(self) -> None:
        self._install(
            {
                "SKILL.md": _skill_md(),
                "references/alpha.md": ALPHA_BODY,
                "references/beta.md": BETA_BODY,
            }
        )
        skills.set_skill_enabled("my-skill", False)
        first = skills.get_skill("my-skill")
        assert first is not None

        replaced_skill, replaced = skills.install_skill(
            _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY}),
            replace=True,
        )

        self.assertTrue(replaced)
        self.assertEqual(replaced_skill.version, 2)
        self.assertFalse(replaced_skill.enabled)
        self.assertEqual(replaced_skill.created_at, first.created_at)
        self.assertEqual(replaced_skill.reference_paths(), ["references/alpha.md"])
        self.assertFalse(self._bundle_file("references/beta.md").exists())
        self.assertEqual(self._temp_entries(), [])

    def test_corrupt_replacement_leaves_the_original_intact(self) -> None:
        self._install()

        with self.assertRaises(SkillError):
            skills.install_skill(b"not a zip at all", replace=True)

        survivor = skills.get_skill("my-skill")
        assert survivor is not None
        self.assertEqual(survivor.version, 1)
        self.assertEqual(survivor.reference_paths(), ["references/alpha.md"])
        self.assertEqual(self._temp_entries(), [])

    def test_failed_replacement_leaves_no_staging_residue(self) -> None:
        self._install()

        with patch.object(skills, "_write_json_file", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                skills.install_skill(
                    _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY}),
                    replace=True,
                )

        survivor = skills.get_skill("my-skill")
        assert survivor is not None
        self.assertEqual(survivor.version, 1)
        self.assertEqual(self._temp_entries(), [])

    def test_delete_skill_is_idempotent(self) -> None:
        self._install()

        self.assertTrue(skills.delete_skill("my-skill"))
        self.assertFalse(skills.delete_skill("my-skill"))
        self.assertIsNone(skills.get_skill("my-skill"))
        self.assertEqual(skills.list_skills(), [])

    def test_load_skills_preserves_request_order_and_reports_missing(self) -> None:
        self._install({"SKILL.md": _skill_md(name="alpha-skill")})
        self._install({"SKILL.md": _skill_md(name="beta-skill")})

        found, missing = skills.load_skills(
            ["beta-skill", "ghost-skill", "alpha-skill"]
        )

        self.assertEqual([skill.id for skill in found], ["beta-skill", "alpha-skill"])
        self.assertEqual(missing, ["ghost-skill"])

    def test_manifest_id_directory_mismatch_is_skipped(self) -> None:
        self._install()
        manifest_path = self._skill_dir() / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["id"] = "some-other-skill"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        self.assertEqual(skills.list_skills(), [])
        self.assertIsNone(skills.get_skill("my-skill"))

    def test_set_skill_enabled_round_trips(self) -> None:
        self._install()

        disabled = skills.set_skill_enabled("my-skill", False)
        self.assertFalse(disabled.enabled)

        reloaded = skills.get_skill("my-skill")
        assert reloaded is not None
        self.assertFalse(reloaded.enabled)
        self.assertEqual(reloaded.version, 1)

    def test_read_skill_markdown_returns_raw_frontmatter(self) -> None:
        self._install()

        markdown = skills.read_skill_markdown("my-skill")

        assert markdown is not None
        self.assertTrue(markdown.lstrip().startswith("---"))
        self.assertIn("name: My-Skill", markdown)

    def test_read_reference_only_serves_manifest_references(self) -> None:
        self._install(
            {
                "SKILL.md": _skill_md(),
                "references/alpha.md": ALPHA_BODY,
                "evals/answers.md": "EVALS-SENTINEL",
            }
        )

        served = skills.read_reference("my-skill", "references/alpha.md")
        assert served is not None
        self.assertIn("ALPHA-REFERENCE-BODY", served)

        self.assertIsNone(skills.read_reference("my-skill", "../manifest.json"))
        self.assertIsNone(
            skills.read_reference("my-skill", "references/../evals/answers.md")
        )
        self.assertIsNone(skills.read_reference("my-skill", "evals/answers.md"))
        self.assertIsNone(skills.read_reference("my-skill", "references/ghost.md"))
        self.assertIsNone(skills.read_reference("ghost-skill", "references/alpha.md"))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class SkillRenderingTests(_SkillsDirTestCase):
    def _render(
        self,
        selected: Optional[Dict[str, List[str]]] = None,
        max_chars: int = 24000,
        reasons: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        skill = skills.get_skill("my-skill")
        assert skill is not None
        return skills.render_skills_context(
            [skill], selected or {}, max_chars, reasons
        )

    def test_rendered_block_omits_frontmatter_and_unused_bundle_files(self) -> None:
        self._install(
            {
                "SKILL.md": _skill_md(
                    extra_frontmatter=["x-secret: FRONTMATTER-SENTINEL"]
                ),
                "references/alpha.md": ALPHA_BODY,
                "references/logo.png": "OTHER-SENTINEL",
                "evals/answers.md": "EVALS-SENTINEL",
                "scripts/run.py": "SCRIPTS-SENTINEL",
                "assets/logo.txt": "ASSETS-SENTINEL",
            }
        )

        rendered, report = self._render({"my-skill": ["references/alpha.md"]})

        assert rendered is not None
        self.assertIn("<skills>", rendered)
        self.assertTrue(rendered.rstrip().endswith("</skills>"))
        self.assertIn("## Skill: my-skill", rendered)
        self.assertIn("ALPHA-REFERENCE-BODY", rendered)
        self.assertNotIn("FRONTMATTER-SENTINEL", rendered)
        self.assertNotIn("x-secret", rendered)
        self.assertNotIn("name: My-Skill", rendered)
        self.assertNotIn("EVALS-SENTINEL", rendered)
        self.assertNotIn("SCRIPTS-SENTINEL", rendered)
        self.assertNotIn("ASSETS-SENTINEL", rendered)
        self.assertNotIn("OTHER-SENTINEL", rendered)
        self.assertEqual(report["chars"], len(rendered))
        self.assertEqual(report["resolved"][0]["id"], "my-skill")
        self.assertEqual(
            report["resolved"][0]["references"][0]["path"], "references/alpha.md"
        )
        self.assertEqual(report["resolved"][0]["bundled_unused"]["evals"], 1)

    def test_only_selected_references_are_inlined(self) -> None:
        self._install(
            {
                "SKILL.md": _skill_md(),
                "references/alpha.md": ALPHA_BODY,
                "references/beta.md": BETA_BODY,
            }
        )

        rendered, report = self._render({"my-skill": ["references/alpha.md"]})

        assert rendered is not None
        self.assertIn("ALPHA-REFERENCE-BODY", rendered)
        self.assertNotIn("BETA-REFERENCE-BODY", rendered)
        self.assertEqual(len(report["resolved"][0]["references"]), 1)

    def test_router_reasons_reach_the_packing_report(self) -> None:
        self._install()

        _rendered, report = self._render(
            {"my-skill": ["references/alpha.md"]},
            reasons={"my-skill": {"references/alpha.md": "guarded vendor reply"}},
        )

        self.assertEqual(
            report["resolved"][0]["references"][0]["reason"], "guarded vendor reply"
        )

    def test_over_budget_drops_whole_references(self) -> None:
        big = (
            "BIG-HEAD\n"
            + ("filler text for the budget squeeze. " * 120)
            + "\nBIG-TAIL"
        )
        self._install(
            {
                "SKILL.md": _skill_md(),
                "references/big.md": big,
                "references/small.md": "SMALL-BODY stays whole.",
            }
        )

        rendered, report = self._render(
            {"my-skill": ["references/big.md", "references/small.md"]},
            max_chars=1500,
        )

        assert rendered is not None
        self.assertLessEqual(len(rendered), 1500)
        self.assertNotIn("BIG-HEAD", rendered)
        self.assertNotIn("BIG-TAIL", rendered)
        # Whatever survives the squeeze survives whole - never a partial file.
        self.assertIn("SMALL-BODY stays whole.", rendered)
        self.assertEqual(report["dropped_references"], ["my-skill/references/big.md"])
        skipped = report["resolved"][0]["references_skipped"]
        self.assertEqual([item["path"] for item in skipped], ["references/big.md"])
        self.assertEqual(
            [item["path"] for item in report["resolved"][0]["references"]],
            ["references/small.md"],
        )

    def test_oversized_skill_md_is_truncated_with_a_marker(self) -> None:
        body = "HEAD-MARKER\n" + ("lorem ipsum dolor sit amet " * 250) + "\nTAIL-MARKER"
        self._install({"SKILL.md": _skill_md(body=body)})

        rendered, report = self._render(max_chars=1200)

        assert rendered is not None
        self.assertLessEqual(len(rendered), 1200)
        self.assertIn("HEAD-MARKER", rendered)
        self.assertNotIn("TAIL-MARKER", rendered)
        self.assertIn(skills._TRUNCATION_MARKER, rendered)
        self.assertEqual(report["truncated_skills"], ["my-skill"])
        self.assertTrue(report["resolved"][0]["truncated"])

    def test_empty_selection_renders_nothing(self) -> None:
        rendered, report = skills.render_skills_context([], {}, 24000)

        self.assertIsNone(rendered)
        self.assertEqual(report["resolved"], [])
        self.assertEqual(report["chars"], 0)

    def test_zero_budget_renders_nothing(self) -> None:
        self._install()

        rendered, _report = self._render({"my-skill": ["references/alpha.md"]}, 0)

        self.assertIsNone(rendered)

    def test_render_is_byte_identical_across_calls(self) -> None:
        self._install(
            {
                "SKILL.md": _skill_md(),
                "references/alpha.md": ALPHA_BODY,
                "references/beta.md": BETA_BODY,
            }
        )
        selected = {"my-skill": ["references/alpha.md", "references/beta.md"]}

        first_text, first_report = self._render(selected, max_chars=4000)
        second_text, second_report = self._render(selected, max_chars=4000)

        self.assertIsNotNone(first_text)
        self.assertEqual(first_text, second_text)
        self.assertEqual(
            json.dumps(first_report, sort_keys=True),
            json.dumps(second_report, sort_keys=True),
        )


# ---------------------------------------------------------------------------
# Router parsing and deterministic fallback
# ---------------------------------------------------------------------------


class ReferenceSelectionParserTests(unittest.TestCase):
    VALID_IDS = {1, 2, 3, 4}

    def _coerce(
        self,
        payload: Any,
        max_selected: int = 4,
        valid_ids: Optional[set] = None,
    ) -> Dict[str, Any]:
        return council._coerce_skill_reference_selection(
            payload,
            self.VALID_IDS if valid_ids is None else valid_ids,
            max_selected,
        )

    def test_accepts_integer_list(self) -> None:
        result = self._coerce({"selected": [1, 4]})

        self.assertTrue(result["parsed"])
        self.assertEqual(result["selected"], [1, 4])

    def test_accepts_string_ids(self) -> None:
        self.assertEqual(self._coerce({"selected": ["1", "4"]})["selected"], [1, 4])

    def test_accepts_comma_separated_string(self) -> None:
        self.assertEqual(self._coerce({"selected": "1, 4"})["selected"], [1, 4])

    def test_accepts_object_ids(self) -> None:
        self.assertEqual(self._coerce({"selected": [{"id": 1}]})["selected"], [1])

    def test_accepts_aliases(self) -> None:
        for key in ("selected_ids", "references", "ids"):
            with self.subTest(key=key):
                result = self._coerce({key: [2]})
                self.assertTrue(result["parsed"])
                self.assertEqual(result["selected"], [2])

    def test_drops_unknown_ids_and_duplicates(self) -> None:
        result = self._coerce({"selected": [1, 99, 1, 0, -3]})

        self.assertEqual(result["selected"], [1])

    def test_caps_at_max_selected(self) -> None:
        result = self._coerce({"selected": [1, 2, 3, 4]}, max_selected=2)

        self.assertEqual(len(result["selected"]), 2)

    def test_explicit_empty_selection_is_parsed(self) -> None:
        result = self._coerce({"selected": []})

        self.assertTrue(result["parsed"])
        self.assertEqual(result["selected"], [])

    def test_unrecognizable_payload_is_not_parsed(self) -> None:
        for payload in ({"foo": 1}, None, "nonsense", []):
            with self.subTest(payload=payload):
                result = self._coerce(payload)
                self.assertFalse(result["parsed"])
                self.assertEqual(result["selected"], [])

    def test_reasons_are_keyed_by_candidate_id(self) -> None:
        result = self._coerce(
            {"selected": [1], "reasons": {"1": "query is a guarded vendor reply"}}
        )

        reasons = {int(key): value for key, value in result["reasons"].items()}
        self.assertEqual(reasons, {1: "query is a guarded vendor reply"})


class FallbackReferenceSelectionTests(unittest.TestCase):
    def test_respects_the_count_cap(self) -> None:
        candidates = [
            _candidate(index, f"references/{index}.md", 100)
            for index in range(1, 6)
        ]

        selected = skills.fallback_reference_ids(
            candidates, "vendor pricing", max_references=2, char_budget=100_000
        )

        self.assertEqual(len(selected), 2)

    def test_packs_whole_files_inside_the_char_budget(self) -> None:
        candidates = [
            _candidate(1, "references/a.md", 1000),
            _candidate(2, "references/b.md", 1000),
            _candidate(3, "references/c.md", 1000),
        ]

        selected = skills.fallback_reference_ids(
            candidates, "vendor pricing", max_references=5, char_budget=2500
        )

        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        total = sum(by_id[candidate_id].chars for candidate_id in selected)
        self.assertEqual(len(selected), 2)
        self.assertLessEqual(total, 2500)

    def test_prefers_token_overlap_over_size(self) -> None:
        irrelevant = _candidate(1, "references/gardening.md", 100, headings=["Mulch"])
        relevant = _candidate(
            2,
            "references/vendor-pricing.md",
            5000,
            headings=["Vendor pricing pushback"],
            mentions=["Load references/vendor-pricing.md before a vendor reply."],
        )

        selected = skills.fallback_reference_ids(
            [irrelevant, relevant],
            "Draft a reply rejecting a vendor pricing increase",
            max_references=1,
            char_budget=100_000,
        )

        self.assertEqual(selected, [2])

    def test_returns_empty_without_candidates_or_budget(self) -> None:
        candidates = [_candidate(1, "references/a.md", 100)]

        self.assertEqual(skills.fallback_reference_ids([], "q", 4, 1000), [])
        self.assertEqual(skills.fallback_reference_ids(candidates, "q", 0, 1000), [])
        self.assertEqual(skills.fallback_reference_ids(candidates, "q", 4, 0), [])

    def test_build_reference_candidates_numbers_from_one(self) -> None:
        skill = Skill(
            id="my-skill",
            name="my-skill",
            description="A skill.",
            version=1,
            enabled=True,
            created_at="",
            updated_at="",
            source_filename=None,
            archive_sha256="",
            skill_md_chars=10,
            references=[
                skills.SkillReference(
                    path="references/alpha.md",
                    chars=10,
                    headings=["Alpha"],
                    mentions=[],
                    summary="Alpha",
                ),
                skills.SkillReference(
                    path="references/beta.md",
                    chars=20,
                    headings=["Beta"],
                    mentions=[],
                    summary="Beta",
                ),
            ],
        )

        candidates = skills.build_reference_candidates([skill])

        self.assertEqual([item.candidate_id for item in candidates], [1, 2])
        self.assertEqual(
            [item.path for item in candidates],
            ["references/alpha.md", "references/beta.md"],
        )


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


class Stage1SystemPromptTests(unittest.TestCase):
    def _interactive_context(self) -> FlowContext:
        context = FlowContext(
            trace=TraceCollector(),
            collected_responses={
                "pre_query_config": PromptResponse(
                    prompt_id="pre_query_config",
                    responses={
                        "domain": "coding",
                        "style": "concise",
                        "priority": "accuracy",
                    },
                )
            },
        )
        context.refresh_interactive_config()
        return context

    def test_combine_blocks_joins_with_a_blank_line(self) -> None:
        self.assertEqual(langgraph_flow._combine_blocks(["a", None, "b"]), "a\n\nb")
        self.assertIsNone(langgraph_flow._combine_blocks([]))
        self.assertIsNone(langgraph_flow._combine_blocks([None, "  "]))

    def test_stage1_system_prompt_joins_skills_and_modifier_with_two_newlines(
        self,
    ) -> None:
        context = self._interactive_context()
        block = "<skills>\nSkill instructions.\n</skills>"
        context.skills_runtime = langgraph_flow.SkillsRuntime(
            stage1_block=block, stage3_block=None, metadata={}
        )

        prompt = langgraph_flow._stage1_system_prompt(context)
        modifier = langgraph_flow._interactive_modifier(context)

        assert prompt is not None
        assert modifier is not None
        self.assertEqual(prompt, f"{block}\n\n{modifier}")
        self.assertNotIn("</skills> ", prompt)

    def test_stage1_system_prompt_is_none_without_skills_or_modifier(self) -> None:
        context = FlowContext(trace=TraceCollector())

        self.assertIsNone(langgraph_flow._stage1_system_prompt(context))


class SkillsRuntimeTests(_SkillsDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.router_calls: List[Any] = []
        skills.install_skill(
            _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY})
        )

    @contextlib.contextmanager
    def _no_router(self) -> Any:
        async def _fake_router(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            self.router_calls.append((args, kwargs))
            return {
                "selected": [],
                "reasons": {},
                "parsed": True,
                "model": "unused",
                "error": None,
            }

        with contextlib.ExitStack() as stack:
            patched = False
            for module in (council, langgraph_flow):
                if hasattr(module, "select_skill_references"):
                    stack.enter_context(
                        patch.object(module, "select_skill_references", _fake_router)
                    )
                    patched = True
            if not patched:
                self.fail(
                    "select_skill_references is not exposed on council or "
                    "langgraph_flow"
                )
            yield

    def _persisted_metadata(self, version: int = 1) -> Dict[str, Any]:
        return {
            "requested": ["my-skill"],
            "resolved": [
                {
                    "id": "my-skill",
                    "version": version,
                    "skill_md_chars": 100,
                    "truncated": False,
                    "references": [
                        {
                            "path": "references/alpha.md",
                            "chars": len(ALPHA_BODY),
                            "reason": "persisted plan",
                        }
                    ],
                    "references_skipped": [],
                    "bundled_unused": {
                        "scripts": 0,
                        "assets": 0,
                        "evals": 0,
                        "other": 0,
                    },
                }
            ],
            "missing": [],
            "reference_selection": {
                "enabled": True,
                "model": "test-model",
                "candidate_count": 1,
                "selected_count": 1,
                "fallback": None,
            },
            "budget": {
                "stage1_chars": 100,
                "stage1_max": 24000,
                "stage3_chars": 100,
                "stage3_max": 12000,
                "stage3_dropped_references": [],
            },
            "drift": False,
        }

    def test_persisted_plan_is_reused_without_a_router_call(self) -> None:
        context = FlowContext(trace=TraceCollector(), skill_ids=["my-skill"])
        state: Dict[str, Any] = {"metadata": {"skills": self._persisted_metadata()}}

        with self._no_router():
            asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        self.assertEqual(self.router_calls, [])
        runtime = context.skills_runtime
        assert runtime is not None
        assert runtime.stage1_block is not None
        self.assertIn("## Skill: my-skill", runtime.stage1_block)
        self.assertIn("ALPHA-REFERENCE-BODY", runtime.stage1_block)
        self.assertEqual(runtime.metadata["requested"], ["my-skill"])
        self.assertFalse(runtime.metadata.get("drift"))

    def test_router_runs_when_there_is_no_persisted_plan(self) -> None:
        # Control for the test above: proves the patched router is reachable, so
        # "zero calls" with a persisted plan is a real result and not a no-op.
        context = FlowContext(trace=TraceCollector(), skill_ids=["my-skill"])
        state: Dict[str, Any] = {"user_query": "Draft a guarded vendor reply"}

        with patch.object(langgraph_flow, "SKILLS_REFERENCE_SELECTION_ENABLED", True):
            with self._no_router():
                asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        self.assertEqual(len(self.router_calls), 1)
        runtime = context.skills_runtime
        assert runtime is not None
        assert runtime.stage1_block is not None
        self.assertIn("## Skill: my-skill", runtime.stage1_block)
        # The stub selected nothing, so no reference body is inlined.
        self.assertNotIn("ALPHA-REFERENCE-BODY", runtime.stage1_block)

    def test_disabled_router_falls_back_deterministically(self) -> None:
        context = FlowContext(trace=TraceCollector(), skill_ids=["my-skill"])
        state: Dict[str, Any] = {"user_query": "Draft a guarded vendor reply"}

        with patch.object(langgraph_flow, "SKILLS_REFERENCE_SELECTION_ENABLED", False):
            with self._no_router():
                asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        self.assertEqual(self.router_calls, [])
        runtime = context.skills_runtime
        assert runtime is not None
        assert runtime.stage1_block is not None
        self.assertIn("ALPHA-REFERENCE-BODY", runtime.stage1_block)
        self.assertEqual(
            runtime.metadata["reference_selection"]["fallback"], "router_disabled"
        )

    def test_missing_skill_is_reported_not_fatal(self) -> None:
        context = FlowContext(
            trace=TraceCollector(), skill_ids=["my-skill", "ghost-skill"]
        )
        state: Dict[str, Any] = {"user_query": "Draft a guarded vendor reply"}

        with patch.object(langgraph_flow, "SKILLS_REFERENCE_SELECTION_ENABLED", False):
            with self._no_router():
                asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        runtime = context.skills_runtime
        assert runtime is not None
        self.assertEqual(runtime.metadata["missing"], ["ghost-skill"])
        self.assertEqual(
            [entry["id"] for entry in runtime.metadata["resolved"]], ["my-skill"]
        )

    def test_runtime_is_cached_after_the_first_call(self) -> None:
        context = FlowContext(trace=TraceCollector(), skill_ids=["my-skill"])
        state: Dict[str, Any] = {"metadata": {"skills": self._persisted_metadata()}}

        with self._no_router():
            asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))
            first = context.skills_runtime
            asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        self.assertIs(context.skills_runtime, first)
        self.assertEqual(self.router_calls, [])

    def test_version_mismatch_sets_drift(self) -> None:
        context = FlowContext(trace=TraceCollector(), skill_ids=["my-skill"])
        state: Dict[str, Any] = {
            "metadata": {"skills": self._persisted_metadata(version=99)}
        }

        with self._no_router():
            asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        self.assertEqual(self.router_calls, [])
        runtime = context.skills_runtime
        assert runtime is not None
        self.assertTrue(runtime.metadata.get("drift"))

    def test_no_skill_ids_yields_an_empty_runtime(self) -> None:
        context = FlowContext(trace=TraceCollector())

        with self._no_router():
            asyncio.run(langgraph_flow._ensure_skills_runtime(context, {}))

        runtime = context.skills_runtime
        assert runtime is not None
        self.assertIsNone(runtime.stage1_block)
        self.assertIsNone(runtime.stage3_block)
        self.assertEqual(self.router_calls, [])

    def test_persisted_plan_is_reused_even_when_a_skill_was_deleted(self) -> None:
        """A skill deleted mid-flight must not cost the survivors their plan.

        Discarding the whole persisted plan would re-run the router on resume and
        could hand Stage 3 a different reference set than Stage 1 ran under.
        """
        context = FlowContext(
            trace=TraceCollector(), skill_ids=["my-skill", "ghost-skill"]
        )
        persisted = self._persisted_metadata()
        persisted["requested"] = ["my-skill", "ghost-skill"]
        state: Dict[str, Any] = {"metadata": {"skills": persisted}}

        with self._no_router():
            asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        self.assertEqual(self.router_calls, [])
        runtime = context.skills_runtime
        assert runtime is not None
        self.assertEqual(runtime.metadata["missing"], ["ghost-skill"])
        self.assertEqual(
            [entry["id"] for entry in runtime.metadata["resolved"]], ["my-skill"]
        )
        self.assertEqual(
            [ref["path"] for ref in runtime.metadata["resolved"][0]["references"]],
            ["references/alpha.md"],
        )

    def test_resolved_plan_survives_a_downstream_metadata_write(self) -> None:
        """Regression: stage2_node must not overwrite the plan with the seed.

        ``_ensure_skills_runtime`` writes the resolved plan straight to storage, but
        later nodes rebuild metadata as ``{**state["metadata"], ...}`` and
        ``update_assistant_message`` merges only one level deep. Without
        ``_state_metadata_with_skills`` the ``{"requested": [...]}`` seed silently
        replaces the resolved plan, breaking resynthesis, crash-resume, the markdown
        export and the UI summary.
        """
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        old_data_dir = storage.DATA_DIR
        storage.DATA_DIR = str(Path(tempdir.name) / "conversations")
        self.addCleanup(lambda: setattr(storage, "DATA_DIR", old_data_dir))

        conversation_id = "conv-skills-regression"
        storage.create_conversation(conversation_id)
        # The placeholder seed consultation_manager writes before Stage 1 runs.
        index = storage.add_assistant_placeholder(
            conversation_id, metadata={"skills": {"requested": ["my-skill"]}}
        )

        context = FlowContext(trace=TraceCollector(), skill_ids=["my-skill"])
        state: Dict[str, Any] = {
            "user_query": "Draft a guarded vendor reply",
            "metadata": {"skills": {"requested": ["my-skill"]}},
        }

        with patch.object(langgraph_flow, "SKILLS_REFERENCE_SELECTION_ENABLED", False):
            with self._no_router():
                asyncio.run(langgraph_flow._ensure_skills_runtime(context, state))

        runtime = context.skills_runtime
        assert runtime is not None
        storage.update_assistant_message(
            conversation_id, index, {"metadata": {"skills": runtime.metadata}}
        )

        # stage1_node hands the refreshed metadata back to the graph...
        propagated = langgraph_flow._state_metadata_with_skills(context, state)
        self.assertIn("resolved", propagated["skills"])
        self.assertIs(state["metadata"], propagated)

        # ...so a stage2_node-shaped write carries the plan instead of the seed.
        stage2_metadata = {**propagated, "label_to_model": {"Response A": "model-a"}}
        storage.update_assistant_message(
            conversation_id, index, {"stage2": [], "metadata": stage2_metadata}
        )

        stored = storage.get_conversation(conversation_id)["messages"][index]
        self.assertIn("resolved", stored["metadata"]["skills"])
        self.assertEqual(
            [entry["id"] for entry in stored["metadata"]["skills"]["resolved"]],
            ["my-skill"],
        )


class SkillIdsPlumbingTests(unittest.TestCase):
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
        # ConsultationManager is a process-wide singleton: never shut its executor
        # down here or every later test in the run loses its thread pool.
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

    def test_build_resume_metadata_round_trips_skill_ids(self) -> None:
        metadata = ConsultationManager._build_resume_metadata(
            "job-1", "content", None, skill_ids=["my-skill"]
        )

        self.assertEqual(metadata["resume"]["skill_ids"], ["my-skill"])

    def test_build_resume_metadata_none_does_not_wipe_stored_ids(self) -> None:
        stored = ConsultationManager._build_resume_metadata(
            "job-1", "content", None, skill_ids=["my-skill"]
        )

        merged = ConsultationManager._build_resume_metadata(
            "job-1", "content", None, existing=stored, skill_ids=None
        )

        self.assertEqual(merged["resume"]["skill_ids"], ["my-skill"])

    def test_skill_ids_survive_queued_message_promotion(self) -> None:
        conversation_id = "skillqueue"
        storage.create_conversation(conversation_id)
        with patch.object(
            ConsultationManager, "_run_consultation", lambda self, state: None
        ):
            active = self.manager._create_started_state(
                "job-active",
                conversation_id,
                "first question",
                None,
                resume_from_storage=False,
                skill_ids=["my-skill"],
            )
            with self.manager._lock:
                self.manager._consultations[conversation_id] = active
                self.manager._queues[conversation_id] = [
                    QueuedMessage(
                        id="job-queued",
                        content="second question",
                        negative_prompt=None,
                        skill_ids=["my-skill"],
                    )
                ]

            self.manager._on_consultation_complete(active)

        promoted = self.manager._consultations[conversation_id]
        self.assertEqual(promoted.id, "job-queued")
        self.assertEqual(list(promoted.skill_ids), ["my-skill"])


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


class SkillsUploadRouteTests(_SkillsDirTestCase):
    def test_raw_zip_upload_returns_201(self) -> None:
        # Imported inside the test so an import-time failure in main.py cannot mask
        # the rest of this module's coverage.
        from backend.main import app

        payload = _zip({"SKILL.md": _skill_md(), "references/alpha.md": ALPHA_BODY})

        async def _upload() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.post(
                    "/api/skills",
                    params={"filename": "my-skill.zip"},
                    content=payload,
                    headers={
                        "X-API-Key": API_AUTH_KEY,
                        "Content-Type": "application/zip",
                    },
                )

        response = asyncio.run(_upload())

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["skill"]["id"], "my-skill")
        self.assertFalse(body["replaced"])
        self.assertEqual(body["skill"]["reference_count"], 1)
        self.assertIsNotNone(skills.get_skill("my-skill"))


if __name__ == "__main__":
    unittest.main()
