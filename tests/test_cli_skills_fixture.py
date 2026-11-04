"""The CLI list fixture follows the real backend Skill summary contract."""

import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("OPENROUTER_API_KEY", "synthetic-unused-key")
os.environ.setdefault("API_AUTH_KEY", "synthetic-unused-key")

from backend.skills import Skill


class CliSkillFixtureTests(unittest.TestCase):
    def test_fixture_matches_backend_summary_types_and_fields(self):
        summaries = [
            Skill(
                id=identity,
                name=name,
                description=description,
                version=1,
                enabled=enabled,
                created_at="2026-11-01T00:00:00",
                updated_at="2026-11-01T00:00:00",
                source_filename=None,
                archive_sha256="0" * 64,
                skill_md_chars=42,
                source=source,
            ).to_summary()
            for identity, name, description, source, enabled in (
                ("first", "Первый", "Useful guidance", "uploaded", True),
                ("second", "Second", "Not selected", "codex", False),
            )
        ]
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/cli/skills-list.json").read_text()
        )
        self.assertEqual(fixture["skills"], summaries)
        self.assertEqual(fixture["sources"], {"codex": True, "claude": False})
